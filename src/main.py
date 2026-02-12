# -*- coding: utf-8 -*-
"""
p2workflowy - 英語論文処理プログラム (Agentic Skills版)
"""
import sys
import time
import re
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

from .skills import PaperProcessorSkills
from .utils import Utils


def print_progress(message: str, percentage: int | None = None) -> None:
    """進捗を表示する"""
    if percentage is not None:
        print(f"\r[{percentage:3d}%] {message}", end="", flush=True)
    else:
        print(f"\n{message}")


async def main():
    """メイン処理パイプライン (Summary-First Approach)"""
    # パス設定
    project_dir = Path(__file__).parent.parent
    glossary_file = project_dir / "glossary.csv"
    inter_dir = project_dir / "intermediate"
    output_dir = project_dir / "output"
    
    # 引数またはユーザー入力から入力ファイルを取得
    if len(sys.argv) > 1:
        input_path_str = sys.argv[1]
    else:
        print("\n" + "=" * 60)
        print("処理する論文のテキストファイル（.txt）のパスを入力してください。")
        print("例: /Users/username/Downloads/paper.txt")
        print("=" * 60)
        input_path_str = input("ファイルパス: ").strip()
    
    # 引用符（ドラッグ&ドロップで付く場合がある）を削除
    input_path_str = input_path_str.strip("'\"")
    input_file = Path(input_path_str)
    
    # 入力ファイルの確認
    if not input_file.exists():
        print(f"エラー: 入力ファイルが見つかりません: {input_file}")
        sys.exit(1)
        
    # 出力ファイルの設定
    output_final = input_file.parent / f"{input_file.stem}_output.txt"
    output_summary = input_file.parent / f"{input_file.stem}_summary.txt"
    structured_md_file = inter_dir / "structured.md"
    
    # ディレクトリ作成
    inter_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    
    print("=" * 60)
    print("p2workflowy - Summary-First Sequential Pipeline")
    print("=" * 60)
    
    # スキルとユーティリティの初期化
    try:
        # main.py (CLI) では引数なしで初期化することで、.envのGOOGLE_API_KEYを使用
        skills = PaperProcessorSkills()
    except ValueError as e:
        print(f"\nエラー: {e}")
        sys.exit(1)

    # Step 1: Load Input & Glossary
    raw_text = Utils.read_text_file(input_file)
    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""

    # Phase 1: Semantic Mapping (要約生成)
    print_progress("Phase 1: 原文から意味的な構造（地図）を把握中...", 10)
    summary_text = await skills.summarize_raw_text(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
    )
    # 要約単体でも保存
    Utils.write_text_file(output_summary, summary_text)
    print_progress("Phase 1: 要約生成完了 (出力を保存しました)", 30)

    # Phase 2: Anchored Structuring (構造化)
    print_progress("Phase 2: 要約をガイドにして原文の構造を復元中...", 30)
    try:
        structured_md = await skills.structure_text_with_hint(
            raw_text,
            summary_text,
            progress_callback=lambda msg: print_progress(f"Phase 2: {msg}")
        )
        Utils.write_text_file(structured_md_file, structured_md)
        print_progress("Phase 2: 構造化完了", 50)
    except Exception as e:
        print(f"\nエラー: 構造化に失敗しました: {e}")
        sys.exit(1)

    # Phase 3: Contextual Translation (並列翻訳)
    print_progress("Phase 3: 文脈を考慮した並列翻訳を実施中...", 50)
    try:
        translated_text = await skills.translate_academic(
            structured_md,
            glossary_text,
            summary_text=summary_text,
            progress_callback=lambda msg: print_progress(f"Phase 3: {msg}")
        )
        print_progress("Phase 3: 翻訳完了", 90)
    except Exception as e:
        print(f"\nエラー: 翻訳に失敗しました: {e}")
        sys.exit(1)

    # Phase 4: Assembly (結合)
    print_progress("Phase 4: 成果物を統合中...", 90)
    
    # Workflowy形式への変換
    summary_workflowy = Utils.markdown_to_workflowy(summary_text)
    translation_workflowy = Utils.markdown_to_workflowy(translated_text)
    
    final_content = f"# {input_file.stem}\n\n## 要約 (Summary)\n{summary_workflowy}\n\n## 翻訳 (Translation)\n{translation_workflowy}"
    Utils.write_text_file(output_final, final_content)
    
    print_progress("Phase 4: 処理完了!", 100)
    print("\n" + "=" * 60)
    print(f"成果物が生成されました:")
    print(f"  - 最終出力 (Workflowy形式): {output_final}")
    print(f"  - 要約のみ: {output_summary}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
