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
        """前付け11ページによる固定オフセットを補正できる。

        [I-22 復元] タイトルは "Chapter 1" のみ（章番号のみで説明語なし）。
        _normalize_title が章番号プレフィックスを完全に剥がすため
        norm_title は空文字列になるが、_classify_match は norm_title が
        空でも title_lower による部分文字列一致にフォールバックするため
        （旧 _matches_heading() 相当・レビュー指摘で復元）、この裸タイトルの
        まま本来のオフセット補正機能を検証できる。
        """
        s = make_splitter()
        # 物理ページ 0-11: 前付け, 12: Chapter 1 本文
        pages = ["front matter"] * 12 + ["Chapter 1\n\nIntroduction text..."] + ["body"] * 50
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Chapter 1", "start_page": 1, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 12  # 0-indexed 物理ページ

    def test_corrects_variable_offset(self):
        """前付け分のオフセットが章ごとに異なる場合も補正できる。

        [I-22 復元] 本文全ページに "chapter 1 body text" のように章番号の
        裸文字列を撒いても、見出しページが探索窓内で最初に見つかる
        candidate であり、かつ本文頁とスコアが同点の場合は先に見つかった
        方（＝見出しページ）が勝つ（"score > best_score" の厳密不等号）ため、
        title_lower フォールバックが有効でも見出しページが正しく選ばれる。
        """
        s = make_splitter()
        # Chapter 1 見出しページ: 物理12, Chapter 5 見出しページ: 物理109
        pages = (
            ["front"] * 12
            + ["Chapter 1\n\nIntroduction text..."]
            + ["chapter 1 body text"] * 96
            + ["Chapter 5\n\nFurther text..."]
            + ["chapter 5 body text"] * 49
        )
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Chapter 1", "start_page": 1, "role": "chapter"},
            {"title": "Chapter 5", "start_page": 105, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        # Chapter 1: ページ12 (0-indexed) の見出しに一致する
        assert result[0]["start_page"] == 12
        # Chapter 5: ページ109 (0-indexed) の見出しに一致する
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

    def test_bare_numeral_toc_entry_lands_on_title_page_not_header(self):
        """PSE 実測相当: TOC が説明語を持たない裸の 'Chapter 1' を返す場合、
        title_lower フォールバックが復元されていないと本文照合が一切
        働かず論理ページへ無補正フォールバックしてしまう回帰を検証する。
        本文中には同じ 'Chapter 1' を含むランニングヘッダー頁もあるが、
        _classify_match の隣接判定と _score_candidate の採点により
        章扉頁（本タイトル頁）が正しく選ばれることを end-to-end で確認する。
        """
        s = make_splitter()
        header_page = "Chapter 1\n15\nbody text continues across this running header page..."
        title_page = "Chapter 1\nThe Ethnographic Effect\nBody begins here with the real chapter opening..."
        pages = ["front matter"] * 10 + [header_page, title_page] + ["body"] * 29
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Chapter 1", "start_page": 11, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        # ランニングヘッダー頁(10)ではなく章扉頁(11)に着地する
        assert result[0]["start_page"] == 11


# ============================================================
# _parse_page_number / _is_chapter_marker (I-22)
# ============================================================

class TestParsePageNumber:
    def test_plain_number(self):
        assert make_splitter()._parse_page_number("29") == 29

    def test_ocr_i_for_one(self):
        """Naven 実測: '3 I' は 31 の OCR 崩れ。"""
        assert make_splitter()._parse_page_number("3 I") == 31

    def test_ocr_r_for_one(self):
        """Naven 実測: 'r 72' は 172 の OCR 崩れ。"""
        assert make_splitter()._parse_page_number("r 72") == 172

    def test_roman_numeral_is_not_page_number(self):
        """章マーカー 'XIII' を頁番号と誤認してはならない。"""
        assert make_splitter()._parse_page_number("XIII") is None

    def test_bare_i_is_not_page_number(self):
        """数字を1文字も含まない文字列は頁番号ではない。"""
        assert make_splitter()._parse_page_number("I") is None

    def test_prose_is_not_page_number(self):
        assert make_splitter()._parse_page_number("In this distributed process") is None


class TestIsChapterMarker:
    def test_chapter_word(self):
        assert make_splitter()._is_chapter_marker("CHAPTER", None) is True

    def test_matching_arabic_numeral(self):
        """corfra 実測: 扉頁は '4' / 'Things'。"""
        assert make_splitter()._is_chapter_marker("4", "4") is True

    def test_matching_roman_numeral(self):
        """Naven 実測: 扉頁は 'CHAPTER' / 'XIII'。"""
        assert make_splitter()._is_chapter_marker("XIII", "XIII") is True

    def test_non_matching_number_is_not_marker(self):
        """Naven 実測: 本文頁の '29' は第III章の章マーカーではない。"""
        assert make_splitter()._is_chapter_marker("29", "III") is False


# ============================================================
# _classify_match (I-22)
# ============================================================

class TestClassifyMatch:
    def test_same_line_running_header_is_header(self):
        """corfra 実測: 'Knowing | 147' はランニングヘッダー。"""
        s = make_splitter()
        text = "Knowing | 147\nwoman drove it fast, with her sunglasses\nhand. As we snaked"
        assert s._classify_match(text, "knowing", None) == "header"

    def test_adjacent_line_running_header_is_header(self):
        """Naven 実測: タイトルと頁番号が別行のヘッダー。"""
        s = make_splitter()
        text = "The Concepts of Structure and Function\n29\nnot more so than is the use"
        assert s._classify_match(text, "the concepts of structure and function", "III") == "header"

    def test_chapter_number_adjacent_is_title(self):
        """corfra 実測: 扉頁 '4' / 'Things'。"""
        s = make_splitter()
        text = "4\nThings\nThe difference between ambiguity"
        assert s._classify_match(text, "things", "4") == "title"

    def test_multiline_title_page_is_title(self):
        """Naven 実測: 'CHAPTER' / 'XIII' / 2行に割れたタイトル。"""
        s = make_splitter()
        text = ("CHAPTER\nXIII\nEthological Contrast, Competition\n"
                "and Schismogenesis\nT\nHE foregoing description")
        kind = s._classify_match(
            text, "ethological contrast competition and schismogenesis", "XIII")
        assert kind == "title"

    def test_body_prose_prefix_is_not_title_page(self):
        """corfra 実測: 本文行 'things, and it is against them…' で誤検出しない。"""
        s = make_splitter()
        text = ("Place | 81\nIn this distributed process, people are helped by\n"
                "things, and it is against them that we measure\nthe terrain can")
        assert s._classify_match(text, "things", "4") != "title"

    def test_no_match_returns_none(self):
        s = make_splitter()
        assert s._classify_match("unrelated body text\nmore text", "knowing", None) is None

    def test_bare_numeral_title_page_fallback_is_title(self):
        """PSE 実測相当: 'Chapter 1' は norm_title が空文字列になるため、
        title_lower による部分文字列一致フォールバックが必要（レビュー指摘で復元）。
        章扉ページ（直後が本文題名・本文）では "title" となる。
        """
        s = make_splitter()
        text = "Chapter 1\nThe Ethnographic Effect\nBody text begins here..."
        assert s._classify_match(text, "", None, "chapter 1") == "title"

    def test_bare_numeral_title_fallback_running_header_is_header(self):
        """同フォールバックでも、隣接行が裸の頁番号ならランニングヘッダー
        と判定する（章扉と同じ隣接判定を通ることの検証）。
        """
        s = make_splitter()
        text = "Chapter 1\n42\nbody text continues here"
        assert s._classify_match(text, "", None, "chapter 1") == "header"

    def test_both_empty_returns_none(self):
        """norm_title・title_lower がともに空文字列なら、何にでも一致する
        退行的挙動を避けるため None を返す。"""
        s = make_splitter()
        assert s._classify_match("Chapter 1\nsome body text", "", None, "") is None


# ============================================================
# 候補スコアリング (I-22)
# ============================================================

class TestCandidateScoring:
    def test_title_page_outranks_earlier_body_match(self):
        """corfra 実測: idx89(本文) より idx93(扉) を選ぶ。"""
        s = make_splitter()
        body = ("Place | 81\nIn this distributed process, people are helped\n"
                "things, and it is against them that we measure\n" + "x " * 800)
        title_pg = "4\nThings\nThe difference between ambiguity and clarity"
        pages = ["filler"] * 80 + [body] + ["filler"] * 3 + [title_pg] + ["filler"] * 30
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [{"title": "4 Things", "start_page": 85}])
        assert result[0]["start_page"] == 84

    def test_joined_match_does_not_beat_standalone_title(self):
        """corfra 実測: '3 Place' が本文結合一致(idx73)へ退行しない。"""
        s = make_splitter()
        body = "Mystery | 65\nslowly snakes up the tall stone house\n" + "y " * 900
        title_pg = "3\nPlace\nThis then, may be a way out of the dichotomy"
        pages = ["filler"] * 64 + [body] + ["filler"] * 3 + [title_pg] + ["filler"] * 40
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [{"title": "3 Place", "start_page": 69}])
        assert result[0]["start_page"] == 68

    def test_monotonic_ordering_enforced(self):
        """後続章が前章より前に着地してはならない。"""
        s = make_splitter()
        pages = ["filler"] * 20 + ["1\nAlpha\nbody"] + ["filler"] * 20 + ["2\nBeta\nbody"] \
            + ["filler"] * 20
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [
            {"title": "1 Alpha", "start_page": 21},
            {"title": "2 Beta", "start_page": 42},
        ])
        assert result[0]["start_page"] < result[1]["start_page"]


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


