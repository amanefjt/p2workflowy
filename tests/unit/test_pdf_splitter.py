"""
PDFSplitter のユニットテスト

テスト対象:
  - _get_chapters_from_outline: PDF ネイティブ outline の読み取り
  - _apply_content_scan: 論理ページ→物理ページのコンテンツスキャン補正
  - _normalize_title: タイトル正規化
  - _classify_role: role 推定
  - split: ルート選択（outline 優先 → LLM）
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from core.engine.p1_ingest.pdf_splitter import PDFSplitter


def make_splitter() -> PDFSplitter:
    with patch.object(PDFSplitter, "_load_cache"):
        s = PDFSplitter(api_key="test_key")
        s.cache = {}
        return s


def make_mock_page(text: str) -> MagicMock:
    page = MagicMock()
    page.get_text.return_value = text
    return page


def make_mock_doc(page_texts: list[str], toc: list = None) -> MagicMock:
    """fitz.Document のモックを生成する。"""
    doc = MagicMock()
    pages = [make_mock_page(t) for t in page_texts]
    doc.__len__.return_value = len(pages)
    # side_effect で idx のみ受け取る（self は MagicMock が解決済み）
    doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
    doc.get_toc.return_value = toc if toc is not None else []
    return doc


# ============================================================
# _normalize_title
# ============================================================

class TestNormalizeTitle:
    def test_strips_chapter_prefix(self):
        s = make_splitter()
        assert s._normalize_title("Chapter 3: Methods") == "methods"

    def test_strips_part_prefix(self):
        s = make_splitter()
        assert s._normalize_title("PART II Foundations") == "foundations"

    def test_strips_leading_numeral(self):
        s = make_splitter()
        assert s._normalize_title("5. Results") == "results"

    def test_strips_roman_numeral(self):
        s = make_splitter()
        assert s._normalize_title("IV. Discussion") == "discussion"

    def test_plain_title_lowercased(self):
        s = make_splitter()
        assert s._normalize_title("Introduction") == "introduction"

    def test_empty_string(self):
        s = make_splitter()
        assert s._normalize_title("") == ""


# ============================================================
# _classify_role
# ============================================================

class TestClassifyRole:
    def test_preface(self):
        s = make_splitter()
        assert s._classify_role("Preface") == "preface"

    def test_foreword(self):
        s = make_splitter()
        assert s._classify_role("Foreword") == "preface"

    def test_appendix(self):
        s = make_splitter()
        assert s._classify_role("Appendix A") == "appendix"

    def test_introduction(self):
        s = make_splitter()
        assert s._classify_role("Introduction") == "introduction"

    def test_default_chapter(self):
        s = make_splitter()
        assert s._classify_role("Methods and Materials") == "chapter"


# ============================================================
# _get_chapters_from_outline
# ============================================================

class TestGetChaptersFromOutline:
    def test_returns_none_when_no_toc(self):
        s = make_splitter()
        doc = make_mock_doc(["page1"], toc=[])
        assert s._get_chapters_from_outline(doc) is None

    def test_level1_entries_extracted(self):
        s = make_splitter()
        toc = [
            (1, "Introduction", 13),
            (1, "Methods", 40),
            (2, "Data Collection", 42),
        ]
        doc = make_mock_doc([""] * 100, toc=toc)
        result = s._get_chapters_from_outline(doc)
        assert len(result) == 2
        assert result[0]["title"] == "Introduction"
        assert result[0]["start_page"] == 12  # 1-indexed 13 → 0-indexed 12
        assert result[1]["title"] == "Methods"
        assert result[1]["start_page"] == 39

    def test_falls_back_to_level2_when_no_level1(self):
        s = make_splitter()
        toc = [
            (2, "Chapter 1", 5),
            (2, "Chapter 2", 20),
        ]
        doc = make_mock_doc([""] * 50, toc=toc)
        result = s._get_chapters_from_outline(doc)
        assert len(result) == 2
        assert result[0]["start_page"] == 4  # 0-indexed

    def test_role_classified_correctly(self):
        s = make_splitter()
        toc = [
            (1, "Preface", 3),
            (1, "Chapter 1: Background", 10),
            (1, "Appendix A", 90),
        ]
        doc = make_mock_doc([""] * 100, toc=toc)
        result = s._get_chapters_from_outline(doc)
        assert result[0]["role"] == "preface"
        assert result[1]["role"] == "chapter"
        assert result[2]["role"] == "appendix"


# ============================================================
# _apply_content_scan
# ============================================================

class TestApplyContentScan:
    def test_corrects_fixed_offset(self):
        """前付け11ページによる固定オフセットを補正できる。"""
        s = make_splitter()
        # 物理ページ 0-11: 前付け, 12: Chapter 1 本文
        pages = ["front matter"] * 12 + ["Chapter 1\nIntroduction text..."] + ["body"] * 50
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Chapter 1", "start_page": 1, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 12  # 0-indexed 物理ページ

    def test_corrects_variable_offset(self):
        """前付け分のオフセットが章ごとに異なる場合も補正できる。"""
        s = make_splitter()
        # Chapter 1: 論理1 → 物理12, Chapter 5: 論理105 → 物理110
        pages = ["front"] * 12 + ["chapter 1 content"] * 97 + ["chapter 5 content"] * 50
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Chapter 1", "start_page": 1, "role": "chapter"},
            {"title": "Chapter 5", "start_page": 105, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        # Chapter 1: ページ12 (0-indexed) に "chapter 1" が含まれる
        assert result[0]["start_page"] == 12
        # Chapter 5: ページ109 (0-indexed) に "chapter 5" が含まれる
        assert result[1]["start_page"] == 109

    def test_fallback_when_title_not_found(self):
        """スキャンで見つからない場合は論理ページ-1をフォールバックとして使用する。"""
        s = make_splitter()
        pages = ["unrelated content"] * 50
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Invisible Chapter", "start_page": 10, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 9  # 論理ページ 10 → 0-indexed 9

    def test_no_correction_when_already_correct(self):
        """オフセットがない PDF ではページ番号が変わらない。"""
        s = make_splitter()
        pages = ["Chapter 2 content starts here"] + ["body"] * 30
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Chapter 2", "start_page": 1, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 0  # 物理ページ 0

    def test_preserves_other_fields(self):
        """start_page 以外のフィールドが保持される。"""
        s = make_splitter()
        pages = ["Methods section"] * 20
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Methods", "start_page": 1, "role": "chapter", "extra": "value"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["title"] == "Methods"
        assert result[0]["role"] == "chapter"
        assert result[0]["extra"] == "value"

    def test_short_title_not_matched_in_body(self):
        """短い章タイトルがページ冒頭以外に出現しても誤ヒットしない。"""
        s = make_splitter()
        # pages[0]: 直前の章の本文（"methods" を含む長い文章）
        # pages[1]: 章見出しページ（冒頭に "Methods" がある）
        body_text = "In previous work, the methods used were diverse. " * 5  # > 250 chars
        heading_text = "Methods\n\nIn this chapter we describe..."
        pages = [body_text, heading_text]
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Methods", "start_page": 2, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        # body_text の "methods" は250文字以降に現れるため、冒頭窓にマッチしない
        # heading_text の "Methods" は冒頭にあるのでマッチする
        assert result[0]["start_page"] == 1  # pages[1] の 0-indexed

    def test_toc_page_skipped(self):
        """目次ページ（複数タイトルを列挙するページ）はスキップされる。"""
        s = make_splitter()
        toc_page = "Chapter 1: Introduction\nChapter 2: Methods\nChapter 3: Results\n"
        chapter1_page = "Introduction\n\nThis chapter introduces..."
        pages = [toc_page, chapter1_page]
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Introduction", "start_page": 1, "role": "chapter"},
            {"title": "Methods", "start_page": 2, "role": "chapter"},
            {"title": "Results", "start_page": 3, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        # pages[0] は目次ページなのでスキップ、pages[1] の "Introduction" が正しく選ばれる
        assert result[0]["start_page"] == 1  # pages[1]

    def test_multiline_heading_matched(self):
        """TOCで1行のタイトルが実ページで2行に分割されていても照合できる。
        例: relationspdf の "Introductions: The Compulsion of Relations"
        """
        s = make_splitter()
        # 実際のページでは見出しと副題が別行
        heading_page = "Introductions\nThe Compulsion of Relations\nRelations are ubiquitous..."
        pages = ["front"] * 14 + [heading_page]
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Introductions: The Compulsion of Relations", "start_page": 1, "role": "introduction"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        # 物理ページ14（0-indexed）にマッチする
        assert result[0]["start_page"] == 14

    def test_fallback_skip_when_ordering_violation(self):
        """フォールバックページが前章より前になる場合はエントリをスキップする。

        例: Chapter 11 が物理P242 で見つかり、続く Concluded が
        コンテンツスキャンで見つからず論理P233 → フォールバック物理P232 となる場合。
        242 > 232 なので Concluded はスキップし、Chapter 11 の範囲に吸収される。
        """
        s = make_splitter()
        # pages[241] に Chapter 11 見出し、Concluded 相当のタイトルは存在しない
        pages = ["body"] * 242 + ["Concluded body text"] * 50
        pages[241] = "The Ethnographic Effect II\n\nBody text..."
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Chapter 11: The Ethnographic Effect II", "start_page": 229, "role": "chapter"},
            {"title": "Writing societies, writing persons", "start_page": 233, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        # Chapter 11 は物理P241（0-indexed）で見つかる
        assert len(result) == 1
        assert result[0]["title"] == "Chapter 11: The Ethnographic Effect II"
        assert result[0]["start_page"] == 241

    def test_fallback_ok_when_no_ordering_violation(self):
        """フォールバックページが前章より後ならエントリを維持する。"""
        s = make_splitter()
        pages = ["body"] * 60
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Chapter 1", "start_page": 10, "role": "chapter"},
            {"title": "Invisible Chapter", "start_page": 40, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        # Chapter 1 が見つからず論理P10→フォールバックP9, Invisible も見つからず論理P40→フォールバックP39
        # 9 < 39 なので順序違反なし → 両方保持
        assert len(result) == 2
        assert result[1]["start_page"] == 39


# ============================================================
# _is_toc_page
# ============================================================

class TestIsTocPage:
    def test_toc_page_detected(self):
        s = make_splitter()
        page = "Chapter 1: Introduction\nChapter 2: Methods\nChapter 3: Results\n"
        titles = ["Introduction", "Methods", "Results", "Discussion"]
        assert s._is_toc_page(page, titles) is True

    def test_body_page_not_detected_as_toc(self):
        s = make_splitter()
        page = "In this study, we applied novel methods to understand the results."
        titles = ["Introduction", "Methods", "Results", "Discussion"]
        # "methods" と "results" の2つのみマッチ → 3未満なので TOC ではない
        assert s._is_toc_page(page, titles) is False

    def test_empty_titles_not_counted(self):
        s = make_splitter()
        page = "Some content here"
        titles = ["", "", "", ""]
        assert s._is_toc_page(page, titles) is False


# ============================================================
# split: ルート選択
# ============================================================

class TestSplitRouting:
    def test_outline_takes_priority_over_llm(self):
        """PDF outline が存在する場合、LLM を呼ばない。"""
        s = make_splitter()
        toc = [(1, "Chapter 1", 5), (1, "Chapter 2", 20)]
        doc = make_mock_doc([""] * 50, toc=toc)

        with patch("fitz.open", return_value=doc), \
             patch.object(s, "_extract_toc") as mock_extract, \
             patch.object(s, "_get_pdf_hash", return_value="hash"), \
             patch("fitz.Document") as mock_fitz_doc:
            doc.close = MagicMock()
            # 各章 PDF の生成をモック
            chapter_doc = MagicMock()
            chapter_doc.save = MagicMock()
            chapter_doc.close = MagicMock()
            with patch("fitz.open", side_effect=[doc, chapter_doc, chapter_doc]):
                try:
                    s.split("dummy.pdf", Path("/tmp/test_out"))
                except Exception:
                    pass

            mock_extract.assert_not_called()

    def test_llm_called_when_no_outline(self):
        """outline がない場合、LLM 抽出が呼ばれる。"""
        s = make_splitter()
        doc = make_mock_doc(["no toc"] * 10, toc=[])

        with patch("fitz.open", return_value=doc), \
             patch.object(s, "_extract_toc", return_value=[]) as mock_extract, \
             patch.object(s, "_get_pdf_hash", return_value="hash"):
            doc.close = MagicMock()
            result = s.split("dummy.pdf", Path("/tmp/test_out"))

            mock_extract.assert_called_once()
        # TOC がなければ全編単独章として返す
        assert len(result) == 1
        assert result[0]["path"] == "dummy.pdf"


# ============================================================
# _extract_toc: VLM フォールバック
# ============================================================

class TestExtractTocVlmFallback:
    def test_vlm_fallback_triggered_when_few_chapters(self):
        """テキスト抽出で章が 2 件以下のとき VLM フォールバックが呼ばれる。"""
        s = make_splitter()
        doc = make_mock_doc([""] * 5)

        text_toc = [{"title": "Acknowledgments", "start_page": 1, "role": "preface"}]
        vlm_toc = [
            {"title": "Prologue", "start_page": 1, "role": "introduction"},
            {"title": "Chapter 1", "start_page": 9, "role": "chapter"},
            {"title": "Chapter 2", "start_page": 39, "role": "chapter"},
        ]

        with patch.object(s, "_extract_toc_vlm", return_value=vlm_toc) as mock_vlm, \
             patch("core.llm_client.call_gemini", return_value=json.dumps({"toc": text_toc})):
            result = s._extract_toc(doc)

        mock_vlm.assert_called_once()
        assert result == vlm_toc

    def test_vlm_fallback_not_triggered_when_enough_chapters(self):
        """テキスト抽出で 3 件以上の章が取れた場合は VLM を呼ばない。"""
        s = make_splitter()
        doc = make_mock_doc([""] * 5)

        text_toc = [
            {"title": "Chapter 1", "start_page": 1, "role": "chapter"},
            {"title": "Chapter 2", "start_page": 20, "role": "chapter"},
            {"title": "Chapter 3", "start_page": 40, "role": "chapter"},
        ]

        with patch.object(s, "_extract_toc_vlm") as mock_vlm, \
             patch("core.llm_client.call_gemini", return_value=json.dumps({"toc": text_toc})):
            result = s._extract_toc(doc)

        mock_vlm.assert_not_called()
        assert result == text_toc

    def test_vlm_result_ignored_if_not_better(self):
        """VLM の結果がテキスト抽出より改善していない場合はテキスト結果を維持する。"""
        s = make_splitter()
        doc = make_mock_doc([""] * 5)

        text_toc = [
            {"title": "Prologue", "start_page": 1, "role": "introduction"},
            {"title": "Chapter 1", "start_page": 9, "role": "chapter"},
        ]
        vlm_toc = [{"title": "Prologue", "start_page": 1, "role": "introduction"}]

        with patch.object(s, "_extract_toc_vlm", return_value=vlm_toc), \
             patch("core.llm_client.call_gemini", return_value=json.dumps({"toc": text_toc})):
            result = s._extract_toc(doc)

        # VLM の結果（1件）より text_toc（2件）の方が多いので text_toc を維持
        assert result == text_toc
