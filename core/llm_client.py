"""
p2workflowy V2: Gemini API クライアント
google-genai SDK を使用したリトライ付き LLM 呼び出しラッパー。
"""

import json
import os
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

    if purpose == "resume":
        override = os.environ.get("DEFAULT_MODEL_RESUME") or prompts.get("DEFAULT_MODEL_RESUME", "")
        if override:
            return override
        purpose = "default"  # 空 → 通常の tier 追従にフォールバック

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
    """APIキーのティア（有料/無料）状態を管理するシングルトン。

    Webアプリの並行パイプライン実行はそれぞれ別スレッド（server.py の asyncio.to_thread）で
    動くため、内部状態は threading.local() を裏に持つプロパティとしてスレッドごとに分離する。
    `tier_manager` オブジェクト自体は今まで通りプロセス全体で単一のシングルトンとして import
    され続け、外部からの属性アクセス（`tier_manager.current_tier` 等の読み書き）も無変更。
    """
    _instance = None
    _local = threading.local()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_local(self):
        if not hasattr(self._local, "current_tier"):
            self._local.current_tier = GeminiTier.UNKNOWN
            self._local.was_downgraded = False

    @property
    def current_tier(self) -> GeminiTier:
        self._ensure_local()
        return self._local.current_tier

    @current_tier.setter
    def current_tier(self, value: GeminiTier):
        self._ensure_local()
        self._local.current_tier = value

    @property
    def was_downgraded(self) -> bool:
        self._ensure_local()
        return self._local.was_downgraded

    @was_downgraded.setter
    def was_downgraded(self, value: bool):
        self._ensure_local()
        self._local.was_downgraded = value

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


