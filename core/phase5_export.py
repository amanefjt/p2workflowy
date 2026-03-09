"""
p2workflowy V2 Phase 5: Export
3部構成（レジュメ/English/日本語）でMarkdownとWorkflowy形式のファイルを出力する。
"""

import re
import json
from pathlib import Path
from typing import List, Tuple

from .config import print_log
from .models import TreeNode


# =============================================================================
# Markdown 出力
# =============================================================================

def tree_to_markdown(nodes: List[TreeNode], base_level: int = 2) -> str:
    """TreeNodeリストをMarkdown形式に再帰的に変換する。"""
    lines: List[str] = []

    for node in nodes:
        if node.role.startswith("h"):
            # role が "h2" なら base_level + 0, "h3" なら base_level + 1
            try:
                level_offset = int(node.role[1:]) - 2
            except (ValueError, IndexError):
                level_offset = 0
            level = base_level + level_offset
            prefix = "#" * level
            lines.append(f"{prefix} {node.text}")
            lines.append("")
        else:
            lines.append(node.text)
            lines.append("")

        if node.children:
            child_md = tree_to_markdown(node.children, base_level)
            lines.append(child_md)

    return "\n".join(lines)


def format_resume_markdown(resume_content: str) -> str:
    """レジュメの見出しレベルを+2に調整する。"""
    lines = resume_content.split("\n")
    adjusted: List[str] = []
    for line in lines:
        if line.startswith("#"):
            match = re.match(r"^(#+)\s", line)
            if match:
                current_level = len(match.group(1))
                new_level = current_level + 2
                adjusted.append("#" * new_level + " " + line[current_level:].strip())
                continue
        adjusted.append(line)
    return "\n".join(adjusted)


def generate_markdown_output(
    title: str,
    resume_content: str,
    english_tree: List[TreeNode],
    japanese_tree: List[TreeNode],
) -> str:
    """3部構成のMarkdown出力を生成し、不要な改行をクリーンアップする。"""
    parts: List[str] = [
        f"# {title}",
        "",
        "## レジュメ",
        "",
        format_resume_markdown(resume_content),
        "",
        "## English text",
        "",
        tree_to_markdown(english_tree, base_level=2),
        "",
        "## 日本語テキスト",
        "",
        tree_to_markdown(japanese_tree, base_level=2)
    ]

    raw_md = "\n".join(parts)
    # 3つ以上連続する改行を2つに圧縮して可読性を向上
    clean_md = re.sub(r'\n{3,}', '\n\n', raw_md)
    return clean_md.strip() + "\n"


def generate_ronbun_nihongo_output(
    title: str,
    japanese_tree: List[TreeNode],
) -> str:
    """日本語訳のみのMarkdown(RonbunNihongo)を生成する。"""
    parts: List[str] = [
        f"# {title}",
        "",
        "## 日本語訳",
        "",
        tree_to_markdown(japanese_tree, base_level=2)
    ]

    raw_md = "\n".join(parts)
    clean_md = re.sub(r'\n{3,}', '\n\n', raw_md)
    return clean_md.strip() + "\n"


# =============================================================================
# Workflowy 出力
# =============================================================================

def tree_to_workflowy(nodes: List[TreeNode], base_depth: int = 0) -> str:
    """TreeNodeリストをWorkflowy形式（タブインデント + "- "）に変換する。"""
    lines: List[str] = []

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
    wf_lines: List[str] = []
    current_depth = base_depth

    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue

        # 1. 見出しの処理
        heading_match = re.match(r"^(#+)\s+(.+)$", line_strip)
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
        wf_lines.append(f"{indent}- {line_strip}")

    return "\n".join(wf_lines)


def generate_workflowy_output(
    title: str,
    resume_content: str,
    english_tree: List[TreeNode],
    japanese_tree: List[TreeNode],
) -> str:
    """3部構成のWorkflowy出力を生成する (ideal_structure.txt 準拠)。"""
    lines: List[str] = []

    # 1. 論文タイトル (Top Level)
    lines.append(f"{title}")

    # 2. レジュメ (Resume) [Level 1]
    lines.append("- レジュメ")
    lines.append(resume_to_workflowy(resume_content, base_depth=1))

    # 3. English text [Level 1]
    lines.append("- English text")
    # 文書内の各セクション（Abstract等）をタイトルの直下（Level 1）にするため base_depth=0
    lines.append(tree_to_workflowy(english_tree, base_depth=0))

    # 4. 日本語テキスト [Level 1]
    lines.append("- 日本語テキスト") 
    lines.append(tree_to_workflowy(japanese_tree, base_depth=0))

    return "\n".join(lines)


# =============================================================================
# メイン実行関数
# =============================================================================

def run_phase5(
    input_path_str: str,
    title: str,
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    phase4_state_path: str | Path,
) -> Tuple[Path, Path, Path]:
    """
    Phase 5 メイン処理。
    中間状態ファイルを読み込み、最終ファイルを出力する。
    """
    input_path = Path(input_path_str)
    phase2_state_path = Path(phase2_state_path)
    structure_state_path = Path(structure_state_path)
    phase4_state_path = Path(phase4_state_path)
    
    if not phase4_state_path.exists():
        raise FileNotFoundError(f"Phase 4 の出力が見つかりません: {phase4_state_path}")
    if not structure_state_path.exists():
        raise FileNotFoundError(f"Phase 3 の出力が見つかりません: {structure_state_path}")
    if not phase2_state_path.exists():
        raise FileNotFoundError(f"Phase 2 の出力が見つかりません: {phase2_state_path}")

    # データ読み込み
    with open(phase2_state_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    resume_content = meta["resume_content"]

    with open(structure_state_path, "r", encoding="utf-8") as f:
        english_tree_data = json.load(f)
        english_tree = [TreeNode.from_dict(d) for d in english_tree_data]

    with open(phase4_state_path, "r", encoding="utf-8") as f:
        japanese_tree_data = json.load(f)
        japanese_tree = [TreeNode.from_dict(d) for d in japanese_tree_data]

    # 出力パスの設定
    stem = input_path.stem
    output_dir = input_path.parent
    md_path = output_dir / f"{stem}_p2.md"
    wf_path = output_dir / f"{stem}_p2.txt"
    rn_path = output_dir / f"{stem}_ronbun.md" # RonbunNihongo 出力

    print_log(f"  [Phase 5] ファイル出力開始: {stem}")

    # Markdown 生成と保存
    md_content = generate_markdown_output(title, resume_content, english_tree, japanese_tree)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print_log(f"  [Phase 5] Markdown 保存完了: {md_path.name}")

    # Workflowy 生成と保存
    wf_content = generate_workflowy_output(title, resume_content, english_tree, japanese_tree)
    with open(wf_path, "w", encoding="utf-8") as f:
        f.write(wf_content)
    print_log(f"  [Phase 5] Workflowy (txt) 保存完了: {wf_path.name}")

    # RonbunNihongo 生成と保存
    rn_content = generate_ronbun_nihongo_output(title, japanese_tree)
    with open(rn_path, "w", encoding="utf-8") as f:
        f.write(rn_content)
    print_log(f"  [Phase 5] RonbunNihongo 保存完了: {rn_path.name}")

    return md_path, wf_path, rn_path
