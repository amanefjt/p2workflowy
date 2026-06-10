"""
p2workflowy V2 Phase 3: Structuring & Clipping
Pre-scanner + Section Detector + 除外クリッピング → 英語ツリー構築。
indi_pre_scanner.md / indi_section_detector.md に完全準拠。
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import fitz  # PyMuPDF

import statistics

from .config import (print_log, 
    load_coreprompts,
)
from .models import RawChunk, TreeNode, load_chunks_from_json, save_tree_to_json
from .text_utils import _SENTENCE_END_RE, _TRAILING_WORDS
from .engine.p3_structure.heading_matcher import (
    normalize_heading,
    is_excluded_heading,
    match_heading,
    extract_headings_from_resume,
)
from .engine.p3_structure.tree_builder import (
    structure_nodes_by_headings,
    build_tree,
    structure_nodes_by_markdown,
)
from .engine.p3_structure.toc_extractor import (
    extract_toc_via_llm,
    extract_toc_from_chunks,
    apply_toc_titles,
    _should_join_lines,
    _matches_toc_entry,
)


# ============================================================
# 6. Pipeline Phase Execution
# ============================================================

def run_phase3(
    phase1_state_path: str | Path,
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    save_state: bool = True,
    state: "Any" = None,
    is_book: bool = False,
    api_key: str | None = None,
    model: str | None = None,
    input_path: str | Path | None = None,
    pdf_mode: str = "hybrid",
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """Phase 3 メイン処理."""

    intro_pre_heading = None  # Paper Mode で DNA から取得、Book Mode では使わない
    chunks = load_chunks_from_json(str(phase1_state_path))
    
    # --- Route C: VLM Markdown 構造化 (pdf_mode == "full_vlm" かつ Markdown見出しが存在する場合) ---
    if pdf_mode == "full_vlm":
        has_markdown_headers = any(re.match(r'^#\s+', c.text.strip()) for c in chunks)
        if has_markdown_headers:
            print_log("  [Phase 3] Route C: VLM Markdown Mode (正規表現パース) を実行します")
            toc_list = []
            if is_book:
                toc_path = Path(structure_state_path).parent / "phase3_toc.json"
                if toc_path.exists():
                    with open(toc_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                        toc_list = [entry["title"] for entry in cached_data.get("toc", [])]
                else:
                    toc_list = extract_toc_from_chunks(chunks, api_key=api_key, model=model)

            tree, sections_dict = structure_nodes_by_markdown(chunks, is_book=is_book, toc_list=toc_list)
            if save_state:
                save_tree_to_json(tree, str(structure_state_path))
                with open(sections_state_path, "w", encoding="utf-8") as f:
                    json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            return tree, sections_dict
        else:
            print_log("  [Phase 3] pdf_mode='full_vlm' ですが Markdown 見出しが未検出です。標準構造化へフォールバックします。")

    anchors = {"metadata_ids": []}
    headings = []
    exclude_keywords = []

    if is_book and input_path:
        print_log("  [Phase 3] Book Mode (PyMuPDF + TOC補正)")

        toc_cache_path = Path(structure_state_path).parent / "phase3_toc.json"

        # TOCキャッシュの確認
        toc_data = None
        if toc_cache_path.exists():
            try:
                with open(toc_cache_path) as f:
                    cached = json.load(f)
                # 新しい形式 (tocリストがある) か確認
                if isinstance(cached.get("toc"), list) and len(cached["toc"]) > 0:
                    toc_data = cached
                    print_log(f"  [Phase 3] TOCキャッシュ使用: {len(toc_data['toc'])}件")
            except Exception as e:
                print_log(f"  [Phase 3] TOCキャッシュ読み込み失敗: {e}")

        # TOCキャッシュがない場合のみ LLM でTOC抽出
        if toc_data is None:
            if not api_key:
                print_log("  [Phase 3] APIキーがないためTOC抽出をスキップします")
                toc_data = {"toc": [], "body_start_page": 1}
            else:
                print_log("  [Phase 3] LLMで目次を抽出します...")
                # 同期呼び出し（awaitなし）
                extracted = extract_toc_via_llm(input_path, api_key=api_key, model=model, state=state)
                # キー名の不一致を吸収 (toc_titles or toc)
                toc = extracted.get("toc_titles") or extracted.get("toc", [])
                body_start_page = extracted.get("body_start_page", 1)
                toc_data = {"toc": toc, "body_start_page": body_start_page}
                
                # キャッシュ保存
                try:
                    with open(toc_cache_path, "w", encoding="utf-8") as f:
                        json.dump(toc_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print_log(f"  [Phase 3] TOCキャッシュ保存失敗: {e}")

        # 取得（キャッシュ経由またはLLM直後）
        body_start_page = int(toc_data.get("body_start_page", 1))
        toc = toc_data.get("toc", [])
        page_offset = body_start_page - 1  # PDF物理ページ - 書籍ページ = offset

        # ChapterParser で章境界を抽出（TOC補正・ノイズフィルタ・ChapterBoundary変換を含む）
        from .engine.p3_structure.chapter_parser import ChapterParser
        boundaries = ChapterParser().parse(
            input_path=input_path,
            body_start_page=body_start_page,
            toc=toc,
            page_offset=page_offset,
        )

        anchors = {"chapters": boundaries}
        chunks = []
        headings = []
        exclude_keywords = []
        dna = {}
        intro_pre_heading = None

    else:
        # Paper Mode
        chunks = load_chunks_from_json(str(phase1_state_path))
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        resume_content = meta.get("resume_content", "")

        # アンカー検知によるスキップを廃止し、レジュメの見出しリストを唯一の基準にする
        anchors = {"metadata_ids": []}
        headings = extract_headings_from_resume(resume_content)

        # 【重要】Abstract を見出し候補の先頭に強制追加（論文モードの標準構成を保証）
        if "Abstract" not in headings and "abstract" not in [h.lower() for h in headings]:
            headings.insert(0, "Abstract")

        prompts = load_coreprompts()
        exclude_keywords = prompts.get("EXCLUDE_SECTION_KEYWORDS", [])

        # DNA の intro_pre_heading を取得（見出しなし Introduction の独立セクション化に使用）
        dna = meta.get("dna", {})
        intro_pre_heading = dna.get("intro_pre_heading") or None

    tree, sections_dict = build_tree(
        chunks, anchors, headings, exclude_keywords,
        is_book=is_book,
        intro_pre_heading=intro_pre_heading if not is_book else None,
        dna=dna if not is_book else None,
    )
    
    if save_state:
        save_tree_to_json(tree, str(structure_state_path))
        with open(sections_state_path, "w", encoding="utf-8") as f:
            json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            
    return tree, sections_dict
