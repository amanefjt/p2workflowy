import asyncio
import random
from typing import List, Dict, Any, Callable, Optional
from aiolimiter import AsyncLimiter
from collections import deque
from core.models import TreeNode
from core.config import print_log
from core.llm_client import translate_batch, tier_manager, GeminiTier, apply_tier_settings, model_rotator, get_free_pool_rate_limiters

class ParallelTranslator:
    """
    非同期並列翻訳を制御するエンジン。
    セマフォ、レートリミッター、バッチング、およびエラーハンドリングを専門に扱う。
    """
    DEFAULT_MAX_BATCH_CHUNKS = 18
    DEFAULT_MAX_BATCH_CHARS = 20000

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        tier: GeminiTier = GeminiTier.PAID,
        max_concurrent_sections: int = 8
    ):
        self.api_key = api_key
        self.model = model
        self.tier = tier
        self.semaphore = asyncio.Semaphore(max_concurrent_sections)
        self.rate_limiter, self.settings = apply_tier_settings(tier, api_key=self.api_key)
        self._rr_index = 0  # 無料枠Liteプールのラウンドロビン用カウンタ（バッチ単位でインクリメント）

    def _pick_batch_target(self):
        """バッチ1件分の (model, rate_limiter, pinned) を決定する。

        ユーザーがモデルを明示指定していない FREE tier かつプールが複数モデルを持つ場合のみ、
        バッチ単位でプール内モデルへラウンドロビン割り当てする（各モデルは独立した
        AsyncLimiter を持つため、単一リミッタ共有時よりスループットが上がる）。それ以外は
        従来どおり self.model / self.rate_limiter をそのまま使う（pinned=False、429時は
        ModelRotator の既存フォールバックに委ねる）。
        ラウンドロビン対象バッチは pinned=True とし、call_gemini_async 側で
        ModelRotator.resolve()/advance() による上書きを受けないようにする
        （プロアクティブな負荷分散とリアクティブなRPD枯渇対応を混在させないため）。
        """
        if self.model is None and self.tier == GeminiTier.FREE:
            pool = model_rotator.pool_models()
            if len(pool) > 1:
                limiters = get_free_pool_rate_limiters(self.api_key)
                batch_model = pool[self._rr_index % len(pool)]
                self._rr_index += 1
                return batch_model, limiters[batch_model], True
        return self.model, self.rate_limiter, False

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
                    self.rate_limiter, self.settings = apply_tier_settings(self.tier, api_key=self.api_key)

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

                # 3. 翻訳実行（無料枠Liteプールが複数モデルの場合はバッチ単位でラウンドロビン割り当て）
                previous = prompt_builder_func(all_translated)
                batch_model, batch_limiter, batch_pinned = self._pick_batch_target()
                print_log(f"  [ParallelTranslator] {section_name}: Batch {batch_idx} ({len(batch)} chunks, {batch_chars} chars, model={batch_model or 'default'})")

                try:
                    batch_nodes = await translate_func(
                        chunks=batch,
                        previous_translation=previous,
                        rate_limiter=batch_limiter,
                        api_key=self.api_key,
                        model=batch_model, # ユーザー指定モデル、またはラウンドロビン割り当て先を尊重する
                        model_pinned=batch_pinned,
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