# ============================================================
# _is_plausible_outline (I-24)
# ============================================================

class TestIsPlausibleOutline:
    def test_rejects_one_entry_per_page(self):
        """PSEpdf.pdf 実測: 175頁に175件の f1…f175 は章目次ではない。"""
        s = make_splitter()
        entries = [(f"f{i+1}", i + 1) for i in range(175)]
        assert s._is_plausible_outline(entries, total_pages=175) is False

    def test_rejects_sequential_page_labels(self):
        """比が閾値内でも、連番ラベル形式なら棄却する。"""
        s = make_splitter()
        entries = [(f"f{i*20+1}", i * 20 + 1) for i in range(9)]
        assert s._is_plausible_outline(entries, total_pages=300) is False

    def test_accepts_normal_chapter_outline(self):
        s = make_splitter()
        entries = [
            ("Preface", 1), ("1. Introduction", 12), ("2. Methods", 45),
            ("3. Results", 88), ("4. Discussion", 130), ("Appendix", 170),
        ]
        assert s._is_plausible_outline(entries, total_pages=200) is True

    def test_rejects_too_few_pages_per_chapter(self):
        s = make_splitter()
        entries = [(f"Chapter {i}", i * 2 + 1) for i in range(20)]
        assert s._is_plausible_outline(entries, total_pages=45) is False

    def test_empty_entries_rejected(self):
        s = make_splitter()
        assert s._is_plausible_outline([], total_pages=100) is False


