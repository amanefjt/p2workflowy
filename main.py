"""
p2workflowy V2: CLI エントリーポイント
"""

import argparse
import shlex
import sys
import traceback
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.pipeline import run_pipeline
from core.engine.p1_ingest.docling_ingester import is_docling_viable
from core.engine.p1_ingest.routing import decide_pdf_mode
from core.engine.p1_ingest.spread_splitter import is_spread_pdf


def main():
    parser = argparse.ArgumentParser(
        description="p2workflowy V2: 学術論文テキスト → Workflowy 変換ツール",
    )
    parser.add_argument(
        "input_files",
        nargs="*",
        help="入力ファイル (.pdf または .txt)",
    )
    parser.add_argument(
        "--book",
        action="store_true",
        help="書籍モード（章・節の階層を維持）",
    )
    parser.add_argument(
        "--max-chapters",
        type=int,
        default=None,
        dest="max_chapters",
        help="書籍モード: 処理する最大章数（コスト削減・デバッグ用）",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="論文モード（デフォルト）",
    )
    parser.add_argument(
        "--ronbunnihongo",
        action="store_true",
        help="RonbunNihongo モード（日本語要約・全訳のみを出力）",
    )
    parser.add_argument(
        "--glossary",
        default=None,
        help="glossary.csv のパス",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM モデル名 (例: gemini-1.5-pro)",
    )
    parser.add_argument(
        "--thinking",
        default="High",
        choices=["Low", "High"],
        help="Thinking Level (Low, High)",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="セッション ID を指定して再開",
    )
    parser.add_argument(
        "--resume",
        type=int,
        default=None,
        choices=[1, 2, 3, 4, 5],
        help="再開するフェーズ番号 (1-5)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="論文のタイトルを指定（省略時は自動推定）",
    )
    parser.add_argument(
        "--heavy-ocr",
        action="store_true",
        help="【高度な設定】OpenCV による高度なレイアウト解析を有効化",
    )
    parser.add_argument(
        "--free",
        action="store_true",
        help="【高度な設定】レート制限を重視した低速モード（無料版向け）",
    )
    parser.add_argument(
        "--pdf-mode",
        default=None,
        choices=["hybrid", "full_vlm"],
        help="【高度な設定】PDF 抽出モード。未指定時は PDF なら full_vlm になります。",
    )
    parser.add_argument(
        "--hybrid-pdf",
        action="store_true",
        help="【高度な設定】ハイブリッド OCR モード（高速・低解像）",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="最大処理ページ数（テスト用）",
    )
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help="【高度な設定】構造化フェーズで停止",
    )
    parser.add_argument(
        "--resume-only",
        action="store_true",
        help="【高度な設定】要約と原文のみを出力（翻訳スキップ）",
    )
    parser.add_argument(
        "--lite",
        action="store_true",
        help="【推奨】テスト用の低コストモデル（Flash-Lite）を強制的に使用",
    )
    parser.add_argument(
        '--concurrent', type=int, default=4,
        help='Phase 4 の並列セクション数（デフォルト: 4）'
    )

    args = parser.parse_args()

    # --- APIキー選択: 無料キー2本→有料キーの順に自動フォールバック（開発時も通常利用時も常に無料から） ---
    from core.config import GEMINI_API_KEY, GEMINI_API_KEY_FREE_1, GEMINI_API_KEY_FREE_2
    from core.llm_client import key_rotator

    ordered_keys = [GEMINI_API_KEY_FREE_1, GEMINI_API_KEY_FREE_2, GEMINI_API_KEY]
    key_rotator.configure(ordered_keys, tiers=["free", "free", "paid"])

    if not key_rotator.is_configured():
        print("エラー: GEMINI_API_KEY_FREE_1/2 / GEMINI_API_KEY のいずれも未設定です。.env を確認してください。")
        return

    selected_api_key = key_rotator.current()
    free_count = sum(1 for k in (GEMINI_API_KEY_FREE_1, GEMINI_API_KEY_FREE_2) if k)
    print(f"使用APIキー: 無料キー{free_count}本設定済み（1本目から使用）"
          f"{' + 有料キーあり（429/503が続いた場合の最終フォールバック）' if GEMINI_API_KEY else ''}")

    # 引数がない場合は対話モード
    if not args.input_files:
        print("\n=== p2workflowy V2 対話モード ===")
        print("処理したい PDF のパスまたはディレクトリをここに貼り付けて Enter を押してください。")
        input_str = input("\nファイルパス: ").strip()
        
        if not input_str:
            print("エラー: パスが入力されませんでした。")
            return
            
        try:
            input_files = shlex.split(input_str)
        except ValueError:
            input_files = [input_str.strip("'").strip('"')]
    else:
        input_files = args.input_files

    if not input_files:
        print("エラー: 入力ファイルが指定されていません。")
        return

    # ディレクトリの展開
    expanded_files = []
    for path_str in input_files:
        p = Path(path_str)
        if p.is_dir():
            txt_files = sorted(list(p.glob("*.txt")))
            pdf_files = sorted(list(p.glob("*.pdf")))
            expanded_files.extend([str(f) for f in (pdf_files + txt_files)])
        else:
            expanded_files.append(path_str)
    
    input_files = expanded_files

    if not input_files:
        print("エラー: 処理対象のファイルが見つかりませんでした。")
        return

    print(f"\n計 {len(input_files)} 件のファイルを処理します。")

    # 処理実行
    if args.book:
        # --- 書籍モード: BookManager による一括オーケストレーション ---
        from core.book_manager import BookManager

        # 代表的な入力パスを選択（ディレクトリ指定時は展開済みリストの[0]）
        main_input = input_files[0]
        manager = BookManager(input_path=main_input, api_key=selected_api_key, model=args.model)
        
        try:
            output_paths = manager.run(
                glossary_path=args.glossary,
                thinking_level=args.thinking,
                pdf_mode=args.pdf_mode,
                tier="free" if (args.free or args.lite) else "paid",
                heavy_ocr=args.heavy_ocr,
                max_pages=args.max_pages,
                max_chapters=args.max_chapters,
                resume_only=args.resume_only,
                structure_only=args.structure_only,
                resume_from=args.resume
            )
            
            # 1) 入力ファイルがあったところに出力コピー
            import shutil
            out_dir = Path(main_input).parent
            if output_paths:
                print(f"\n最終出力ファイルをコピー中: {out_dir}")
                for out_path in output_paths:
                    if out_path.exists():
                        target_path = out_dir / out_path.name
                        shutil.copy2(out_path, target_path)
                        print(f"  -> {target_path.name}")
                        
        except Exception as e:
            print(f"書籍処理中にエラー発生: {e}")
            traceback.print_exc()
    else:
        # --- 論文モード: 従来の個別ループ処理 ---
        for i, file_path in enumerate(input_files, 1):
            p = Path(file_path)
            if not p.exists():
                print(f"\n[{i}/{len(input_files)}] エラー: ファイルが見つかりません: {file_path}")
                continue

            is_pdf = p.suffix.lower() == ".pdf"
            if is_pdf:
                # 書籍モード（BookManager）と同じ①〜④ルーティング規則を論文単位で適用。
                # 以前は未指定時に無条件で "hybrid" 固定していたため、Docling不可PDFで
                # VLMフォールバックが働かず生の物理テキスト抽出に静かに落ちていた。
                pdf_mode, routing_reason = decide_pdf_mode(
                    args.pdf_mode, is_spread_pdf(str(p)), is_docling_viable(str(p))
                )
            else:
                pdf_mode, routing_reason = "hybrid", "not_pdf"
            export_mode = "ronbunnihongo" if args.ronbunnihongo else "p2workflowy"

            print(f"\n[{i}/{len(input_files)}] --- 構成: Paper / エンジン: {pdf_mode}（{routing_reason}） / [Target: {p.name}] ---")
            
            try:
                run_pipeline(
                    input_path=str(p),
                    glossary_path=args.glossary,
                    title=args.title if len(input_files) == 1 else None,
                    resume_from=args.resume,
                    api_key=selected_api_key,
                    session_id=args.session,
                    export_mode=export_mode,
                    model=args.model,
                    thinking_level=args.thinking,
                    pdf_mode=pdf_mode,
                    tier="free" if (args.free or args.lite) else "paid",
                    is_book=False,
                    structure_only=args.structure_only,
                    resume_only=args.resume_only,
                    heavy_ocr=args.heavy_ocr,
                    max_pages=args.max_pages,
                    max_concurrent_sections=args.concurrent,
                )
            except Exception as e:
                print(f"[{i}/{len(input_files)}] エラー発生: {e}")
                traceback.print_exc()


    print("\n=== 全ての処理が終了しました ===")


if __name__ == "__main__":
    main()
