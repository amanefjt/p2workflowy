"""
p2workflowy V2: Gemini API クライアント
google-genai SDK を使用したリトライ付き LLM 呼び出しラッパー。
"""

import json
import time
from typing import Any

from google import genai
from google.genai import types

from datetime import datetime
from .config import GEMINI_API_KEY, STATE_DIR, load_coreprompts, print_log


# プロンプト定数の読み込み
_PROMPTS: dict | None = None

def _get_prompts() -> dict:
    global _PROMPTS
    if _PROMPTS is None:
        _PROMPTS = load_coreprompts()
    return _PROMPTS


def get_default_model() -> str:
    """coreprompts.json から DEFAULT_MODEL を取得。"""
    return _get_prompts().get("DEFAULT_MODEL", "gemini-2.0-flash")


# Gemini クライアントのシングルトン
_CLIENT: genai.Client | None = None

def _get_client(api_key: str | None = None) -> genai.Client:
    global _CLIENT
    
    # 手動指定のキーがある場合は新しいクライアントを返す
    if api_key:
        return genai.Client(api_key=api_key)

    if _CLIENT is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY (または GOOGLE_API_KEY) がセットされていません。.env ファイルを確認してください。")
        _CLIENT = genai.Client(api_key=GEMINI_API_KEY)
    return _CLIENT


