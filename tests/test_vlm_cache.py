import pytest
import os
import json
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from core.pdf_ingester import run_pdf_ingestion_async
from core.config import SessionState

@pytest.mark.asyncio
async def test_vlm_cache_hit_miss(tmp_path):
    """VLMキャッシュのHit/Missおよび保存のテスト"""
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    
    # SessionStateのモック
    state = MagicMock(spec=SessionState)
    state.session_dir = session_dir
    state.vlm_cache = session_dir / "vlm_cache.json"
    
    pdf_path = "dummy.pdf"
    
    # 1. 初回実行 (Miss -> 保存)
    with patch("fitz.open") as mock_open, \
         patch("core.pdf_ingester.process_page_vlm") as mock_process:
        
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        # get_text() の引数によって戻り値を変える
        def mock_get_text(mode=None):
            if mode == "dict":
                return {"blocks": []}
            return ""
        mock_page.get_text.side_effect = mock_get_text
        
        mock_page.get_images.return_value = []
        mock_page.rect.width = 100
        mock_page.rect.height = 100
        
        mock_doc.__getitem__.return_value = mock_page
        mock_doc.__iter__.return_value = iter([mock_page])
        mock_open.return_value = mock_doc
        
        mock_process.return_value = "VLM Result Page 0"
        
        # 実行
        res = await run_pdf_ingestion_async(pdf_path, api_key="key", state=state)
        
        assert "VLM Result Page 0" in res
        assert mock_process.call_count == 1
        assert state.vlm_cache.exists()
        
        with open(state.vlm_cache, "r", encoding="utf-8") as f:
            cache = json.load(f)
            assert cache["0"] == "VLM Result Page 0"

    # 2. 2回目実行 (Hit -> Gemini呼ばれない)
    with patch("fitz.open") as mock_open, \
         patch("core.pdf_ingester.process_page_vlm") as mock_process:
        
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        mock_page.get_text.return_value = ""
        mock_page.get_images.return_value = []
        
        mock_doc.__getitem__.return_value = mock_page
        mock_doc.__iter__.return_value = iter([mock_page])
        mock_open.return_value = mock_doc
        
        # 実行
        res = await run_pdf_ingestion_async(pdf_path, api_key="key", state=state)
        
        assert "VLM Result Page 0" in res
        # キャッシュヒットにより process_page_vlm は呼ばれないはず
        assert mock_process.call_count == 0
