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
from .constants import EXCLUDE_SECTION_KEYWORDS


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
    # 本文を見出しベースで分割
    chapters = skills._split_markdown_by_headers(raw_text)
    
    clean_chapters_eng = []
    translated_chapters_jp = []
    all_chapter_summaries = []

    for i, chapter_text in enumerate(chapters):
        idx = i + 1
        print_progress(f"Chapter {idx}/{len(chapters)} 処理中...", 30 + int(60 * idx / len(chapters)))
        
        # タイトルの仮抽出
        lines_temp = chapter_text.strip().splitlines()
        first_line = lines_temp[0].strip() if lines_temp else ""
        title_for_check = first_line.lower().replace('#', '').strip()
        
        # 1. 参考文献等の除外
        if any(kw in title_for_check for kw in EXCLUDE_SECTION_KEYWORDS):
            print_progress(f"  ... '{first_line}' を除外キーワードに基づきスキップします。")
            continue

        # 2. 部 (Part) の判定
        # 「部」または「Part」を含み、「章」や「Chapter」を含まない見出し
        is_part = any(kw in first_line for kw in ["部", "Part", "PART"]) and not any(kw in first_line for kw in ["章", "Chapter"])
        # 本文が極端に短い（見出し＋数行程度）場合は「部」の見出しのみと判断
        has_body = len(lines_temp) > 3

        if is_part and not has_body:
            # 部のみ（要約・翻訳スキップ）
            all_chapter_summaries.append("") # 空の要約
            clean_chapters_eng.append(first_line)
            translated_chapters_jp.append(first_line) # そのまま
            continue
        
        chapter_summary = await skills.summarize_chapter(overall_summary, chapter_text)
        all_chapter_summaries.append(chapter_summary)
        
        clean_chapter = await skills.structure_chapter(overall_summary, chapter_text)
        clean_chapters_eng.append(clean_chapter)
        
        translated_chapter = await skills.translate_chapter(overall_summary, chapter_summary, clean_chapter, glossary_text)
        translated_chapters_jp.append(translated_chapter)

    # 各章の構造を構築
    chapter_combined_list = []
    for i, (summary, translation) in enumerate(zip(all_chapter_summaries, translated_chapters_jp)):
        # 章タイトルの抽出
        lines = translation.splitlines()
        chapter_title = f"Chapter {i+1}"
        content_body = translation
        
        if lines and lines[0].strip().startswith('#'):
            chapter_title = lines[0].strip().replace('#', '').strip()
            content_body = "\n".join(lines[1:]).strip()
        
        # 章の構成
        if not summary.strip():
            # 要約がない場合（部レベルの見出しのみ等）
            chapter_md = f"# {chapter_title}"
        else:
            chapter_md = f"# {chapter_title}\n\n## Chapter Summary\n{summary}\n\n## Chapter Body\n{content_body}"
        chapter_combined_list.append(chapter_md)

    structured_md = "\n\n".join(clean_chapters_eng)
    Utils.write_text_file(output_structured, structured_md)
    
    # 章ごとの要約と翻訳を統合した Markdown
    translated_text = "\n\n".join(chapter_combined_list)
    
    # Book Summary (全体要約)
    book_summary_md = "# Book Summary\n" + overall_summary

    print_progress("Phase 4: 成果物を統合中...", 95)
    await _assemble_workflowy(input_file, book_summary_md, translated_text, structured_md, output_final, output_summary, output_structured)

async def _assemble_workflowy(input_file, summary_text, translated_text, structured_md, output_final, output_summary, output_structured):
    # 1. 要約の処理 (全体要約や章の要約が含まれる Markdown を変換)
    summary_workflowy = Utils.markdown_to_workflowy(summary_text)
    
    # 2. 翻訳・タイトルの処理
    eng_lines = structured_md.splitlines()
    title = input_file.stem
    if eng_lines and eng_lines[0].strip().startswith('# '):
        title = eng_lines[0].strip().replace('# ', '').strip()

    translation_workflowy = Utils.markdown_to_workflowy(translated_text)
    
    # 3. 結合
    # - 本のタイトル
    #   - summary_workflowy (Book Summary)
    #   - translation_workflowy (Chapters -> Chapter Summary & Body)
    
    # インデント調整
    summary_section = "\n".join(["    " + line for line in summary_workflowy.splitlines()])
    translation_section = "\n".join(["    " + line for line in translation_workflowy.splitlines()])
    
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
