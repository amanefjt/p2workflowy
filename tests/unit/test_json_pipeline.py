import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch
from core.llm_client import translate_batch
from core.models import TreeNode
from aiolimiter import AsyncLimiter

@pytest.mark.asyncio
async def test_translate_batch_success():
    """正常系: 正しい JSON レスポンスが返された場合に TreeNode リストに変換されること。"""
    chunks = [
        {"id": "chunk_1", "text": "Hello world", "seq_index": 1.0},
        {"id": "chunk_2", "text": "This is a test", "seq_index": 2.0}
    ]
    
    # Mock Response
    mock_response = json.dumps({
        "translations": [
            {"id": "chunk_1", "translation": "こんにちは世界"},
            {"id": "chunk_2", "translation": "これはテストです"}
        ]
    })
    
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
        assert results[0].translation == "こんにちは世界"
        assert results[1].id == "chunk_2"
        assert results[1].translation == "これはテストです"

@pytest.mark.asyncio
async def test_translate_batch_malformed_json():
    """異常系: 壊れた JSON が返された場合、原文維持のフォールバックが行われること。"""
    chunks = [
        {"id": "chunk_1", "text": "Hello", "seq_index": 1.0}
    ]
    
    # 不正な JSON (閉じカッコ不足など)
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
        # 翻訳に失敗した場合は、llm_client.py の実装に従い [Translation Error] が付与されるはず
        assert "[Translation Error]" in results[0].translation

@pytest.mark.asyncio
async def test_translate_batch_missing_id():
    """異常系: レスポンスに一部の ID が欠けている場合、欠けているものだけフォールバックされること。"""
    chunks = [
        {"id": "chunk_1", "text": "Keep", "seq_index": 1.0},
        {"id": "chunk_2", "text": "Missing", "seq_index": 2.0}
    ]
    
    # chunk_2 が欠落しているレスポンス
    mock_response = json.dumps({
        "translations": [
            {"id": "chunk_1", "translation": "保持"}
        ]
    })
    
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
        assert results[0].translation == "保持"
        assert results[1].id == "chunk_2"
        assert "[Translation Error]" in results[1].translation

@pytest.mark.asyncio
async def test_translate_batch_empty_response():
    """異常系: API から空または期待外の形式が返された場合。"""
    chunks = [
        {"id": "chunk_1", "text": "Fail", "seq_index": 1.0}
    ]
    
    mock_response = "{}" # translations がない
    
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
        assert "[Translation Error]" in results[0].translation
