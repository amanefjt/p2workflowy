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
    def test_signature_uses_prev_context_text(self):
        """process_page_vlm は前ページを画像ではなくテキスト文脈で受け取る（I-21）。"""
        sig = inspect.signature(OCRManager.process_page_vlm)
        assert list(sig.parameters.keys()) == [
            "self", "current_img", "prev_context_text", "page_idx", "session_dir",
        ]

    @pytest.mark.asyncio
    async def test_call_pattern_succeeds(self):
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        result = await manager.process_page_vlm(
            img, prev_context_text="", page_idx=0, session_dir=None
        )
        assert result == "# Heading\nBody text"
        manager._call_gemini_raw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_image_not_merged(self):
        """VLM には現ページ画像1枚だけが渡り、2-up 結合されない（I-21 の核心）。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 20), color="white")
        await manager.process_page_vlm(
            img, prev_context_text="前ページ末尾テキスト", page_idx=2, session_dir=None
        )
        # _call_gemini_raw の第1引数（content list）の画像が入力画像と同一寸法であること
        # （結合していれば幅が倍化する）
        call_args = manager._call_gemini_raw.await_args.args[0]
        passed_img = call_args[0]
        assert passed_img.size == (10, 20), "結合されず現ページ画像がそのまま渡るべき"

    @pytest.mark.asyncio
    async def test_prev_context_injected_into_prompt(self):
        """page_idx>=1 では前文脈がプロンプトに差し込まれる。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        await manager.process_page_vlm(
            img, prev_context_text="...ending mid sentence and", page_idx=3, session_dir=None
        )
        prompt = manager._call_gemini_raw.await_args.args[0][1]
        assert "...ending mid sentence and" in prompt
        assert "{prev_context}" not in prompt, "プレースホルダが未置換で残ってはならない"

    @pytest.mark.asyncio
    async def test_page0_uses_front_matter_prompt(self):
        """先頭ページ(page_idx==0)は FRONT_MATTER プロンプトを使う。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        await manager.process_page_vlm(img, prev_context_text="", page_idx=0, session_dir=None)
        prompt = manager._call_gemini_raw.await_args.args[0][1]
        assert prompt == OCRManager.VLM_FRONT_MATTER_PROMPT


class TestNoTextMarker:
    """印刷テキストが無いページの合図（NO_TEXT_MARKER）は空文字列として
    呼び出し元に伝わり、失敗（例外）とは区別される。"""

    @pytest.mark.asyncio
    async def test_marker_converted_to_empty_string(self):
        manager = _make_ocr_manager()
        manager._call_gemini_raw = AsyncMock(return_value=OCRManager.NO_TEXT_MARKER)
        img = Image.new("RGB", (10, 10), color="white")
        result = await manager.process_page_vlm(
            img, prev_context_text="", page_idx=2, session_dir=None
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_marker_converted_to_empty_string_on_cache_hit(self):
        """キャッシュ経由でマーカーが返る場合も同様に空文字列へ変換される。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        # 1回目でキャッシュに書き込ませる
        manager._call_gemini_raw = AsyncMock(return_value=OCRManager.NO_TEXT_MARKER)
        await manager.process_page_vlm(img, prev_context_text="", page_idx=2, session_dir=None)
        manager._call_gemini_raw.reset_mock()
        # 2回目はキャッシュヒットのはず（_call_gemini_raw は呼ばれない）
        result = await manager.process_page_vlm(img, prev_context_text="", page_idx=2, session_dir=None)
        assert result == ""
        manager._call_gemini_raw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_genuine_failure_propagates_instead_of_becoming_empty(self):
        """本物の VLM 失敗（例外）は空文字列に化けず、呼び出し元まで伝播する。
        空文字列と失敗を区別できないと、フォールバック要否を判断できない。"""
        manager = _make_ocr_manager()
        manager._call_gemini_raw = AsyncMock(side_effect=RuntimeError("Gemini API 非同期呼び出し失敗"))
        img = Image.new("RGB", (10, 10), color="white")
        with pytest.raises(RuntimeError):
            await manager.process_page_vlm(img, prev_context_text="", page_idx=2, session_dir=None)
