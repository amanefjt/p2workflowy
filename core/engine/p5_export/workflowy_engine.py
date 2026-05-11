import re
from typing import List
from core.models import TreeNode
from .formatter_utils import sanitize_wf_text, clean_heading_text, parse_resume_heading_line

class WorkflowyEngine:
    """TreeNode ツリーを Workflowy 文字列 (タブインデント + "- ") に変換するエンジン。"""

    def __init__(self, base_depth: int = 0):
        self.base_depth = base_depth

    def render(self, nodes: List[TreeNode], current_depth: int = None) -> str:
        """TreeNode リストを再帰的に Workflowy 形式へ変換。"""
        if current_depth is None:
            current_depth = self.base_depth

        lines: List[str] = []
        for node in nodes:
            indent = "\t" * current_depth
            clean_text = sanitize_wf_text(node.text)
            if not clean_text:
                continue
            
            lines.append(f"{indent}- {clean_text}")

            if node.children:
                child_wf = self.render(node.children, current_depth + 1)
                if child_wf:
                    lines.append(child_wf)

        return "\n".join(lines)

    def render_resume(self, resume_content: str, base_depth: int = 1) -> str:
        """Markdown 形式のレジュメを Workflowy 形式に変換。"""
        if not resume_content:
            return ""

        lines = resume_content.split("\n")
        wf_lines: List[str] = []
        current_depth = base_depth
        in_section_expansion = False

        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue

            # 1. 見出しの処理
            heading_result = parse_resume_heading_line(line_strip, in_section_expansion)
            if heading_result is not None:
                level, cleaned_text, in_section_expansion, add_bracket = heading_result
                if level == 1 or add_bracket:
                    depth = base_depth
                else:
                    depth = base_depth + (level - 1)
                label = f"[{cleaned_text}]" if add_bracket else cleaned_text
                wf_lines.append(f"{'	' * depth}- {label}")
                current_depth = depth + 1
                continue

            # 2. リスト（箇条書き）の処理
            list_match = re.match(r"^(\s*)[-*]\s+(.+)$", line)
            if list_match:
                leading_spaces = len(list_match.group(1))
                extra_depth = min(leading_spaces // 2, 1)
                indent = "\t" * (current_depth + extra_depth)
                wf_lines.append(f"{indent}- {sanitize_wf_text(list_match.group(2))}")
                continue

            # 3. 引用ブロック
            quote_match = re.match(r"^\s*>\s*(.+)$", line)
            if quote_match:
                indent = "\t" * current_depth
                wf_lines.append(f"{indent}- {sanitize_wf_text(quote_match.group(1))}")
                continue

            # 4. 通常のテキスト行
            indent = "\t" * current_depth
            wf_lines.append(f"{indent}- {sanitize_wf_text(line_strip)}")

        return "\n".join(wf_lines)
