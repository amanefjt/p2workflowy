"""
p2workflowy V2: Gemini API クライアント
google-genai SDK を使用したリトライ付き LLM 呼び出しラッパー。
"""

import json
import time
import asyncio
import enum
import re
import csv
import random
from typing import Any, List, Tuple, Dict, Optional
from datetime import datetime
import threading
from pathlib import Path

from google import genai
from google.genai import types
from aiolimiter import AsyncLimiter
from .config import GEMINI_API_KEY, STATE_DIR, load_coreprompts, print_log


# プロンプト定数の読み込み
_PROMPTS: dict | None = None

def _get_prompts() -> dict:
    global _PROMPTS
    if _PROMPTS is None:
        _PROMPTS = load_coreprompts()
    return _PROMPTS


def get_default_model(purpose: str = "default") -> str:
    """coreprompts.json から用途に応じた DEFAULT_MODEL を取得。
    
    Args:
        purpose: "default" (CLI/高品質), "free" (無料ティア/Web), "vlm" (OCR/画像認識)
    """
    prompts = _get_prompts()
    
    # ティア状態のチェック: FREE であれば "default" リクエストも "free" 用に振り分ける
    effective_purpose = purpose
    if purpose == "default" and tier_manager.current_tier == GeminiTier.FREE:
        effective_purpose = "free"

    if effective_purpose == "free":
        return prompts.get("DEFAULT_MODEL_FREE", prompts.get("DEFAULT_MODEL", "gemini-3.1-flash-lite"))
    elif effective_purpose == "vlm":
        return prompts.get("DEFAULT_MODEL_VLM", "gemini-3.1-flash-lite")
    return prompts.get("DEFAULT_MODEL", "gemini-3-flash-preview")


class GeminiTier(enum.Enum):
    PAID = "paid"
    FREE = "free"
    UNKNOWN = "unknown"

