from typing import List
from core.models import TreeNode
from .formatter_utils import clean_heading_text

class MarkdownEngine:
    """TreeNode ツリーを Markdown 文字列に変換するエンジン。"""

    def __init__(self, base_level: int = 2):
        self.base_level = base_level

    def render(self, nodes: List[TreeNode], current_base: int = None) -> str:
        """TreeNode リストを再帰的に変換。"""
        if current_base is None:
            current_base = self.base_level
            
        lines: List[str] = []
        for node in nodes:
            if node.role.startswith("h"):
                try:
                    # h2 を基準点(0)としてオフセット計算
                    role_num = int(node.role[1:])
                    level_offset = role_num - 2
                except (ValueError, IndexError):
                    level_offset = 0
                
                level = current_base + level_offset
                prefix = "#" * level
                clean_text = clean_heading_text(node.text)
                lines.append(f"{prefix} {clean_text}")
                lines.append("")
            else:
                # 本文
                if node.text.strip():
                    lines.append(node.text)
                    lines.append("")

            if node.children:
                # 子要素も同じベースレベルで展開（role 自体が階層情報を持つため）
                lines.append(self.render(node.children, current_base))

        return "\n".join(lines)
