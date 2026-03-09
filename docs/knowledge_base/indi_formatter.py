"""
p2workflowy Phase 5: Export
3部構成（レジュメ/English/日本語）でMarkdownとWorkflowy形式のファイルを出力する。
"""

import re
from pathlib import Path
from .models import TreeNode


# =============================================================================
# Markdown 出力
# =============================================================================

def tree_to_markdown(nodes: list[TreeNode], base_level: int = 2, current_depth: int = 0) -> str:
    """TreeNodeリストをMarkdown形式に再帰的に変換する。"""
    lines: list[str] = []

    for node in nodes:
        if node.role.startswith("h"):
            # role が "h2" なら base_level + 0, "h3" なら base_level + 1
            level_offset = int(node.role[1:]) - 2
            level = base_level + level_offset
            prefix = "#" * level
            lines.append(f"{prefix} {node.text}")
            lines.append("")
        else:
            lines.append(node.text)
            lines.append("")

        if node.children:
            child_md = tree_to_markdown(node.children, base_level, current_depth + 1)
            lines.append(child_md)

    return "\n".join(lines)


def format_resume_markdown(resume_content: str) -> str:
    """レジュメの見出しレベルを+2に調整する。"""
    lines = resume_content.split("\n")
    adjusted: list[str] = []
    for line in lines:
        if line.startswith("#"):
            match = re.match(r"^(#+)\s", line)
            if match:
                current_level = len(match.group(1))
                new_level = current_level + 2
                adjusted.append("#" * new_level + line[current_level:])
                continue
        adjusted.append(line)
    return "\n".join(adjusted)


def generate_markdown_output(
    title: str,
    resume_content: str,
    english_tree: list[TreeNode],
    japanese_tree: list[TreeNode],
) -> str:
    """3部構成のMarkdown出力を生成し、不要な改行をクリーンアップする。"""
    parts: list[str] = [
        f"# {title}",
        "",
        "## レジュメ (Resume)",
        "",
        format_resume_markdown(resume_content),
        "",
        "## English text",
        "",
        tree_to_markdown(english_tree, base_level=3),
        "",
        "## 日本語テキスト (Japanese Text)",
        "",
        tree_to_markdown(japanese_tree, base_level=3)
    ]

    raw_md = "\n".join(parts)
    # 3つ以上連続する改行を2つに圧縮して可読性を向上
    clean_md = re.sub(r'\n{3,}', '\n\n', raw_md)
    return clean_md.strip() + "\n"


# =============================================================================
# Workflowy 出力
# =============================================================================

def tree_to_workflowy(nodes: list[TreeNode], base_depth: int = 0) -> str:
    """TreeNodeリストをWorkflowy形式（タブインデント + "- "）に変換する。"""
    lines: list[str] = []

    for node in nodes:
        indent = "\t" * base_depth  # タブインデントを使用
        lines.append(f"{indent}- {node.text}")

        if node.children:
            child_wf = tree_to_workflowy(node.children, base_depth + 1)
            lines.append(child_wf)

    return "\n".join(lines)


def resume_to_workflowy(resume_content: str, base_depth: int = 2) -> str:
    """Markdown形式のレジュメをWorkflowy形式（タブインデント）に変換する。"""
    lines = resume_content.split("\n")
    wf_lines: list[str] = []
    current_depth = base_depth

    for line in lines:
        if not line.strip():
            continue

        # 1. 見出しの処理
        heading_match = re.match(r"^(#+)\s+(.+)$", line.strip())
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            depth = base_depth + (level - 1)
            indent = "\t" * depth
            wf_lines.append(f"{indent}- {text}")
            current_depth = depth + 1
            continue

        # 2. リスト（箇条書き）の処理
        list_match = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if list_match:
            leading_spaces = len(list_match.group(1))
            extra_depth = leading_spaces // 2 
            indent = "\t" * (current_depth + extra_depth)
            wf_lines.append(f"{indent}- {list_match.group(2)}")
            continue

        # 3. 引用ブロック
        quote_match = re.match(r"^\s*>\s*(.+)$", line)
        if quote_match:
            indent = "\t" * current_depth
            wf_lines.append(f"{indent}- {quote_match.group(1)}")
            continue

        # 4. 通常のテキスト行
        indent = "\t" * current_depth
        wf_lines.append(f"{indent}- {line.strip()}")

    return "\n".join(wf_lines)


def generate_workflowy_output(
    title: str,
    resume_content: str,
    english_tree: list[TreeNode],
    japanese_tree: list[TreeNode],
) -> str:
    """3部構成のWorkflowy出力を生成する。"""
    lines: list[str] = []

    lines.append(f"{title}")

    lines.append("- レジュメ (Resume)")
    lines.append(resume_to_workflowy(resume_content, base_depth=1))

    lines.append("- English text")
    lines.append(tree_to_workflowy(english_tree, base_depth=1))

    lines.append("- 日本語テキスト (Japanese Text)")
    lines.append(tree_to_workflowy(japanese_tree, base_depth=1))

    return "\n".join(lines)


# =============================================================================
# ファイル出力
# =============================================================================

def write_outputs(
    input_path: Path,
    title: str,
    resume_content: str,
    english_tree: list[TreeNode],
    japanese_tree: list[TreeNode],
) -> tuple[Path, Path]:
    """MarkdownとWorkflowy形式のファイルを書き出す。"""
    stem = input_path.stem
    output_dir = input_path.parent

    md_path = output_dir / f"{stem}_p2.md"
    wf_path = output_dir / f"{stem}_p2.txt"

    md_content = generate_markdown_output(title, resume_content, english_tree, japanese_tree)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    wf_content = generate_workflowy_output(title, resume_content, english_tree, japanese_tree)
    with open(wf_path, "w", encoding="utf-8") as f:
        f.write(wf_content)

    return md_path, wf_path