class TierManager:
    """APIキーのティア（有料/無料）状態を管理するシングルトン。"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.current_tier = GeminiTier.UNKNOWN
            cls._instance.was_downgraded = False
        return cls._instance

    def set_tier(self, tier: GeminiTier):
        if self.current_tier != tier:
            print_log(f"  [TierManager] Tier set to: {tier.value}")
            self.current_tier = tier
        # ティアを再設定した時点で、過去のダウンシフト状態はクリアする
        self.was_downgraded = False

    def downgrade(self):
        # ログは初回のみ出力するが、ティアの設定自体は常に行う
        if not self.was_downgraded:
            print_log("  [TierManager] !!! Resource Limit/High Demand (503/429) detected. Downgrading to FREE tier mode (Lite). !!!")
            self.was_downgraded = True
        self.current_tier = GeminiTier.FREE

tier_manager = TierManager()


# Gemini クライアントのキャッシュ（シングルトン辞書）
_CLIENTS: Dict[str, genai.Client] = {}

def _get_client(api_key: str | None = None) -> genai.Client:
    """APIキーごとにクライアントをキャッシュして提供する。"""
    global _CLIENTS
    
    key = api_key or GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY (または GOOGLE_API_KEY) がセットされていません。.env ファイルを確認してください。")
    
    if key not in _CLIENTS:
        _CLIENTS[key] = genai.Client(api_key=key)
    return _CLIENTS[key]


def run_async(coro):
    """環境（Jupyter, FastAPI等）に応じた解決策で非同期処理を実行する。"""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # すでにループが走っている場合
            try:
                import nest_asyncio
                nest_asyncio.apply()
                return asyncio.run(coro)
            except ImportError:
                return loop.run_until_complete(coro)
    except RuntimeError:
        # ループが走っていない通常環境
        return asyncio.run(coro)



# ファイル書き込み時のヘッダー競合を防ぐためのグローバルロック
_METRICS_LOCK = threading.Lock()

def _dump_debug_prompt(prompt: Any, log_dir: Optional[Any], metrics_metadata: Optional[dict], is_async: bool = False):
    """プロンプトをデバッグファイルにダンプする。"""
    try:
        debug_id = "default_async" if is_async else "default"
        if metrics_metadata and isinstance(metrics_metadata, dict):
            m = metrics_metadata
            prefix = "async_" if is_async else ""
            debug_id = f"{prefix}{m.get('section', 'unknown')}_{m.get('batch_id', 'unknown')}"
        
        target_dir = log_dir if log_dir else STATE_DIR
        debug_path = target_dir / f"debug_prompt_{debug_id}.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(str(prompt))
    except:
        pass

def _build_gemini_config(model: str, thinking_level: Optional[str], **kwargs) -> types.GenerateContentConfig:
    """Gemini API 公開用 Config オブジェクトを構築する。"""
    config_kwargs: dict[str, Any] = {
        "max_output_tokens": kwargs.get("max_output_tokens", 65536),
        "temperature": kwargs.get("temperature", 0.3),
    }
    if kwargs.get("response_mime_type"):
        config_kwargs["response_mime_type"] = kwargs["response_mime_type"]
    if kwargs.get("response_schema"):
        config_kwargs["response_schema"] = kwargs["response_schema"]

    # Gemini 3.x 以上のFlash/Lite および Thinkingモデルで有効
    if model and ("gemini-3" in model.lower() or "thinking" in model.lower()):
        level = thinking_level or "High"
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level.upper())

    safety_settings = [
        types.SafetySetting(category=cat, threshold="BLOCK_NONE")
        for cat in [
            "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
    ]
    return types.GenerateContentConfig(safety_settings=safety_settings, **config_kwargs)

def _log_metrics(metrics_metadata: dict, prompt_len: int, p_tokens: int, c_tokens: int, ttft: float, tps: float, duration: float):
    """計測されたメトリクスを CSV に記録する。"""
    try:
        section = metrics_metadata.get("section", "N/A")
        batch_id = metrics_metadata.get("batch_id", "N/A")
        csv_path = Path(metrics_metadata.get("csv_path", str(STATE_DIR / "ttft_metrics.csv")))
        
        with _METRICS_LOCK:
            file_exists = csv_path.exists()
            with open(csv_path, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "section", "batch_id", "input_chars", "p_tokens", "c_tokens", "ttft", "tps", "duration"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    section,
                    batch_id,
                    prompt_len,
                    p_tokens,
                    c_tokens,
                    f"{ttft:.3f}",
                    f"{tps:.1f}",
                    f"{duration:.2f}"
                ])
    except Exception as e_log:
        print_log(f"  [LLM] Metrics logging failed: {e_log}")


def _calc_retry_wait(msg: str, attempt: int, retry_delay: float) -> tuple[float, bool]:
    """429/503 ならダウンシフトしてバックオフ秒数を計算する。戻り値: (wait_seconds, is_resource_limit)"""
    is_resource_limit = any(code in msg for code in ["429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE"])
    if is_resource_limit:
        tier_manager.downgrade()
        match = re.search(r"retry in ([\d\.]+)s", msg)
        base_wait = float(match.group(1)) + 1.0 if match else (10.0 * attempt)
        jitter = random.uniform(0, base_wait * 0.3)
        return base_wait + jitter, True
    return retry_delay * attempt, False


def call_gemini(
    prompt: str | list,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    thinking_level: str | None = None,
    max_retries: int = 5,
    retry_delay: float = 3.0,
    log_dir: Optional[Any] = None, # Pathオブジェクトを想定
    **kwargs,
) -> str:
    """
    Gemini API を同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。
    """
    use_default_model = (model is None)
    client = _get_client(api_key=api_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"))

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # モデルと Config の動的決定 (モデル未指定の場合のみリトライごとに再評価)
            current_model = model
            if use_default_model:
                current_model = get_default_model()
            
            config = _build_gemini_config(
                current_model, thinking_level, 
                max_output_tokens=max_output_tokens, 
                temperature=temperature,
                response_mime_type=response_mime_type,
                response_schema=response_schema
            )

            full_response_text = ""
            start_time = time.time()
            ttft = 0.0
            first_token_time = 0.0
            
            prompt_len = len(prompt) if isinstance(prompt, str) else len(str(prompt))
            print_log(f"  [LLM] API Request: {current_model} (Input: {prompt_len} chars, attempt: {attempt})")
            
            # ストリーミングによる計測
            response_stream = client.models.generate_content_stream(
                model=current_model,
                contents=prompt,
                config=config,
            )
            
            chunk = None
            for chunk in response_stream:
                if ttft == 0.0:
                    first_token_time = time.time()
                    ttft = first_token_time - start_time
                if hasattr(chunk, 'text') and chunk.text:
                    full_response_text += chunk.text
            
            if chunk is None and not full_response_text:
                raise RuntimeError("APIから空のレスポンスが返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time > 0 else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = usage.prompt_token_count if usage else 0
            c_tokens = usage.candidates_token_count if usage else 0
            tps = c_tokens / gen_duration if gen_duration > 0 else 0
            
            print_log(f"  [LLM] Success: Duration {duration:.1f}s (TTFT: {ttft:.1f}s, TPS: {tps:.1f}, Prompt: {p_tokens}tk, Output: {c_tokens}tk)")
            
            # メトリクス記録
            _log_metrics(kwargs.get("metrics_metadata", {}), prompt_len, p_tokens, c_tokens, ttft, tps, duration)
                
            return full_response_text
            
        except Exception as e:
            last_error = e
            msg = str(e)
            print_log(f"  [LLM] リトライ {attempt}/{max_retries}: {type(e).__name__}: {msg}")
            
            if attempt < max_retries:
                wait_time, is_resource_limit = _calc_retry_wait(msg, attempt, retry_delay)
                if is_resource_limit:
                    print_log(f"  [LLM] リソース制限/混雑(503/429)を検知。ダウンシフトして{wait_time:.1f}秒待機...")
                time.sleep(wait_time)
    raise RuntimeError(f"Gemini API 呼び出し失敗: {last_error}")


async def call_gemini_async(
    prompt: str | list,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    thinking_level: str | None = None,
    max_retries: int = 5,
    retry_delay: float = 3.0,
    log_dir: Optional[Any] = None, # Pathオブジェクトを想定
    **kwargs,
) -> str:
    """
    Gemini API を非同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。
    """
    use_default_model = (model is None)
    client = _get_client(api_key=api_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"), is_async=True)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # モデルと Config の動力決定 (モデル未指定の場合のみリトライごとに再評価)
            current_model = model
            if use_default_model:
                current_model = get_default_model()
            
            config = _build_gemini_config(
                current_model, thinking_level, 
                max_output_tokens=max_output_tokens, 
                temperature=temperature,
                response_mime_type=response_mime_type,
                response_schema=response_schema
            )

            full_response_text = ""
            start_time = time.time()
            ttft = 0.0
            first_token_time = 0.0
            
            prompt_len = len(prompt) if isinstance(prompt, str) else len(str(prompt))
            print_log(f"  [LLM async] API Request | Model: {current_model} | Chars: {prompt_len} | Thinking: {thinking_level} | Attempt: {attempt}")
            
            try:
                async with asyncio.timeout(600):
                    stream_gen = await client.aio.models.generate_content_stream(
                        model=current_model,
                        contents=prompt,
                        config=config,
                    )
                    
                    chunk = None
                    async for chunk in stream_gen:
                        if ttft == 0.0:
                            first_token_time = time.time()
                            ttft = first_token_time - start_time
                        if hasattr(chunk, 'text') and chunk.text:
                            full_response_text += chunk.text
            except asyncio.TimeoutError:
                raise RuntimeError(f"Gemini API 応答タイムアウト (600秒超過)")
            
            if chunk is None and not full_response_text:
                raise RuntimeError("APIから空のレスポンスが返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time > 0 else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = usage.prompt_token_count if usage else 0
            c_tokens = usage.candidates_token_count if usage else 0
            tps = c_tokens / gen_duration if gen_duration > 0 else 0
            
            print_log(f"  [LLM async] Success: Duration {duration:.1f}s (TTFT: {ttft:.1f}s, TPS: {tps:.1f}, Prompt: {p_tokens}tk, Output: {c_tokens}tk)")
            
            # メトリクス記録
            _log_metrics(kwargs.get("metrics_metadata", {}), prompt_len, p_tokens, c_tokens, ttft, tps, duration)
            
            return full_response_text
            
        except Exception as e:
            last_error = e
            msg = str(e)
            print_log(f"  [LLM async] リトライ {attempt}/{max_retries}: {type(e).__name__}: {msg}")
            
            if attempt < max_retries:
                wait_time, is_resource_limit = _calc_retry_wait(msg, attempt, retry_delay)
                if is_resource_limit:
                    print_log(f"  [LLM async] リソース制限/混雑(503/429)を検知。ダウンシフトして{wait_time:.1f}秒待機...")
                await asyncio.sleep(wait_time)

    raise RuntimeError(f"Gemini API 非同期呼び出し失敗: {last_error}")


# --- 高レベル API ラッパー ---

async def translate_batch(
    chunks: List[dict],
    glossary_content: str,
    previous_translation: str,
    prompt_template: str,
    resume_content: str,
    section_name: str,
    api_key: str | None = None,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
    rate_limiter: AsyncLimiter = None,
    context_guide: str = "",
    log_dir: Optional[Any] = None,
    max_parse_retries: int = 2,
) -> List["TreeNode"]:
    """チャンク群を XML タグ形式で翻訳し、パース失敗時は再試行する。"""
    from .models import TreeNode

    # 対象 ID セット（パース時の高速フィルタ用）
    known_ids = {str(c['id']) for c in chunks}

    def _parse_response(response: str) -> dict:
        """
        レスポンスから <p2w_chunk_ID> タグを一括抽出する（O(N) 一括パース）。

        戦略:
          Step A: 開始タグ → 次の開始タグ or 末尾 まで非欲張りマッチ
          Step B: 閉じタグが含まれていれば除去して内容を確定
          Step C: 3000文字超は末尾の誤取込み（閉じタグ欠落による複数チャンク混入）と見なし切り捨て
        """
        id_map = {}
        for m in re.finditer(
            r"<p2w_chunk_(\w+)>(.*?)(?=<p2w_chunk_\w+>|\Z)",
            response, re.DOTALL | re.IGNORECASE
        ):
            tag_id = m.group(1)
            if tag_id not in known_ids:
                continue
            content = m.group(2)
            # 閉じタグがあれば除去（その後の余分なテキストも含めて）
            content = re.sub(
                rf"</p2w_chunk_{re.escape(tag_id)}>.*\Z", "",
                content, flags=re.DOTALL | re.IGNORECASE
            ).strip()
            if not content:
                continue
            # Safety: 3000 文字超は閉じタグ欠落による複数チャンク誤取込みの可能性
            if len(content) > 3000:
                # 次の開始タグ手前で強制切断を試みる
                cut = re.split(r"<p2w_chunk_\w+>", content, maxsplit=1)
                content = cut[0].strip()
                print_log(f"  [LLM] ⚠️ chunk_{tag_id}: 抽出内容が3000文字超のため末尾を切り捨てました。")
                if not content:
                    continue
            id_map[tag_id] = content
        return id_map

    # 1. チャンクを XML タグ形式でパッケージング
    combined_text = ""
    for c in chunks:
        combined_text += f"<p2w_chunk_{c['id']}>\n{c['text']}\n</p2w_chunk_{c['id']}>\n\n"

    # 2. プロンプト構築
    base_prompt = prompt_template.format(
        expertise=expertise,
        context_guide=context_guide,
        glossary_content=glossary_content or "なし",
        resume_content=resume_content or "なし",
        previous_translation=previous_translation or "なし",
        chunk_json=combined_text
    )

    id_map = {}
    for attempt in range(max_parse_retries + 1):
        current_prompt = base_prompt
        if attempt > 0:
            # リトライ時: 欠落しているタグを明示してLLMに再挑戦させる
            missing_ids = [str(c['id']) for c in chunks if str(c['id']) not in id_map]
            missing_tags = ", ".join(f"<p2w_chunk_{i}>" for i in missing_ids)
            current_prompt += (
                f"\n\n(IMPORTANT) 前回の回答では以下のタグが正しく出力されませんでした: {missing_tags}\n"
                "今回は必ずすべての <p2w_chunk_ID> ... </p2w_chunk_ID> タグを完全な形で出力してください。"
            )

        async with rate_limiter:
            batch_id = "batch_" + str(chunks[0]['id']) if chunks else "unknown"
            metrics_meta = {"section": section_name, "batch_id": batch_id}
            response = await call_gemini_async(
                current_prompt, model=model, api_key=api_key, thinking_level=thinking_level,
                metrics_metadata=metrics_meta, log_dir=log_dir
            )

        # 3. 一括パース
        id_map = _parse_response(response)

        missing_ids = [str(c['id']) for c in chunks if str(c['id']) not in id_map]
        if not missing_ids:
            print_log(f"  [LLM] バッチ翻訳成功: {len(id_map)}件すべてのタグを正常に抽出しました。")
            break
        else:
            if attempt < max_parse_retries:
                print_log(f"  [LLM] パース失敗 (欠落: {missing_ids})。再試行します ({attempt + 1}/{max_parse_retries})...")
                await asyncio.sleep(2.0)
            else:
                print_log(f"  [LLM] !!! 最大リトライ回数到達。欠落したまま継続します: {missing_ids} !!!")

    # 4. 結果の構築
    results = []
    for c in chunks:
        cid = str(c['id'])
        text = id_map.get(cid)
        if not text:
            text = f"【翻訳失敗】 {c['text']}"
        results.append(TreeNode(id=cid, text=text, role="p", seq_index=c.get("seq_index", 0.0)))

    return results


async def generate_section_resume(
    section_name: str,
    chunks: List[Any],
    resume_content: str,
    api_key: str | None = None,
    expertise: str = "文化人類学",
    model: str | None = None,
    rate_limiter: Any | None = None,
    log_dir: Optional[Any] = None
):
    """セクション（章）の要約と論理展開（h3見出し候補）を生成する。"""
    full_text = "\n".join([c.get("text", "") for c in chunks])
    limit_text = full_text # 制約なく全テキストをプロンプトに込める
    
    prompts = _get_prompts()
    prompt_template = prompts.get("SECTION_SUMMARY_PROMPT")
    
    if not prompt_template:
        # フォールバック
        prompt = f"""あなたは{expertise}の専門家です。以下の章の内容を読み、2つのセクションに分けて出力してください。

