"""
RTT v3.4: XML パースロバスト性 & 適応バッチングの回帰テスト

旧版は正規表現を手書きコピーしていたが、実際の translate_batch 実装と乖離していた。
本ファイルは実装を直接使い、pytest + AsyncMock で動作を検証する。
"""

import pytest
from unittest.mock import AsyncMock, patch
from aiolimiter import AsyncLimiter

from core.llm_client import translate_batch, GeminiTier, apply_tier_settings, tier_manager
from core.engine.p4_translate.parallel_translator import ParallelTranslator


# ============================================================
# XML パースロバスト性テスト
# ============================================================

@pytest.mark.asyncio
async def test_parse_truncated_last_chunk():
    """末尾の閉じタグが欠落していても最後のチャンクが救出されること。"""
    chunks = [
        {"id": "101", "text": "Hello", "seq_index": 1.0},
        {"id": "102", "text": "World", "seq_index": 2.0},
    ]
    # 102 の閉じタグなし（LLM 出力が途中で途切れたケース）
    mock_response = (
        "<p2w_chunk_101>こんにちは</p2w_chunk_101>"
        "<p2w_chunk_102>さようなら（ここで途切れる..."
    )

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response
        results = await translate_batch(
            chunks=chunks,
            glossary_content="", previous_translation="",
            prompt_template="Test {chunk_json}", resume_content="",
            section_name="Test", rate_limiter=AsyncLimiter(10, 1),
        )

    assert results[0].text == "こんにちは"
    assert results[1].text == "さようなら（ここで途切れる..."


@pytest.mark.asyncio
async def test_parse_missing_intermediate_close_tag():
    """中間の閉じタグが欠落していても、次の開始タグによって内容が正しく区切られること。"""
    chunks = [
        {"id": "101", "text": "A", "seq_index": 1.0},
        {"id": "102", "text": "B", "seq_index": 2.0},
    ]
    # 101 の閉じタグがなく、102 が直接続くケース
    mock_response = (
        "<p2w_chunk_101>本文A"
        "<p2w_chunk_102>本文B</p2w_chunk_102>"
    )

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response
        results = await translate_batch(
            chunks=chunks,
            glossary_content="", previous_translation="",
            prompt_template="Test {chunk_json}", resume_content="",
            section_name="Test", rate_limiter=AsyncLimiter(10, 1),
        )

    assert results[0].text == "本文A"
    assert results[1].text == "本文B"


@pytest.mark.asyncio
async def test_parse_case_insensitive_tags():
    """大文字の開始タグ（<P2W_CHUNK_101>）も正しく認識されること。"""
    chunks = [{"id": "101", "text": "Hello", "seq_index": 1.0}]
    mock_response = "<P2W_CHUNK_101>Mixed Case</p2w_chunk_101>"

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response
        results = await translate_batch(
            chunks=chunks,
            glossary_content="", previous_translation="",
            prompt_template="Test {chunk_json}", resume_content="",
            section_name="Test", rate_limiter=AsyncLimiter(10, 1),
        )

    assert results[0].text == "Mixed Case"


@pytest.mark.asyncio
async def test_parse_unknown_id_ignored():
    """known_ids に含まれない ID のタグはパース結果に含まれないこと。"""
    chunks = [{"id": "real_id", "text": "Hello", "seq_index": 1.0}]
    # 実在しない ID のタグを混ぜる
    mock_response = (
        "<p2w_chunk_real_id>正しい翻訳</p2w_chunk_real_id>"
        "<p2w_chunk_ghost_id>ゴースト</p2w_chunk_ghost_id>"
    )

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response
        results = await translate_batch(
            chunks=chunks,
            glossary_content="", previous_translation="",
            prompt_template="Test {chunk_json}", resume_content="",
            section_name="Test", rate_limiter=AsyncLimiter(10, 1),
        )

    assert len(results) == 1
    assert results[0].text == "正しい翻訳"


# ============================================================
# 適応バッチングテスト（ティアダウングレード対応）
# ============================================================

@pytest.mark.asyncio
async def test_adaptive_batching_after_tier_downgrade():
    """ティアが PAID → FREE にダウングレードされた後、次のバッチから設定が反映されること。"""
    tier_manager.set_tier(GeminiTier.PAID)
    translator = ParallelTranslator(tier=GeminiTier.PAID)

    # PAID 設定確認
    assert translator.settings["max_batch_chunks"] == 10

    # ダウングレードをシミュレート
    tier_manager.downgrade()

    # translate_section_chunks 内の動的ティア反映ロジックを模擬:
    # 実装は tier_manager.current_tier != self.tier を検知して apply_tier_settings を呼ぶ
    if tier_manager.current_tier != translator.tier:
        translator.tier = tier_manager.current_tier
        translator.rate_limiter, _, translator.settings = apply_tier_settings(translator.tier)

    # FREE 設定に切り替わっていること
    assert translator.settings["max_batch_chunks"] == 5
    assert translator.settings["max_batch_chars"] == 6000

    # クリーンアップ
    tier_manager.set_tier(GeminiTier.PAID)
