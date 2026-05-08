"""
TextStructureExtractor のユニットテスト

テスト対象:
  - _normalize: 見出し正規化（番号接頭辞・記号除去・小文字化）
  - _find_heading_at_line_boundary: 行境界での見出し検出ロジック
  - assign_roles: チャンクへの h2 ロール付与と分割

直近 commit 949b77d で「行境界＋Title Case 確認」が修正された箇所を中心に回帰テストを置く。
"""

import pytest
from core.engine.p1_ingest.text_structure_extractor import TextStructureExtractor
from core.models import RawChunk


def _extractor() -> TextStructureExtractor:
    return TextStructureExtractor(api_key="dummy")


def _chunk(id: str, text: str, seq: float = 0.0) -> RawChunk:
    return RawChunk(id=id, text=text, seq_index=seq)


# ============================================================
# _normalize
# ============================================================

class TestNormalize:
    def test_strips_numeric_prefix(self):
        assert TextStructureExtractor._normalize("1. Introduction") == "introduction"

    def test_strips_decimal_prefix(self):
        assert TextStructureExtractor._normalize("2.1 Methods") == "methods"

    def test_plain_text(self):
        assert TextStructureExtractor._normalize("Abstract") == "abstract"

    def test_strips_symbols(self):
        # ハイフン・コロン等の記号はスペースに置換される
        result = TextStructureExtractor._normalize("Results: key findings")
        assert "results" in result
        assert "key findings" in result

    def test_trims_whitespace(self):
        assert TextStructureExtractor._normalize("  Introduction  ") == "introduction"


# ============================================================
# _find_heading_at_line_boundary
# ============================================================

class TestFindHeadingAtLineBoundary:
    def setup_method(self):
        self.ext = _extractor()

    def _pairs(self, headings):
        return [(self.ext._normalize(h), h) for h in headings]

    def test_heading_at_start_with_body_on_next_line(self):
        """見出しが第1行、本文が次行のパターン。"""
        result = self.ext._find_heading_at_line_boundary(
            "Introduction\nThis is the body.", self._pairs(["Introduction"])
        )
        assert result is not None
        before, head, after = result
        assert before == ""
        assert head == "Introduction"
        assert "This is the body" in after

    def test_heading_in_middle_with_before_text(self):
        """見出しの前に本文がある場合、before に収集されること。"""
        result = self.ext._find_heading_at_line_boundary(
            "Some pre text.\nMethods\nBody here.", self._pairs(["Methods"])
        )
        assert result is not None
        before, head, after = result
        assert "Some pre text" in before
        assert head == "Methods"
        assert "Body here" in after

    def test_no_heading_returns_none(self):
        """見出しが一切含まれないチャンクは None を返す。"""
        result = self.ext._find_heading_at_line_boundary(
            "Regular paragraph text.", self._pairs(["Introduction"])
        )
        assert result is None

    def test_heading_only_line_no_body(self):
        """見出しのみの行（後続テキストなし）でも正しくマッチすること。"""
        result = self.ext._find_heading_at_line_boundary(
            "Abstract", self._pairs(["Abstract"])
        )
        assert result is not None
        before, head, after = result
        assert before == ""
        assert head == "Abstract"
        assert after == ""

    def test_inline_heading_uppercase_continuation(self):
        """見出し直後に大文字で始まる単語が続く → Title Case → マッチ確定。"""
        # "Introduction Here is the body" → "Here" が大文字 → マッチ
        result = self.ext._find_heading_at_line_boundary(
            "Introduction Here is the body.", self._pairs(["Introduction"])
        )
        assert result is not None
        _, head, after = result
        assert head == "Introduction"
        assert "Here is the body" in after

    def test_inline_heading_lowercase_continuation_rejected(self):
        """見出し直後に小文字で始まる単語が続く → 文中の語とみなしてスキップ。

        これが 949b77d で修正された誤爆防止ロジックの核心。
        例: "introduction in context" → "introduction" が見出しリストにあっても
        次の単語 "in" が小文字 → 文章中の語 → マッチしない。
        """
        result = self.ext._find_heading_at_line_boundary(
            "This is about introduction in a broader context.",
            self._pairs(["introduction"])
        )
        assert result is None

    def test_numbered_heading_normalized(self):
        """番号付き見出し「1 Introduction」が正規化後にマッチすること。"""
        result = self.ext._find_heading_at_line_boundary(
            "1 Introduction\nBody text.", self._pairs(["1 Introduction"])
        )
        assert result is not None
        _, head, after = result
        assert "Introduction" in head
        assert "Body text" in after

    def test_multiword_heading(self):
        """複数単語の見出しが行頭で正しく検出されること。"""
        result = self.ext._find_heading_at_line_boundary(
            "Related Work\nMany papers have studied...", self._pairs(["Related Work"])
        )
        assert result is not None
        _, head, _ = result
        assert "Related Work" in head

    def test_heading_not_matched_mid_word(self):
        """見出し候補が単語の途中に登場しても誤爆しないこと。"""
        # "Abstract" が行頭ではなく、文中の別の文脈で登場するケース
        result = self.ext._find_heading_at_line_boundary(
            "The abstract idea is important.\nAbstract concepts matter.",
            self._pairs(["Abstract"])
        )
        # "The abstract idea..." → "abstract"[0] が小文字ではなく "The" で始まる行なので
        # 先頭単語 "The" ≠ "abstract" → 1行目はマッチしない
        # "Abstract concepts matter." → "Abstract" が先頭、次の単語 "concepts" が小文字 → スキップ
        assert result is None

    def test_empty_text_returns_none(self):
        result = self.ext._find_heading_at_line_boundary("", self._pairs(["Introduction"]))
        assert result is None

    def test_empty_heading_pairs(self):
        result = self.ext._find_heading_at_line_boundary("Introduction", [])
        assert result is None


