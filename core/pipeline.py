"""
p2workflowy V2: パイプライン・オーケストレーター
各フェーズ（Ingest, Meta, Structure, Translate, Export）を順次実行し、state/ に中間データを保存する。
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
    pdf_mode: str = "hybrid",
    tier: str = "paid",
    is_book: bool = False,
    structure_only: bool = False,
    resume_only: bool = False,
    heavy_ocr: bool = False,      # ← 追加
) -> None:
    """
    パイプライン全体を実行する。

    Args:
        input_path: 入力テキストファイルのパス
        glossary_path: glossary.csv のパス（省略時はデフォルト）
        title: 論文タイトル（省略時はファイル名から推定）
        resume_from: 再開するフェーズ番号（1-5）。省略時はフェーズ1から実行。
    """
    if api_key is None:
        from .config import GEMINI_API_KEY
        api_key = GEMINI_API_KEY

    start_phase = resume_from or 1
    state = SessionState(input_path, session_id=session_id)
    original_input_path = input_path

    if title is None:
        title = Path(input_path).stem

    print_log(f"=== p2workflowy V2 Pipeline ===")
    print_log(f"  入力ファイル: {input_path}")
    print_log(f"  タイトル: {title}")
    print_log(f"  開始フェーズ: {start_phase}")
    print_log(f"  Stateディレクトリ: {state.session_dir}")
    print_log()

    # --- Pre-flight Check: PDF Quality Diagnostic ---
    # ユーザーが明示的に Route C (full_vlm) を指定していない場合、テキスト抽出品質を事前診断する
    if input_path.lower().endswith(".pdf"):
        if pdf_mode == "full_vlm":
            print_log("  [Pipeline] Route C (full_vlm) が明示的に指定されています。診断をスキップします。")
        else:
            from .pdf_ingester import diagnose_pdf_quality
            print_log("  [Pipeline] PDFのテキスト品質を診断中 (Pre-flight Check)...")
            is_clean = diagnose_pdf_quality(input_path)
            if not is_clean:
                print_log("  [Warning] PDFのテキスト形式に致命的な破損（またはノイズ）を検知しました。")
                print_log("  [Warning] 安全のため、一貫して Route C (Full VLM Extraction) で処理を行う「Bipolar Routing」を適用します。")
                pdf_mode = "full_vlm"
            else:
                mode_desc = "Hybrid (Python+VLM)" if not is_book else "Pure Python (Bipolar)"
                print_log(f"  [Pipeline] PDFのテキスト品質は良好です。{mode_desc} モードで続行します。")

    # オリジナルのパスを保持しておく（Phase 5 の出力先決定用）
    original_input_path = input_path

    # --- Phase 0: Document Ingestion (PDF / Docx) ---
    if start_phase <= 1:
        if input_path.lower().endswith(".pdf"):
            from .pdf_ingester import run_pdf_ingestion_async
            from .llm_client import run_async
            
            extracted_path = state.session_dir / "extracted_from_pdf.txt"
            if extracted_path.exists():
                print_log(f"  [Phase 0] 既存の PDF 抽出テキストを使用します: {extracted_path}")
                pdf_text = extracted_path.read_text(encoding="utf-8")
            else:
                pdf_text = run_async(run_pdf_ingestion_async(
                    input_path, api_key=api_key, state=state, 
                    pdf_mode=pdf_mode, model=model,
                    is_book=is_book, heavy_ocr=heavy_ocr
                ))
                extracted_path.write_text(pdf_text, encoding="utf-8")
            
            input_path = str(extracted_path)
            print_log(f"  完了: PDFから {len(pdf_text)} 文字を抽出。入力を {input_path} に切り替えます。\n")
        elif input_path.lower().endswith(".docx"):
            try:
                import docx
                print_log(f"  [Phase 0] Wordファイル (.docx) を読み込み中...")
                doc = docx.Document(input_path)
                # 全段落のテキストを結合
                docx_text = "\n".join([para.text for para in doc.paragraphs])
                
                extracted_path = state.session_dir / "extracted_from_docx.txt"
                extracted_path.write_text(docx_text, encoding="utf-8")
                input_path = str(extracted_path)
                print_log(f"  完了: docxから {len(docx_text)} 文字を抽出。入力を {input_path} に切り替えます。\n")
            except ImportError:
                print_log("  エラー: .docx ファイルを処理するには 'python-docx' ライブラリが必要です。")
                raise Exception("python-docx not installed")
            except Exception as e:
                print_log(f"  エラー: Wordファイルの読み込み中にエラーが発生しました: {e}")
                raise e
    else:
        # 再開モードかつ入力が PDF/Docx の場合、前回の抽出結果があればそれを利用
        if input_path.lower().endswith(".pdf"):
            extracted_path = state.session_dir / "extracted_from_pdf.txt"
            if extracted_path.exists():
                input_path = str(extracted_path)
                print_log(f"  [Resume] 既存の PDF 抽出テキストを使用します: {input_path}")
        elif input_path.lower().endswith(".docx"):
            extracted_path = state.session_dir / "extracted_from_docx.txt"
            if extracted_path.exists():
                input_path = str(extracted_path)
                print_log(f"  [Resume] 既存の Docx 抽出テキストを使用します: {input_path}")

    # --- Phase 1: Ingest & Preprocess ---
    if start_phase <= 1:
        state.update_status("テキストの準備中...", 20)
        print_log("--- Phase 1: Ingest & Preprocess ---")
        chunks = run_phase1(input_path, state.phase1, glossary_path, state=state)
        print_log(f"  完了: {len(chunks)} チャンクを処理\n")

    # --- Phase 2: Meta-Generation ---
    if start_phase <= 2:
        state.update_status("内容を分析中...", 40)
        print_log("--- Phase 2: Meta-Generation ---")
        meta = run_phase2(state.phase1, state.phase2, glossary_path, api_key=api_key, expertise=expertise, model=model, thinking_level=thinking_level, state=state, is_book=is_book)
        print_log(f"  完了: レジュメ {len(meta['resume_content'])} 文字, キーワード {len(meta['keywords_data'])} 件\n")

    # --- Phase 3: Structuring & Clipping ---
    if start_phase <= 3:
        state.update_status("本文構造を構築中...", 60)
        print_log("--- Phase 3: Structuring & Clipping ---")
        
        # 【修正】.docx 入力時の PyMuPDF クラッシュガード
        if is_book and str(original_input_path).lower().endswith(".docx"):
            print_log("  [Pipeline] 警告: Book Modeが指定されましたが、入力が.docxのためPyMuPDF解析をスキップし、Paper Modeにフォールバックします。")
            is_book = False  # 以降のPhase 4, 5もPaper Modeとして処理させる
            phase3_input = input_path
        else:
            # Book Mode の場合、構造解析にオリジナルの PDF が必要なため、
            # 抽出済みテキスト (input_path) ではなく元のパスを使用する
            phase3_input = original_input_path if is_book else input_path
        
        tree, sections = run_phase3(
            state.phase1, state.phase2, state.phase3_structure, state.phase3_sections, 
            state=state,
            is_book=is_book,
            api_key=api_key,
            model=model,
            input_path=phase3_input,
            pdf_mode=pdf_mode,   # Route C (full_vlm) のルーティングに使用
        )
        print_log(f"  完了: {len(tree)} セクション\n")

        if structure_only:
            print_log("  [Pipeline] --structure-only 指定により、構造化フェーズで処理を停止します。")
            state.cleanup_old_sessions()
            return

    # --- Phase 4: Sliding-Window Translation ---
    if start_phase <= 4:
        state.update_status("本文を翻訳中...", 70)
        print_log("--- Phase 4: Sliding-Window Translation ---")
        japanese_tree = run_phase4(
            state.phase2, state.phase3_structure, state.phase3_sections, state.phase4, 
            glossary_path, api_key=api_key, expertise=expertise, 
            model=model, thinking_level=thinking_level, 
            state=state, tier=tier,
            resume_only=resume_only,
            is_book=is_book,
            pdf_mode=pdf_mode,
        )
        print_log(f"  完了: {len(japanese_tree)} セクション翻訳完了\n")

    # --- Phase 5: Export ---
    if start_phase <= 5:
        state.update_status("最終ファイルを作成中...", 95)
        print_log("--- Phase 5: Export ---")
        output_paths = run_phase5(
            original_input_path, title, state.phase2, state.phase3_structure, state.phase4, 
            export_mode=export_mode, 
            resume_only=resume_only,
            is_book=is_book,
        )
        print_log(f"  完了: 出力ファイル作成済 (計 {len(output_paths)} 件)\n")
        for p in output_paths:
            print_log(f"    - {p}")
        print_log()

    # クリーンアップ（Race Condition 回避のため完了後に実行）
    state.cleanup_old_sessions()
    print_log("=== Pipeline 完了 ===")
