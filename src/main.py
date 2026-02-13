# -*- coding: utf-8 -*-
"""
p2workflowy - 英語論文・書籍処理プログラム
"""
import os
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
    """メイン処理パイプライン"""
    project_dir = Path(__file__).parent.parent
    glossary_file = project_dir / "glossary.csv"
    inter_dir = project_dir / "intermediate"
    output_dir = project_dir / "output"
    
    if len(sys.argv) > 1:
        input_path_str = sys.argv[1]
    else:
        print("\n" + "=" * 60)
        print("処理するテキストファイル（.txt）のパスを入力してください。")
        print("=" * 60)
        input_path_str = input("ファイルパス: ").strip()
    
    input_path_str = input_path_str.strip("'\"")
    input_file = Path(input_path_str)

    if not input_file.exists():
        print(f"エラー: 入力ファイルが見つかりません: {input_file}")
        sys.exit(1)

    # モード選択
    print("\nモードを選択してください:")
    print("1. 論文モード (Paper Mode) - 数十ページ程度の論文に最適")
    print("2. 書籍モード (Book Mode) - 100ページ超の書籍に最適")
    mode_input = input("選択 (1/2): ").strip()
    mode = "book" if mode_input == "2" else "paper"
    
    output_final = input_file.parent / f"{input_file.stem}_output.txt"
    output_summary = input_file.parent / f"{input_file.stem}_summary.txt"
    output_structured = input_file.parent / f"{input_file.stem}_structured_eng.md"
    
    inter_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    
    print("=" * 60)
    print(f"p2workflowy - {'Book' if mode == 'book' else 'Paper'} Processing Mode")
    print("=" * 60)
    
    try:
        skills = PaperProcessorSkills()
    except ValueError as e:
        print(f"\nエラー: {e}")
        sys.exit(1)

    raw_text = Utils.read_text_file(input_file)
    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""

    if mode == "paper":
        await _process_paper(skills, raw_text, glossary_text, input_file, output_summary, output_structured, output_final)
    else:
        await _process_book(skills, raw_text, glossary_text, input_file, output_summary, output_structured, output_final)

async def _process_paper(skills, raw_text, glossary_text, input_file, output_summary, output_structured, output_final):
    print_progress("Phase 1 (論文): 原文から意味的な構造を把握中...", 10)
    summary_text = await skills.summarize_raw_text(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
    )
    Utils.write_text_file(output_summary, summary_text)

    print_progress("Phase 2 (論文): 構造を復元中...", 30)
    structured_md = await skills.structure_text_with_hint(
        raw_text,
        summary_text,
        progress_callback=lambda msg: print_progress(f"Phase 2: {msg}")
    )
    Utils.write_text_file(output_structured, structured_md)

    print_progress("Phase 3 (論文): 並列翻訳中...", 60)
    translated_text = await skills.translate_academic(
        structured_md,
        glossary_text,
        summary_context=summary_text,
        progress_callback=lambda msg: print_progress(f"Phase 3: {msg}")
    )

    print_progress("Phase 4: 成果物を統合中...", 90)
    await _assemble_workflowy(input_file, summary_text, translated_text, structured_md, output_final, output_summary, output_structured)

async def _process_book(skills, raw_text, glossary_text, input_file, output_summary, output_structured, output_final):
    print_progress("Phase 1 (書籍): 全体の構成と要約を分析中...", 10)
    structure_info = await skills.analyze_book_structure(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
    )
    Utils.write_text_file(output_summary, structure_info)
    
    overall_summary = ""
    toc_match = re.split(r'# Table of Contents', structure_info, flags=re.IGNORECASE)
    overall_summary = toc_match[0].replace("# Overall Summary", "").strip()

    print_progress("Phase 2 (書籍): 章ごとに分割し、個別要約を作成中...", 30)
    # 本文を見出しベースで分割（書籍の場合は章立ての見出しがあるはず）
    chapters = skills._split_markdown_by_headers(raw_text)
    
    clean_chapters_eng = []
    translated_chapters_jp = []
    all_chapter_summaries = []

    for i, chapter_text in enumerate(chapters):
        idx = i + 1
        print_progress(f"Chapter {idx}/{len(chapters)} 処理中...", 30 + int(60 * idx / len(chapters)))
        
        chapter_summary = await skills.summarize_chapter(overall_summary, chapter_text)
        all_chapter_summaries.append(chapter_summary)
        
        clean_chapter = await skills.structure_chapter(overall_summary, chapter_text)
        clean_chapters_eng.append(clean_chapter)
        
        translated_chapter = await skills.translate_chapter(overall_summary, chapter_summary, clean_chapter, glossary_text)
        translated_chapters_jp.append(translated_chapter)

    structured_md = "\n\n".join(clean_chapters_eng)
    Utils.write_text_file(output_structured, structured_md)
    
    translated_text = "\n\n".join(translated_chapters_jp)
    
    combined_summary = "# Book Summary\n" + overall_summary + "\n\n# Chapters Summary\n" + "\n\n".join(all_chapter_summaries)

    print_progress("Phase 4: 成果物を統合中...", 95)
    await _assemble_workflowy(input_file, combined_summary, translated_text, structured_md, output_final, output_summary, output_structured)

async def _assemble_workflowy(input_file, summary_text, translated_text, structured_md, output_final, output_summary, output_structured):
    # 1. 要約の処理
    summary_workflowy = Utils.markdown_to_workflowy(summary_text)
    summary_section = "    - 要約 (Summary)\n" + "\n".join(["        " + line for line in summary_workflowy.splitlines()])

    # 2. 翻訳・タイトルの処理
    eng_lines = structured_md.splitlines()
    title = input_file.stem
    if eng_lines and eng_lines[0].strip().startswith('# '):
        title = eng_lines[0].strip().replace('# ', '').strip()

    lines = translated_text.splitlines()
    if lines and lines[0].strip().startswith('# '):
        lines = lines[1:]
    
    body_text_no_title = "\n".join(lines).strip()
    translation_workflowy = Utils.markdown_to_workflowy(body_text_no_title)
    translation_section = "\n".join(["    " + line for line in translation_workflowy.splitlines()])
    
    # 3. 結合
    final_content = f"- {title}\n{summary_section}\n{translation_section}"
    Utils.write_text_file(output_final, final_content)
    
    print_progress("Phase 4: 処理完了!", 100)
    print("\n" + "=" * 60)
    print(f"成果物が生成されました:")
    print(f"  - 最終出力 (Workflowy形式): {output_final}")
    print(f"  - 要約ファイル: {output_summary}")
    print(f"  - 英語構造化ファイル: {output_structured}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
