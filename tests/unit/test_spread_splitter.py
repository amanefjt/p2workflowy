"""
SpreadSplitter のユニットテスト

テスト対象:
  - is_spread_pdf: アスペクト比による見開き判定
  - _find_gutter_x: ノド検出（輝度積分）
  - split_spread_pdf: 分割処理・キャッシュ・スキップ
"""

import numpy as np
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from core.spread_splitter import (
    is_spread_pdf,
    split_spread_pdf,
    _find_gutter_x,
    _page_aspect,
    SPREAD_ASPECT_THRESHOLD,
)


def make_mock_page(width: float, height: float) -> MagicMock:
    page = MagicMock()
    page.rect = MagicMock()
    page.rect.width = width
    page.rect.height = height
    return page


def make_mock_doc(pages: list) -> MagicMock:
    doc = MagicMock()
    doc.__len__.return_value = len(pages)
    doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
    doc.__enter__ = MagicMock(return_value=doc)
    doc.__exit__ = MagicMock(return_value=False)
    return doc


# ────────────────────────────────────────────
# _page_aspect
# ────────────────────────────────────────────

class TestPageAspect:
    def test_landscape_returns_gt_1(self):
        page = make_mock_page(800, 600)
        assert _page_aspect(page) == pytest.approx(800 / 600)

    def test_portrait_returns_lt_1(self):
        page = make_mock_page(432, 648)
        assert _page_aspect(page) < 1.0

    def test_zero_height_returns_0(self):
        page = make_mock_page(800, 0)
        assert _page_aspect(page) == 0.0


# ────────────────────────────────────────────
# is_spread_pdf
# ────────────────────────────────────────────

class TestIsSpreadPdf:
    def test_landscape_pages_detected_as_spread(self):
        pages = [make_mock_page(800, 600)] * 5   # ratio 1.33 > 1.1
        doc = make_mock_doc(pages)
        with patch("fitz.open", return_value=doc):
            assert is_spread_pdf("dummy.pdf") is True

    def test_portrait_pages_not_detected_as_spread(self):
        pages = [make_mock_page(432, 648)] * 5   # ratio 0.67 < 1.1
        doc = make_mock_doc(pages)
        with patch("fitz.open", return_value=doc):
            assert is_spread_pdf("dummy.pdf") is False

    def test_mixed_pages_majority_decides(self):
        # 3 landscape + 2 portrait → majority landscape → spread
        pages = (
            [make_mock_page(800, 600)] * 3 +
            [make_mock_page(432, 648)] * 2
        )
        doc = make_mock_doc(pages)
        with patch("fitz.open", return_value=doc):
            assert is_spread_pdf("dummy.pdf") is True


# ────────────────────────────────────────────
# _find_gutter_x
# ────────────────────────────────────────────

class TestFindGutterX:
    def _make_page_with_gutter(self, width_px: int, height_px: int,
                                gutter_col: int, page_width_pt: float) -> MagicMock:
        """指定列付近に白い縦帯（ノド）を持つグレー画像をモックページとして返す。

        背景を暗め(50)・ノドを10列幅の白(240)にして信頼性判定を確実に通す。
        """
        img = np.ones((height_px, width_px, 3), dtype=np.uint8) * 50
        # 10列幅のノドで max/avg の比率を 1.1 以上にする
        for c in range(gutter_col - 5, gutter_col + 5):
            img[:, c, :] = 240

        pix = MagicMock()
        pix.height = height_px
        pix.width = width_px
        pix.samples = img.tobytes()

        page = MagicMock()
        page.rect = MagicMock()
        page.rect.width = page_width_pt
        page.rect.height = page_width_pt * height_px / width_px
        page.get_pixmap.return_value = pix
        return page

    def test_gutter_detected_near_center(self):
        """中央付近の明るい列をノドとして検出する。"""
        w, h = 400, 300
        gutter_col = 200  # 中央
        page = self._make_page_with_gutter(w, h, gutter_col, page_width_pt=400.0)
        pt_x, is_reliable = _find_gutter_x(page, dpi=72)
        # 中央±10% 以内に検出されるはず
        assert 160 <= pt_x <= 240
        assert is_reliable is True

    def test_uniform_image_not_reliable(self):
        """輝度が一様な画像はノドを信頼できない。"""
        w, h = 400, 300
        img = np.ones((h, w, 3), dtype=np.uint8) * 128  # 均一グレー

        pix = MagicMock()
        pix.height = h
        pix.width = w
        pix.samples = img.tobytes()

        page = MagicMock()
        page.rect = MagicMock()
        page.rect.width = 400.0
        page.rect.height = 300.0
        page.get_pixmap.return_value = pix

        _, is_reliable = _find_gutter_x(page, dpi=72)
        assert is_reliable is False


# ────────────────────────────────────────────
# split_spread_pdf
# ────────────────────────────────────────────

class TestSplitSpreadPdf:
    def test_returns_cached_path_if_exists(self, tmp_path):
        """キャッシュ済みファイルがあれば再処理せず即返す。"""
        cached = tmp_path / "book_split.pdf"
        cached.write_bytes(b"dummy")
        input_pdf = str(tmp_path / "book.pdf")

        result = split_spread_pdf(input_pdf, output_path=str(cached))
        assert result == str(cached)

    def test_spread_page_produces_two_pages(self, tmp_path):
        """見開きページが2ページに分割される。"""
        import fitz as real_fitz

        output = str(tmp_path / "out_split.pdf")

        # 横長の空PDFを作成
        src = real_fitz.open()
        src.new_page(width=800, height=600)  # ratio=1.33 > 1.1
        src_path = str(tmp_path / "spread.pdf")
        src.save(src_path)
        src.close()

        # _find_gutter_x をモックして信頼性あり・中央で返す
        with patch("core.spread_splitter._find_gutter_x", return_value=(400.0, True)):
            result = split_spread_pdf(src_path, output_path=output)

        out_doc = real_fitz.open(result)
        assert len(out_doc) == 2  # 1見開き → 2ページ
        out_doc.close()

    def test_unreliable_gutter_uses_midpoint(self, tmp_path):
        """ノドが不鮮明なページは中点フォールバックで2ページに分割する。"""
        import fitz as real_fitz

        output = str(tmp_path / "out_mid.pdf")

        src = real_fitz.open()
        src.new_page(width=800, height=600)
        src_path = str(tmp_path / "spread_mid.pdf")
        src.save(src_path)
        src.close()

        with patch("core.spread_splitter._find_gutter_x", return_value=(400.0, False)):
            result = split_spread_pdf(src_path, output_path=output)

        out_doc = real_fitz.open(result)
        assert len(out_doc) == 2  # 中点分割 → 2ページ
        out_doc.close()
