# -*- coding: utf-8 -*-
"""
p2workflowy - 英語論文処理プログラム
"""
import sys
import asyncio
import argparse
import json
import re
from typing import List, Dict, Any, cast
from pathlib import Path
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

from .skills import PaperProcessorSkills
from .utils import Utils
from .constants import EXCLUDE_SECTION_KEYWORDS


def print_progress(message: str, percentage: int | None = None) -> None:
    """進捗を表示する"""
    if percentage is not None:
        # :<80 で既存の長いメッセージを空白で上書きクリアする
        print(f"\r[{percentage:3d}%] {message:<80}", end="", flush=True)
    else:
        print(f"\r{message:<80}")


async def run_pipeline(input_file: Path, skills: PaperProcessorSkills, glossary_text: str):
    """
    標準的な論文処理パイプライン (要約 -> 構造化 -> 翻訳)
    """
    output_final = input_file.parent / f"{input_file.stem}_output.txt"
    output_structured = input_file.parent / f"{input_file.stem}_structured_eng.md"

    raw_text = Utils.read_text_file(input_file)

    # Phase 1: Semantic Mapping (レジュメ生成)
    print_progress("Phase 1: 原文から意味的な構造（レジュメ）を把握中...", 10)
    resume_text = await skills.generate_resume(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}", 10)
    )
    print_progress("Phase 1: レジュメ生成完了", 30)

    # Phase 2: Anchored Structuring (構造化) - 以前の状態に戻し一括処理（あるいは再考）
    print_progress("Phase 2: レジュメをガイドにして原文の構造を復元中...", 30)
    structure_hint = Utils.extract_structure_from_resume(resume_text)
    structured_md = await skills.structure_text_with_hint(
        raw_text,
        structure_hint,
        progress_callback=lambda msg: print_progress(f"Phase 2: {msg}", 30),
        enable_chunking=False # 一括処理に戻す
    )
    Utils.write_text_file(output_structured, structured_md)
    print_progress("Phase 2: 構造化完了", 50)

    # 不要なセクションを物理的に削除 (References 等)
    structured_md = Utils.remove_unwanted_sections(structured_md, EXCLUDE_SECTION_KEYWORDS)

    # Phase 3: Contextual Translation (並列翻訳)
    print_progress("Phase 3: 文脈を考慮した並列翻訳を実施中...", 50)
    translated_text = await skills.translate_academic(
        structured_md,
        glossary_text,
        summary_context=resume_text,
        progress_callback=lambda msg: print_progress(f"Phase 3: {msg}", 50)
    )
    print_progress("Phase 3: 翻訳完了", 90)

    # Phase 4: Assembly (結合)
    print_progress("Phase 4: 成果物を統合中...", 90)
    resume_workflowy = Utils.markdown_to_workflowy(resume_text)
    resume_section = "  - レジュメ (Resume)\n" + "\n".join(["    " + line for line in resume_workflowy.splitlines()])

    # タイトル抽出
    eng_lines = structured_md.splitlines()
    title = input_file.stem
    if eng_lines and eng_lines[0].strip().startswith('# '):
        title = eng_lines[0].strip().replace('# ', '').strip()

    # 翻訳結果の処理
    lines = translated_text.splitlines()
    if lines and lines[0].strip().startswith('# '):
        lines = lines[1:]
    body_text_no_title = "\n".join(lines).strip()
    translation_workflowy = Utils.markdown_to_workflowy(body_text_no_title)
    translation_section = "\n".join(["  " + line for line in translation_workflowy.splitlines()])

    final_content = f"- {title}\n{resume_section}\n{translation_section}"
    Utils.write_text_file(output_final, final_content)
    
    print_progress("Phase 4: 処理完了!", 100)
    print(f"\n成果物: {output_final}")


async def main():
    """メインエントリーポイント"""
    project_dir = Path(__file__).parent.parent
    glossary_file = project_dir / "glossary.csv"
    
    parser = argparse.ArgumentParser(
        description="p2workflowy - 英語論文処理（Paper Mode 専用）"
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="処理対象のファイルパス"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="テストモード"
    )
    
    args = parser.parse_args()
    
    if not args.input_file:
        print("\n" + "=" * 60)
        print("p2workflowy - 英語論文処理")
        print("=" * 60)
        input_path_str = input("ファイルパス: ").strip()
    else:
        input_path_str = args.input_file
    
    input_file = Path(input_path_str.strip("'\""))
    if not input_file.exists():
        print(f"エラー: ファイルが見つかりません: {input_file}")
        return

    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""

    print(f"\n処理を開始します...")
    skills = PaperProcessorSkills()
    await run_pipeline(input_file, skills, glossary_text)


if __name__ == "__main__":
    asyncio.run(main())
