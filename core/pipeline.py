"""
p2workflowy V2: パイプライン・オーケストレーター
各フェーズを順次実行し、state/ に中間データを保存する。
"""

from pathlib import Path
from typing import Optional

from .config import (
    SessionState,
    print_log,
)
from .phase1_preprocess import run_phase1
from .phase2_meta import run_phase2
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
) -> None:
    """
    パイプライン全体を実行する。

    Args:
        input_path: 入力テキストファイルのパス
        glossary_path: glossary.csv のパス（省略時はデフォルト）
        title: 論文タイトル（省略時はファイル名から推定）
        resume_from: 再開するフェーズ番号（1-5）。省略時はフェーズ1から実行。
    """
    start_phase = resume_from or 1
    state = SessionState(input_path, session_id=session_id)

    if title is None:
        title = Path(input_path).stem

    print_log(f"=== p2workflowy V2 Pipeline ===")
    print_log(f"  入力ファイル: {input_path}")
    print_log(f"  タイトル: {title}")
    print_log(f"  開始フェーズ: {start_phase}")
    print_log(f"  Stateディレクトリ: {state.session_dir}")
    print_log()

    # --- Phase 0: PDF Ingestion (VLM OCR) ---
    if input_path.lower().endswith(".pdf"):
        print_log("--- Phase 0: PDF Ingestion (VLM OCR) ---")
        from .pdf_ingester import run_pdf_ingestion
        pdf_text = run_pdf_ingestion(input_path, api_key=api_key)
        
        extracted_path = state.session_dir / "extracted_from_pdf.txt"
        extracted_path.write_text(pdf_text, encoding="utf-8")
        input_path = str(extracted_path)
        print_log(f"  完了: PDFから {len(pdf_text)} 文字を抽出。入力を {input_path} に切り替えます。\n")

    # --- Phase 1: Ingest & Preprocess ---
    if start_phase <= 1:
        print_log("--- Phase 1: Ingest & Preprocess ---")
        chunks = run_phase1(input_path, state.phase1, glossary_path)
        print_log(f"  完了: {len(chunks)} チャンクを処理\n")

    # --- Phase 2: Meta-Generation ---
    if start_phase <= 2:
        print_log("--- Phase 2: Meta-Generation ---")
        meta = run_phase2(state.phase1, state.phase2, glossary_path, api_key=api_key, expertise=expertise, model=model, thinking_level=thinking_level)
        print_log(f"  完了: レジュメ {len(meta['resume_content'])} 文字, キーワード {len(meta['keywords_data'])} 件\n")

    # --- Phase 3: Structuring & Clipping ---
    if start_phase <= 3:
        print_log("--- Phase 3: Structuring & Clipping ---")
        tree, sections = run_phase3(state.phase1, state.phase2, state.phase3_structure, state.phase3_sections)
        print_log(f"  完了: {len(tree)} セクション\n")

    # --- Phase 4: Sliding-Window Translation ---
    if start_phase <= 4:
        print_log("--- Phase 4: Sliding-Window Translation ---")
        japanese_tree = run_phase4(state.phase2, state.phase3_structure, state.phase3_sections, state.phase4, glossary_path, api_key=api_key, expertise=expertise, model=model, thinking_level=thinking_level)
        print_log(f"  完了: {len(japanese_tree)} セクション翻訳完了\n")

    # --- Phase 5: Export ---
    if start_phase <= 5:
        print_log("--- Phase 5: Export ---")
        output_paths = run_phase5(input_path, title, state.phase2, state.phase3_structure, state.phase4, export_mode=export_mode)
        print_log(f"  完了: 出力ファイル作成済 (計 {len(output_paths)} 件)\n")
        for p in output_paths:
            print_log(f"    - {p}")
        print_log()

    print_log("=== Pipeline 完了 ===")
