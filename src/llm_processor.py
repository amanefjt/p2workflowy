# -*- coding: utf-8 -*-
"""
LLMProcessor: Gemini APIとの通信を管理するクラス
"""
import os
import time
from google import genai
from google.genai import types

from .constants import DEFAULT_MODEL


class LLMProcessor:
    """
    Gemini APIとの通信を管理するクラス
    
    - リトライ処理（exponential backoff）
    - 温度設定: 0.0（学術翻訳向け）
    """

    MAX_RETRIES = 3
    BASE_DELAY = 2  # 秒

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        """
        Args:
            api_key: Google API Key。Noneの場合は環境変数から取得
            model_name: 使用するモデル名。Noneの場合はDEFAULT_MODELを使用
        """
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY が設定されていません")
        
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name or DEFAULT_MODEL

    def call_api(self, prompt: str, progress_callback=None) -> str:
        """
        Gemini APIを呼び出す（リトライ処理付き）
        
        Args:
            prompt: プロンプト
            progress_callback: 進捗コールバック関数（オプション）
            
        Returns:
            APIレスポンスのテキスト
        """
        last_error = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=65536,  # Gemini 1.5/3 Flash の物理上限
                    )
                )
                
                if response.text:
                    return response.text
                else:
                    raise ValueError("APIからのレスポンスが空です")
                    
            except Exception as e:
                last_error = e
                # エラーの詳細を把握しやすくする
                error_msg = str(e)
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BASE_DELAY * (2 ** attempt)
                    msg = f"リトライ中... ({attempt + 1}/{self.MAX_RETRIES}) - 原因: {error_msg}"
                    if progress_callback:
                        progress_callback(msg)
                    else:
                        print(msg)
                    time.sleep(delay)
        
        raise RuntimeError(f"API呼び出しに失敗しました（{self.MAX_RETRIES}回試行）: {last_error}")