def call_gemini(
    prompt: str,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> str:
    """
    Gemini API を同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。
    """
    if model is None:
        model = get_default_model()

    client = _get_client(api_key=api_key)

    # デバッグ用に全ペイロードをダンプ (一回のみ上書き)
    try:
        debug_path = STATE_DIR / "debug_prompt.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(prompt)
    except:
        pass

    config_kwargs: dict[str, Any] = {
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
    }
    if response_mime_type:
        config_kwargs["response_mime_type"] = response_mime_type
    if response_schema:
        config_kwargs["response_schema"] = response_schema

    safety_settings = [
        types.SafetySetting(category=cat, threshold="BLOCK_NONE")
        for cat in [
            "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
    ]

    config = types.GenerateContentConfig(safety_settings=safety_settings, **config_kwargs)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            full_response_text = ""
            start_time = time.time()
            ttft = None
            first_token_time = None
            
            print_log(f"  [LLM] API Request: {model} (Input: {len(prompt)} chars, attempt: {attempt})")
            
            # ストリーミングによる計測
            response_stream = client.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=config,
            )
            
            chunk = None
            for chunk in response_stream:
                if ttft is None:
                    first_token_time = time.time()
                    ttft = first_token_time - start_time
                if hasattr(chunk, 'text') and chunk.text:
                    full_response_text += chunk.text
            
            if chunk is None:
                raise RuntimeError("APIから空のレスポンスが返されました。")

            end_time = time.time()
            duration = end_time - start_time
            
            # TTFT と TPS の計算 (None チェック)
            gen_duration = (end_time - first_token_time) if first_token_time is not None else 0
            
            # メタデータ取得 (最後のチャンクから)
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = usage.prompt_token_count if usage else 0
            c_tokens = usage.candidates_token_count if usage else 0
            tps = c_tokens / gen_duration if gen_duration > 0 else 0
            
            ttft_val = ttft if ttft is not None else 0
            print_log(f"  [LLM] Success: Duration {duration:.1f}s (TTFT: {ttft_val:.1f}s, TPS: {tps:.1f}, Prompt: {p_tokens}tk, Output: {c_tokens}tk)")
            return full_response_text
            
        except Exception as e:
            last_error = e
            msg = str(e)
            print_log(f"  [LLM] リトライ {attempt}/{max_retries}: {type(e).__name__}: {msg}")
            
            if attempt < max_retries:
                wait_time = retry_delay * attempt
                # 429 (Rate Limit) の場合は待機時間を調整
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    import re
                    # "Please retry in 40.669259345s" のような形式を抽出
                    match = re.search(r"retry in ([\d\.]+)s", msg)
                    if match:
                        wait_time = float(match.group(1)) + 1.0 # 余裕を持って +1秒
                    else:
                        wait_time = 30.0 * attempt # デフォルトで長めに待機
                    print_log(f"  [LLM] 429 レート制限を検知。{wait_time:.1f}秒待機します...")
                
                time.sleep(wait_time)
    raise RuntimeError(f"Gemini API 呼び出し失敗: {last_error}")


async def call_gemini_async(
    prompt: str,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    max_retries: int = 3,
    retry_delay: float = 5.0,
    **kwargs,
) -> str:
    """
    Gemini API を非同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。
    """
    import asyncio

    if model is None:
        model = get_default_model()

    client = _get_client(api_key=api_key)

    config_kwargs: dict[str, Any] = {
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
    }
    if response_mime_type:
        config_kwargs["response_mime_type"] = response_mime_type
    if response_schema:
        config_kwargs["response_schema"] = response_schema

    safety_settings = [
        types.SafetySetting(category=cat, threshold="BLOCK_NONE")
        for cat in [
            "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
    ]

    config = types.GenerateContentConfig(safety_settings=safety_settings, **config_kwargs)

    # デバッグ用に全ペイロードをダンプ (一回のみ上書き)
    try:
        debug_path = STATE_DIR / "debug_prompt.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(prompt)
    except:
        pass

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            full_response_text = ""
            start_time = time.time()
            ttft = None
            first_token_time = None
            
            print_log(f"  [LLM async] API Request: {model} (Input: {len(prompt)} chars, attempt: {attempt})")
            
            # 非同期ストリーミング
            stream_gen = await client.aio.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=config,
            )
            
            chunk = None
            async for chunk in stream_gen:
                if ttft is None:
                    first_token_time = time.time()
                    ttft = first_token_time - start_time
                if hasattr(chunk, 'text') and chunk.text:
                    full_response_text += chunk.text
            
            if chunk is None:
                raise RuntimeError("APIから空のレスポンスが返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time is not None else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = usage.prompt_token_count if usage else 0
            c_tokens = usage.candidates_token_count if usage else 0
            tps = c_tokens / gen_duration if gen_duration > 0 else 0
            
            ttft_val = ttft if ttft is not None else 0
            print_log(f"  [LLM async] Success: Duration {duration:.1f}s (TTFT: {ttft_val:.1f}s, TPS: {tps:.1f}, Prompt: {p_tokens}tk, Output: {c_tokens}tk)")
            
            # --- メトリクスを CSV に記録 ---
            try:
                from .config import METRICS_CSV_PATH
                import csv
                metadata = kwargs.get("metrics_metadata", {})
                section = metadata.get("section", "N/A")
                batch_id = metadata.get("batch_id", "N/A")
                
                # ファイルが存在しない場合はヘッダーを作成
                file_exists = METRICS_CSV_PATH.exists()
                with open(METRICS_CSV_PATH, "a", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(["timestamp", "section", "batch_id", "input_chars", "p_tokens", "c_tokens", "ttft", "tps", "duration"])
                    writer.writerow([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        section,
                        batch_id,
                        len(prompt),
                        p_tokens,
                        c_tokens,
                        f"{ttft_val:.3f}",
                        f"{tps:.1f}",
                        f"{duration:.2f}"
                    ])
                print_log(f"  [LLM async] Metrics logged to {METRICS_CSV_PATH}")
            except Exception as e_log:
                print_log(f"  [LLM async] Metrics logging failed: {e_log}")
            
            return full_response_text
            
        except Exception as e:
            last_error = e
            msg = str(e)
            print_log(f"  [LLM async] リトライ {attempt}/{max_retries}: {type(e).__name__}: {msg}")
            
            if attempt < max_retries:
                wait_time = retry_delay * attempt
                # 429 (Rate Limit) の場合は待機時間を調整
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    import re
                    match = re.search(r"retry in ([\d\.]+)s", msg)
                    if match:
                        wait_time = float(match.group(1)) + 1.0
                    else:
                        wait_time = 30.0 * attempt
                    print_log(f"  [LLM async] 429 レート制限を検知。{wait_time:.1f}秒待機します...")
                
                await asyncio.sleep(wait_time)

    raise RuntimeError(f"Gemini API 非同期呼び出し失敗: {last_error}")
