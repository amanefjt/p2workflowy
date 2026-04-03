import re
from pathlib import Path
from typing import List, Tuple
from .workflowy_engine import WorkflowyEngine

class TextBookIntegrator:
    """
    Stage 1 で出力された章ファイルを読み込み、テキストレベルで結合するエンジン。
    """

    def __init__(self, tab_char: str = "\t"):
        self.tab_char = tab_char

    def shift_markdown_headings(self, content: str, shift: int = 1) -> str:
        """Markdown の見出しレベルを shift 分だけ上げる。"""
        if shift <= 0:
            return content
        
        lines = content.splitlines()
        shifted_lines = []
        for line in lines:
            if line.startswith("#"):
                shifted_lines.append(("#" * shift) + line)
            else:
                shifted_lines.append(line)
        return "\n".join(shifted_lines)

    def shift_workflowy_indent(self, content: str, shift: int = 1) -> str:
        """Workflowy のインデントを shift 分だけ深くする。"""
        if shift <= 0:
            return content
        
        lines = content.splitlines()
        shifted_lines = []
        indent = self.tab_char * shift
        for line in lines:
            if line.strip():
                shifted_lines.append(indent + line)
            else:
                shifted_lines.append(line)
        return "\n".join(shifted_lines)


    def merge_markdown(self, book_title: str, global_resume: str, chapters: List[Tuple[str, Path]]) -> str:
        """全章の Markdown を統合。"""
        parts = []
        parts.append(f"# {book_title}")
        parts.append("")
        
        if global_resume:
            parts.append("## [Summary] 書籍全体の要約 (Overall Summary)")
            parts.append("")
            # 要約の中身も H2->H3 (または H1->H3) へシフト。specでは ### なので shift=2
            parts.append(self.shift_markdown_headings(global_resume.strip(), shift=2))
            parts.append("")

        for title, path in chapters:
            if not path.exists():
                print(f"  [Integrator] 警告: 章ファイルが見つかりません: {path}")
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                c_lines = content.splitlines()
                
                # 最初の一行目がタイトル（# Title）の場合はスキップして重複を防ぐ
                if c_lines and c_lines[0].lstrip("# ").strip().lower() == title.strip().lower():
                    content = "\n".join(c_lines[1:]).strip()

                # 章タイトルを H2 として追加し、中身を H2->H3, H3->H4 へシフト
                parts.append(f"## {title}")
                parts.append("")
                parts.append(self.shift_markdown_headings(content, shift=1))
                parts.append("")

        return "\n".join(parts)

    def merge_workflowy(self, book_title: str, global_resume: str, chapters: List[Tuple[str, Path]]) -> str:
        """全章の Workflowy を統合。"""
        lines = []
        # Book Title (Level 0)
        lines.append(f"{book_title}")
        
        if global_resume:
            # Overall Summary (Level 1: No Tab)
            lines.append("- [Summary] 書籍全体の要約 (Overall Summary)")
            # WorkflowyEngine を使用して Markdown を高品質変換 (base_depth=1 により H1 が Depth 2=1tab になる)
            wf_engine = WorkflowyEngine(base_depth=1)
            lines.append(wf_engine.render_resume(global_resume.strip()))

        for title, path in chapters:
            if not path.exists():
                print(f"  [Integrator] 警告: 章ファイルが見つかりません: {path}")
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                c_lines = content.splitlines()

                # 最初の一行目が章タイトルの場合はスキップして二重タイトルを防ぐ
                if c_lines and c_lines[0].strip().lower() == title.strip().lower():
                    content = "\n".join(c_lines[1:]).strip()

                # 章見出し (Level 1: No Tab) を - で作成し、中身を 1段シフト (Depth 2 = 1 tab)
                lines.append(f"- {title}")
                lines.append(self.shift_workflowy_indent(content, shift=1))

        return "\n".join(lines)