# ============================================================
# assign_roles
# ============================================================

class TestAssignRoles:
    def setup_method(self):
        self.ext = _extractor()

    def test_simple_heading_split(self):
        """見出し行 + 本文行 のチャンクが h2 + p に分割されること。"""
        chunks = [_chunk("1", "Introduction\nThis is the body.", 1.0)]
        result = self.ext.assign_roles(chunks, ["Introduction"])

        assert len(result) == 2
        assert result[0].role == "h2"
        assert result[0].text == "Introduction"
        assert result[1].role == "p"
        assert "This is the body" in result[1].text

    def test_no_match_preserves_chunk(self):
        """見出しが含まれないチャンクは変更されないこと。"""
        chunks = [_chunk("1", "Regular paragraph.", 1.0)]
        result = self.ext.assign_roles(chunks, ["Introduction"])

        assert len(result) == 1
        assert result[0].role == "p"
        assert result[0].text == "Regular paragraph."

    def test_empty_headings_returns_unchanged(self):
        """見出しリストが空の場合、全チャンクがそのまま返ること。"""
        chunks = [
            _chunk("1", "Introduction\nBody.", 1.0),
            _chunk("2", "Methods\nDetail.", 2.0),
        ]
        result = self.ext.assign_roles(chunks, [])
        assert len(result) == 2
        assert all(c.role == "p" for c in result)

    def test_three_way_split_before_heading_after(self):
        """見出し前に本文がある場合、p + h2 + p の3分割になること。"""
        text = "Pre-heading text.\nMethods\nPost-heading body."
        chunks = [_chunk("1", text, 1.0)]
        result = self.ext.assign_roles(chunks, ["Methods"])

        assert len(result) == 3
        assert result[0].role == "p"
        assert "Pre-heading text" in result[0].text
        assert result[1].role == "h2"
        assert result[1].text == "Methods"
        assert result[2].role == "p"
        assert "Post-heading body" in result[2].text

    def test_heading_only_chunk(self):
        """見出しのみのチャンク（前後テキストなし）は h2 1件のみになること。"""
        chunks = [_chunk("1", "Abstract", 1.0)]
        result = self.ext.assign_roles(chunks, ["Abstract"])

        assert len(result) == 1
        assert result[0].role == "h2"
        assert result[0].text == "Abstract"

    def test_multiple_chunks_only_matching_split(self):
        """複数チャンクのうち、見出しを含むものだけ分割されること。"""
        chunks = [
            _chunk("1", "Just a paragraph.", 1.0),
            _chunk("2", "Methods\nWe did this.", 2.0),
            _chunk("3", "Another paragraph.", 3.0),
        ]
        result = self.ext.assign_roles(chunks, ["Methods"])

        # chunk 1 → そのまま (p)
        # chunk 2 → h2 + p (2件)
        # chunk 3 → そのまま (p)
        assert len(result) == 4
        assert result[0].role == "p"
        assert result[1].role == "h2"
        assert result[2].role == "p"
        assert result[3].role == "p"

    def test_id_preservation_and_suffix(self):
        """分割後の ID が元 ID を継承し、衝突を避けるサフィックスが付くこと。"""
        chunks = [_chunk("chunk_5", "Pre.\nResults\nPost.", 5.0)]
        result = self.ext.assign_roles(chunks, ["Results"])

        ids = [c.id for c in result]
        # before: "chunk_5"（元 ID）
        # heading: "chunk_5_h"（_h サフィックス）
        # after: "chunk_5_b"（_b サフィックス）
        assert "chunk_5" in ids
        assert "chunk_5_h" in ids
        assert "chunk_5_b" in ids

    def test_seq_index_ordering(self):
        """分割後の seq_index が before < heading < after の順であること。"""
        chunks = [_chunk("1", "Before.\nIntroduction\nAfter.", 10.0)]
        result = self.ext.assign_roles(chunks, ["Introduction"])

        assert len(result) == 3
        assert result[0].seq_index < result[1].seq_index < result[2].seq_index

    def test_false_positive_not_split(self):
        """小文字で続く単語があるため見出しとみなされない → 分割しない。"""
        # 949b77d で修正: "introduction in the field" は見出しではない
        chunks = [_chunk("1", "This discusses introduction in the broader field.", 1.0)]
        result = self.ext.assign_roles(chunks, ["introduction"])

        assert len(result) == 1
        assert result[0].role == "p"

    def test_numbered_heading_detected(self):
        """番号付き見出し「2 Methods」が正しく検出・分割されること。"""
        chunks = [_chunk("1", "2 Methods\nWe conducted experiments.", 1.0)]
        result = self.ext.assign_roles(chunks, ["2 Methods"])

        assert any(c.role == "h2" for c in result)
        h2 = next(c for c in result if c.role == "h2")
        assert "Methods" in h2.text
