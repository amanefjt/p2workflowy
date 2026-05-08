"""
Phase 5 エクスポートエンジンのユニットテスト

テスト対象:
  - formatter_utils.sanitize_wf_text: Workflowy 用サニタイズ
  - formatter_utils.clean_heading_text: 見出しの # 除去
  - formatter_utils.format_resume_markdown: レジュメ見出しレベル調整
  - WorkflowyEngine.render: TreeNode ツリー → Workflowy 形式
  - WorkflowyEngine.render_resume: Markdown レジュメ → Workflowy 形式
  - MarkdownEngine.render: TreeNode ツリー → Markdown 形式

【設計メモ】
"References 系セクションを出力から除外し、Appendix は保持する" という不変条件は
Phase 3（exclude_keywords による見出し除外 / STOP_SECTIONS）で担保されている。
Phase 5 はツリーをそのまま描画するため、フィルタリングの責務を持たない。
"""

import pytest
from core.models import TreeNode
from core.engine.p5_export.formatter_utils import (
    sanitize_wf_text,
    clean_heading_text,
    format_resume_markdown,
)
from core.engine.p5_export.workflowy_engine import WorkflowyEngine
from core.engine.p5_export.markdown_engine import MarkdownEngine


def _h(id: str, text: str, role: str = "h2", children=None) -> TreeNode:
    return TreeNode(id=id, text=text, role=role, seq_index=0.0, children=children or [])

def _p(id: str, text: str) -> TreeNode:
    return TreeNode(id=id, text=text, role="p", seq_index=0.0)


# ============================================================
# sanitize_wf_text
# ============================================================

class TestSanitizeWfText:
    def test_removes_chunk_tags(self):
        assert sanitize_wf_text("<chunk_123>hello</chunk_123>") == "hello"

    def test_flattens_newlines(self):
        result = sanitize_wf_text("line1\nline2\nline3")
        assert "\n" not in result
        assert "line1" in result and "line2" in result

    def test_collapses_multiple_spaces(self):
        result = sanitize_wf_text("too   many    spaces")
        assert "  " not in result

    def test_empty_string(self):
        assert sanitize_wf_text("") == ""

    def test_plain_text_unchanged(self):
        assert sanitize_wf_text("Hello world") == "Hello world"


# ============================================================
# clean_heading_text
# ============================================================

class TestCleanHeadingText:
    def test_removes_leading_hashes(self):
        assert clean_heading_text("## Introduction") == "Introduction"

    def test_removes_surrounding_hashes(self):
        assert clean_heading_text("# Methods #") == "Methods"

    def test_empty_string(self):
        assert clean_heading_text("") == ""

    def test_no_hashes(self):
        assert clean_heading_text("Results") == "Results"

    def test_preserves_brackets(self):
        # [] は除去対象外（内容保護優先）
        assert clean_heading_text("[Unlabeled Section]") == "[Unlabeled Section]"


# ============================================================
# format_resume_markdown
# ============================================================

class TestFormatResumeMarkdown:
    def test_h1_shifted_by_default_shift_2(self):
        result = format_resume_markdown("# Section Title", shift=2)
        assert result.startswith("### Section Title")

    def test_h2_shifted(self):
        result = format_resume_markdown("## Sub Section", shift=2)
        assert result.startswith("#### Sub Section")

    def test_empty_returns_empty(self):
        assert format_resume_markdown("") == ""

    def test_non_heading_lines_preserved(self):
        resume = "# Title\n\nThis is body text."
        result = format_resume_markdown(resume, shift=2)
        assert "This is body text." in result

    def test_section_expansion_h2_flattened_with_brackets(self):
        # 「各セクション」が含まれる H1 の下の H2 はブラケット付きでフラット化
        resume = "# 3. 各セクションの展開\n## Introduction"
        result = format_resume_markdown(resume, shift=2)
        assert "### [Introduction]" in result


# ============================================================
# WorkflowyEngine.render
# ============================================================