class KeyRotator:
    """CLI (main.py) 用の複数APIキーローテーション管理シングルトン。

    プロセスグローバルな状態のまま（スレッドローカル化しない）— CLIはスレッドを使わず単一
    プロセス内で完結するため（Phase4の並行も単一イベントループ内の asyncio.Semaphore のみ）。
    forward-only（一度進んだキーインデックスは戻らない、TierManager.downgrade() と同じ設計
    思想）。main.py が起動時に configure() を一度だけ呼ぶ。server.py は一切呼ばないため、
    Webアプリの挙動には影響しない（is_configured() が常に False のまま）。
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._keys = []
            cls._instance._tiers = []
            cls._instance._index = 0
        return cls._instance

    def configure(self, keys: List[Optional[str]], tiers: Optional[List[str]] = None) -> None:
        """keys と同じ並びの tiers（例: ["free","free","paid"]）を渡すと、ローテーション後に
        現在のキーが有料かどうかを current_tier() で判定できる。省略時は全キー種別不明扱い。"""
        pairs = [(k, t) for k, t in zip(keys, tiers or [None] * len(keys)) if k]
        self._keys = [k for k, _ in pairs]
        self._tiers = [t for _, t in pairs]
        self._index = 0

    def is_configured(self) -> bool:
        return bool(self._keys)

    def current(self) -> Optional[str]:
        return self._keys[self._index] if self._keys else None

    def current_tier(self) -> Optional[str]:
        """現在選択中のキーの種別（"free"/"paid"）。configure() に tiers を渡していなければ None。"""
        return self._tiers[self._index] if self._tiers else None

    def has_next(self) -> bool:
        return bool(self._keys) and self._index < len(self._keys) - 1

    def advance(self) -> Optional[str]:
        if self.has_next():
            self._index += 1
        return self.current()

    @property
    def index(self) -> int:
        return self._index

    @property
    def count(self) -> int:
        return len(self._keys)

key_rotator = KeyRotator()


class ModelRotator:
    """無料枠Liteモデルプール内でのフォワードオンリー・ローテーション（TierManagerと同じくスレッドローカル）。

    KeyRotatorがAPIキー単位のローテーションであるのに対し、こちらは同一キー・同一プロジェクト内で
    複数のLiteモデル（gemini-3.1-flash-lite / gemini-3.5-flash-lite 等）のRPD/RPMが別カウンターで
    管理されている性質を利用し、片方が429/503でリソース枯渇したときにもう片方へ切り替える。
    Web側の並行パイプライン実行にも対応できるよう TierManager と同じくスレッドローカルにする。
    """
    _local = threading.local()

    def _ensure_local(self):
        if not hasattr(self._local, "pool"):
            prompts = _get_prompts()
            pool = prompts.get("DEFAULT_MODEL_FREE_POOL") or [prompts.get("DEFAULT_MODEL_FREE", "gemini-3.1-flash-lite")]
            self._local.pool = pool
            self._local.index = 0

    def reset(self):
        self._ensure_local()
        self._local.index = 0

    def is_pool_member(self, model: str | None) -> bool:
        self._ensure_local()
        return model in self._local.pool

    def current(self) -> str:
        self._ensure_local()
        return self._local.pool[self._local.index]

    def has_next(self) -> bool:
        self._ensure_local()
        return self._local.index < len(self._local.pool) - 1

    def advance(self) -> str:
        self._ensure_local()
        if self.has_next():
            self._local.index += 1
        return self.current()

    def resolve(self, model: str | None) -> str | None:
        """model がプールのメンバーなら現在のローテーション先へ差し替える。プール外はそのまま返す。"""
        self._ensure_local()
        if model in self._local.pool:
            return self.current()
        return model

    def pool_models(self) -> List[str]:
        """プール全体のモデル名リストを返す（Phase4のラウンドロビン割り当て用）。"""
        self._ensure_local()
        return list(self._local.pool)

model_rotator = ModelRotator()


def get_free_pool_rate_limiters(api_key: str | None = None) -> Dict[str, AsyncLimiter]:
    """無料枠Liteプールの各モデルに独立した AsyncLimiter を割り当てて返す（Phase4ラウンドロビン用）。

    apply_tier_settings() の FREE tier 既定と同じレート（1 req / 4s ≒ 15RPM相当）をモデルごとに
    独立して持たせることで、単一リミッタ共有時よりも高いスループットを狙う。既存の
    apply_tier_settings() の (tier, api_key) キーとは別に (tier, api_key, model) でキーイングする
    ため、ローテーション（resolve()/advance()）用の共有リミッタとは衝突しない。
    """
    limiters = _get_limiters_dict()
    result = {}
    for m in model_rotator.pool_models():
        cache_key = (GeminiTier.FREE, api_key, m)
        if cache_key not in limiters:
            limiters[cache_key] = AsyncLimiter(1, 4.0)
        result[m] = limiters[cache_key]
    return result


# Gemini クライアントのキャッシュ（スレッドごとに独立した辞書）。
# 値は (client, 生成時のイベントループ) のペア。genai.Client の非同期トランスポートは
# 生成時のループに紐付くため、ループが変わったら再生成が必要（下記 _get_client 参照）。
_clients_local = threading.local()

def _get_clients_dict() -> Dict[str, tuple]:
    if not hasattr(_clients_local, "clients"):
        _clients_local.clients = {}
    return _clients_local.clients

def _get_client(api_key: str | None = None) -> genai.Client:
    """APIキーごとにクライアントをキャッシュして提供する（呼び出しスレッドごとに独立）。

    reset_pipeline_state() はパイプライン開始時にこのキャッシュを丸ごとクリアするが、
    それだけでは不十分だった: 1回の run_pipeline() 内でも Phase 1 (VLM ingestion) と
    Phase 4 (translation) はそれぞれ独立して run_async()/asyncio.run() を呼ぶため、
    別々のイベントループを持つ。Phase 1 で生成・キャッシュされたクライアントを Phase 4 の
    新しいループでそのまま再利用すると、非同期トランスポートが前のループ（既に閉じている）
    に紐付いたままのため初回呼び出しが "RuntimeError: Event loop is closed" になる
    （2026-07-21、書籍モードで章ごとに毎回発生することを確認）。
    そのため、カレントループが生成時のループと異なる場合はキャッシュを破棄して再生成する。
    同期呼び出し（ループなしのコンテキスト）はループ不問で既存クライアントを再利用する。
    """
    key = api_key or GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY (または GOOGLE_API_KEY) がセットされていません。.env ファイルを確認してください。")

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    clients = _get_clients_dict()
    cached = clients.get(key)
    if cached is not None:
        cached_client, cached_loop = cached
        if current_loop is None or cached_loop is current_loop:
            return cached_client

    client = genai.Client(api_key=key)
    clients[key] = (client, current_loop)
    return client


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


# キーローテーション直後の待機秒数（新しいプロジェクトの枠なのでクールダウン不要、短いジッターのみ）
ROTATION_RETRY_DELAY_BASE = 1.5

# モデルローテーション直後の待機秒数（同一キー内での切替のためクールダウン不要、短いジッターのみ）
MODEL_ROTATION_RETRY_DELAY_BASE = 1.0


def _maybe_restore_tier_after_rotation() -> None:
    """キーローテーションで有料キーへ切り替わった直後に呼ぶ。

    429/503 検知時の tier_manager.downgrade()（無条件・キー種別を問わない）は、無料キー内で
    ローテーションしただけ（free1→free2）なら正しい状態だが、有料キーへ切り替わった後も
    FREE のまま残ると、以降のリクエストが不必要に Lite モデル・縮小バッチで処理され続けて
    しまう（2026-07-21 レビュー指摘）。有料キーに切り替わった場合のみ PAID へ戻す。
    """
    if key_rotator.current_tier() == "paid":
        tier_manager.set_tier(GeminiTier.PAID)


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
    model_pinned: bool = False,
    **kwargs,
) -> str:
    """
    Gemini API を同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。

    model_pinned: True の場合、呼び出し元が明示した model を ModelRotator の
    resolve()/advance() で上書きしない（Phase4のラウンドロビン割り当てなど、呼び出し元が
    プール内のどのモデルを使うか自分で管理したい場合に使う。通常の呼び出しは False のまま）。
    """
    use_default_model = (model is None)
    effective_key = key_rotator.current() if key_rotator.is_configured() else api_key
    client = _get_client(api_key=effective_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"))

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # モデルと Config の動的決定 (モデル未指定の場合のみリトライごとに再評価)
            current_model = model
            if use_default_model:
                current_model = get_default_model()
            # 無料枠Liteプールのメンバーなら現在のローテーション先へ差し替える
            # （model_pinned=True の場合は呼び出し元の指定を尊重してスキップ）
            if not model_pinned:
                current_model = model_rotator.resolve(current_model)

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
            
            # テキスト出力が空なら失敗として扱いリトライさせる。
            # chunk が返っていても finish_reason=MALFORMED_RESPONSE / MAX_TOKENS / SAFETY 等で
            # text が 0 トークンのことがあり、これを無言で "" として返すとレジュメ欠落など
            # 静かな品質劣化を招く（troubleshooting_log I-19）。空出力は常に異常とみなす。
            if not full_response_text:
                raise RuntimeError("APIから空のレスポンス（テキスト0トークン）が返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time > 0 else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = (usage.prompt_token_count or 0) if usage else 0
            c_tokens = (usage.candidates_token_count or 0) if usage else 0
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
                rotated = False
                if is_resource_limit and not model_pinned and model_rotator.is_pool_member(current_model) and model_rotator.has_next():
                    new_model = model_rotator.advance()
                    wait_time = MODEL_ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                    rotated = True
                    print_log(f"  [LLM] モデルローテーション: {new_model} に切替")
                elif is_resource_limit and key_rotator.is_configured() and key_rotator.has_next():
                    new_key = key_rotator.advance()
                    client = _get_client(api_key=new_key)
                    wait_time = ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                    rotated = True
                    _maybe_restore_tier_after_rotation()
                    model_rotator.reset()  # 新しいキー = 別プロジェクトの独立枠なのでプール先頭から再開させる
                    print_log(f"  [LLM] キーローテーション: {key_rotator.index + 1}/{key_rotator.count} 番目のキーに切替")
                if is_resource_limit and not rotated:
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
    model_pinned: bool = False,
    **kwargs,
) -> str:
    """
    Gemini API を非同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。

    model_pinned: True の場合、呼び出し元が明示した model を ModelRotator の
    resolve()/advance() で上書きしない（Phase4のラウンドロビン割り当てなど、呼び出し元が
    プール内のどのモデルを使うか自分で管理したい場合に使う。通常の呼び出しは False のまま）。
    """
    use_default_model = (model is None)
    effective_key = key_rotator.current() if key_rotator.is_configured() else api_key
    client = _get_client(api_key=effective_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"), is_async=True)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # モデルと Config の動力決定 (モデル未指定の場合のみリトライごとに再評価)
            current_model = model
            if use_default_model:
                current_model = get_default_model()
            # 無料枠Liteプールのメンバーなら現在のローテーション先へ差し替える
            # （model_pinned=True の場合は呼び出し元の指定を尊重してスキップ）
            if not model_pinned:
                current_model = model_rotator.resolve(current_model)

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
            
            # テキスト出力が空なら失敗として扱いリトライさせる。
            # chunk が返っていても finish_reason=MALFORMED_RESPONSE / MAX_TOKENS / SAFETY 等で
            # text が 0 トークンのことがあり、これを無言で "" として返すとレジュメ欠落など
            # 静かな品質劣化を招く（troubleshooting_log I-19）。空出力は常に異常とみなす。
            if not full_response_text:
                raise RuntimeError("APIから空のレスポンス（テキスト0トークン）が返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time > 0 else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = (usage.prompt_token_count or 0) if usage else 0
            c_tokens = (usage.candidates_token_count or 0) if usage else 0
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
                rotated = False
                if is_resource_limit and not model_pinned and model_rotator.is_pool_member(current_model) and model_rotator.has_next():
                    new_model = model_rotator.advance()
                    wait_time = MODEL_ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                    rotated = True
                    print_log(f"  [LLM async] モデルローテーション: {new_model} に切替")
                elif is_resource_limit and key_rotator.is_configured() and key_rotator.has_next():
                    new_key = key_rotator.advance()
                    client = _get_client(api_key=new_key)
                    wait_time = ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                    rotated = True
                    _maybe_restore_tier_after_rotation()
                    model_rotator.reset()  # 新しいキー = 別プロジェクトの独立枠なのでプール先頭から再開させる
                    print_log(f"  [LLM async] キーローテーション: {key_rotator.index + 1}/{key_rotator.count} 番目のキーに切替")
                if is_resource_limit and not rotated:
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
    log_dir: Optional[Any] = None,
    max_parse_retries: int = 2,
    model_pinned: bool = False,
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
                metrics_metadata=metrics_meta, log_dir=log_dir, model_pinned=model_pinned
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


_limiters_local = threading.local()

def _get_limiters_dict() -> dict:
    if not hasattr(_limiters_local, "limiters"):
        _limiters_local.limiters = {}
    return _limiters_local.limiters


def reset_pipeline_state() -> None:
    """
    パイプライン開始前に呼び出す。
    AsyncLimiter・genai.Client の非同期トランスポートはいずれも生成時のイベントループに
    紐付くため、新しいパイプライン（新しい asyncio.run() ループ）が始まる前にキャッシュを
    クリアし、次回アクセス時に現在のループへ再生成させる。これを怠ると、前のパイプライン
    の（すでに閉じた）ループに紐付いたクライアントを再利用してしまい、非同期呼び出しの
    1回目が "RuntimeError: Event loop is closed" で失敗する。
    TierManager も paid にリセットして前回の downgrade 状態を引き継がないようにする。
    _CLIENTS・_CACHED_LIMITERS・TierManager はいずれもスレッドごとに独立しているため、
    ここでのクリア・リセットは呼び出したスレッド自身の状態にしか影響しない（Web側で複数の
    パイプラインが別スレッドで並行実行されていても、互いの状態を消し合わない）。
    """
    _get_limiters_dict().clear()
    _get_clients_dict().clear()
    tier_manager.set_tier(GeminiTier.PAID)
    model_rotator.reset()


def apply_tier_settings(tier: str | GeminiTier, api_key: str | None = None) -> Tuple[AsyncLimiter, dict]:
    """
    ティアに応じたレートリミッターと設定を返す。
    ティアの文字列表記を受け入れ、tier_manager のグローバル状態を更新する。
    api_key を渡すと (tier, api_key) 単位でレートリミッタを分離する（同一プロセス内で複数
    キーを切り替える場合や、Webアプリで複数キーを同時使用する場合に、キーごとに正しい
    残余レートを持たせるため）。省略時は従来通り tier のみでキーイングする。
    """
    if isinstance(tier, str):
        try:
            tier = GeminiTier(tier.lower())
        except ValueError:
            print_log(f"  [LLM] 警告: 未知のティア '{tier}'。PAID を使用します。")
            tier = GeminiTier.PAID

    # グローバルなティア状態を更新
    tier_manager.set_tier(tier)

    cache_key = tier if api_key is None else (tier, api_key)
    limiters = _get_limiters_dict()
    if tier == GeminiTier.FREE:
        settings = {"max_batch_chunks": 8, "max_batch_chars": 9000}
        if cache_key not in limiters:
            limiters[cache_key] = AsyncLimiter(1, 4.0)  # 1 request per 4 seconds
    else:
        settings = {"max_batch_chunks": 18, "max_batch_chars": 20000}
        if cache_key not in limiters:
            limiters[cache_key] = AsyncLimiter(100, 60.0)  # 100 requests per minute

    rate_limiter = limiters[cache_key]

    return rate_limiter, settings