1. 【要約】: この章の核心的な議論を300字程度で簡潔にまとめてください。
2. 【詳細な論理展開】: この章の内部構造（節見出しに相当する議論の区切り）を抽出し、以下の形式で列挙してください。
   - # [見出し1]
   - # [見出し2]
   ...

---
章のタイトル: {section_name}
本全体の要約（参考）: {resume_content}
本文（冒頭4万文字）:
{limit_text}
"""
    else:
        # SECTION_SUMMARY_PROMPT を使用
        prompt = prompt_template.format(
            expertise=expertise,
            section_name=section_name,
            resume_content=resume_content or "なし",
            text=limit_text
        )

    async with rate_limiter:
        response = await call_gemini_async(
            prompt, model=model, api_key=api_key, thinking_level="Low",
            log_dir=log_dir
        )
    return response

_CACHED_LIMITERS = {}
_LIMITER_LOCK = threading.Lock()


def reset_pipeline_state() -> None:
    """
    パイプライン開始前に呼び出す。
    AsyncLimiter は生成時のイベントループに紐付くため、新しいパイプライン（新しいループ）
    が始まる前にキャッシュをクリアし、次の apply_tier_settings 呼び出しで再生成させる。
    TierManager も paid にリセットして前回の downgrade 状態を引き継がないようにする。
    """
    global _CACHED_LIMITERS
    with _LIMITER_LOCK:
        _CACHED_LIMITERS.clear()
    tier_manager.set_tier(GeminiTier.PAID)


def apply_tier_settings(tier: str | GeminiTier) -> Tuple[AsyncLimiter, dict]:
    """
    ティアに応じたレートリミッターと設定を返す。
    ティアの文字列表記を受け入れ、tier_manager のグローバル状態を更新する。
    """
    global _CACHED_LIMITERS

    if isinstance(tier, str):
        try:
            tier = GeminiTier(tier.lower())
        except ValueError:
            print_log(f"  [LLM] 警告: 未知のティア '{tier}'。PAID を使用します。")
            tier = GeminiTier.PAID

    # グローバルなティア状態を更新
    tier_manager.set_tier(tier)

    with _LIMITER_LOCK:
        if tier == GeminiTier.FREE:
            settings = {"max_batch_chunks": 5, "max_batch_chars": 6000}
            if tier not in _CACHED_LIMITERS:
                _CACHED_LIMITERS[tier] = AsyncLimiter(1, 4.0)  # 1 request per 4 seconds
        else:
            settings = {"max_batch_chunks": 10, "max_batch_chars": 11000}
            if tier not in _CACHED_LIMITERS:
                _CACHED_LIMITERS[tier] = AsyncLimiter(100, 60.0)  # 100 requests per minute

        rate_limiter = _CACHED_LIMITERS[tier]

    return rate_limiter, settings