class TestWorkflowyEngineRender:
    def setup_method(self):
        self.engine = WorkflowyEngine(base_depth=0)

    def test_single_node(self):
        nodes = [_h("1", "Introduction")]
        result = self.engine.render(nodes)
        assert result == "- Introduction"

    def test_depth_0_no_indent(self):
        nodes = [_p("1", "Some text")]
        result = self.engine.render(nodes)
        assert result.startswith("- ")
        assert not result.startswith("\t")

    def test_child_nodes_indented_one_level(self):
        parent = _h("1", "Abstract", children=[_p("2", "Abstract body.")])
        result = self.engine.render([parent])
        lines = result.split("\n")
        assert lines[0] == "- Abstract"
        assert lines[1] == "\t- Abstract body."

    def test_two_level_nesting(self):
        grandchild = _p("3", "Deep text")
        child = _h("2", "Sub", role="h3", children=[grandchild])
        parent = _h("1", "Section", children=[child])
        result = self.engine.render([parent])
        lines = result.split("\n")
        assert lines[0] == "- Section"
        assert lines[1] == "\t- Sub"
        assert lines[2] == "\t\t- Deep text"

    def test_empty_text_nodes_skipped(self):
        nodes = [_p("1", ""), _p("2", "Real content")]
        result = self.engine.render(nodes)
        assert "Real content" in result
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) == 1

    def test_multiple_top_level_sections(self):
        nodes = [
            _h("1", "Abstract"),
            _h("2", "Introduction"),
            _h("3", "Methods"),
        ]
        result = self.engine.render(nodes)
        lines = result.split("\n")
        assert lines[0] == "- Abstract"
        assert lines[1] == "- Introduction"
        assert lines[2] == "- Methods"

    def test_base_depth_shifts_indent(self):
        engine = WorkflowyEngine(base_depth=1)
        nodes = [_p("1", "Indented")]
        result = engine.render(nodes)
        assert result.startswith("\t- ")


# ============================================================
# WorkflowyEngine.render_resume
# ============================================================

class TestWorkflowyEngineRenderResume:
    def setup_method(self):
        self.engine = WorkflowyEngine()

    def test_h1_becomes_top_level(self):
        result = self.engine.render_resume("# Summary", base_depth=1)
        assert "- Summary" in result

    def test_list_item_rendered(self):
        result = self.engine.render_resume("- bullet point", base_depth=1)
        assert "bullet point" in result

    def test_empty_returns_empty(self):
        assert self.engine.render_resume("") == ""

    def test_quote_block_rendered(self):
        result = self.engine.render_resume("> quoted text", base_depth=1)
        assert "quoted text" in result

    def test_section_expansion_h2_uses_brackets(self):
        # "各セクション" H1 の下の H2 はブラケット付きになる
        resume = "# 3. 各セクションの展開\n## Introduction"
        result = self.engine.render_resume(resume, base_depth=1)
        assert "[Introduction]" in result


# ============================================================
# MarkdownEngine.render
# ============================================================

class TestMarkdownEngineRender:
    def setup_method(self):
        self.engine = MarkdownEngine(base_level=2)

    def test_h2_node_renders_as_h2(self):
        nodes = [_h("1", "Introduction", role="h2")]
        result = self.engine.render(nodes)
        assert "## Introduction" in result

    def test_h3_node_renders_as_h3(self):
        nodes = [_h("1", "Subsection", role="h3")]
        result = self.engine.render(nodes)
        assert "### Subsection" in result

    def test_p_node_renders_as_plain_text(self):
        nodes = [_p("1", "This is a paragraph.")]
        result = self.engine.render(nodes)
        assert "This is a paragraph." in result
        assert "#" not in result

    def test_empty_p_node_skipped(self):
        nodes = [_p("1", ""), _p("2", "Real paragraph.")]
        result = self.engine.render(nodes)
        assert "Real paragraph." in result
        # 空行が余分に出ないこと（厳密な行数は問わない）
        assert result.count("Real paragraph.") == 1

    def test_heading_followed_by_blank_line(self):
        # Markdown 慣例: 見出し後に空行
        nodes = [_h("1", "Title", role="h2")]
        result = self.engine.render(nodes)
        assert "## Title\n" in result

    def test_children_rendered_recursively(self):
        child = _p("2", "Child paragraph.")
        parent = _h("1", "Section", role="h2", children=[child])
        result = self.engine.render([parent])
        assert "## Section" in result
        assert "Child paragraph." in result

    def test_base_level_shifts_heading_depth(self):
        engine = MarkdownEngine(base_level=3)
        nodes = [_h("1", "Deep", role="h2")]
        result = engine.render(nodes)
        # h2 role + base_level=3 → level_offset=0 → ### Deep
        assert "### Deep" in result
