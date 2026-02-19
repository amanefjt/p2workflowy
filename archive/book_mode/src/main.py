# -*- coding: utf-8 -*-
"""
p2workflowy - 英語論文・書籍処理プログラム
"""
import sys
import asyncio
import argparse
import json
import re
import shutil
from typing import List, Dict, Any, cast
from pathlib import Path
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

from .skills import PaperProcessorSkills
from .utils import Utils
from .constants import EXCLUDE_SECTION_KEYWORDS
from .book_processor import map_book_toc, split_by_anchors
from .llm_processor import LLMProcessor


def print_progress(message: str, percentage: int | None = None) -> None:
    """進捗を表示する"""
    if percentage is not None:
        # パーセンテージ表示時は、行の最後にカーソルを置いて上書き可能にする
        print(f"\r[{percentage:3d}%] {message}", end="", flush=True)
    else:
        # \r で行頭に戻り、前回の表示を上書きしてから改行する。
        # 古いメッセージが残らないよう、スペースでパディングする。
        print(f"\r{message:<80}")


async def run_paper_pipeline(input_file: Path, skills: PaperProcessorSkills, glossary_text: str):
    """論文モードのパイプライン"""
    output_final = input_file.parent / f"{input_file.stem}_output.txt"
    output_structured = input_file.parent / f"{input_file.stem}_structured_eng.md"

    raw_text = Utils.read_text_file(input_file)

    # Phase 1: Semantic Mapping (レジュメ生成)
    print_progress("Phase 1: 原文から意味的な構造（レジュメ）を把握中...", 10)
    resume_text = await skills.generate_resume(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
    )
    print_progress("Phase 1: レジュメ生成完了", 30)

    # Phase 2: Anchored Structuring (構造化)
    print_progress("Phase 2: レジュメをガイドにして原文の構造を復元中...", 30)
    structure_hint = Utils.extract_structure_from_resume(resume_text)
    structured_md = await skills.structure_text_with_hint(
        raw_text,
        structure_hint,
        progress_callback=lambda msg: print_progress(f"Phase 2: {msg}")
    )
    Utils.write_text_file(output_structured, structured_md)
    print_progress("Phase 2: 構造化完了", 50)

    # 追加: 翻訳前に不要なセクションを物理的に削除 (References 等)
    structured_md = Utils.remove_unwanted_sections(structured_md, EXCLUDE_SECTION_KEYWORDS)

    # Phase 3: Contextual Translation (並列翻訳)
    print_progress("Phase 3: 文脈を考慮した並列翻訳を実施中...", 50)
    translated_text = await skills.translate_academic(
        structured_md,
        glossary_text,
        summary_context=resume_text,
        progress_callback=lambda msg: print_progress(f"Phase 3: {msg}")
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


async def process_single_chapter(
    chapter_idx: int,
    total_chapters: int,
    chapter_title: str,
    chapter_text: str,
    skills: PaperProcessorSkills,
    glossary_text: str,
    context_guide: str = "",
) -> tuple[str, str]:
    """
    1つの章を論文モードパイプラインで処理する（Phase 3 の個別処理単位）。
    各章を独立した「論文」とみなし、PaperProcessorSkills の3フェーズを適用する。
    
    Returns:
        (resume_text, translated_text)
    """
    prefix = f"  [第{chapter_idx + 1}章/{total_chapters}章 '{chapter_title}']"
    print_progress(f"{prefix} 処理開始...")

    try:
        # Phase 3a: レジュメ生成（章単位）
        print_progress(f"{prefix} レジュメ生成中...")
        resume_text = await skills.generate_resume(
            chapter_text,
            context_guide=context_guide,
            progress_callback=lambda msg: print_progress(f"{prefix} Resume: {msg}")
        )

        # Phase 3b: 構造化（章単位）
        print_progress(f"{prefix} 構造化中...")
        structure_hint = Utils.extract_structure_from_resume(resume_text)
        structured_md = await skills.structure_text_with_hint(
            chapter_text,
            structure_hint,
            context_guide=context_guide,
            progress_callback=lambda msg: print_progress(f"{prefix} Structure: {msg}")
        )

        # 追加: 翻訳前に不要なセクションを物理的に削除 (References 等)
        structured_md = Utils.remove_unwanted_sections(structured_md, EXCLUDE_SECTION_KEYWORDS)

        # Phase 3c: 翻訳（章単位 — チャンクの並列処理は translate_academic 内部で実行）
        print_progress(f"{prefix} 翻訳中...")
        translated_text = await skills.translate_academic(
            structured_md,
            glossary_text,
            summary_context=resume_text,
            context_guide=context_guide,
            progress_callback=lambda msg: print_progress(f"{prefix} Translate: {msg}")
        )

        print_progress(f"{prefix} 完了 ✓")
        return resume_text, translated_text

    except Exception as e:
        error_msg = f"{prefix} エラー: {e}"
        print_progress(error_msg)
        err_ret = f"[第{chapter_idx + 1}章 '{chapter_title}' の処理中にエラーが発生しました: {e}]"
        return err_ret, err_ret


async def run_book_pipeline(input_file: Path, skills: PaperProcessorSkills, glossary_text: str):
    """
    書籍モードのパイプライン: Map-Split-Reuse パターン

    Phase 1: Full-Text Mapping (AI が ToC + Anchor を JSON で返す)
    Phase 2: Anchor-Based Splitting (ファジーマッチで物理分割)
    Phase 3: Reuse Paper Mode (各章に論文モード3フェーズを順次適用、章内チャンクは並列)
    Phase 4: Mechanical Merging (テンプレートリテラルで結合)
    """
    output_final = input_file.parent / f"{input_file.stem}_output.txt"

    raw_text = Utils.read_text_file(input_file)
    llm = LLMProcessor()

    # === Phase 0: Book Resume Generation (全体レジュメ) ===
    print_progress("Phase 0: 書籍全体のレジュメを生成中...", 0)
    # 本の冒頭（Introduction等）をコンテキストとして全体レジュメを生成
    # 全文は長すぎるため、最初の5000文字を使用
    book_intro_text = raw_text[:5000]
    book_resume = await skills.generate_resume(
        book_intro_text,
        context_guide=f"This is the introductory part of the book '{input_file.stem}'. Please generate a summary for the WHOLE book based on this introduction.",
        progress_callback=lambda msg: print_progress(f"Phase 0: {msg}")
    )
    print_progress("Phase 0: 完了", 5)

    # === Phase 1: Full-Text Mapping ===
    print_progress("Phase 1: 書籍全文から目次構造を解析中...", 10)
    toc_mappings = await map_book_toc(
        llm, raw_text,
        progress_callback=lambda msg: print_progress(f"Phase 1: {msg}")
    )
    print_progress(f"Phase 1: 完了 - {len(toc_mappings)}章を検出", 15)
    for i, m in enumerate(toc_mappings):
        print(f"  {i+1}. {m['chapter_title']}")

    # === Phase 2: Anchor-Based Splitting ===
    print_progress("Phase 2: アンカーテキストで章を分割中...", 20)
    chapters = split_by_anchors(
        raw_text, toc_mappings,
        progress_callback=lambda msg: print_progress(f"Phase 2: {msg}")
    )
    print_progress(f"Phase 2: 完了 - {len(chapters)}章に分割", 25)

    # === Phase 3: 各章を順次処理（章内の翻訳チャンクは並列） ===
    print_progress(f"Phase 3: {len(chapters)}章を論文モードで順次処理中...", 30)

    chapter_results = []
    for i, ch in enumerate(chapters):
        # 序論などで他章への言及が見出しになるのを防ぐためのガイド
        context_guide = f"This text is Chapter {i+1} '{ch['title']}' of the book. Do not treat references to other chapters as new headings. Make sure to structure ONLY the content of this chapter."
        
        # 戻り値は (resume_text, translated_text) のタプル
        result_tuple = await process_single_chapter(
            chapter_idx=i,
            total_chapters=len(chapters),
            chapter_title=ch["title"],
            chapter_text=ch["text"],
            skills=skills,
            glossary_text=glossary_text,
            context_guide=context_guide
        )
        chapter_results.append(result_tuple)

    print_progress("Phase 3: 全章の処理完了", 90)

    # === Phase 4: Mechanical Merging (機械的結合) ===
    print_progress("Phase 4: 成果物を統合中...", 90)

    # 書籍タイトルの抽出（ファイル名をデフォルトとする）
    book_title = input_file.stem

    # 全体レジュメの Workflowy 変換
    book_resume_wf = Utils.markdown_to_workflowy(book_resume)
    book_resume_section = "  - 全体レジュメ\n" + "\n".join(["    " + line for line in book_resume_wf.splitlines()])

    # 各章の結合
    all_chapter_sections = []
    for ch, (ch_resume, ch_translated) in zip(chapters, chapter_results):
        chapter_title = ch["title"]
        
        # 章ノード生成 (リファクタリング: テスト可能にするため関数化)
        section = format_chapter_node(chapter_title, ch_resume, ch_translated)
        all_chapter_sections.append(section)

    final_content = f"- {book_title}\n{book_resume_section}\n" + "\n".join(all_chapter_sections)
    Utils.write_text_file(output_final, final_content)

    print_progress("Phase 4: 処理完了!", 100)
    print(f"\n成果物: {output_final}")
    print(f"処理した章数: {len(chapters)}")


def format_chapter_node(chapter_title: str, ch_resume: str, ch_translated: str) -> str:
    """
    章のタイトル、レジュメ、翻訳本文を結合してWorkflowy形式のノード文字列を作成する
    """
    # 章レジュメ
    ch_resume_wf = Utils.markdown_to_workflowy(ch_resume)
    ch_resume_node = "    - 章レジュメ\n" + "\n".join(["      " + line for line in ch_resume_wf.splitlines()])

    # 翻訳本文 (H1除去)
    lines = ch_translated.splitlines()
    if lines and lines[0].strip().startswith('# '):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    
    ch_trans_wf = Utils.markdown_to_workflowy(body)
    # 「本文翻訳」ノードを削除し、章タイトル直下に配置（インデント4スペース）
    ch_trans_node = "\n".join(["    " + line for line in ch_trans_wf.splitlines()])

    # 章ノード結合
    return f"  - {chapter_title}\n{ch_resume_node}\n{ch_trans_node}"



async def main():
    """メインエントリーポイント"""
    project_dir = Path(__file__).parent.parent
    glossary_file = project_dir / "glossary.csv"
    
    # argparseでコマンドライン引数を処理
    parser = argparse.ArgumentParser(
        description="p2workflowy - 英語論文・書籍処理",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="処理対象のファイルパス"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["paper", "p", "1", "book", "b", "2"],
        default="paper",
        help="処理モード: paper/p/1=論文, book/b/2=書籍"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="テストモード: 中間生成物を <input_file>_test/ ディレクトリに保存"
    )
    
    args = parser.parse_args()
    
    # インタラクティブモード（引数なし）
    if not args.input_file:
        print("\n" + "=" * 60)
        print("p2workflowy - 英語論文・書籍処理")
        print("=" * 60)
        input_path_str = input("ファイルパス: ").strip()
        print("\nモードを選択してください:")
        print("  1. 論文モード (paper)")
        print("  2. 書籍モード (book)")
        mode_input = input("モード [1]: ").strip() or "1"
        mode = "book" if mode_input in ["2", "book", "b"] else "paper"
        test_mode = False
    else:
        input_path_str = args.input_file
        mode = args.mode
        test_mode = args.test
    
    # モードの正規化
    if mode in ["paper", "p", "1"]:
        mode = "paper"
    elif mode in ["book", "b", "2"]:
        mode = "book"
    
    input_file = Path(input_path_str.strip("'\""))
    if not input_file.exists():
        print(f"エラー: ファイルが見つかりません: {input_file}")
        return

    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""

    print(f"\n処理を開始します... (モード: {'📄 論文' if mode == 'paper' else '📖 書籍'})")
    skills = PaperProcessorSkills()

    if mode == "book":
        await run_book_pipeline(input_file, skills, glossary_text)
    else:
        await run_paper_pipeline(input_file, skills, glossary_text)


if __name__ == "__main__":
    asyncio.run(main())
