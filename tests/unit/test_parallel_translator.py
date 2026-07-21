import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from core.engine.p4_translate.parallel_translator import ParallelTranslator
from core.models import TreeNode
from core.llm_client import GeminiTier

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
