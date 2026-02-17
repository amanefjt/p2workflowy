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

from .skills import PaperProcessorSkills, BookProcessorSkills
from .utils import Utils
from .constants import EXCLUDE_SECTION_KEYWORDS


def print_progress(message: str, percentage: int | None = None) -> None:
    """進捗を表示する"""
    if percentage is not None:
        print(f"\r[{percentage:3d}%] {message}", end="", flush=True)
    else:
        print(f"\n{message}")


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


async def run_book_pipeline(input_file: Path, skills: BookProcessorSkills, glossary_text: str, test_mode: bool = False):
    """
    書籍モードのパイプライン
    test_mode=True の場合、中間生成物を <input_file>_test/ に保存する
    """
    output_final = input_file.parent / f"{input_file.stem}_book_output.txt"
    
    # テストモード時の保存先ディレクトリを作成
    test_dir = None
    if test_mode:
        test_dir = input_file.parent / f"{input_file.stem}_test"
        
        # テストディレクトリ全体の作成（存在すればそのまま使用、サブディレクトリは個別にクリア）
        test_dir.mkdir(exist_ok=True)
        
        # 各サブディレクトリをクリアして再作成
        subdirs = [
            "03_chapters", 
            "04_chapter_resumes", 
            "05_section_tocs", 
            "06_structured", 
            "07_translated"
        ]
        
        for subdir in subdirs:
            p = test_dir / subdir
            if p.exists():
                shutil.rmtree(p)
            p.mkdir(exist_ok=True)
        print_progress(f"テストモード: 中間生成物を {test_dir} に保存します")
    raw_text = Utils.read_text_file(input_file)

    # 1) 全体レジュメ生成
    print_progress("Step 1: 全体レジュメを生成中...", 5)
    global_resume = await skills.generate_global_resume(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Step 1: {msg}")
    )
    # テストモード: 全体レジュメを保存
    if test_mode and test_dir:
        (test_dir / "01_global_resume.md").write_text(global_resume, encoding="utf-8")
    print_progress("Step 1: 全体レジュメ生成完了", 15)

    # 2) 目次抽出
    print_progress("Step 2: 目次を抽出中...", 15)
    toc = await skills.extract_toc(
        raw_text,
        progress_callback=lambda msg: print_progress(f"Step 2: {msg}")
    )
    # テストモード: TOCを保存
    if test_mode and test_dir:
        (test_dir / "02_toc.json").write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
    if not toc:
        print_progress("Warning: 目次が抽出できませんでした。全文を一つのセクションとして処理します。")
        toc = [{"title": "Main Content", "type": "chapter"}]
    print_progress(f"Step 2: 目次抽出完了 ({len(toc)}項目)", 20)

    # 3) 章ごとに分割
    print_progress("Step 3: テキストを章ごとに分割中...", 25)
    chapters = skills.split_by_toc(raw_text, toc)
    # テストモード: 各章のテキストを保存
    if test_mode and test_dir:
        for idx, chap in enumerate(chapters, 1):
            # タイトル除外チェック
            if any(keyword in chap["title"].lower() for keyword in EXCLUDE_SECTION_KEYWORDS):
                continue

            sanitized_title = re.sub(r'[^\w\s-]', '', chap["title"])[:50].strip().replace(' ', '_')
            chapter_filename = f"chapter_{idx:03d}_{sanitized_title}.txt"
            (test_dir / "03_chapters" / chapter_filename).write_text(chap["text"], encoding="utf-8")
    print_progress(f"Step 3: 分割完了 ({len(chapters)}章)", 30)

    # 4) それぞれに対して処理
    final_output_blocks = []
    
    for i, chap in enumerate(chapters):
        chap_title = chap["title"]
        chap_text = chap["text"]
        
        print_progress(f"Step 4 [{i+1}/{len(chapters)}]: {chap_title} を処理中...", 30 + int(60 * (i / len(chapters))))
        
        # タイトル除外チェック
        if any(keyword in chap_title.lower() for keyword in EXCLUDE_SECTION_KEYWORDS):
            print_progress(f"  Skipping excluded chapter: {chap_title}")
            continue
        
        # 本文がない場合はタイトルのみ追加
        if len(chap_text) < 100 and chap["type"] == "part":
            final_output_blocks.append(f"- {chap_title}")
            continue

        # 4-a) 章レジュメ生成
        chap_resume = await skills.generate_chapter_resume(chap_text)
        chap_resume_wf = Utils.markdown_to_workflowy(chap_resume)
        
        # テストモード: 章レジュメを保存
        if test_mode and test_dir:
            resume_filename = f"chapter_{i+1:03d}_resume.md"
            (test_dir / "04_chapter_resumes" / resume_filename).write_text(chap_resume, encoding="utf-8")
        
        # 4-b) 章内の節（Section）分割
        print_progress(f"  [{chap_title}] 内部構成を分析中...")
        section_toc = await skills.extract_chapter_sections(chap_text)
        
        # テストモード: 節TOCを保存
        if test_mode and test_dir:
            section_toc_filename = f"chapter_{i+1:03d}_sections.json"
            (test_dir / "05_section_tocs" / section_toc_filename).write_text(
                json.dumps(section_toc, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        
        if not section_toc:
            print_progress(f"  [{chap_title}] Warning: 節分割に失敗しました。一括処理を試みます。")
            sections = [{"title": chap_title, "text": chap_text}]
        else:
            sections = skills.split_chapter_by_sections(chap_text, section_toc)
            print_progress(f"  [{chap_title}] {len(sections)}個の節に分割完了。並列処理を開始します。")

        # 4-c) 各節を並列に「構造化＋翻訳」
        # セクションごとに [構造化 -> 翻訳] をセットで並列実行
        print_progress(f"  [{chap_title}] {len(sections)}個の節を並列処理中...")
        
        tasks: List[asyncio.Task[str]] = []
        for j, s in enumerate(sections):
            sec_title = s.get("title", f"Section {j+1}")
            sec_text = s["text"]
            # 個別に進捗を表示するためのコールバック
            cb = lambda msg, idx=j: print_progress(f"    [Sec {idx+1}/{len(sections)}] {msg}")
            
            # process_section は内部で構造化と翻訳を await する
            coro = skills.process_section(sec_text, sec_title, chap_resume, 
                                        glossary_text=glossary_text, 
                                        progress_callback=cb,
                                        test_mode=test_mode,
                                        test_dir=test_dir,
                                        chapter_idx=i+1,
                                        section_idx=j+1)
            tasks.append(asyncio.create_task(coro))

        # すべての節の完了を待機
        section_results = await asyncio.gather(*tasks)
        
        # 章の結果を統合
        combined_chap = f"- {chap_title}\n"
        combined_chap += f"  - {chap_title} レジュメ\n" + "\n".join(["    " + line for line in chap_resume_wf.splitlines()]) + "\n"
        
        for res in section_results:
            # res は str 型であることを保証
            if not isinstance(res, str):
                res = str(res)
            
            # 各節の翻訳結果（構造化済み）からタイトルを除去（必要に応じて）
            res_lines = res.splitlines()
            first_line = next(iter(res_lines), "")
            if first_line.strip().startswith('# '):
                res_lines = res_lines[1:]
            
            body_text = "\n".join(res_lines).strip()
            res_wf = Utils.markdown_to_workflowy(body_text)
            combined_chap += "\n".join(["  " + line for line in res_wf.splitlines()]) + "\n"
        
        final_output_blocks.append(combined_chap.strip())

    # 5) まとめて出力
    print_progress("Step 5: 最終成果物を統合中...", 95)
    
    # 全体レジュメも含める
    global_resume_wf = Utils.markdown_to_workflowy(global_resume)
    global_resume_section = f"- 全体レジュメ\n" + "\n".join(["  " + line for line in global_resume_wf.splitlines()])
    
    book_title = input_file.stem
    all_content = "\n".join(final_output_blocks)
    
    # タイトルを最上位にして全体を統合
    final_content = f"- {book_title}\n{global_resume_section}\n{all_content}"
    Utils.write_text_file(output_final, final_content)
    
    print_progress("Step 5: 処理完了!", 100)
    print(f"\n成果物: {output_final}")


async def main():
    """メインエントリーポイント"""
    project_dir = Path(__file__).parent.parent
    glossary_file = project_dir / "glossary.csv"
    
    # argparseでコマンドライン引数を処理
    parser = argparse.ArgumentParser(
        description="p2workflowy - マルチモード・ドキュメント処理",
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
        choices=["paper", "book", "p", "b", "1", "2"],
        help="処理モード: paper/p/1 (論文モード) または book/b/2 (書籍モード)"
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
        print("p2workflowy - マルチモード・ドキュメント処理")
        print("=" * 60)
        input_path_str = input("ファイルパス: ").strip()
        print("\nモードを選択してください:")
        print("1: 論文モード (Paper Mode)")
        print("2: 書籍モード (Book Mode)")
        mode = input("> ").strip().lower()
        test_mode = False # インタラクティブモードではデフォルトFalse
    else:
        input_path_str = args.input_file
        mode = args.mode
        test_mode = args.test
    
    input_file = Path(input_path_str.strip("'\""))
    if not input_file.exists():
        print(f"エラー: ファイルが見つかりません: {input_file}")
        return

    glossary_text = Utils.load_glossary(glossary_file) if glossary_file.exists() else ""

    if mode in ['2', 'b', 'book']:
        print("\n[書籍モード] を開始します...")
        skills = BookProcessorSkills()
        await run_book_pipeline(input_file, skills, glossary_text, test_mode=test_mode)
    else:
        print("\n[論文モード] を開始します...")
        skills = PaperProcessorSkills()
        await run_paper_pipeline(input_file, skills, glossary_text)


if __name__ == "__main__":
    asyncio.run(main())
