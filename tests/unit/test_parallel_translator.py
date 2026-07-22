import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from core.engine.p4_translate.parallel_translator import ParallelTranslator
from core.models import TreeNode
from core.llm_client import GeminiTier, model_rotator

@pytest.mark.asyncio
async def test_parallel_translator_batching():
    """バッチングの検証: max_batch_chars を超える場合にバッチが分割されること。

    バッチ分割は translate_section_chunks 内にインラインで実装されている（ティアの動的変更に
    追従するため）。以前は別メソッド _create_batches をテストしていたが、そちらは本番コードから
    一切呼ばれない死コードで、実際のバッチ境界ロジックの変更を検知できなかった（2026-07-21
    レビュー指摘）。_create_batches は削除し、実運用経路を直接検証する。
    """
    translator = ParallelTranslator(tier=GeminiTier.FREE)
    # 現行 FREE tier デフォルト値を上書きして確定的なテストにする
    translator.settings["max_batch_chunks"] = 3
    translator.settings["max_batch_chars"] = 1500

    # 600文字のチャンクを 4つ用意
    chunks = [
        {"id": f"c{i}", "text": "A" * 600, "seq_index": float(i)}
        for i in range(4)
    ]

    seen_batches = []

    async def mock_translate_func(**kwargs):
        batch = kwargs.get("chunks")
        seen_batches.append(batch)
        return [
            TreeNode(id=c["id"], text=c["text"], translation=c["text"])
            for c in batch
        ]

    def mock_prompt_builder(nodes):
        return ""

    await translator.translate_section_chunks(
        section_name="Test",
        chunks=chunks,
        prompt_builder_func=mock_prompt_builder,
        translate_func=mock_translate_func,
    )

    # バッチ1: c0 (600) + c1 (600) = 1200 < 1500。c2 を足すと 1800 > 1500 → ここで切断
    # バッチ2: c2 (600) + c3 (600) = 1200 < 1500
    assert len(seen_batches) == 2
    assert len(seen_batches[0]) == 2
    assert len(seen_batches[1]) == 2

@pytest.mark.asyncio
async def test_parallel_translator_translate_section():
    """セクション全体の翻訳実行と TreeNode リストの結合を検証。"""
    translator = ParallelTranslator(max_concurrent_sections=1)
    chunks = [
        {"id": "c1", "text": "Content 1"},
        {"id": "c2", "text": "Content 2"}
    ]
    
    # 擬似的な翻訳関数 (llm_client.translate_batch 相当)
    async def mock_translate_func(**kwargs):
        batch = kwargs.get("chunks")
        return [
            TreeNode(id=c["id"], text=c["text"], translation=f"Trans {c['id']}")
            for c in batch
        ]
    
    def mock_prompt_builder(nodes):
        return "Context"

    results = await translator.translate_section_chunks(
        section_name="Test",
        chunks=chunks,
        prompt_builder_func=mock_prompt_builder,
        translate_func=mock_translate_func
    )
    
    assert len(results) == 2
    assert results[0].translation == "Trans c1"
    assert results[1].translation == "Trans c2"

@pytest.mark.asyncio
async def test_parallel_translator_batch_failure_isolation():
    """バッチが1つ失敗しても、他のバッチに影響を与えずフォールバックが行われること。"""
    translator = ParallelTranslator(tier=GeminiTier.PAID)
    # 2バッチになるように設定を上書き (本来は settings にある)
    translator.settings["max_batch_chunks"] = 1
    
    chunks = [
        {"id": "c1", "text": "Success"},
        {"id": "c2", "text": "Fail"}
    ]
    
    # c2 のバッチの時だけ例外を投げる
    async def mock_translate_func(**kwargs):
        batch = kwargs.get("chunks")
        if batch[0]["id"] == "c2":
            raise RuntimeError("API Error")
        return [TreeNode(id=batch[0]["id"], text=batch[0]["text"], translation="OK")]

    def mock_prompt_builder(nodes): return ""

    results = await translator.translate_section_chunks(
        section_name="Test",
        chunks=chunks,
        prompt_builder_func=mock_prompt_builder,
        translate_func=mock_translate_func
    )
    
    assert len(results) == 2
    assert results[0].translation == "OK"
    assert "【翻訳失敗】" in results[1].text  # text に全角括弧のエラーマーカーが書き込まれる仕様