class TestOutlineGuardIntegration:
    def test_garbage_outline_returns_none(self):
        """棄却された outline は None を返し Route 3 へ落とす。"""
        s = make_splitter()
        toc = [(1, f"f{i+1}", i + 1) for i in range(175)]
        doc = make_mock_doc(["text"] * 175, toc=toc)
        assert s._get_chapters_from_outline(doc) is None

    def test_valid_outline_still_works(self):
        s = make_splitter()
        toc = [(1, "Preface", 1), (1, "1. Introduction", 20), (1, "2. Methods", 60)]
        doc = make_mock_doc(["text"] * 100, toc=toc)
        result = s._get_chapters_from_outline(doc)
        assert result is not None
        assert len(result) == 3
        assert result[0]["start_page"] == 0


# ============================================================
# _find_toc_pages (I-25)
# ============================================================

class TestFindTocPages:
    def test_finds_toc_beyond_fixed_window(self):
        """Naven.pdf 実測: 目次が idx 16 にあり従来の15頁窓の外。"""
        s = make_splitter()
        texts = ["front matter"] * 16 + ["TABLE\nOF CONTENTS\nChap. I. METHODS"] \
            + ["contents cont."] * 8 + ["body"] * 100
        doc = make_mock_doc(texts)
        pages = s._find_toc_pages(doc)
        assert 16 in pages

    def test_finds_toc_near_front(self):
        """corfra 実測: 目次は idx 3。"""
        s = make_splitter()
        texts = ["cover", "title", "copyright", "Contents\n1 Arbitrary Location\n9"] \
            + ["body"] * 50
        doc = make_mock_doc(texts)
        assert 3 in s._find_toc_pages(doc)

    def test_falls_back_to_leading_pages_when_absent(self):
        """目次が見つからない場合は先頭から TOC_FALLBACK_PAGES 頁を返す（I-26）。"""
        s = make_splitter()
        doc = make_mock_doc(["body text"] * 50)
        pages = s._find_toc_pages(doc)
        assert pages == list(range(s.TOC_FALLBACK_PAGES))

    def test_includes_pages_following_toc(self):
        """目次は複数頁にまたがるため後続頁も含める（上限 TOC_SAMPLE_PAGES 頁を厳密検証）。"""
        s = make_splitter()
        texts = ["x"] * 5 + ["Contents\nChapter 1"] + ["y"] * 40
        pages = s._find_toc_pages(make_mock_doc(texts))
        assert pages == list(range(5, 5 + s.TOC_SAMPLE_PAGES))

    def test_does_not_exceed_document_length(self):
        s = make_splitter()
        doc = make_mock_doc(["Contents\nChapter 1", "b"])
        assert all(0 <= p < 2 for p in s._find_toc_pages(doc))

    def test_search_boundary_beyond_range_uses_fallback(self):
        """TOC_SEARCH_PAGES(30) を超えた位置の見出しは検出されず、
        フォールバック窓（先頭 TOC_FALLBACK_PAGES 頁）が返る。"""
        s = make_splitter()
        # Contents ヘッダを探索範囲(idx 0-29)の外、idx 35 にのみ配置
        page_texts = ["body"] * 35 + ["Contents\nChapter 1"] + ["body"] * 10
        doc = make_mock_doc(page_texts)
        pages = s._find_toc_pages(doc)
        assert pages == list(range(s.TOC_FALLBACK_PAGES))

    def test_search_boundary_at_last_in_range_index_is_found(self):
        """探索範囲の最終 index (29 = TOC_SEARCH_PAGES - 1) の見出しは検出される。"""
        s = make_splitter()
        page_texts = ["body"] * 29 + ["Contents\nChapter 1"] + ["body"] * 20
        doc = make_mock_doc(page_texts)
        pages = s._find_toc_pages(doc)
        assert 29 in pages
        assert pages == list(range(29, 29 + s.TOC_SAMPLE_PAGES))
