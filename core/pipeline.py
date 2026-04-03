"""
p2workflowy V2: パイプライン・オーケストレーター
各フェーズ（Ingest, Meta, Structure, Translate, Export）を順次実行し、state/ に中間データを保存する。
"""

from pathlib import Path
from typing import Optional, List, Any
import json

from .config import (
    SessionState,
    print_log,
)
from .phase1_preprocessor import run_phase1, run_phase1_v3
from .phase2_meta import run_phase2, run_phase2_v3
from .phase3_structure import run_phase3
from .phase4_translate import run_phase4
from .phase5_export import run_phase5


def run_pipeline(
    input_path: str,
    glossary_path: str | None = None,
    title: str | None = None,
    resume_from: int | None = None,
    api_key: str | None = None,
    session_id: str | None = None,
    expertise: str = "文化人類学",
    export_mode: str = "p2workflowy",
    model: str | None = None,
    thinking_level: str = "High",
    pdf_mode: str = "hybrid",
    tier: str = "paid",
    is_book: bool = False,
    structure_only: bool = False,
    resume_only: bool = False,
    heavy_ocr: bool = False,
    max_pages: Optional[int] = None,
    resume_content: Optional[str] = None,
    simple_mode: bool = False,
) -> List[Path]:
    """パイプライン全体を実行する。"""
    if simple_mode:
        print_log(f"  [Pipeline] Simple Mode Enabled for: {title}")

    if api_key is None:
        from .config import GEMINI_API_KEY
        api_key = GEMINI_API_KEY

    start_phase = resume_from or 1
    state = SessionState(session_id=session_id, mode="book" if is_book else "paper")
    original_input_path = input_path

    if title is None:
        title = Path(input_path).stem

    print_log(f"=== p2workflowy V2 Pipeline ===")

    # --- Pre-flight Check: PDF Quality Diagnostic ---
    if input_path.lower().endswith(".pdf"):
        if pdf_mode == "full_vlm":
            print_log("  [Pipeline] Route C (full_vlm) が明示的に指定されています。")
        else:
            from .pdf_ingester import diagnose_pdf_quality
            is_clean = diagnose_pdf_quality(input_path)
            if not is_clean:
                print_log("  [Warning] PDF破損検知。Route C (Full VLM) を適用。")
                pdf_mode = "full_vlm"
            else:
                print_log(f"  [Pipeline] PDF品質良好。ハイブリッドモード続行。")

    # --- Phase 1: Preprocessor ---
    if start_phase <= 1:
        if state.phase1_preprocessor.exists():
            print_log(f"  [Pipeline] Phase 1 (Preprocessor) already finished. Skipping.")
        else:
            state.update_status("テキストの準備中...", 20)
            from .phase1_preprocessor import run_phase1_v3
            run_phase1_v3(
                input_path, state.phase1_preprocessor, api_key=api_key, 
                state=state, pdf_mode=pdf_mode, is_book=is_book, 
                heavy_ocr=heavy_ocr, max_pages=max_pages
            )
            print_log(f"  完了: Phase 1 解析完了\n")

    # --- Phase 2: Meta-Generation ---
    if start_phase <= 2:
        if state.phase2_meta.exists():
            print_log(f"  [Pipeline] Phase 2 (Meta) already finished. Skipping.")
            meta_json = state.phase2_meta.read_text(encoding="utf-8")
            meta = json.loads(meta_json)
        else:
            state.update_status("内容を分析中...", 40)
            if simple_mode:
                meta = {"dna": {"title": title, "keywords": []}, "resume_content": "Simple Mode"}
                state.phase2_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                meta = run_phase2_v3(
                    state.phase1_preprocessor, state.phase2_meta, glossary_path, 
                    api_key=api_key, expertise=expertise, model=model, 
                    thinking_level=thinking_level, state=state, is_book=is_book,
                    resume_content=resume_content
                )
        if meta and "dna" in meta and meta["dna"].get("title"):
            title = meta["dna"]["title"]

    # --- Phase 3: Structural Skeleton Construction ---
    if start_phase <= 3:
        if state.phase3_structure.exists() and state.phase3_sections.exists():
            print_log(f"  [Pipeline] Phase 3 (Structure) already finished. Skipping.")
            tree = json.loads(state.phase3_structure.read_text(encoding="utf-8"))
        else:
            state.update_status("構造を構築中...", 60)
            from .phase3_structure import run_phase3
            phase3_input = original_input_path if is_book else input_path
            tree, sections = run_phase3(phase3_input, state=state, api_key=api_key, model=model)
            print_log(f"  [Phase 3] 構造化完了: {len(tree)} セクション")

        if structure_only:
            state.cleanup_old_sessions()
            return []

    # --- Phase 4: Sliding-Window Translation ---
    if start_phase <= 4:
        if state.phase4_translate.exists() and not resume_only:
            print_log(f"  [Pipeline] Phase 4 (Translate) already finished. Skipping.")
            from .models import TreeNode
            japanese_tree = [TreeNode.from_dict(d) for d in json.loads(state.phase4_translate.read_text(encoding="utf-8"))]
        else:
            state.update_status("本文を翻訳中...", 70)
            print_log("--- Phase 4: Sliding-Window Translation ---")
            japanese_tree = run_phase4(
                phase2_state_path=state.phase2_meta,
                structure_state_path=state.phase3_structure,
                sections_state_path=state.phase3_sections,
                phase4_state_path=state.phase4_translate,
                glossary_path=glossary_path,
                api_key=api_key,
                expertise=expertise,
                model=model,
                thinking_level=thinking_level,
                state=state,
                tier=tier,
                resume_only=resume_only,
                is_book=is_book,
                pdf_mode=pdf_mode,
            )
            print_log(f"  完了: {len(japanese_tree)} セクション翻訳完了\n")

    # --- Phase 5: Export ---
    output_paths = []
    if start_phase <= 5:
        state.update_status("最終ファイルを作成中...", 95)
        print_log("--- Phase 5: Export ---")
        output_paths = run_phase5(
            original_input_path, title, state.phase2_meta, state.phase3_structure, state.phase4_translate, 
            export_mode=export_mode, resume_only=resume_only, is_book=is_book,
        )
        print_log(f"  完了: 出力ファイル作成済\n")

    state.cleanup_old_sessions()
    print_log("=== Pipeline 完了 ===")
    return output_paths