def test_pick_batch_target_round_robins_free_pool():
    """FREE tier・モデル未指定なら、無料枠Liteプールをバッチ単位でラウンドロビン割り当てし、
    各バッチは pinned=True（ModelRotatorのresolve()/advance()による上書きを受けない）になる。"""
    model_rotator.reset()
    try:
        translator = ParallelTranslator(tier=GeminiTier.FREE)
        pool = model_rotator.pool_models()
        assert len(pool) > 1, "テスト前提: DEFAULT_MODEL_FREE_POOL に2要素以上必要"

        picks = [translator._pick_batch_target() for _ in range(len(pool) * 2)]
        models = [m for m, _, _ in picks]
        pinned_flags = [p for _, _, p in picks]

        assert models == (pool * 2)  # 順番にラウンドロビン
        assert all(pinned_flags)
        # モデルごとに独立した AsyncLimiter インスタンスであること（同一モデルは同一インスタンス）
        limiters_by_model = {m: l for m, l, _ in picks}
        assert limiters_by_model[pool[0]] is not limiters_by_model[pool[1]]
    finally:
        model_rotator.reset()


def test_pick_batch_target_respects_explicit_model():
    """ユーザーが model を明示指定した場合は FREE tier でもラウンドロビンせず、
    指定モデル・共有 rate_limiter・pinned=False のまま（既存の「ユーザー指定を尊重する」仕様）。"""
    translator = ParallelTranslator(tier=GeminiTier.FREE, model="gemini-3.6-flash")

    model, limiter, pinned = translator._pick_batch_target()

    assert model == "gemini-3.6-flash"
    assert limiter is translator.rate_limiter
    assert pinned is False


def test_pick_batch_target_no_round_robin_on_paid_tier():
    """PAID tier ではモデル未指定でもラウンドロビンしない（既存の単一 self.model/self.rate_limiter のまま）。"""
    translator = ParallelTranslator(tier=GeminiTier.PAID)

    model, limiter, pinned = translator._pick_batch_target()

    assert model is translator.model  # None のまま(呼び出し先の get_default_model() に委ねる)
    assert limiter is translator.rate_limiter
    assert pinned is False


@pytest.mark.asyncio
async def test_translate_section_chunks_uses_round_robin_models():
    """統合: FREE tier・モデル未指定で複数バッチを翻訳すると、実際に translate_func へ渡される
    model がプール内で交互に切り替わる。"""
    model_rotator.reset()
    try:
        translator = ParallelTranslator(tier=GeminiTier.FREE)
        pool = model_rotator.pool_models()
        translator.settings["max_batch_chunks"] = 1  # 1チャンク=1バッチにして境界を確定的にする

        chunks = [{"id": f"c{i}", "text": "x", "seq_index": float(i)} for i in range(4)]
        seen_models = []
        seen_pinned = []

        async def mock_translate_func(**kwargs):
            seen_models.append(kwargs.get("model"))
            seen_pinned.append(kwargs.get("model_pinned"))
            batch = kwargs["chunks"]
            return [TreeNode(id=c["id"], text=c["text"], translation=c["text"]) for c in batch]

        await translator.translate_section_chunks(
            section_name="Test",
            chunks=chunks,
            prompt_builder_func=lambda nodes: "",
            translate_func=mock_translate_func,
        )

        assert seen_models == [pool[0], pool[1], pool[0], pool[1]]
        assert all(seen_pinned)
    finally:
        model_rotator.reset()
