# -*- coding: utf-8 -*-
"""
p2workflowy - 英語論文処理プログラム (Agentic Skills版)
"""
import sys
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
    output_structured = input_file.parent / f"{input_file.stem}_structured_eng.md"
    
    # ディレクトリ作成（中間ファイル用は維持）
    inter_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    
    print("=" * 60)
    print("p2workflowy - Summary-First Sequential Pipeline")
    print("=" * 60)
    
    # スキルとユーティリティの初期化
    try:
        # constants.py で定義された DEFAULT_MODEL (gemini-3-flash-preview) を使用
        skills = PaperProcessorSkills()
    except ValueError as e:
        print(f"\nエラー: {e}")
        sys.exit(1)

    # Step 1: Load Input & Glossary
    raw_text = Utils.read_text_file(input_file)
    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""

    # Phase 1: Semantic Mapping (レジュメ生成)
    print_progress("Phase 1: 原文から意味的な構造（レジュメ）を把握中...", 10)
    resume_text = await skills.generate_resume(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
    )
    # レジュメは内部用および最終成果物(Workflowy)への統合用として使用
    print_progress("Phase 1: レジュメ生成完了", 30)

    # Phase 2: Anchored Structuring (構造化)
    print_progress("Phase 2: レジュメをガイドにして原文の構造を復元中...", 30)
    try:
        # レジュメからセクション見出しのみを抽出してヒントにする（効率化）
        structure_hint = Utils.extract_structure_from_resume(resume_text)
        
        structured_md = await skills.structure_text_with_hint(
            raw_text,
            structure_hint,
            progress_callback=lambda msg: print_progress(f"Phase 2: {msg}")
        )
        Utils.write_text_file(output_structured, structured_md)
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
            summary_context=resume_text,
            progress_callback=lambda msg: print_progress(f"Phase 3: {msg}")
        )
        print_progress("Phase 3: 翻訳完了", 90)
    except Exception as e:
        print(f"\nエラー: 翻訳に失敗しました: {e}")
        sys.exit(1)

    # Phase 4: Assembly (結合)
    print_progress("Phase 4: 成果物を統合中...", 90)
    
    # Workflowy形式への変換と階層調整
    
    # レジュメの処理
    # LLMが生成したMarkdown形式のレジュメをWorkflowy形式に変換
    resume_workflowy = Utils.markdown_to_workflowy(resume_text)
    # インデントを追加 (ルートの下にぶら下げるため、2スペースずつ下げる。内容は4スペースから開始)
    resume_section = "  - レジュメ (Resume)\n" + "\n".join(["    " + line for line in resume_workflowy.splitlines()])

    # 2. 翻訳・タイトルの処理
    # 構造化された英語（Phase 2の結果）からタイトルを抽出する
    eng_lines = structured_md.splitlines()
    title = input_file.stem  # デフォルト
    if eng_lines and eng_lines[0].strip().startswith('# '):
        title = eng_lines[0].strip().replace('# ', '').strip()

    # 翻訳結果の処理
    lines = translated_text.splitlines()
    
    # 翻訳結果の最初の行がH1 (# タイトル) であれば、本文からは削除する
    # (ルートには英語タイトルを使うため)
    if lines and lines[0].strip().startswith('# '):
        lines = lines[1:]
    
    body_text_no_title = "\n".join(lines).strip()
    
    # 見出しレベルの正規化
    translation_workflowy = Utils.markdown_to_workflowy(body_text_no_title)
    
    # インデントを追加（ルートノードの下にぶら下げるため、2スペース分下げる）
    translation_section = "\n".join(["  " + line for line in translation_workflowy.splitlines()])
    
    # ルートノードを英語の論文タイトルにし、その下に要約と翻訳本文を配置する
    final_content = f"- {title}\n{resume_section}\n{translation_section}"
    
    Utils.write_text_file(output_final, final_content)
    
    print_progress("Phase 4: 処理完了!", 100)
    print("\n" + "=" * 60)
    print(f"成果物が生成されました:")
    print(f"  - 最終出力 (Workflowy形式 - レジュメ含む): {output_final}")
    print(f"  - 構造化された英語: {output_structured}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
