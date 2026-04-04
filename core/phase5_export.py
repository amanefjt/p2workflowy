"""
p2workflowy V2 Phase 5: Export Orchestrator
300行ルールに従い、詳細なレンダリングロジックをサブエンジンへ委譲。
"""

import json
import re
from pathlib import Path
from typing import List, Any

from .config import print_log
from .models import TreeNode

# サブエンジンのインポート
from .engine.p5_export.formatter_utils import clean_heading_text, format_resume_markdown
from .engine.p5_export.markdown_engine import MarkdownEngine
from .engine.p5_export.workflowy_engine import WorkflowyEngine

def run_phase5(
    input_path_str: str,
    title: str,
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    phase4_state_path: str | Path,
    export_mode: str = "p2workflowy", # "p2workflowy" or "ronbunnihongo"
    resume_only: bool = False,
    is_book: bool = False,
) -> List[Path]:
    """Phase 5 メイン処理（オーケストレーター）。"""
    input_path = Path(input_path_str)
    print_log(f"\n[Phase 5] エクスポートを開始します: {title} (is_book={is_book})")

    # 1. データのロード
    with open(phase2_state_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    resume_content = meta.get("resume_content", "")

    with open(structure_state_path, "r", encoding="utf-8") as f:
        english_tree = [TreeNode.from_dict(d) for d in json.load(f)]

    with open(phase4_state_path, "r", encoding="utf-8") as f:
        japanese_tree = [TreeNode.from_dict(d) for d in json.load(f)]

    # 1.5 脚注の集約ロジック (Endnotes形式: 各言語ブロックの末尾に配置)
    def _reposition_notes(tree: List[TreeNode], label: str) -> List[TreeNode]:
        notes = []
        def _process(nodes: List[TreeNode]):
            to_remove = []
            for n in nodes:
                if n.children:
                    _process(n.children)
                is_note = n.role == "note" or (n.text and "[note]" in n.text.lower())
                if is_note:
                    notes.append(n)
                    to_remove.append(n)
            # id() ベースのセットで判定: text が同じ複数ノードの誤削除を防ぐ
            to_remove_ids = set(id(n) for n in to_remove)
            nodes[:] = [n for n in nodes if id(n) not in to_remove_ids]
        
        _process(tree)
        if notes:
            # 注釈が存在する場合、ツリーの末尾に [Notes] ブロックを追加
            note_node = TreeNode(
                id=f"notes_{label}", text=label, 
                role="h3", # 本文の見出し(h2)の下にぶら下がるため h3
                children=notes
            )
            tree.append(note_node)
            print_log(f"  [Phase 5] {len(notes)} 件の注釈を収集し、{label} として末尾に配置しました。")
        return tree

    english_tree = _reposition_notes(english_tree, "[Notes]")
    japanese_tree = _reposition_notes(japanese_tree, "[注釈]")

    # 2. エンジンの初期化
    md_engine = MarkdownEngine(base_level=2)
    wf_engine = WorkflowyEngine(base_depth=0)

    # 3. コンテンツ生成
    md_content = ""
    wf_content = ""
    # ステムを作成
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", title) or input_path.stem
    output_dir = input_path.parent
    output_paths = []

    # Paper Mode (Stage 1) のみの処理とする (Book 統合は StateIntegrator が担当)
    # NOTE: Book Mode も含め、すべての章を Paper Mode（論文形式）として処理する。
    # Book 全体の統合は StateIntegrator が別途担当する（意図的な統一処理アーキテクチャ）。
    if True:
        # Paper Mode: 論文形式
        if export_mode == "p2workflowy":
            if resume_only:
                # 要約 + 原文 (English) のみ
                md_parts = [
                    f"# {title}", "",
                    "## [Chapter Summary]", "",
                    format_resume_markdown(resume_content, shift=2), "",
                    "## [English Text]", "",
                    md_engine.render(english_tree, current_base=3)
                ]
                md_content = "\n".join(md_parts)
                
                wf_parts = [
                    f"{title}",
                    "- [Chapter Summary]",
                    wf_engine.render_resume(resume_content, base_depth=1),
                    "- [English Text]",
                    wf_engine.render(english_tree, current_depth=1)
                ]
                wf_content = "\n".join(wf_parts)
            else:
                # 二言語出力 (通常)
                md_parts = [f"# {title}", ""]
                # レジュメ（空でない場合のみ追加）
                if resume_content and resume_content.strip() not in ["", "Simple Mode", "...", "None"]:
                    md_parts.extend(["## レジュメ", "", format_resume_markdown(resume_content, shift=2), ""])
                
                md_parts.extend([
                    "## English text", "", 
                    md_engine.render(english_tree, current_base=3), "",
                    "## 日本語本文", "",
                    md_engine.render(japanese_tree, current_base=2)
                ])
                
                md_content = "\n".join(md_parts)
                
            # 4. Workflowy 構成
            wf_parts = [f"{title}"]
            # レジュメ（空でない場合のみ追加）
            if resume_content and resume_content.strip() not in ["", "Simple Mode", "...", "None"]:
                wf_parts.append("- レジュメ")
                wf_parts.append(wf_engine.render_resume(resume_content, base_depth=1))
            
            wf_parts.append("- English text")
            wf_parts.append(wf_engine.render(english_tree, current_depth=1))
            wf_parts.append("- 日本語本文")
            wf_parts.append(wf_engine.render(japanese_tree, current_depth=0))
            
            wf_content = "\n".join(wf_parts)
        elif export_mode == "ronbunnihongo":
            md_content = f"# {title}\n\n" + md_engine.render(japanese_tree, current_base=2)

    # 4. 書き出し
    if md_content:
        # 出力ディレクトリの決定 (Book Mode の場合は [InputName]_export/)
        if is_book:
            export_dir = output_dir / f"{safe_title}_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            path = export_dir / f"{safe_title}_p2.md"
        else:
            suffix = "_p2.md" if export_mode == "p2workflowy" else "_ronbun.md"
            path = output_dir / f"{safe_title}{suffix}"

        # 3つ以上の改行を2つに圧縮
        md_content = re.sub(r'\n{3,}', '\n\n', md_content).strip() + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(md_content)
        output_paths.append(path)
        print_log(f"  [Phase 5] Markdown 保存完了: {path.name}")

    if wf_content and export_mode == "p2workflowy":
        if is_book:
            export_dir = output_dir / f"{safe_title}_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            path = export_dir / f"{safe_title}_p2.txt"
        else:
            path = output_dir / f"{safe_title}_p2.txt"

        with open(path, "w", encoding="utf-8") as f:
            f.write(wf_content)
        output_paths.append(path)
        print_log(f"  [Phase 5] Workflowy 保存完了: {path.name}")

    return output_paths