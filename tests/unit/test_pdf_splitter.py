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
import fitz
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from core.engine.p1_ingest.pdf_splitter import PDFSplitter


@pytest.fixture(autouse=True)
def _no_real_llm_calls():
    """層3（BoundaryAdjudicator）は要審査の章について実際に Gemini を呼ぶ。
    本ファイルのテストは api_key="test_key" という無効なキーを使うため、
    モックしない場合は本物の call_gemini がリトライ付きで実ネットワーク
    呼び出しを試み、テストが極端に遅く・不安定になる。ここで一律に
    モックし、LLM は常に判断不能（page: null 相当）として扱う。
    個々のテストで層3の挙動そのものを検証したい場合は、このモックの
    戻り値をテスト内でさらに上書きする。
    """
    with patch(
        "core.engine.p1_ingest.boundary_adjudicator.call_gemini",
        return_value='{"page": null}',
    ):
        yield


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
        空でも title_lower による前方一致（行頭一致＋非英数字境界）に
        フォールバックするため、この裸タイトルのまま本来のオフセット
        補正機能を検証できる。
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

    def test_ordering_violation_places_entry_after_previous_instead_of_dropping(self):
        """フォールバックページが前章より前になる場合でも、章を欠落させず
        前章の直後に配置する (I-22 Task4)。

        例: Chapter 11 が物理P242 で見つかり、続く Concluded が
        コンテンツスキャンで見つからず論理P233 → フォールバック物理P232 となる場合。
        232 <= 241 のため、以前は Concluded を丸ごとスキップして結果から
        消していた。しかし「章が結果から silently 消える」よりも
        「順序上ずれた位置に配置される」方が望ましいため、前章の直後
        （物理P242）に配置し、章としては必ず出力に残す。
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
        # このテストの対象は層1（本文照合ループ自体のフォールバック配置）で
        # あり、層2・層3（要審査の章のオフセット補間・LLM 裁定）が対象では
        # ない。ここでは唯一の確定隣接章が Chapter 11 のみのため、層3は
        # 補間オフセットに基づき Writing societies を再配置しうるが、それは
        # 本テストが検証したい「順序違反時に前章直後へ配置する」という
        # 層1の契約とは別の関心事なので、層2・層3は無効化して測定する。
        with patch.object(s, "_adjudicate_boundaries", side_effect=lambda d, results: results):
            result = s._apply_content_scan(doc, llm_toc)

        # Chapter 11 は物理P241（0-indexed）で見つかる
        assert len(result) == 2
        assert result[0]["title"] == "Chapter 11: The Ethnographic Effect II"
        assert result[0]["start_page"] == 241
        # Concluded 相当の章は消えず、前章の直後（物理P242, 0-indexed）に配置される
        assert result[1]["title"] == "Writing societies, writing persons"
        assert result[1]["start_page"] == 242

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

    def test_plain_fallback_clamped_to_document_bounds(self):
        """C2: TOC の論理ページが文書の総頁数を大きく超える場合、
        フォールバックは文書末尾を超えてはならない。

        PSEpdf.pdf 実測相当: 175頁の文書に対し TOC 論理ページが500まで
        ある場合、補正なしフォールバック(499)をそのまま採用すると
        insert_pdf() が範囲外の from_page を渡され、PyMuPDF が黙って
        末尾頁にクランプして無関係な頁（索引頁等）を複製してしまう
        (I-27)。クランプ後は必ず [0, total_pages-1] に収まる。
        """
        s = make_splitter()
        pages = ["unrelated content"] * 10  # total_pages = 10
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Ghost Chapter", "start_page": 500, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert len(result) == 1
        assert 0 <= result[0]["start_page"] < len(pages)
        assert result[0]["start_page"] == 9  # min(499, total_pages-1)

    def test_clamped_fallback_that_violates_monotonicity_is_skipped_not_duplicated(self):
        """C2: クランプ後のフォールバックが前章と同じ（または前章より前の）
        物理ページになり、かつ配置可能な空きページも残っていない場合は、
        rescued_fallback 分岐と同じパターンで章をスキップする（欠落を
        受け入れる方が、無関係な頁を章として複製するより安全）。
        """
        s = make_splitter()
        # 物理ページ9（0-indexed, 文書最終頁）に一致する本文を置く。
        pages = ["filler"] * 9 + ["Ghost Chapter One\n\nBody text begins here for real content."]
        doc = make_mock_doc(pages)  # total_pages = 10

        llm_toc = [
            {"title": "Ghost Chapter One", "start_page": 10, "role": "chapter"},
            # 本文に存在せず、クランプ後のフォールバックが最終頁(9)と衝突する
            {"title": "Ghost Chapter Two", "start_page": 500, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)

        assert len(result) == 1
        assert result[0]["title"] == "Ghost Chapter One"
        assert result[0]["start_page"] == 9

    def test_plain_fallback_branch_advances_watermark_for_monotonicity(self):
        """Finding 3: 順序違反のない通常フォールバック分岐（本文未検出かつ
        fallback > last_found_phys）でも last_found_phys を更新しなければ
        ならない。更新しないと、後続章の探索窓は今回のフォールバック位置を
        知らないまま前章の位置しか避けないため、フォールバック位置より
        手前の頁に誤って一致し、結果リストが非単調になりうる。

        Alpha は phys10 で本文一致（last_found_phys=10）。Beta は本文中
        どこにも現れず論理P41→フォールバックP40（40>10 のため順序違反
        分岐は通らない）。ここで last_found_phys を更新しないと、Gamma の
        探索窓([16,70])は phys20（last_found_phys=10より後）の 'Gamma' 見出し
        に一致してしまい、結果が [10, 40, 20] という非単調なリストになる
        （split() では Beta の end_page が 20-1=19 となり start_page(40) >
        end_page(19) で Beta が消失する）。修正後は last_found_phys=40 と
        なり Gamma は phys20 をスキップして順序違反分岐へ回り、単調性が
        保たれる。
        """
        s = make_splitter()
        pages = ["filler"] * 90
        pages[10] = "Alpha\nBody text of chapter alpha begins here for real content."
        pages[20] = "Gamma\nBody text of chapter gamma begins here for real content."
        doc = make_mock_doc(pages)

        llm_toc = [
            {"title": "Alpha", "start_page": 11, "role": "chapter"},
            {"title": "Beta", "start_page": 41, "role": "chapter"},
            {"title": "Gamma", "start_page": 21, "role": "chapter"},
        ]
        # このテストの対象は層1（本文照合ループの watermark 更新）であり、
        # 層2・層3が対象ではない。TOC の論理ページが本来の順序に反する
        # （Gamma の論理ページ21がBetaの41より前）人工的な入力のため、
        # 層3の補間オフセットに掛けると Beta と Gamma がともに唯一の確定
        # 隣接章 Alpha を基準に補正され、本テストが検証したい watermark
        # 由来の単調性とは無関係に非単調な結果になりうる。層2・層3を
        # 無効化して層1の契約のみを測定する。
        with patch.object(s, "_adjudicate_boundaries", side_effect=lambda d, results: results):
            result = s._apply_content_scan(doc, llm_toc)

        starts = [e["start_page"] for e in result]
        assert starts == sorted(starts), f"non-monotonic result: {starts}"
        assert starts == [10, 40, 41]

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

    def test_bare_numeral_sparse_prose_does_not_outrank_title_page(self):
        """PSE 実測 (Finding 1): PSEpdf.pdf の TOC エントリ 'Chapter 10' で、
        疎な写真キャプション頁（本文中に 'Chapter 10' への言及を含む）が
        真の章扉頁より先に選ばれてしまう回帰を検証する。修正前は部分文字列
        一致でキャプション頁が "title" 判定され、疎密加点(+20)で章扉頁(0点)
        を上回っていた。
        """
        s = make_splitter()
        title_page = "Chapter 10\nPuzzles of Scale\n" + ("x " * 2000)
        caption_page = "Photo caption.\nThey are the subject of Chapter 10.\nMore caption."
        pages = (
            ["front"] * 108
            + [title_page]
            + ["filler"] * 10
            + [caption_page]
            + ["filler"] * 20
        )
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Chapter 10", "start_page": 109, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 108  # title_page（0-indexed）


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
        title_lower による前方一致フォールバックが必要（行頭一致＋非英数字境界）。
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

    def test_bare_numeral_fallback_rejects_prose_mention(self):
        """PSE 実測 (Finding 1): 'They are the subject of Chapter 10.' のような
        本文中の言及は、行頭一致でないため裸番号フォールバックで拾ってはならない。
        """
        s = make_splitter()
        text = "Photo caption.\nThey are the subject of Chapter 10.\nMore caption text."
        assert s._classify_match(text, "", None, "chapter 10") is None

    def test_bare_numeral_fallback_matches_line_starting_with_title(self):
        """行頭が title_lower と完全一致すれば通常の隣接判定にかけて良い。"""
        s = make_splitter()
        text = "Chapter 10\nPuzzles of Scale\nBody begins here..."
        assert s._classify_match(text, "", None, "chapter 10") == "title"

    def test_bare_numeral_fallback_prefix_collision_guard(self):
        """Finding 1: 'chapter 1' を探索中に 'Chapter 10' へ桁違いで
        前方一致してしまう衝突を、直後の非英数字境界チェックで防ぐ。
        """
        s = make_splitter()
        text = "Chapter 10\nPuzzles of Scale\nBody begins here..."
        assert s._classify_match(text, "", None, "chapter 1") is None

    # --- 裸の数字だけの TOC タイトル（'1' 等、説明語を伴わない章番号のみ）---
    # norm_title は '1' のまま非空になり（Chapter 等の説明語が無いため
    # _normalize_title が末尾空白を要求する2番目の正規表現にマッチしない）、
    # title_lower フォールバックとは別の経路を通る。

    def test_bare_numeral_norm_title_stray_digit_in_prose_is_not_title(self):
        """本文中に孤立した '1' の行（脚注番号・リスト項目等）があっても、
        隣接行に章マーカーも頁番号も無ければ章扉と断定してはならない。
        """
        s = make_splitter()
        text = (
            "Some ordinary preceding line of prose here.\n"
            "1\n"
            "And the discussion continues normally without any chapter marker nearby."
        )
        assert s._classify_match(text, "1", None, "1") is None

    def test_bare_numeral_norm_title_with_chapter_marker_neighbor_is_title(self):
        """隣接行が明示的な章マーカー（'CHAPTER'）なら、裸数字一致でも
        章扉と判定してよい（確証があるケース）。"""
        s = make_splitter()
        text = "CHAPTER\n1\nBody text begins here..."
        assert s._classify_match(text, "1", None, "1") == "title"

    def test_bare_numeral_norm_title_with_page_number_neighbor_is_header(self):
        """隣接行が裸の頁番号ならランニングヘッダーと判定する。"""
        s = make_splitter()
        text = "1\n88\nbody text continues here"
        assert s._classify_match(text, "1", None, "1") == "header"


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

    def test_all_header_window_earliest_wins_not_sparsest(self):
        """Finding 2: 探索窓内の全候補が running header の場合、疎密加点は
        header に適用してはならない。適用すると最も疎な header が勝ち、
        任意性の高いページに着地してしまう（corfra '7 Knowing' 実測で
        index169 という14頁の回帰を引き起こした）。修正後は疎密に関わらず
        最も早い header が決定的に選ばれる。
        """
        s = make_splitter()
        # 3つとも "Knowing | <page>" のランニングヘッダーで kind は "header" 一択。
        # header_b が最も疎（短い）だが、最も早い header_a (idx20) が勝つべき。
        # header_a / header_c は SCORE_SPARSE_PAGE_CHARS(1500) を確実に超える
        # 長さにする。閾値未満だと修正前のコードでも3件が同点になり、
        # 早い者勝ちで偶然正解してしまい、このテストが回帰を検出できない。
        header_a = "Knowing | 147\n" + ("body text continues here. " * 60)
        header_b = "Knowing | 148\nshort."
        header_c = "Knowing | 149\n" + ("body text continues here. " * 60)
        pages = (
            ["filler"] * 20
            + [header_a]
            + ["filler"] * 19
            + [header_b]
            + ["filler"] * 19
            + [header_c]
            + ["filler"] * 10
        )
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Knowing", "start_page": 21, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 20  # header_a（最も早い header）


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
# split: 範囲外 start_page の防御 (C2 defence in depth)
# ============================================================

class TestSplitOutOfRangeGuard:
    def test_split_skips_chapter_with_out_of_range_start_page(self, tmp_path):
        """C2 defence in depth: start_page が文書範囲外なら split() が
        insert_pdf() を呼ぶ前に確実にスキップする。

        _apply_content_scan() のクランプ (C2) は Route 3 専用であり、
        Route 1（ローカル TOC）・Route 2（PDF outline）はこの補正を経由
        しない。それらの経路が範囲外の start_page を返した場合でも
        split() 自身がガードしなければ、insert_pdf() に渡った
        PyMuPDF が黙って末尾頁にクランプし、無関係な頁を「章」として
        出力してしまう（実測: PSEpdf.pdf で索引頁が複数章に複製された）。
        実際の fitz.Document（5頁）を使い、insert_pdf の実挙動込みで
        検証する。
        """
        src = tmp_path / "tiny.pdf"
        real_doc = fitz.open()
        for _ in range(5):
            real_doc.new_page()
        real_doc.save(str(src))
        real_doc.close()

        s = make_splitter()
        # Chapter 2 の end_page は「末尾章なら len(doc)-1」ではなく
        # 次エントリ(Chapter 3)の start_page-1 から計算されるため、
        # start_page(10) 自体が文書外でも既存の
        # "start_page > end_page" 判定だけでは弾けない
        # （10 <= end_page(14) のため通過してしまう）。
        # ここで新設した "start_page >= len(doc)" 判定が唯一の防波堤になる。
        bad_toc = [
            {"title": "Chapter 1", "start_page": 0, "role": "chapter"},
            # 文書は5頁 (idx 0-4) しかないのに start_page=10 は範囲外
            {"title": "Chapter 2: Out of Range", "start_page": 10, "role": "chapter"},
            {"title": "Chapter 3", "start_page": 15, "role": "chapter"},
        ]
        out_dir = tmp_path / "out"
        with patch.object(s, "_get_chapters_from_outline", return_value=bad_toc):
            results = s.split(str(src), out_dir)

        titles = [r["title"] for r in results]
        assert "Chapter 2: Out of Range" not in titles
        assert any(t == "Chapter 1" for t in titles)
        # 範囲外の章に対応するファイルが生成されていないこと
        produced_files = list(out_dir.glob("*.pdf")) if out_dir.exists() else []
        assert all("Out_of_Range" not in f.name for f in produced_files)


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


# ============================================================
# 局所オフセット救済 (I-22 / Knowing)
# ============================================================

class TestLocalOffsetRescue:
    def test_derives_true_start_from_running_header(self):
        """corfra 実測: idx155 の 'Knowing | 147' から論理145 → idx153。"""
        s = make_splitter()
        pages = ["filler"] * 160
        pages[155] = "Knowing | 147\nwoman drove it fast, with her sunglasses"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 155, 145, "knowing") == 153

    def test_reads_page_number_from_adjacent_line(self):
        """Naven 形式: タイトルと頁番号が別行。"""
        s = make_splitter()
        pages = ["filler"] * 80
        pages[60] = "The Concepts of Structure and Function\n31\nbody text"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(
            doc, 60, 23, "the concepts of structure and function") == 52

    def test_returns_none_when_no_page_number(self):
        s = make_splitter()
        pages = ["filler"] * 50
        pages[30] = "Knowing\nbody text without any page number"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 30, 20, "knowing") is None

    def test_returns_none_when_out_of_range(self):
        s = make_splitter()
        pages = ["filler"] * 20
        pages[10] = "Knowing | 900\nbody"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 10, 5, "knowing") is None

    def test_knowing_end_to_end(self):
        """扉頁にタイトル文字が無くても正しい開始位置を得る。"""
        s = make_splitter()
        pages = ["filler"] * 200
        pages[153] = "The tree imposes the verb to be\nCollisions and Connections\nThis story starts"
        pages[155] = "Knowing | 147\nwoman drove it fast"
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [{"title": "7 Knowing", "start_page": 145}])
        assert result[0]["start_page"] == 153

    def test_misfire_rejected_when_rescue_lands_outside_search_window(self):
        """corfra 実測 (Task4 実データ検証で発見): TOC タイトルが章番号を
        欠いて渡された場合（例: 'Things' のみ、'4 Things' でない）、真の
        章扉頁 '4\\nThings\\n...' 自体が隣接する裸の章番号 '4' を印刷頁番号と
        誤認して "header" 判定されうる。この場合 _rescue_by_local_offset は
        章番号 '4' を印刷頁として扱い、探索窓から大きく外れた無関係な頁を
        導出してしまう。窓外の救済結果は採用せず、誤爆前の best_phys
        （＝この場合は元々正しい章扉頁）を維持しなければならない。
        """
        s = make_splitter()
        pages = ["front"] * 200
        # 実ページ93(0-indexed): 真の章扉。直前に裸の章番号 '4' があり、
        # 隣接判定で頁番号と誤認され kind="header" になる。
        pages[93] = "4\nThings\nThe difference between ambiguity and clarity is not enormous."
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Things", "start_page": 85, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        # 誤った局所オフセット救済（探索窓外）は採用されず、元々見つかっていた
        # 正しい章扉頁(93, 0-indexed)がそのまま使われる。
        assert result[0]["start_page"] == 93

    def test_previous_line_bare_numeral_rejected_by_plausibility(self):
        """Finding 1 実測相当・plausibility 版: 章番号が単独行で先行し本題が
        続くレイアウト（'3\\nPlace\\nThe difference between…'）では、一致行の
        直前行は単なる裸の章番号であって印刷頁番号ではない。直前行の読み取り
        自体は（verso 対応のため）復元済みで '3' は候補として読まれるが、
        論理P25との差22が RESCUE_PAGE_NUMBER_TOLERANCE(10) を超えるため
        plausibility 検査で却下される（位置ではなく数値の妥当性で却下される
        点が Finding 1 時点との違い）。他に候補がないため rescue は None を
        返し、章扉頁30がそのまま維持される。
        """
        s = make_splitter()
        pages = ["front"] * 80
        pages[30] = "3\nPlace\nThe difference between ambiguity and clarity is not enormous."
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "Place", "start_page": 25, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 30

    def test_rescue_only_fires_for_header_not_title(self):
        """Finding 2: 局所オフセット救済は best_kind == 'header' の場合にのみ
        発動しなければならない。'title' と判定された勝者候補に対しても
        発動してしまうと、その扉頁自体に含まれる無関係な数字（キャプション
        番号等）を印刷頁番号と誤読し、正しく見つかった扉頁を無関係な頁へ
        動かしてしまいうる。

        頁は '4\\nThings\\n90\\nThe difference between…' というレイアウト
        で、隣接行 '4' が章番号と一致するため _classify_match は "title" を
        返す（救済ゲートが正しく効いていれば発動しない）。しかし救済ゲート
        なしで _rescue_by_local_offset を実行すると、一致行 'Things' の
        次行 '90' を印刷頁番号と誤読し、predicted = 85 + (93 - 90) = 88 を
        算出してしまう（88 は探索窓 [80, 134] 内に収まるためガードも
        すり抜ける）。best_kind == 'header' ゲートを外すとこのテストは
        88 を返し失敗する。
        """
        s = make_splitter()
        pages = ["front"] * 150
        pages[93] = (
            "4\nThings\n90\nThe difference between ambiguity and clarity is not "
            "enormous, but subtle and layered throughout everyday interpretation."
        )
        doc = make_mock_doc(pages)

        llm_toc = [{"title": "4 Things", "start_page": 85, "role": "chapter"}]
        result = s._apply_content_scan(doc, llm_toc)

        assert result[0]["start_page"] == 93

    def test_verso_layout_page_number_before_title(self):
        """Naven 実測相当: verso頁は頁番号がタイトルより前に来るレイアウト
        （'24\\nThe Concepts of Structure and Function\\n...'）。直前行の
        読み取りを復元しないとこの verso 形式は救済できない（printed は
        常に None のまま）。
        """
        s = make_splitter()
        pages = ["filler"] * 60
        pages[53] = "24\nThe Concepts of Structure and Function\nbody text"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(
            doc, 53, 23, "the concepts of structure and function") == 52

    def test_plausibility_boundary_accepts_within_tolerance(self):
        """RESCUE_PAGE_NUMBER_TOLERANCE ちょうどの差は妥当とみなし採用する。
        ハードコードした10ではなく属性から閾値を取得して境界を作る。
        """
        s = make_splitter()
        tol = s.RESCUE_PAGE_NUMBER_TOLERANCE
        logical_page = 50
        printed_ok = logical_page - tol  # 差 == tol → 妥当
        pages = ["filler"] * 100
        pages[80] = f"Knowing | {printed_ok}\nbody"
        doc = make_mock_doc(pages)
        expected = logical_page + (80 - printed_ok)
        assert s._rescue_by_local_offset(doc, 80, logical_page, "knowing") == expected

    def test_plausibility_boundary_rejects_just_outside_tolerance(self):
        """RESCUE_PAGE_NUMBER_TOLERANCE を1超える差は却下し None を返す。
        ハードコードした10ではなく属性から閾値を取得して境界を作る。
        """
        s = make_splitter()
        tol = s.RESCUE_PAGE_NUMBER_TOLERANCE
        logical_page = 50
        printed_bad = logical_page - tol - 1  # 差 == tol + 1 → 却下
        pages = ["filler"] * 100
        pages[80] = f"Knowing | {printed_bad}\nbody"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 80, logical_page, "knowing") is None

    def test_tries_next_position_when_same_line_number_implausible(self):
        """同一行の数字が plausibility 検査で却下されても、そこで諦めず
        次の行の数字を試す（候補は位置の順に逐次試行される）。同一行の
        999 は論理P45との差が大きすぎて却下されるが、次行の40は妥当。
        """
        s = make_splitter()
        pages = ["filler"] * 100
        pages[75] = "Knowing | 999\n40\nbody text"
        doc = make_mock_doc(pages)
        expected = 45 + (75 - 40)
        assert s._rescue_by_local_offset(doc, 75, 45, "knowing") == expected


# ============================================================
# 層1（TOC 検算）の Route 3 への配線
# ============================================================

class TestTocVerifierWiring:
    """層1（TOC 検算）が Route 3 にのみ掛かることを確認する。"""

    def test_route3_calls_verify_and_fix_toc(self, tmp_path):
        s = make_splitter()
        s.cache = {"dummyhash_toc": [{"title": "A", "start_page": 1, "role": "chapter"}]}
        doc = make_mock_doc(["1\nA\n", "2\n本文\n"])

        with patch("fitz.open", return_value=doc), \
             patch.object(s, "_get_pdf_hash", return_value="dummyhash"), \
             patch.object(s, "_get_chapters_from_outline", return_value=None), \
             patch.object(s, "_apply_content_scan", return_value=[]), \
             patch("core.engine.p1_ingest.toc_verifier.verify_and_fix_toc") as mock_verify:
            mock_verify.return_value = [{"title": "A", "start_page": 1, "role": "chapter"}]
            s.split("dummy.pdf", tmp_path)

        assert mock_verify.called, "Route 3 では層1が呼ばれなければならない"

    def test_route2_does_not_call_verify_and_fix_toc(self, tmp_path):
        """ネイティブ outline は物理頁を直接持つため検算を掛けてはならない。"""
        s = make_splitter()
        doc = make_mock_doc(["A\n", "本文\n"])
        outline_toc = [{"title": "A", "start_page": 0, "role": "chapter"}]

        with patch("fitz.open", return_value=doc), \
             patch.object(s, "_get_chapters_from_outline", return_value=outline_toc), \
             patch.object(s, "_get_pdf_hash", return_value="dummyhash"), \
             patch("core.engine.p1_ingest.toc_verifier.verify_and_fix_toc") as mock_verify:
            s.split("dummy.pdf", tmp_path)

        assert not mock_verify.called, "Route 2 で層1が呼ばれてはならない"


# ============================================================
# 探索窓の不変性（既存入力に対する振る舞い不変の担保）
# ============================================================

class TestSearchWindowInvariance:
    """start_page が数値の場合、探索窓は従来の式と完全に同一でなければならない。

    層2・層3（_adjudicate_boundaries）は要審査の章についてさらに doc の
    ページを読むため、モックしないと doc.__getitem__ の呼び出し記録が
    汚染され、このテストが検証したい「本文照合ループの探索窓」を正しく
    測れなくなる。ここでは _adjudicate_boundaries を無効化（results を
    そのまま返す）してから記録する。
    """

    def test_numeric_logical_page_uses_original_window(self):
        s = make_splitter()
        # 論理頁50 → 従来の窓は idx45..99（logical-5 … logical+49、総頁数でクリップ）
        doc = make_mock_doc(["x\n"] * 200)
        scanned = []

        original_getitem = doc.__getitem__

        def record(idx):
            scanned.append(idx)
            return original_getitem(idx)

        doc.__getitem__ = MagicMock(side_effect=record)
        with patch.object(s, "_adjudicate_boundaries", side_effect=lambda d, results: results):
            s._apply_content_scan(doc, [{"title": "NotPresent", "start_page": 50, "role": "chapter"}])

        assert min(scanned) == 45, "探索開始が logical-5 でない"
        assert max(scanned) == 99, "探索終了が logical+49 でない"

    def test_none_logical_page_scans_from_previous_chapter(self):
        s = make_splitter()
        doc = make_mock_doc(["x\n"] * 20)
        scanned = []
        original_getitem = doc.__getitem__

        def record(idx):
            scanned.append(idx)
            return original_getitem(idx)

        doc.__getitem__ = MagicMock(side_effect=record)
        with patch.object(s, "_adjudicate_boundaries", side_effect=lambda d, results: results):
            s._apply_content_scan(doc, [{"title": "NotPresent", "start_page": None, "role": "chapter"}])

        assert min(scanned) == 0
        assert max(scanned) == 19


# ============================================================
# 層2・層3の配線（Task 8）
# ============================================================

class TestAdjudicatorWiring:
    def test_content_scan_records_matched_flag(self):
        """フォールバックした章と照合成立した章が区別できること。"""
        s = make_splitter()
        doc = make_mock_doc([
            "x\n", "x\n",
            "Alpha\n本文\n",
            "x\n", "x\n", "x\n",
        ])
        llm_toc = [
            {"title": "Alpha", "start_page": 3, "role": "chapter"},
            {"title": "NotPresent", "start_page": 5, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)
        assert result[0].get("matched") is True
        assert result[1].get("matched") is False
