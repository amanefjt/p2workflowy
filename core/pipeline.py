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
from .phase1_preprocessor import run_phase1_unified
from .phase2_meta import run_phase2_v3 as run_phase2
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
    max_concurrent_sections: int = 4,
    cleanup_sessions: bool = True,
    state_base_dir: Optional[Path] = None,
) -> List[Path]:
    """パイプライン全体を実行する。"""
    from .llm_client import tier_manager, GeminiTier, reset_pipeline_state
    # 新しいパイプライン開始時に AsyncLimiter キャッシュと TierManager 状態をリセットする。
    # AsyncLimiter は生成時のイベントループに紐付くため、前回実行分を引き継ぐと
    # "Future attached to a different loop" エラーが発生する。
    reset_pipeline_state()
    tier_manager.set_tier(GeminiTier.FREE if tier == "free" else GeminiTier.PAID)

    if simple_mode:
        print_log(f"  [Pipeline] Simple Mode Enabled for: {title}")

    if api_key is None:
        from .config import GEMINI_API_KEY
        api_key = GEMINI_API_KEY

    start_phase = resume_from or 1
    state = SessionState(session_id=session_id, base_dir=state_base_dir, mode="book" if is_book else "paper")
    original_input_path = input_path

    # Track if the caller explicitly provided a title (e.g. BookManager passing chapter titles)
    explicit_title = title is not None
    if title is None:
        title = Path(input_path).stem

    print_log(f"=== p2workflowy V2 Pipeline ===")

    # --- Pre-flight Check: PDF Quality Diagnostic ---
    if input_path.lower().endswith(".pdf"):
        if pdf_mode == "full_vlm":
            print_log("  [Pipeline] Route C (full_vlm) が明示的に指定されています。")
        else:
            from .engine.p1_ingest.pdf_ingester import diagnose_pdf_quality
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
            run_phase1_unified(
                input_path, state.phase1_preprocessor, api_key=api_key,
                state=state, pdf_mode=pdf_mode, is_book=is_book,
                heavy_ocr=heavy_ocr, max_pages=max_pages,
            )
            print_log(f"  完了: Phase 1 解析完了\n")

    # --- Phase 2: Meta-Generation ---
    meta: dict = {}
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
                meta = run_phase2(
                    state.phase1_preprocessor, state.phase2_meta, glossary_path,
                    api_key=api_key, expertise=expertise, model=model,
                    thinking_level=thinking_level, state=state, is_book=is_book,
                    resume_content=resume_content,
                )
        if meta and "dna" in meta and meta["dna"].get("title") and not explicit_title:
            title = meta["dna"]["title"]

    # --- Phase 3: Structural Skeleton Construction ---
    if start_phase <= 3:
        if state.phase3_structure.exists() and state.phase3_sections.exists():
            print_log(f"  [Pipeline] Phase 3 (Structure) already finished. Skipping.")
            tree = json.loads(state.phase3_structure.read_text(encoding="utf-8"))
        else:
            state.update_status("構造を構築中...", 60)
            phase3_input = original_input_path if is_book else input_path
            tree, sections = run_phase3(
                state.phase1_preprocessor,
                state.phase2_meta,
                state.phase3_structure,
                state.phase3_sections,
                state=state,
                is_book=is_book,
                api_key=api_key,
                model=model,
                input_path=phase3_input,
            )

            # simple_mode（Preface / Coda 等）で 0 セクションが返った場合のフォールバック。
            # Phase 2 がスタブのため見出しリストが空になり、全チャンクが未帰属になる。
            # この場合は Phase 1 の全チャンクを単一セクションとしてまとめ直す。
            if simple_mode and len(tree) == 0:
                from .models import load_chunks_from_json, TreeNode
                raw_chunks = load_chunks_from_json(str(state.phase1_preprocessor))
                body_chunks = [c for c in raw_chunks if c.text.strip()]
                section_id = "simple_body"
                section_node = TreeNode(
                    id=section_id, text=title, role="h2", seq_index=0.0,
                    children=[
                        TreeNode(id=c.id, text=c.text, role="p", seq_index=c.seq_index)
                        for c in body_chunks
                    ],
                )
                tree = [section_node]
                sections = {
                    f"{section_id}|{title}": [{"text": c.text, "id": c.id} for c in body_chunks]
                }
                from .models import save_tree_to_json
                save_tree_to_json(tree, str(state.phase3_structure))
                with open(state.phase3_sections, "w", encoding="utf-8") as _f:
                    json.dump(sections, _f, ensure_ascii=False, indent=2)
                print_log(f"  [Simple Mode] {len(body_chunks)} チャンクを単一セクションとして構成")

            print_log(f"  [Phase 3] 構造化完了: {len(tree)} セクション")

        if structure_only:
            if cleanup_sessions:
                state.cleanup_old_sessions()
            return []

    # --- Phase 4: Sliding-Window Translation ---
    if start_phase <= 4:
        if state.phase4_translate.exists():
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
                book_resume=(resume_content or "") if is_book else "",
                pdf_mode=pdf_mode,
                max_concurrent_sections=max_concurrent_sections,
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

    if cleanup_sessions:
        state.cleanup_old_sessions()
    print_log("=== Pipeline 完了 ===")
    return output_paths
