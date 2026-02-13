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
        await _process_book(skills, raw_text, glossary_text, input_file, output_summary, output_structured, output_final, inter_dir)

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

async def _process_book(skills, raw_text, glossary_text, input_file, output_summary, output_structured, output_final, inter_dir):
    # Phase 1: Structure Analysis
    print_progress("Phase 1 (書籍): 全文を読み込み、目次とアンカーを分析中 (Map作成)...", 10)
    
    try:
        structure_data = await skills.analyze_book_structure(
            raw_text,
            progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
        )
    except Exception as e:
        print_progress(f"Error: 目次分析に失敗しました: {e}")
        return

    # データ取り出し
    overall_summary = structure_data.get("book_summary", "Summary not available.")
    chapters_toc = structure_data.get("chapters", [])

    # 目次情報の保存（デバッグ用・確認用）
    toc_text = "\n".join([f"- {item.get('title')}" for item in chapters_toc])
    Utils.write_text_file(output_summary, f"# Book Summary\n{overall_summary}\n\n# Generated TOC\n{toc_text}")
    print(f"\n  => {len(chapters_toc)} 個の章を検出しました。")

    # Phase 2: Deterministic Splitting
    print_progress("Phase 2 (書籍): アンカー検索による分割を実行中 (Cut)...", 20)
    
    # split_by_anchors は [{"title":..., "text":...}, ...] のリストを返す
    processed_chapters = skills.split_by_anchors(raw_text, structure_data)
    
    if not processed_chapters:
        print("Error: テキストの分割に失敗しました（アンカーが見つかりませんでした）。")
        return

    print(f"  => {len(processed_chapters)} 個のチャンクに分割されました。")
    
    # 中間ファイルの保存 (User Request)
    chapters_dir = inter_dir / "chapters"
    chapters_dir.mkdir(exist_ok=True, parents=True)
    print(f"  => 中間ファイルを保存中: {chapters_dir}")
    
    for i, chapter in enumerate(processed_chapters):
        # ファイル名に使えない文字を除去
        safe_title = re.sub(r'[\\/*?:"<>|]', "", chapter.get("title", "unknown"))
        safe_title = safe_title.replace(" ", "_").strip()
        filename = f"chap{i+1:03d}_{safe_title}.txt"
        Utils.write_text_file(chapters_dir / filename, chapter.get("text", ""))

    # Phase 3: Per-Chapter Processing
    print_progress("Phase 3 (書籍): 各章の並列処理を開始...", 30)
    
    async def process_single_chapter(chapter_info):
        chapter_title = chapter_info.get("title", "Unknown Chapter")
        chapter_text = chapter_info.get("text", "")
        
        # 1. 除外キーワードチェック (Contributorsなど)
        title_lower = chapter_title.lower()
        if any(kw in title_lower for kw in EXCLUDE_SECTION_KEYWORDS):
            print(f"  Skipping excluded section: {chapter_title}")
            return None

        # 2. 文量チェック (極端に短いものはスキップ)
        if len(chapter_text) < 50:
             print(f"  Skipping short section: {chapter_title} ({len(chapter_text)} chars)")
             return None
        
        # 並列数が多すぎるとエラーになるので、必要ならSemaphoreなどで制御
        
        chapter_summary = await skills.summarize_chapter(overall_summary, chapter_text)
        clean_chapter = await skills.structure_chapter(overall_summary, chapter_text)
        translated_chapter = await skills.translate_chapter(overall_summary, chapter_summary, clean_chapter, glossary_text)
        
        return {
            "title": chapter_title, # TOC由来の正しいタイトルを維持
            "summary": chapter_summary,
            "clean_eng": clean_chapter,
            "translated_jp": translated_chapter
        }

    # 全章を並列で実行
    tasks = [process_single_chapter(c) for c in processed_chapters]
    results = await asyncio.gather(*tasks)

    # 結果を整理
    valid_results = [r for r in results if r is not None]

    # 各章の構造を構築
    chapter_combined_list = []
    clean_chapters_eng = []
    
    for res in valid_results:
        summary = res["summary"]
        translation = res["translated_jp"]
        # TOC由来のタイトルを使用 (翻訳結果のタイトルよりも信頼できる)
        final_title = res["title"]
        
        # 翻訳テキストからMarkdownのH1タグ(# )を除去して本文のみにする処理が必要か？
        # 現状のプロンプトではMarkdown構造を維持させるので、H1が含まれる可能性がある。
        # ただし、階層構造を整えるために、H1を強制的に final_title に置き換えるのが安全。
        
        lines = translation.splitlines()
        content_body = translation
        
        # 翻訳の冒頭がH1の場合、それを削除して本文だけにする（タイトルはTOC由来のものを使うため）
        if lines and lines[0].strip().startswith('# '):
             content_body = "\n".join(lines[1:]).strip()
        
        # 章の構成
        chapter_md = f"# {final_title}\n\n## Chapter Summary\n{summary}\n\n{content_body}"
        chapter_combined_list.append(chapter_md)
        clean_chapters_eng.append(res["clean_eng"])

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
