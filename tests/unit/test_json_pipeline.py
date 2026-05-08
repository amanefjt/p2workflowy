import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch
from core.llm_client import translate_batch
from core.models import TreeNode
from aiolimiter import AsyncLimiter

@pytest.mark.asyncio
async def test_translate_batch_success():
    """正常系: 正しい XML タグ形式のレスポンスが返された場合に TreeNode リストに変換されること。"""
    chunks = [
        {"id": "chunk_1", "text": "Hello world", "seq_index": 1.0},
        {"id": "chunk_2", "text": "This is a test", "seq_index": 2.0}
    ]

    # XML タグ形式のモックレスポンス（現行の _parse_response が期待するフォーマット）
    mock_response = (
        "<p2w_chunk_chunk_1>\nこんにちは世界\n</p2w_chunk_chunk_1>\n\n"
        "<p2w_chunk_chunk_2>\nこれはテストです\n</p2w_chunk_chunk_2>\n"
    )

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response

        limiter = AsyncLimiter(10, 1)
        results = await translate_batch(
            chunks=chunks,
            glossary_content="",
            previous_translation="",
            prompt_template="Test {chunk_json}",
            resume_content="",
            section_name="Test Section",
            rate_limiter=limiter
        )

        assert len(results) == 2
        assert results[0].id == "chunk_1"
        assert results[0].text == "こんにちは世界"
        assert results[1].id == "chunk_2"
        assert results[1].text == "これはテストです"

@pytest.mark.asyncio
async def test_translate_batch_malformed_json():
    """異常系: XML タグが見つからない場合、フォールバック（【翻訳失敗】）が行われること。"""
    chunks = [
        {"id": "chunk_1", "text": "Hello", "seq_index": 1.0}
    ]

    # XML タグを含まないレスポンス → _parse_response がタグを見つけられない
    mock_response = '{"translations": [{"id": "chunk_1", "translation": "こんにちは"'

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response

        limiter = AsyncLimiter(10, 1)
        results = await translate_batch(
            chunks=chunks,
            glossary_content="",
            previous_translation="",
            prompt_template="Test",
            resume_content="",
            section_name="Test Section",
            rate_limiter=limiter
        )

        assert len(results) == 1
        assert results[0].id == "chunk_1"
        # 翻訳失敗時は text フィールドに【翻訳失敗】プレフィックスが付与される
        assert "【翻訳失敗】" in results[0].text

@pytest.mark.asyncio
async def test_translate_batch_missing_id():
    """異常系: レスポンスに一部の ID が欠けている場合、欠けているものだけフォールバックされること。"""
    chunks = [
        {"id": "chunk_1", "text": "Keep", "seq_index": 1.0},
        {"id": "chunk_2", "text": "Missing", "seq_index": 2.0}
    ]

    # chunk_1 のみ含むレスポンス（chunk_2 は欠落）
    mock_response = "<p2w_chunk_chunk_1>\n保持\n</p2w_chunk_chunk_1>\n"

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response

        limiter = AsyncLimiter(10, 1)
        results = await translate_batch(
            chunks=chunks,
            glossary_content="",
            previous_translation="",
            prompt_template="Test",
            resume_content="",
            section_name="Test Section",
            rate_limiter=limiter
        )

        assert len(results) == 2
        assert results[0].id == "chunk_1"
        assert results[0].text == "保持"
        assert results[1].id == "chunk_2"
        # 欠落した chunk_2 は【翻訳失敗】フォールバックになる
        assert "【翻訳失敗】" in results[1].text

@pytest.mark.asyncio
async def test_translate_batch_empty_response():
    """異常系: XML タグが一切含まれないレスポンスが返された場合のフォールバック。"""
    chunks = [
        {"id": "chunk_1", "text": "Fail", "seq_index": 1.0}
    ]

    mock_response = "{}"  # XML タグなし

    with patch("core.llm_client.call_gemini_async", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response

        limiter = AsyncLimiter(10, 1)
        results = await translate_batch(
            chunks=chunks,
            glossary_content="",
            previous_translation="",
            prompt_template="Test",
            resume_content="",
            section_name="Test Section",
            rate_limiter=limiter
        )

        assert len(results) == 1
        assert "【翻訳失敗】" in results[0].text
