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


def main():
    parser = argparse.ArgumentParser(
        description="p2workflowy V2: 学術論文テキスト → Workflowy 変換ツール",
    )
    parser.add_argument(
        "input_files",
        nargs="*",
        help="入力テキストファイルのパス（複数指定可）",
    )
    parser.add_argument(
        "--glossary",
        default=None,
        help="glossary.csv のパス（省略時はデフォルト）",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="論文タイトル（単一ファイル処理時のみ有効。省略時はファイル名から推定）",
    )
    parser.add_argument(
        "--resume",
        type=int,
        default=None,
        choices=[1, 2, 3, 4, 5],
        help="再開するフェーズ番号（1-5）",
    )
    parser.add_argument(
        "--ronbun",
        action="store_true",
        help="RonbunNihongo モードで実行（日本語訳のみのMarkdownを出力）",
    )

    args = parser.parse_args()

    # 引数がない場合は対話モード
    if not args.input_files:
        print("\n=== p2workflowy V2 対話モード ===")
        print("処理したい論文のパス（.txtファイル）またはディレクトリをここに貼り付けて Enter を押してください。")
        input_str = input("\nファイルパス: ").strip()
        
        if not input_str:
            print("エラー: パスが入力されませんでした。")
            return
            
        # ドラッグ&ドロップによる引用符の処理と分割（簡易的）
        # 'path1' 'path2' 形式や "path1" "path2" 形式に対応
        try:
            input_files = shlex.split(input_str)
        except ValueError:
            # Fallback if shlex fails, e.g., malformed quotes
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
            # ディレクトリ内の .txt ファイルを自動収集（直下のみ、ソート済み）
            txt_files = sorted(list(p.glob("*.txt")))
            if not txt_files:
                print(f"警告: ディレクトリ内に .txt ファイルが見つかりませんでした: {p}")
            expanded_files.extend([str(f) for f in txt_files])
        else:
            expanded_files.append(path_str)
    
    input_files = expanded_files

    if not input_files:
        print("エラー: 処理対象のファイルが見つかりませんでした。")
        return

    print(f"\n計 {len(input_files)} 件のファイルを処理します。")

    for i, file_path in enumerate(input_files, 1):
        p = Path(file_path)
        if not p.exists():
            print(f"\n[{i}/{len(input_files)}] エラー: ファイルが見つかりません: {file_path}")
            continue

        print(f"\n[{i}/{len(input_files)}] --- 処理を開始します: {p.name} ---")
        try:
            export_mode = "ronbunnihongo" if args.ronbun else "p2workflowy"
            run_pipeline(
                input_path=str(p),
                glossary_path=args.glossary,
                title=args.title if len(input_files) == 1 else None, # titleは単一ファイル処理時のみ有効
                resume_from=args.resume,
                export_mode=export_mode,
            )
        except Exception as e:
            print(f"[{i}/{len(input_files)}] エラー発生: {e}")
            traceback.print_exc() # 詳細なエラー情報を出力

    print("\n=== 全ての処理が終了しました ===")


if __name__ == "__main__":
    main()
