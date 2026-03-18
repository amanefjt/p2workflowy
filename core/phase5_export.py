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

# デフォルトの見出しレベル
DEFAULT_SECTION_LEVEL = 2


# =============================================================================
# ユーティリティ
# =============================================================================

def clean_heading_text(text: str) -> str:
    """見出しから不要な記号（# や []）を除去する。"""
    if not text: return ""
    # 行頭の #, スペース, [ を除去。末尾の ] とスペースも除去。
    cleaned = re.sub(r'^[#\s\[\（]+', '', text)
    cleaned = re.sub(r'[\]\）\s#]+$', '', cleaned)
    return cleaned.strip()


def _sanitize_wf_text(text: str) -> str:
    """Workflowy 出力用テキストのサニタイズ（タグ除去・改行平坦化・空白正規化）"""
    if not text:
        return ""
    # 1. 残留タグの除去 (</?chunk_12345>)
    text = re.sub(r'</?chunk_\d+>', '', text)
    # 2. 改行をスペースに置換し、連続する空白を1つに集約
    text = text.replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# =============================================================================
# Markdown 出力
# =============================================================================

def tree_to_markdown(nodes: List[TreeNode], base_level: int = DEFAULT_SECTION_LEVEL) -> str:
    """TreeNodeリストをMarkdown形式に再帰的に変換する。"""
    lines: List[str] = []

    for node in nodes:
        if node.role.startswith("h"):
            # role が "h2" (章) なら base_level + 0
            # role が "h3" (節) なら base_level + 1
            # coreprompts では節を h3 と定義しているので、base_level=3 (H3) の下では H4 に動かす
            try:
                role_num = int(node.role[1:])
                # 章(h2) を基準点(0)として、ズレを計算
                level_offset = role_num - 2 
            except (ValueError, IndexError):
                level_offset = 0
            
            level = base_level + level_offset
            prefix = "#" * level
            clean_text = clean_heading_text(node.text)
            lines.append(f"{prefix} {clean_text}")
            lines.append("")
        else:
            lines.append(node.text)
            lines.append("")

        if node.children:
            child_md = tree_to_markdown(node.children, base_level)
            lines.append(child_md)

    return "\n".join(lines)


def format_resume_markdown(resume_content: str, shift: int = 2) -> str:
    """レジュメの見出しレベルを調整する。
    shift=2: H1→H3（書籍全体レジュメ・論文レジュメ）
    shift=3: H1→H4（各章レジュメ）
    """
    lines = resume_content.split("\n")
    adjusted: List[str] = []
    for line in lines:
        if line.strip().startswith("#"):
            match = re.match(r"^\s*(#+)\s*(.*)$", line)
            if match:
                current_level = len(match.group(1))
                title_text = clean_heading_text(match.group(2))
                new_level = current_level + shift
                adjusted.append("#" * new_level + " " + title_text)
                continue
        adjusted.append(line)
    return "\n".join(adjusted)


