"""章ごとに分かれた日本語 Markdown から、レジュメだけを作る。

翻訳・構造化は行わない。既存パイプライン（core/）は変更せず、そのプロンプトと関数を再利用する。

    1. 本全体のレジュメ → `00_[Summary]_書籍全体の要約_(Overall_Summary).md`
       （BookManager の BOOK_SUMMARY_PROMPT / BOOK_SUMMARY_COMBINE_PROMPT 経路を写したもの）
    2. 章ごとのレジュメ → `<章ファイル名の拡張子を除いた部分>_レジュメ.md`
       （core.phase2_meta.generate_resume(is_book=True, resume_context=書名・著者＋本全体のレジュメ)）

出力先は入力フォルダと同じ。既にあるファイルは飛ばすので、途中で止まっても再実行で続きから作れる。

    venv/bin/python scripts/resume_from_md.py <章Markdownのフォルダ>
    venv/bin/python scripts/resume_from_md.py <フォルダ> --only 00_はじめに.md   # 一章だけ試す
"""

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import GEMINI_API_KEY, GEMINI_API_KEY_FREE_1, print_log
from core.llm_client import apply_tier_settings, call_gemini, get_default_model, key_rotator, load_coreprompts
from core.phase2_meta import generate_resume

BOOK_SUMMARY_NAME = "00_[Summary]_書籍全体の要約_(Overall_Summary).md"
RESUME_SUFFIX = "_レジュメ"

# 無料枠の TPM 上限は一律 250,000 tok（docs/gemini_models.md §4）。core/book_manager.py の
# FREE_TIER_TPM_SAFE_CHAR_LIMIT（800,000字）は英語 PDF（≒4字/tok）の換算なので日本語には
# 使えない。日本語は gemini-3.6-flash の count_tokens 実測で 1.67 字/tok（2026-10-05、
# 橋爪『土地を切り裂く人々』265,426字 = 158,775 tok）。プロンプト分と余裕を見て
# 約 20 万 tok 相当の 300,000 字を、無料キー時に単発送信してよい上限とする。
FREE_TIER_TPM_SAFE_CHAR_LIMIT_JA = 300_000


def read_nfc(path: Path) -> str:
    """本文が NFD で保存されているファイルがある（Cultivator 由来）ので NFC に揃えて読む。"""
    return unicodedata.normalize("NFC", path.read_text(encoding="utf-8"))


def list_chapters(folder: Path) -> List[Path]:
    """章 Markdown を並び順で返す。_meta.md・生成物（レジュメ・全体要約）は除く。"""
    chapters = []
    for p in sorted(folder.glob("*.md")):
        name = unicodedata.normalize("NFC", p.name)
        if name.startswith("_") or name == BOOK_SUMMARY_NAME or p.stem.endswith(RESUME_SUFFIX):
            continue
        chapters.append(p)
    return chapters


def read_bibliography(folder: Path) -> str:
    """`_meta.md` の「書名：」「著者：」行を `resume_context` の先頭に足す書誌ヘッダにする。

    章の本文に書名はなく、本全体のレジュメだけを渡すと、章題や概念名（終章の副題など）を
    書名と取り違えて「本書『…』」と書いてしまう（2026-10-05、00_はじめに で実際に発生）。
    フォルダ名は Workflowy 側の名前で書名と一致しないことがある（「土地」/「大地」）ため使わない。
    """
    meta = folder / "_meta.md"
    if not meta.exists():
        return ""
    lines = []
    for line in read_nfc(meta).splitlines():
        for key in ("書名", "著者"):
            if line.startswith(f"- {key}："):
                lines.append(f"{key}：{line.split('：', 1)[1].strip()}")
    return "\n".join(lines)


def split_by_chapters(chapters: List[Tuple[str, str]], max_chars: int) -> List[str]:
    """章の境界を保ったまま max_chars 以下のブロックにまとめる（1章が超える場合は単独）。

    BookManager._split_into_chunks は段落境界で切るが、ここでは章ごとのファイルが既にあり、
    BOOK_SUMMARY_PROMPT が章見出しの一字一句照合を求めるため、章の途中では切らない。
    """
    blocks: List[str] = []
    current = ""
    for _, text in chapters:
        candidate = f"{current}\n\n{text}" if current else text
        if len(candidate) > max_chars and current:
            blocks.append(current)
            current = text
        else:
            current = candidate
    if current:
        blocks.append(current)
    return blocks


