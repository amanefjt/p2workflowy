"""
OCRManager.process_page_vlm の二重定義バグ（I-15）に対する回帰テスト。

クラス内に同名メソッドが2つ定義されていると Python は後者のみを生存させる。
唯一の呼び出し元 pdf_ingester.py:67 は画像引数版のシグネチャで呼ぶため、
pdf_path 引数版が生存していると毎回 TypeError になる。
"""

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from core.engine.p1_ingest.ocr_manager import OCRManager


def _make_ocr_manager() -> OCRManager:
    """API 初期化や環境変数を経由せずに OCRManager インスタンスを作る。"""
    manager = OCRManager.__new__(OCRManager)
    manager.semaphore = asyncio.Semaphore(1)
    manager.cache = {}
    manager._save_cache = lambda: None
    manager._call_gemini_raw = AsyncMock(return_value="# Heading\nBody text")
    return manager


class TestProcessPageVlmSignature:
    def test_signature_matches_pdf_ingester_call_site(self):
        """pdf_ingester.py:67 は (curr_img, prev_img=, page_idx=, session_dir=) で呼ぶ。
        生存すべきシグネチャはこれと一致する画像引数版でなければならない。"""
        sig = inspect.signature(OCRManager.process_page_vlm)
        assert list(sig.parameters.keys()) == [
            "self", "current_img", "prev_img", "page_idx", "session_dir",
        ]

    @pytest.mark.asyncio
    async def test_call_with_pdf_ingester_call_pattern_succeeds(self):
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")

        result = await manager.process_page_vlm(
            img, prev_img=None, page_idx=0, session_dir=None
        )

        assert result == "# Heading\nBody text"
        manager._call_gemini_raw.assert_awaited_once()
