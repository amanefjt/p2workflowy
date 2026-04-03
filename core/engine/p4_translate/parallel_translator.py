import asyncio
import random
from typing import List, Dict, Any, Callable, Optional
from aiolimiter import AsyncLimiter
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
        max_concurrent_sections: int = 3
    ):
        self.api_key = api_key
        self.model = model
        self.tier = tier
        self.semaphore = asyncio.Semaphore(max_concurrent_sections)
        self.rate_limiter, _, self.settings = apply_tier_settings(tier)

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
        セクション内のチャンクを並列/シーケンシャルに翻訳実行する。
        """
        async with self.semaphore:
            all_translated: List[TreeNode] = []
            batches = self._create_batches(chunks)
            total_batches = len(batches)

            for idx, batch in enumerate(batches, 1):
                # ティアの動的変更（ダウングレード対応）を反映
                if tier_manager.was_downgraded:
                    self.rate_limiter, _, self.settings = apply_tier_settings(tier_manager.current_tier)

                # 過去の翻訳コンテキストを取得
                previous = prompt_builder_func(all_translated)
                
                print_log(f"  [ParallelTranslator] {section_name}: Batch {idx}/{total_batches} ({len(batch)} chunks)")
                
                # APIへの過負荷軽減のためのサブ秒待機
                await asyncio.sleep(random.uniform(0.5, 1.5))
                
                try:
                    batch_nodes = await translate_func(
                        chunks=batch,
                        previous_translation=previous,
                        rate_limiter=self.rate_limiter,
                        api_key=self.api_key,
                        model=self.model,
                        section_name=section_name,
                        **kwargs
                    )
                    all_translated.extend(batch_nodes)
                except Exception as e:
                    print_log(f"  [ERROR] batch 翻訳失敗 ({section_name}): {e}")
                    # フォールバック: 原文を維持したノードを作成
                    for c in batch:
                        all_translated.append(TreeNode(
                            id=c.get("id"),
                            text=f"[翻訳失敗] {c.get('text', '')}",
                            role="p",
                            seq_index=c.get("seq_index", 0.0)
                        ))

                # 進捗コールバック（必要に応じて）
                if "progress_callback" in kwargs:
                    kwargs["progress_callback"](len(batch))

            return all_translated
