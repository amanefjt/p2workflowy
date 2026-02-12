# -*- coding: utf-8 -*-
"""
p2workflowy - 英語論文処理プログラム (Agentic Skills版)
"""
import sys
import time
import re
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


def main():
    """メイン処理パイプライン"""
    # パス設定
    project_dir = Path(__file__).parent.parent
    glossary_file = project_dir / "glossary.csv"
    inter_dir = project_dir / "intermediate"
    
    # 引数またはユーザー入力から入力ファイルを取得
    if len(sys.argv) > 1:
        input_path_str = sys.argv[1]
    else:
        print("\n" + "=" * 60)
        print("処理する論文のテキストファイル（.txt）のパスを入力してください。")
        print("例: /Users/shufujita/Downloads/paper.txt")
        print("=" * 60)
        input_path_str = input("ファイルパス: ").strip()
    
    # 引用符（ドラッグ&ドロップで付く場合がある）を削除
    input_path_str = input_path_str.strip("'\"")
    input_file = Path(input_path_str)
    
    # 入力ファイルの確認
    if not input_file.exists():
        print(f"エラー: 入力ファイルが見つかりません: {input_file}")
        sys.exit(1)
        
    # 出力ファイルの設定（入力ファイルと同じディレクトリに _output.txt を作成）
    output_final = input_file.parent / f"{input_file.stem}_output.txt"
    structured_md_file = inter_dir / "structured.md"
    
    # ディレクトリ作成
    inter_dir.mkdir(exist_ok=True)
    
    print("=" * 60)
    print("p2workflowy - Cognitive Skills Pipeline")
    print("=" * 60)
    
    # スキルとユーティリティの初期化
    try:
        skills = PaperProcessorSkills()
    except ValueError as e:
        print(f"\nエラー: {e}")
        print("ヒント: .envファイルにGOOGLE_API_KEYを設定してください")
        sys.exit(1)

    # 1. Load Input
    print_progress("Step 1/4: 入力ファイルを読み込み中...", 0)
    raw_text = Utils.read_text_file(input_file)
    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""
    print_progress("Step 1/4: 入力ファイルを読み込み中...", 25)

    # 2. Phase 1 - Structuring
    print_progress("Step 2/4: 文献の構造を復元中 (Cognitive Structuring)...", 25)
    try:
        structured_md = skills.restore_structure(
            raw_text,
            progress_callback=lambda msg: print_progress(f"Step 2/4: {msg}")
        )
        
        # 思考プロセス（<thought>）を除去
        if "</thought>" in structured_md:
            structured_md = structured_md.split("</thought>")[-1].strip()
        
        # コードブロックマーカー（```markdown / ```）を除去
        structured_md = re.sub(r'^```(markdown)?\n', '', structured_md, flags=re.MULTILINE)
        structured_md = re.sub(r'\n```$', '', structured_md, flags=re.MULTILINE)
        structured_md = structured_md.strip()
        
        Utils.write_text_file(structured_md_file, structured_md)
        print_progress("Step 2/4: 構造復元完了", 50)
    except Exception as e:
        print(f"\nエラー: 構造復元に失敗しました: {e}")
        sys.exit(1)

    # 3. Phase 2 - Parallel Processing (Summarization & Translation)
    
    # Summarization
    print_progress("Step 3/4: Workflowy形式の要約を生成中...", 50)
    try:
        summary_workflowy = skills.summarize_workflowy(
            structured_md,
            progress_callback=lambda msg: print_progress(f"Step 3/4: {msg}")
        )
        # 要約からもマーカーを除去
        summary_workflowy = re.sub(r'^```(markdown)?\n', '', summary_workflowy, flags=re.MULTILINE)
        summary_workflowy = re.sub(r'\n```$', '', summary_workflowy, flags=re.MULTILINE)
        summary_workflowy = summary_workflowy.strip()

        print_progress("Step 3/4: 要約生成完了", 75)
    except Exception as e:
        print(f"\nエラー: 要約生成に失敗しました: {e}")
        sys.exit(1)

    # Translation
    print_progress("Step 4/4: アカデミック翻訳を実施中 (辞書適用)...", 75)
    
    # セクション分割して翻訳
    sections = Utils.split_into_sections(structured_md)
    translated_parts = []
    
    for i, section in enumerate(sections):
        content = section["content"].strip()
        if not content:
            continue

        msg = f"翻訳中... ({i+1}/{len(sections)})"
        print_progress(f"Step 4/4: {msg}", 75 + int((i / len(sections)) * 20))
        try:
            translated = skills.translate_academic(
                content,
                glossary_text,
                progress_callback=None
            )
            # 翻訳結果からもマーカーを除去
            translated = re.sub(r'^```(markdown)?\n', '', translated, flags=re.MULTILINE)
            translated = re.sub(r'\n```$', '', translated, flags=re.MULTILINE)
            translated_parts.append(translated.strip())
        except Exception as e:
            print(f"\n警告: セクション '{section['title']}' の翻訳に失敗: {e}")
            translated_parts.append(f"[翻訳エラー: {section['title']}]")

    full_translation_md = "\n\n".join(translated_parts)
    translation_workflowy = Utils.markdown_to_workflowy(full_translation_md)
    print_progress("Step 4/4: 翻訳完了", 95)

    # 4. Assembly
    print_progress("Step 5/5: 最終出力を構築中...", 95)
    
    # タイトル取得
    title_lines = [l.lstrip("# ").strip() for l in structured_md.split("\n") if l.strip()]
    title_line = title_lines[0] if title_lines else "Untitled Paper"
    
    final_output = f"{title_line}\n\n- 要約\n{summary_workflowy}\n{translation_workflowy}"
    Utils.write_text_file(output_final, final_output)
    
    print_progress("Step 5/5: 処理完了!", 100)
    
    # 完了
    print("\n" + "=" * 60)
    print("リファクタリング後のパイプライン処理が完了しました")
    print("=" * 60)
    print(f"\n出力ファイル:")
    print(f"  1. 中間ファイル (Markdown): {structured_md_file}")
    print(f"  2. 最終成果物 (Workflowy): {output_final}")
    print()


if __name__ == "__main__":
    main()
