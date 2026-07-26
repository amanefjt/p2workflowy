import asyncio
import random
from typing import List, Dict, Any, Callable, Optional
from aiolimiter import AsyncLimiter
from collections import deque
from core.models import TreeNode
from core.config import print_log
from core.llm_client import (
    translate_batch, tier_manager, GeminiTier, apply_tier_settings,
    model_rotator, key_rotator, get_free_pool_rate_limiters, pick_lane,
)

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
        """バッチ1件分の (api_key, model, rate_limiter, model_pinned, key_pinned) を決定する。

        ユーザーがモデルを明示指定していない FREE tier のときのみ、バッチ単位で
        「キー × モデル」の2軸ラウンドロビン割り当てを行う（docs/model_optimization.md §8）。
        無料キーは別GCPプロジェクト、プール内の各Liteモデルは独立したカウンターなので、
        (キー, モデル) の組み合わせ＝レーンがそれぞれ独立した RPM 枠を持つ。K本 × M モデルで
        K*M レーンになり、理論上のスループット上限がその分だけ広がる。

        割り当て式は i を通し番号として key = keys[i % K], model = models[(i // K) % M]。
        こうすると連続するリクエストが必ず別キーへ散り（同一キーの枠を連打しない）、K*M 回で
        全レーンをちょうど一巡する。

        キー軸が使えない環境（無料キーが1本以下、tiers 未設定、PAID tier）ではキー軸だけが
        自然に無効化され、§7 のモデル軸ラウンドロビンにそのままフォールバックする。
        ラウンドロビン対象は model_pinned/key_pinned=True とし、call_gemini_async 側で
        ModelRotator / KeyRotator による上書きを受けないようにする
        （プロアクティブな負荷分散とリアクティブな429フォールバックを混在させないため）。

        §9: 429/503 を検知したレーンはクールダウンレジストリに記録され（`core.llm_client.
        lane_cooldown`）、`pick_lane()` がクールダウン中でないレーンを優先的に選ぶ。全レーンが
        クールダウン中の場合のみ、クールダウンを無視した従来どおりの割り当てにフォールバック
        する（例外を投げない・処理を止めない）。
        """
        if self.model is None and self.tier == GeminiTier.FREE:
            pool = model_rotator.pool_models()
            keys = key_rotator.pool_keys() if key_rotator.is_configured() else []
            key_axis = keys if len(keys) > 1 else []
            if len(pool) > 1 or key_axis:
                i = self._rr_index
                self._rr_index += 1
                lane = pick_lane(pool, key_axis, i)
                if lane is not None:
                    batch_key, batch_model = lane
                    key_pinned = bool(key_axis)
                    if not key_pinned:
                        batch_key = self.api_key
                elif key_axis:
                    # 全レーンがクールダウン中 → クールダウンを無視した §8 従来どおりの割当て
                    batch_key = key_axis[i % len(key_axis)]
                    batch_model = pool[(i // len(key_axis)) % len(pool)] if pool else None
                    key_pinned = True
                else:
                    batch_key = self.api_key
                    batch_model = pool[i % len(pool)]
                    key_pinned = False
                if batch_model is not None:
                    limiters = get_free_pool_rate_limiters(batch_key)
                    return batch_key, batch_model, limiters[batch_model], True, key_pinned
        return self.api_key, self.model, self.rate_limiter, False, False

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

                # 3. 翻訳実行（FREE tier ではバッチ単位でキー×モデルの2軸ラウンドロビン割り当て）
                previous = prompt_builder_func(all_translated)
                batch_key, batch_model, batch_limiter, batch_model_pinned, batch_key_pinned = self._pick_batch_target()
                lane = f"model={batch_model or 'default'}"
                if batch_key_pinned:
                    lane += f", key#{key_rotator.pool_keys().index(batch_key) + 1}"
                print_log(f"  [ParallelTranslator] {section_name}: Batch {batch_idx} ({len(batch)} chunks, {batch_chars} chars, {lane})")

                try:
                    batch_nodes = await translate_func(
                        chunks=batch,
                        previous_translation=previous,
                        rate_limiter=batch_limiter,
                        api_key=batch_key, # ラウンドロビン割り当て先のキー（非適用時は self.api_key）
                        model=batch_model, # ユーザー指定モデル、またはラウンドロビン割り当て先を尊重する
                        model_pinned=batch_model_pinned,
                        key_pinned=batch_key_pinned,
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