def generate_markdown_output(
    title: str,
    resume_content: str,
    english_tree: List[TreeNode],
    japanese_tree: List[TreeNode],
    is_book: bool = False,
) -> str:
    """3部構成のMarkdown出力を生成する（ideal_mdstructure.md / ideal_bookmdstructure.md 準拠）。"""
    if is_book:
        # Book Mode: H1: タイトル, H2: 全体レジュメ, H2: 各章
        parts: List[str] = [
            f"# {title}",
            "",
            # 書籍全体のレジュメ (H2)
            "## 書籍全体のレジュメ",
            "",
            format_resume_markdown(resume_content, shift=2), # H1 → H3 (全項目統一)
            "",
        ]
        # 章ごとにループ
        for node in japanese_tree:
            # 章タイトル (H2)
            clean_node_text = clean_heading_text(node.text)
            parts.append(f"## {clean_node_text}")
            parts.append("")
            
            # 章レジュメ (H3)
            summary = node.metadata.get("summary", "")
            if summary:
                parts.append(f"### {clean_node_text}のレジュメ")
                parts.append(format_resume_markdown(summary, shift=3)) # H1 -> H4
                parts.append("")
            
            # --- 並列配置セクション ---
            english_section = []
            japanese_section = []
            
            for child in node.children:
                if child.text == "English text":
                    english_section.append(child)
                else:
                    japanese_section.append(child)

            # English text of [Chapter] (H3)
            if english_section:
                parts.append(f"### English text of {clean_node_text}")
                for esc in english_section:
                    # 中身を H4 ベースで展開 (Mockup L40: ####)
                    parts.append(tree_to_markdown(esc.children, base_level=3))
                parts.append("")

            # [Chapter]の日本語本文 (H3)
            if japanese_section:
                parts.append(f"### {clean_node_text}の日本語本文")
                parts.append("")
                # 日本語セクションは H3 形式の見出し (### ) で展開するために base_level=2 (意味的には H3 になる)
                parts.append(tree_to_markdown(japanese_section, base_level=2))
                parts.append("")
    else:
        # Paper Mode: ideal_mdstructure.md 準拠 (並列)
        parts: List[str] = [
            f"# {title}",
            "",
            "## レジュメ",
            "",
            format_resume_markdown(resume_content, shift=2), # H1 -> H3
            "",
            "## English text",
            "",
            # 警告: English text 内の見出しは階層を下げて ### (H3) に維持すること。
            # 詳細は ideal_mdstructure.md (L21) を参照。tree_to_markdown の base_level=3 は必須
            tree_to_markdown(english_tree, base_level=3),
            "",
            "## 日本語本文",
            "",
            # 詳細は ideal_mdstructure.md (L31) を参照。tree_to_markdown の base_level=2 は必須。
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
    """日本語訳のみのMarkdown(RonbunNihongo)を生成する（ideal_ronbunmdstructure.md 準拠）。"""
    parts: List[str] = [
        f"# {title}",
        "",
        # "## 日本語訳" などのラッパーを削除
        tree_to_markdown(japanese_tree, base_level=2)
    ]

    raw_md = "\n".join(parts)
    clean_md = re.sub(r'\n{3,}', '\n\n', raw_md)
    return clean_md.strip() + "\n"


def generate_resume_only_output(
    title: str,
    resume_content: str,
    japanese_tree: List[TreeNode],
    is_book: bool = False,
) -> str:
    """Resume Only モード用の Markdown 出力（全体レジュメ + セクション別要約付き原文）。"""
    if is_book:
        # Book Mode では H1: 書籍タイトル, H2: 書籍全体のレジュメ
        parts: List[str] = [
            f"# {title}",
            "",
            "## 書籍全体のレジュメ",
            "",
            format_resume_markdown(resume_content, shift=2),
            "",
        ]
        for node in japanese_tree:
            # 章タイトル
            clean_node_text = clean_heading_text(node.text)
            parts.append(f"## {clean_node_text}")
            parts.append("")
            summary = node.metadata.get("summary", "")
            if summary:
                parts.append(f"### {clean_node_text}のレジュメ")
                parts.append(format_resume_markdown(summary, shift=3))
                parts.append("")
            
            if node.children:
                parts.append(f"### English text of {clean_node_text}")
                # Phase 4 の "English text" ラッパー (role=h3) をアンラップし、
                # 中身（節見出し + 本文）だけを展開する。
                # これにより「### English text of Chapter X」と
                # 「#### English text」の二重ヘッダーを防ぐ。
                content_nodes = []
                for child in node.children:
                    if child.text == "English text" and child.children:
                        content_nodes.extend(child.children)
                    else:
                        content_nodes.append(child)
                parts.append(tree_to_markdown(content_nodes, base_level=3))
                parts.append("")
    else:
        # Paper Mode
        parts: List[str] = [
            f"# {title}",
            "",
            "## レジュメ",
            "",
            format_resume_markdown(resume_content, shift=2),
            "",
            "## 英語原文",
            "",
        ]
        for node in japanese_tree:
            # セクションタイトル (H3)
            clean_node_text = clean_heading_text(node.text)
            parts.append(f"### {clean_node_text}")
            parts.append("")
            
            if node.children:
                # 原文を表示 (base_level=3 にすることで、h3ノード等が #### になる)
                parts.append(tree_to_markdown(node.children, base_level=3))
                parts.append("")
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
        # テキスト出力をサニタイズ
        clean_text = _sanitize_wf_text(node.text)
        lines.append(f"{indent}- {clean_text}")

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
            wf_lines.append(f"{indent}- {_sanitize_wf_text(text)}")
            current_depth = depth + 1
            continue

        # 2. リスト（箇条書き）の処理
        list_match = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if list_match:
            leading_spaces = len(list_match.group(1))
            # 修正: 4スペース以上のインデントがあっても、Workflowy上では1階層(1タブ)分のみの追加とする
            extra_depth = min(leading_spaces // 2, 1) 
            indent = "\t" * (current_depth + extra_depth)
            wf_lines.append(f"{indent}- {_sanitize_wf_text(list_match.group(2))}")
            continue

        # 3. 引用ブロック
        quote_match = re.match(r"^\s*>\s*(.+)$", line)
        if quote_match:
            indent = "\t" * current_depth
            wf_lines.append(f"{indent}- {_sanitize_wf_text(quote_match.group(1))}")
            continue

        # 4. 通常のテキスト行
        indent = "\t" * current_depth
        wf_lines.append(f"{indent}- {_sanitize_wf_text(line_strip)}")

    return "\n".join(wf_lines)


def generate_workflowy_output(
    title: str,
    resume_content: str,
    english_tree: List[TreeNode],
    japanese_tree: List[TreeNode],
    is_book: bool = False,
) -> str:
    """3部構成のWorkflowy出力を生成する (ideal_wfstructure.txt 準拠)。"""
    lines: List[str] = []

    if is_book:
        # 1. 書籍タイトル
        lines.append(f"{title}")
        # 2. 全体レジュメ
        lines.append("- 書籍全体のレジュメ")
        lines.append(resume_to_workflowy(resume_content, base_depth=1))
        # 3. 章ごとにループ
        for node in japanese_tree:
            lines.append(f"- {_sanitize_wf_text(node.text)}") # 章タイトル (Level 1)
            # 章レジュメ (Level 2)
            summary = node.metadata.get("summary", "")
            if summary:
                lines.append("\t- 章レジュメ")
                lines.append(resume_to_workflowy(summary, base_depth=2))
            
            # 並列展開
            for child in node.children:
                if child.text == "English text":
                    lines.append(f"\t- English text")
                    lines.append(tree_to_workflowy(child.children, base_depth=2))
                else:
                    # 翻訳セクション (Level 2 へ並列展開するために depth=1 で渡す)
                    # tree_to_workflowy は内部で各ノードを - text にするので、
                    # depth=1 で渡せば \t- text (Level 2) になる
                    lines.append(tree_to_workflowy([child], base_depth=1))
    else:
        # Paper Mode (並列)
        lines.append(f"{title}")
        lines.append("- レジュメ")
        lines.append(resume_to_workflowy(resume_content, base_depth=1))
        lines.append("- English text")
        lines.append(tree_to_workflowy(english_tree, base_depth=1))
        lines.append("- 日本語本文")
        # 重要: 日本語本文の各セクションは Level 1 (インデントなし) で並列させること。
        # 内部で tree_to_workflowy(japanese_tree, base_depth=0) を維持すること。
        lines.append(tree_to_workflowy(japanese_tree, base_depth=0))

    return "\n".join(lines)


def generate_resume_only_workflowy(
    title: str,
    resume_content: str,
    japanese_tree: List[TreeNode],
    is_book: bool = False,
) -> str:
    """Resume Only モード用の Workflowy 出力。"""
    if is_book:
        lines: List[str] = [
            f"{title}",
            "- 書籍全体のレジュメ",
            resume_to_workflowy(resume_content, base_depth=1),
        ]
        for node in japanese_tree:
            lines.append(f"- {_sanitize_wf_text(node.text)}")
            summary = node.metadata.get("summary", "")
            if summary:
                lines.append("\t- 章レジュメ")
                lines.append(resume_to_workflowy(summary, base_depth=2))
            
            if node.children:
                # 原文を表示
                lines.append("\t- English text")
                lines.append(tree_to_workflowy(node.children, base_depth=2))
    else:
        lines: List[str] = [
            f"{title}",
            "- レジュメ",
            resume_to_workflowy(resume_content, base_depth=1),
            "- 英語原文（セクション要約付き）",
            tree_to_workflowy(japanese_tree, base_depth=1)
        ]
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
    export_mode: str = "p2workflowy", # "p2workflowy" or "ronbunnihongo"
    resume_only: bool = False,
    is_book: bool = False, # ← 追加
) -> List[Path]:
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
    # タイトルからファイル名を生成（不適切な文字を置換）
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
    if not safe_title or safe_title == "Untitled":
        safe_title = input_path.stem
        if safe_title == "extracted_from_pdf":
            # もし一時的な名前なら、可能なら元のファイル名を使いたいが、
            # ここでは確実なタイトルを使う
            safe_title = "ronbun_result"
            
    stem = safe_title
    output_dir = input_path.parent
    
    output_paths = []

    print_log(f"  [Phase 5] ファイル出力開始: {stem} (Mode: {export_mode})")

    if export_mode == "p2workflowy":
        md_path = output_dir / f"{stem}_p2.md"
        wf_path = output_dir / f"{stem}_p2.txt"

        if resume_only:
            # Resume Only モード専用の生成
            md_content = generate_resume_only_output(title, resume_content, japanese_tree, is_book)
            wf_content = generate_resume_only_workflowy(title, resume_content, japanese_tree, is_book)
        else:
            # 通常モード (Bilingual)
            md_content = generate_markdown_output(title, resume_content, english_tree, japanese_tree, is_book)
            wf_content = generate_workflowy_output(title, resume_content, english_tree, japanese_tree, is_book)

        # 保存
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print_log(f"  [Phase 5] Markdown 保存完了: {md_path.name}")
        output_paths.append(md_path)

        with open(wf_path, "w", encoding="utf-8") as f:
            f.write(wf_content)
        print_log(f"  [Phase 5] Workflowy (txt) 保存完了: {wf_path.name}")
        output_paths.append(wf_path)

    elif export_mode == "ronbunnihongo":
        rn_path = output_dir / f"{stem}_ronbun.md"
        # RonbunNihongo 生成と保存
        rn_content = generate_ronbun_nihongo_output(title, japanese_tree)
        with open(rn_path, "w", encoding="utf-8") as f:
            f.write(rn_content)
        print_log(f"  [Phase 5] RonbunNihongo 保存完了: {rn_path.name}")
        output_paths.append(rn_path)

    return output_paths