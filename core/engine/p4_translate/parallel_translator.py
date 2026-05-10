import asyncio
import random
from typing import List, Dict, Any, Callable, Optional
from aiolimiter import AsyncLimiter
from collections import deque
from core.models import TreeNode
from core.config import print_log
from core.llm_client import translate_batch, tier_manager, GeminiTier, apply_tier_settings

class ParallelTranslator:
    """
    非同期並列翻訳を制御するエンジン。
    セマフォ、レートリミッター、バッチング、およびエラーハンドリングを専門に扱う。
    """
    DEFAULT_MAX_BATCH_CHUNKS = 10
    DEFAULT_MAX_BATCH_CHARS = 11000

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        tier: GeminiTier = GeminiTier.PAID,
        max_concurrent_sections: int = 4
    ):
        self.api_key = api_key
        self.model = model
        self.tier = tier
        self.semaphore = asyncio.Semaphore(max_concurrent_sections)
        self.rate_limiter, self.settings = apply_tier_settings(tier)

    def _create_batches(self, chunks: List[dict]) -> List[List[dict]]:
        """チャンク数と文字数に基づいてバッチを生成する。"""
        max_chunks = self.settings.get("max_batch_chunks", self.DEFAULT_MAX_BATCH_CHUNKS)
        max_chars = self.settings.get("max_batch_chars", self.DEFAULT_MAX_BATCH_CHARS)
        
        batches = []
        i = 0
        while i < len(chunks):
            batch = []
            batch_chars = 0
            while i < len(chunks) and len(batch) < max_chunks:
                c = chunks[i]
                c_len = len(c.get("text", ""))
                if len(batch) > 0 and (batch_chars + c_len) > max_chars:
                    break
                batch.append(c)
                batch_chars += c_len
                i += 1
            batches.append(batch)
        return batches

    async def translate_section_chunks(
        self,
        section_name: str,
        chunks: List[dict],
        prompt_builder_func: Callable[[List[TreeNode]], str],
        translate_func: Callable, # llm_client.translate_batch 相当
        **kwargs
    ) -> List[TreeNode]:
        """
        セクション内のチャンクを動的なバッチサイズで翻訳実行する（Adaptive Batching）。
        """
        async with self.semaphore:
            all_translated: List[TreeNode] = []
            remaining_chunks = deque(chunks)
            batch_idx = 1

            while remaining_chunks:
                # 1. ティアの動的変更（ダウングレード対応）を反映 (P4-2: 変更があった時のみ適用)
                if tier_manager.current_tier != self.tier:
                    self.tier = tier_manager.current_tier
                    self.rate_limiter, self.settings = apply_tier_settings(self.tier)

                # 2. 現在の設定に基づきバッチを切り出し
                max_chunks = self.settings.get("max_batch_chunks", self.DEFAULT_MAX_BATCH_CHUNKS)
                max_chars = self.settings.get("max_batch_chars", self.DEFAULT_MAX_BATCH_CHARS)
                
                batch = []
                batch_chars = 0
                while remaining_chunks and len(batch) < max_chunks:
                    c = remaining_chunks[0]
                    c_len = len(c.get("text", ""))
                    # 最初の1つ目は強制的に入れ、2つ目以降で文字数制限をチェック
                    if len(batch) > 0 and (batch_chars + c_len) > max_chars:
                        break
                    batch.append(remaining_chunks.popleft())
                    batch_chars += c_len

                # 3. 翻訳実行
                previous = prompt_builder_func(all_translated)
                print_log(f"  [ParallelTranslator] {section_name}: Batch {batch_idx} ({len(batch)} chunks, {batch_chars} chars)")
                
                try:
                    batch_nodes = await translate_func(
                        chunks=batch,
                        previous_translation=previous,
                        rate_limiter=self.rate_limiter,
                        api_key=self.api_key,
                        model=self.model, # ユーザー指定モデルを尊重する
                        section_name=section_name,
                        **kwargs
                    )
                    all_translated.extend(batch_nodes)
                except Exception as e:
                    print_log(f"  [ERROR] batch {batch_idx} 翻訳失敗 ({section_name}): {e}")
                    # フォールバック: 原文を維持したノードを作成
                    for c in batch:
                        all_translated.append(TreeNode(
                            id=c.get("id"),
                            text=f"【翻訳失敗】 {c.get('text', '')}",
                            role="p",
                            seq_index=c.get("seq_index", 0.0)
                        ))

                batch_idx += 1
                # 進捗コールバック（必要に応じて）
                if "progress_callback" in kwargs:
                    kwargs["progress_callback"](len(batch))

            return all_translated
