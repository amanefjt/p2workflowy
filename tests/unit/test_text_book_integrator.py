"""
TextBookIntegrator のユニットテスト

テスト対象:
  - shift_markdown_headings: 見出しレベルシフトと二重# 除去
  - shift_workflowy_indent: インデントシフト
  - merge_markdown: 書籍 Markdown 統合
  - merge_workflowy: 書籍 Workflowy 統合
"""

import pytest
from pathlib import Path
from core.engine.p5_export.text_book_integrator import TextBookIntegrator


@pytest.fixture
def ti():
    return TextBookIntegrator()


# ============================================================
# shift_markdown_headings
# ============================================================

class TestShiftMarkdownHeadings:
    def test_shifts_h1_to_h3_with_shift2(self, ti):
        result = ti.shift_markdown_headings("# Introduction", shift=2)
        assert result == "### Introduction"

    def test_shifts_h2_to_h3_with_shift1(self, ti):
        result = ti.shift_markdown_headings("## Methods", shift=1)
        assert result == "### Methods"

    def test_removes_double_hash_from_llm_output(self, ti):
        """LLM が "## # Title" と出力した場合の二重# を除去する。"""
        result = ti.shift_markdown_headings("## # 1. Experimentations", shift=2)
        assert result == "#### 1. Experimentations"
        assert "## #" not in result

    def test_non_heading_lines_unchanged(self, ti):
        result = ti.shift_markdown_headings("Normal paragraph text.", shift=2)
        assert result == "Normal paragraph text."

    def test_empty_input(self, ti):
        assert ti.shift_markdown_headings("", shift=1) == ""

    def test_shift_zero_returns_unchanged(self, ti):
        original = "## Section"
        assert ti.shift_markdown_headings(original, shift=0) == original

    def test_multiline_content(self, ti):
        content = "# Title\n\nParagraph text.\n\n## Section"
        result = ti.shift_markdown_headings(content, shift=1)
        lines = result.split("\n")
        assert lines[0] == "## Title"
        assert lines[2] == "Paragraph text."
        assert lines[4] == "### Section"


# ============================================================
# shift_workflowy_indent
# ============================================================

class TestShiftWorkflowyIndent:
    def test_adds_one_tab(self, ti):
        result = ti.shift_workflowy_indent("- Item", shift=1)
        assert result == "\t- Item"

    def test_adds_two_tabs(self, ti):
        result = ti.shift_workflowy_indent("Content", shift=2)
        assert result == "\t\tContent"

    def test_blank_lines_unchanged(self, ti):
        result = ti.shift_workflowy_indent("Line\n\nNext", shift=1)
        lines = result.split("\n")
        assert lines[0] == "\tLine"
        assert lines[1] == ""          # blank line: no indent
        assert lines[2] == "\tNext"

    def test_shift_zero_returns_unchanged(self, ti):
        original = "- Item"
        assert ti.shift_workflowy_indent(original, shift=0) == original


# ============================================================
# merge_markdown
# ============================================================

class TestMergeMarkdown:
    def test_book_title_as_h1(self, ti, tmp_path):
        ch_md = tmp_path / "ch1_p2.md"
        ch_md.write_text("# Chapter 1\n\nContent here.")
        result = ti.merge_markdown("My Book", "Summary text", [("Chapter 1", ch_md)])
        assert result.startswith("# My Book")

    def test_global_resume_included(self, ti, tmp_path):
        ch_md = tmp_path / "ch1_p2.md"
        ch_md.write_text("Content")
        result = ti.merge_markdown("Book", "## Key Point\n\nDetail.", [("Ch1", ch_md)])
        assert "Key Point" in result
        assert "[Summary]" in result

    def test_chapter_heading_as_h2(self, ti, tmp_path):
        ch_md = tmp_path / "ch1_p2.md"
        ch_md.write_text("Chapter content.")
        result = ti.merge_markdown("Book", "", [("Chapter One", ch_md)])
        assert "## Chapter One" in result

    def test_chapter_content_shifted(self, ti, tmp_path):
        ch_md = tmp_path / "ch1_p2.md"
        ch_md.write_text("## Section A\n\nText.")
        result = ti.merge_markdown("Book", "", [("Chapter 1", ch_md)])
        # ## Section A は shift=1 で ### になるはず
        assert "### Section A" in result

    def test_missing_chapter_file_skipped(self, ti, tmp_path):
        nonexistent = tmp_path / "missing_p2.md"
        result = ti.merge_markdown("Book", "", [("Ghost Chapter", nonexistent)])
        assert "Ghost Chapter" not in result or "## Ghost Chapter" not in result


# ============================================================
# merge_workflowy
# ============================================================

class TestMergeWorkflowy:
    def test_book_title_at_top(self, ti, tmp_path):
        ch_txt = tmp_path / "ch1_p2.txt"
        ch_txt.write_text("- Item")
        result = ti.merge_workflowy("My Book", "", [("Chapter 1", ch_txt)])
        assert result.startswith("My Book")

    def test_chapter_content_indented(self, ti, tmp_path):
        ch_txt = tmp_path / "ch1_p2.txt"
        ch_txt.write_text("- Top item\n\t- Sub item")
        result = ti.merge_workflowy("Book", "", [("Chapter 1", ch_txt)])
        # 章内容は1段インデント増し
        assert "\t- Top item" in result or "\t\t- Sub item" in result
