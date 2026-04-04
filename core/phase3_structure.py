"""
p2workflowy 黄金の再構築: Phase 3 Structure Orchestrator
モノリスからアトミック・エンジン構成への移行。
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any

from core.models import RawChunk, TreeNode, ProcessingContext, save_tree_to_json, load_chunks_from_json
from core.config import load_coreprompts, print_log
from core.engine.p3_structure.heading_detector import HeadingDetector
from core.engine.p3_structure.toc_manager import TOCManager
from core.engine.p3_structure.tree_constructor import TreeConstructor

def run_phase3(
    input_path: str | Path,
    state: Any = None,
    api_key: str | None = None,
    model: str | None = None,
    save_state: bool = True,
) -> Tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    Phase 3: 階層構造化エンジン (Orchestrator)
    """
    print_log(f"\n[Phase 3] 構造化プロセスを開始します: {Path(input_path).name}")
    
    # 1. コンテキストとデータのロード
    input_path = Path(input_path)
    is_book = state.mode == "book" if state else True
    structure_state_path = state.phase3_structure if state else input_path.parent / "phase3_structure.json"
    sections_state_path = structure_state_path.with_name("phase3_sections.json")
    
    chunks = load_chunks_from_json(state.phase1_preprocessor)
    nodes = [TreeNode(id=c.id, text=c.text, font_size=c.font_size, is_bold=c.is_bold, 
                      is_italic=c.is_italic, font_name=c.font_name, seq_index=c.seq_index,
                      role=c.role) for c in chunks]

    # 2. アトミック・エンジンの初期化
    prompts = load_coreprompts()
    toc_mgr = TOCManager(api_key=api_key, model=model)
    
    # 本文フォントサイズの自動補正 (Physical Sovereignty)
    body_size = HeadingDetector.compute_body_font_size(chunks)
    print_log(f"  [Phase 3] 本文推定フォントサイズ: {body_size} pt")
    
    detector = HeadingDetector(body_font_size=body_size)
    exclude_keywords = prompts.get("EXCLUDE_SECTION_KEYWORDS", [])
    constructor = TreeConstructor(detector, is_book=is_book, exclude_keywords=exclude_keywords)
    
    # 3. 目次（TOC）とページオフセットの取得
    # 論文モードの場合、Phase 2 のレジュメから抽出した見出しを優先利用する
    toc = []
    body_start_page = 1
    
    if not is_book and state and state.phase2_meta.exists():
        try:
            with open(state.phase2_meta, "r", encoding="utf-8") as f:
                p2_data = json.load(f)
                resume_text = p2_data.get("resume_content", "")
                if resume_text:
                    p2_headings = toc_mgr.extract_headings_from_resume(resume_text)
                    if p2_headings:
                        print_log(f"  [Phase 3] Phase 2 レジュメから {len(p2_headings)} 個の見出しを抽出しました。")
                        toc = [{"title": h, "page": -1} for h in p2_headings]
        except Exception as e:
            print_log(f"  [Warning] Phase 2 メタデータの読み込みに失敗しました: {e}")

    # TOC がまだ空の場合（または Book Mode の場合）は既存の TOCManager を使用
    if not toc:
        toc_data = toc_mgr.get_toc(state, input_path, chunks, is_book=is_book)
        toc = toc_data.get("toc", [])
        body_start_page = int(toc_data.get("body_start_page", 1))
    
    # 4. 構造化の実行 (Unified Path)
    # 本の章も論文と同様の「チャンクベースの論理構造化」に一本化する
    print_log(f"  [Phase 3] 論理構造化を実行中... (is_book={is_book})")
    
    # TOC タイトルを Detector に登録し、VLM の先行知見を構造化に活かす
    detector.headings = [e["title"] for e in toc]
    tree, sections_dict = constructor.construct(nodes)

    # 5. ステートの保存
    if save_state:
        save_tree_to_json(tree, str(structure_state_path))
        with open(sections_state_path, "w", encoding="utf-8") as f:
            json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            
    print_log(f"  [Phase 3] 構造化完了: {len(tree)} ルートノード, {len(sections_dict)} セクション")
    return tree, sections_dict

# --- 互換用ラッパー (Shims for Phase 4/5) ---

def extract_headings_from_resume(resume_text: str) -> List[str]:
    """旧インターフェース互換用: レジュメから見出しを抽出"""
    return TOCManager().extract_headings_from_resume(resume_text)

def structure_nodes_by_headings(
    nodes: List[TreeNode], 
    headings: List[str], 
    exclude_keywords: List[str] = None,
    is_book: bool = False
) -> Tuple[List[TreeNode], Dict[str, List[dict]]]:
    """旧インターフェース互換用: チャンクを見出しに基づいて構造化"""
    detector = HeadingDetector()
    detector.headings = headings
    constructor = TreeConstructor(detector, is_book=is_book)
    return constructor.construct(nodes)

if __name__ == "__main__":
    # 簡易テスト用
    pass