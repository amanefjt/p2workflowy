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
    if purpose == "free":
        return prompts.get("DEFAULT_MODEL_FREE", prompts.get("DEFAULT_MODEL", "gemini-3-flash-preview"))
    elif purpose == "vlm":
        return prompts.get("DEFAULT_MODEL_VLM", "gemini-3.1-flash-lite-preview")
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

    def downgrade(self):
        if not self.was_downgraded:
            print_log("  [TierManager] !!! 429 RESOURCE_EXHAUSTED detected. Downgrading to FREE tier mode. !!!")
            self.current_tier = GeminiTier.FREE
            self.was_downgraded = True

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
                # nest_asyncio がなければ既存ループの run_until_complete (実質的に困難な場合が多いが) 
                # または、新しいスレッドで実行する等の高度な処理が必要
                # ここでは簡易的に現在のループで試行
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
    if thinking_level and model and ("gemini-3" in model.lower() or "thinking" in model.lower()):
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


def call_gemini(
    prompt: str | list,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    thinking_level: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 5.0,
    log_dir: Optional[Any] = None, # Pathオブジェクトを想定
    **kwargs,
) -> str:
    """
    Gemini API を同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。
    """
    if model is None:
        model = get_default_model()

    client = _get_client(api_key=api_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"))

    # Config 構築
    config = _build_gemini_config(
        model, thinking_level, 
        max_output_tokens=max_output_tokens, 
        temperature=temperature,
        response_mime_type=response_mime_type,
        response_schema=response_schema
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            full_response_text = ""
            start_time = time.time()
            ttft = 0.0
            first_token_time = 0.0
            
            prompt_len = len(prompt) if isinstance(prompt, str) else len(str(prompt))
            print_log(f"  [LLM] API Request: {model} (Input: {prompt_len} chars, attempt: {attempt})")
            
            # ストリーミングによる計測
            response_stream = client.models.generate_content_stream(
                model=model,
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
            
            if chunk is None:
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
                wait_time = retry_delay * attempt
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    tier_manager.downgrade()
                    match = re.search(r"retry in ([\d\.]+)s", msg)
                    wait_time = float(match.group(1)) + 1.0 if match else 30.0 * attempt
                    print_log(f"  [LLM] 429 レート制限を検知。{wait_time:.1f}秒待機します...")
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
    max_retries: int = 3,
    retry_delay: float = 5.0,
    log_dir: Optional[Any] = None, # Pathオブジェクトを想定
    **kwargs,
) -> str:
    """
    Gemini API を非同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。
    """
    if model is None:
        model = get_default_model()

    client = _get_client(api_key=api_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"), is_async=True)

    # Config 構築
    config = _build_gemini_config(
        model, thinking_level, 
        max_output_tokens=max_output_tokens, 
        temperature=temperature,
        response_mime_type=response_mime_type,
        response_schema=response_schema
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            full_response_text = ""
            start_time = time.time()
            ttft = 0.0
            first_token_time = 0.0
            
            prompt_len = len(prompt) if isinstance(prompt, str) else len(str(prompt))
            print_log(f"  [LLM async] Sending Request | Model: {model} | Chars: {prompt_len} | Thinking: {thinking_level} | Attempt: {attempt}")
            
            try:
                async with asyncio.timeout(600):
                    stream_gen = await client.aio.models.generate_content_stream(
                        model=model,
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
                wait_time = retry_delay * attempt
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    tier_manager.downgrade()
                    match = re.search(r"retry in ([\d\.]+)s", msg)
                    wait_time = float(match.group(1)) + 1.0 if match else 30.0 * attempt
                    print_log(f"  [LLM async] 429 レート制限を検知。{wait_time:.1f}秒待機します...")
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
    rate_limiter: AsyncLimiter = None, # セマフォ削除、レートリミッターのみ
    context_guide: str = "",
    log_dir: Optional[Any] = None
) -> List["TreeNode"]:
    """チャンク群を一つのバッチとして翻訳する。"""
    from .models import TreeNode
    
    # チャンクをテキストに結合 (あるいは JSON 形式)
    combined_text = ""
    for c in chunks:
        combined_text += f"<chunk_{c['id']}>\n{c['text']}\n</chunk_{c['id']}>\n\n"

    # プロンプトテンプレート内のプレースホルダーに合わせてフォーマット
    # coreprompts.json の TRANSLATION_PROMPT は {expertise}, {context_guide}, {resume_content}, {glossary_content}, {chunk_json} を持つ
    prompt = prompt_template.format(
        expertise=expertise,
        context_guide=context_guide,
        glossary_content=glossary_content or "なし",
        resume_content=resume_content or "なし",
        previous_translation=previous_translation or "なし",
        chunk_json=combined_text  # coreprompts.json のプレースホルダー名に合わせる
    )

    async with rate_limiter:
        metrics_meta = {"section": section_name, "batch_id": "batch_" + str(chunks[0]['id']) if chunks else "unknown"}
        response = await call_gemini_async(
            prompt, model=model, api_key=api_key, thinking_level=thinking_level,
            metrics_metadata=metrics_meta, log_dir=log_dir
        )

    # 2. レスポンスのパース (ログ解析に基づいた修正実装)
    results = []
    import re
    import json

    # --- ステップA: JSON解析とキー正規化 ---
    id_map = {}
    json_match = re.search(r"\[\s*\{.*\}\s*\]", response, re.DOTALL)
    if json_match:
        try:
            clean_json = re.sub(r"```json|```", "", json_match.group(0)).strip()
            json_data = json.loads(clean_json)
            for item in json_data:
                raw_id = str(item.get("id", item.get("chunk_id", "")))
                clean_id = raw_id.removeprefix("chunk_") # キーの正規化
                val = item.get("trans", item.get("text", item.get("translation", "")))
                if clean_id and val:
                    id_map[clean_id] = val
            if id_map:
                print_log(f"  [LLM] JSON形式({len(id_map)}件)を正常にパースしました。")
        except Exception:
            pass # JSONが壊れている場合は後続の処理へ

    for c in chunks:
        cid = str(c['id'])
        text = None

        # 1. 正常なJSONマップから取得
        if cid in id_map:
            text = id_map[cid]
        
        # 2. タグ形式、あるいは壊れた閉じタグからの救出 (先読み正規表現)
        if not text:
            # <(?:chunk[ _-]*)?{cid}> : <chunk_123>, <chunk 123>, <chunk-123>, <123> すべてに対応
            pattern = rf"<(?:chunk[ _-]*)?{cid}>(.*?)(?=</(?:chunk[ _-]*)?{cid}>|<(?:chunk[ _-]*)?\d+>|}}\s*,|]\s*```|$)"
            match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
            if match:
                text = match.group(1).strip()

        # 3. 出力打ち切り対策：開始タグはあるが閉じタグがない場合、末尾まで全取得
        if not text:
            pattern_unclosed = rf"<(?:chunk_)?{cid}>\s*(.*)"
            match = re.search(pattern_unclosed, response, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = match.group(1).strip()
                # 誤って次のチャンクの内容まで取り込まないよう長さ制限 (約1000文字程度を想定)
                if len(extracted) < 3000:
                    text = extracted

        # 最終判定
        if not text:
            print_log(f"  [LLM] !!! パース完全失敗 chunk_id={cid} !!!")
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
    limit_text = full_text[:40000] # 長すぎる場合は制限
    
    prompts = _get_prompts()
    prompt_template = prompts.get("SECTION_SUMMARY_PROMPT")
    
    if not prompt_template:
        # フォールバック
        prompt = f"""あなたは{expertise}の専門家です。以下の章の内容を読み、2つのセクションに分けて出力してください。

1. 【要約】: この章の核心的な議論を300字程度で簡潔にまとめてください。
2. 【詳細な論理展開】: この章の内部構造（節見出しに相当する議論の区切り）を抽出し、以下の形式で列挙してください。
   - ##見出し1
   - ##見出し2
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

def apply_tier_settings(tier: GeminiTier) -> Tuple[AsyncLimiter, asyncio.Semaphore, dict]:
    """ティアに応じたレート制限とセマフォを返す（キャッシュ版）。"""
    global _CACHED_LIMITERS
    
    with _LIMITER_LOCK:
        if tier in _CACHED_LIMITERS:
            return _CACHED_LIMITERS[tier]
            
        if tier == GeminiTier.FREE:
            # 無料枠: 秒間制限が厳しいため、1並列、低速
            rate_limiter = AsyncLimiter(1, 4.0) # 1 request per 4 seconds
            semaphore = asyncio.Semaphore(1)
            settings = {"max_batch_chunks": 3, "max_batch_chars": 1500}
        else:
            # 有料枠: 15並列程度
            rate_limiter = AsyncLimiter(100, 60.0) # 100 requests per minute
            semaphore = asyncio.Semaphore(15)
            settings = {"max_batch_chunks": 5, "max_batch_chars": 2500}
            
        _CACHED_LIMITERS[tier] = (rate_limiter, semaphore, settings)
        return _CACHED_LIMITERS[tier]
