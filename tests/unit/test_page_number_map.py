"""
page_number_map のユニットテスト

テスト対象:
  - parse_page_number: 行を頁番号として解釈（OCR 崩れ耐性）
  - harvest_printed_page: 頁のヘッダー/フッターから印刷頁番号を1つ得る
  - estimate_offset: 文書全体から最頻オフセット（物理idx - 印刷頁）を推定
"""

import pytest
from unittest.mock import MagicMock

from core.engine.p1_ingest.page_number_map import (
    parse_page_number,
    harvest_printed_page,
    estimate_offset,
)


def make_mock_doc(page_texts: list[str]) -> MagicMock:
    doc = MagicMock()
    pages = []
    for t in page_texts:
        p = MagicMock()
        p.get_text.return_value = t
        pages.append(p)
    doc.__len__.return_value = len(pages)
    doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
    return doc


class TestParsePageNumber:
    def test_plain_digits(self):
        assert parse_page_number("147") == 147

    def test_ocr_corrupted_digits(self):
        # 'I'→1, 'l'→1, 'O'→0 等の誤読に耐える
        assert parse_page_number("3 I") == 31
        assert parse_page_number("l72") == 172

    def test_rejects_pure_roman_numeral(self):
        # 数字を1文字も含まない文字列は頁番号として扱わない（章マーカーとの誤認防止）
        assert parse_page_number("XIII") is None
        assert parse_page_number("I") is None

    def test_rejects_long_string(self):
        assert parse_page_number("Property, Substance and Effect") is None

    def test_rejects_out_of_range(self):
        assert parse_page_number("99999") is None
        assert parse_page_number("0") is None


class TestHarvestPrintedPage:
    def test_recto_title_then_number(self):
        # corfra / PSE の recto 形式: 'Knowing | 147'
        text = "Divisions of Interest\n137\n本文が続く……\n"
        assert harvest_printed_page(text) == 137

    def test_verso_number_then_title(self):
        # verso 形式: 頁番号が先
        text = "144\nProperty, Substance and Effect\n本文が続く……\n"
        assert harvest_printed_page(text) == 144

    def test_rejects_page_with_conflicting_numbers(self):
        # 同一頁から異なる数値が読めた場合は棄却する（誤読の混入を防ぐ）
        text = "12\nTitle\n本文\n99\n"
        assert harvest_printed_page(text) is None

    def test_returns_none_for_empty_page(self):
        assert harvest_printed_page("") is None

    def test_returns_none_when_no_number(self):
        assert harvest_printed_page("Title\n本文だけの頁\n") is None


class TestEstimateOffset:
    def test_constant_offset(self):
        # 物理 idx 2,3,4,5,6 に印刷頁 1,2,3,4,5 → オフセット +1 (5票)
        doc = make_mock_doc([
            "表紙\n", "扉\n",
            "1\nTitle\n", "2\nTitle\n", "3\nTitle\n", "4\nTitle\n", "5\nTitle\n",
        ])
        assert estimate_offset(doc) == 1

    def test_ignores_outliers(self):
        # 年号など（1958）が混じっても最頻値は揺らがない
        # 物理idx 1, 2, 3, 4, 5, 6に印刷頁1, 2, 3, 4, 5が続き、最初のページは年号1958
        # オフセット: idx2-1=1, idx3-2=1, idx4-3=1, idx5-4=1, idx6-5=1 (5票のoffset +1)
        # idx0に1958があるとoffset 0-1958=-1958 (1票)
        doc = make_mock_doc([
            "1958\n奥付\n",       # 年号（1票のoutlier）
            "表紙\n",              # ページ番号なし
            "1\nTitle\n", "2\nTitle\n", "3\nTitle\n", "4\nTitle\n", "5\nTitle\n",  # offset +1 (5票)
        ])
        assert estimate_offset(doc) == 1

    def test_stepped_offsets_picks_most_common(self):
        # 部扉ごとに段が変わる場合、最も多い段を採る（relations 型）。
        # idx0〜4 の印刷頁 1〜5 はオフセット -1 が 5票、idx5〜6 はオフセット 0 が 2票。
        # したがって最頻値 -1 が選ばれなければならない。
        doc = make_mock_doc([
            "1\nT\n", "2\nT\n", "3\nT\n", "4\nT\n", "5\nT\n",  # offset -1 (5票)
            "5\nT\n", "6\nT\n",                                  # offset 0 (2票)
        ])
        result = estimate_offset(doc)
        assert result == -1

    def test_returns_none_when_no_page_numbers(self):
        doc = make_mock_doc(["本文だけ\n", "本文だけ\n"])
        assert estimate_offset(doc) is None