def generate_book_resume(chapters: List[Tuple[str, str]], expertise: str, api_key: str, tier: str) -> str:
    """本全体のレジュメ。BookManager._generate_global_context_single / _chunked の写し（用語集は作らない）。"""
    prompts = load_coreprompts()
    full_text = "\n\n".join(text for _, text in chapters)

    # 有料キーなら単発（TPM 上限は無料枠のみの制約）。無料キーで日本語の安全域に収まる場合も単発。
    if tier == "paid" or len(full_text) <= FREE_TIER_TPM_SAFE_CHAR_LIMIT_JA:
        print_log(f"  [resume_from_md] 本全体のレジュメを単発で生成中... ({len(full_text)} 字)")
        prompt = prompts["BOOK_SUMMARY_PROMPT"].replace("{expertise}", expertise) \
            .replace("{context_guide}", "書籍全体の核心的問い、論理構成を俯瞰して下さい。") \
            .replace("{text}", full_text)
        return call_gemini(prompt, api_key=api_key, model=get_default_model("resume"), thinking_level="High")

    blocks = split_by_chapters(chapters, FREE_TIER_TPM_SAFE_CHAR_LIMIT_JA)
    print_log(f"  [resume_from_md] 全文が無料枠のTPM安全域（{FREE_TIER_TPM_SAFE_CHAR_LIMIT_JA}字）を超えるため{len(blocks)}ブロックに分割します")
    partials = []
    for i, block in enumerate(blocks, 1):
        print_log(f"  [resume_from_md] ブロック {i}/{len(blocks)} のレジュメを生成中...")
        prompt = prompts["BOOK_SUMMARY_PROMPT"].replace("{expertise}", expertise) \
            .replace("{context_guide}", f"これは書籍全体のうち分割ブロック {i}/{len(blocks)} 番目です。このブロックの範囲内で核心的な議論を俯瞰して下さい。") \
            .replace("{text}", block)
        partial = call_gemini(prompt, api_key=api_key, thinking_level="High")
        partials.append(f"## [分割ブロック {i}/{len(blocks)}]\n\n{partial}")

    print_log("  [resume_from_md] 部分レジュメを統合中...")
    combine = prompts["BOOK_SUMMARY_COMBINE_PROMPT"].replace("{expertise}", expertise) \
        .replace("{partial_summaries}", "\n\n---\n\n".join(partials))
    return call_gemini(combine, api_key=api_key, thinking_level="High")


def main() -> int:
    parser = argparse.ArgumentParser(description="章ごとの日本語 Markdown からレジュメだけを作る")
    parser.add_argument("folder", help="章 Markdown が入っているフォルダ（出力先も同じ）")
    parser.add_argument("--only", metavar="FILE", help="この章ファイルだけ処理する（本全体のレジュメは必要なら作る）")
    parser.add_argument("--free", action="store_true", help="両方のキーがあっても無料キー（GEMINI_API_KEY_FREE_1）を使う")
    parser.add_argument("--expertise", default="文化人類学")
    args = parser.parse_args()

    folder = Path(args.folder)
    chapter_paths = list_chapters(folder)
    if not chapter_paths:
        print(f"エラー: {folder} に章 Markdown がありません。")
        return 1

    # キー選択は main.py と同じ（有料優先。--free で無料キーを指定）。1本だけ使い、途中で切り替えない。
    if GEMINI_API_KEY and not args.free:
        api_key, tier = GEMINI_API_KEY, "paid"
    elif GEMINI_API_KEY_FREE_1:
        api_key, tier = GEMINI_API_KEY_FREE_1, "free"
    else:
        print("エラー: GEMINI_API_KEY_FREE_1 / GEMINI_API_KEY のいずれも未設定です。.env を確認してください。")
        return 1
    key_rotator.configure([api_key], tiers=[tier])
    apply_tier_settings(tier, api_key=api_key)
    print(f"使用APIキー: {'有料' if tier == 'paid' else '無料'}キー1本 / 章 {len(chapter_paths)} 件")

    chapters = [(p.name, read_nfc(p)) for p in chapter_paths]

    # 1. 本全体のレジュメ（既にあれば再利用）
    summary_path = folder / BOOK_SUMMARY_NAME
    if summary_path.exists() and summary_path.read_text(encoding="utf-8").strip():
        print(f"本全体のレジュメは既にあります: {summary_path.name}")
        book_resume = summary_path.read_text(encoding="utf-8")
    else:
        book_resume = generate_book_resume(chapters, args.expertise, api_key, tier)
        if not book_resume.strip():
            print("エラー: 本全体のレジュメが空でした。")
            return 1
        summary_path.write_text(book_resume, encoding="utf-8")
        print(f"書き出しました: {summary_path.name} ({len(book_resume)} 字)")

    # 章レジュメの背景には、書誌ヘッダ（書名・著者）＋本全体のレジュメを渡す
    biblio = read_bibliography(folder)
    resume_context = f"{biblio}\n\n{book_resume}" if biblio else book_resume

    # 2. 章ごとのレジュメ（直列。1章ごとに保存し、既存分は飛ばす）
    only = unicodedata.normalize("NFC", args.only) if args.only else None
    failed = []
    for path, (name, text) in zip(chapter_paths, chapters):
        if only and unicodedata.normalize("NFC", name) != only:
            continue
        out_path = folder / f"{path.stem}{RESUME_SUFFIX}.md"
        if out_path.exists() and out_path.read_text(encoding="utf-8").strip():
            print(f"飛ばします（作成済み）: {out_path.name}")
            continue

        heading = text.lstrip().split("\n", 1)[0].lstrip("#").strip() or path.stem
        print(f"レジュメ生成: {name}")
        try:
            resume = generate_resume(text, api_key=api_key, expertise=args.expertise,
                                     is_book=True, resume_context=resume_context)
        except Exception as e:
            print(f"  失敗: {name}: {e}")
            failed.append(name)
            continue
        if not resume.strip():
            print(f"  失敗: {name}: レジュメが空でした")
            failed.append(name)
            continue
        out_path.write_text(f"# {heading}（レジュメ）\n\n{resume.strip()}\n", encoding="utf-8")
        print(f"  書き出しました: {out_path.name} ({len(resume)} 字)")

    if only and not any(unicodedata.normalize("NFC", n) == only for n, _ in chapters):
        print(f"エラー: --only {args.only} に一致する章ファイルがありません。")
        return 1
    if failed:
        print(f"\n未完了の章 {len(failed)} 件（再実行で続きから作れます）: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
