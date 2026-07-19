"""章分割境界の実測検証（I-22 / I-24 / I-25 / C2）。

VLM フルランは行わず PDFSplitter.split() のみを実行する。
Route 3 の LLM TOC 抽出は state/vlm_cache.json にキャッシュされる。

4冊すべてに対して意味のある assert を行う（C1）。corfra / relations は
境界インデックスまで厳密に検証する「確定済み正解」があるが、
Naven / PSE にはそれぞれ既知の未対応欠陥がある:

- Naven (I-26): リガチャ崩れにより Chap. VII / Chap. XII の2章だけ境界が
  誤っている。この2章は「結果に存在すること」のみを検証し、境界の
  正誤は検証しない（既知の欠陥として別集計する）。他の15章は
  確定済み正解として境界まで厳密に検証する。
- PSE (I-27): ランニングヘッダーが書名でありコンテンツスキャンがほぼ
  機能しないため、個々の章境界は信頼できない。境界の正誤は検証しない。
  代わりに C2（範囲外フォールバックのクランプ）が実際に効いているかを
  検証する: (a) 章数が175に遠く及ばないこと、(b) 全章の頁範囲が文書内に
  収まること、(c) 同一頁範囲を持つ章が無いこと（＝索引頁等の複製バグが
  再発していないこと）。

失敗は2種類に分類して集計する:
  - regressions: 新規の退行。存在すれば exit status を非0にする。
  - known_defects: I-26 / I-27 として記録済みの既知の欠陥。exit status
    には影響しないが、まとめて別枠で表示する（規範から漏れないように）。
"""
import sys
from pathlib import Path

import re

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.engine.p1_ingest.pdf_splitter import PDFSplitter  # noqa: E402
from core.config import PROJECT_ROOT  # noqa: E402

BOOKS = {
    "corfra": "data/input/Booksample/corfra/corfrapdf_split.pdf",
    "relations": "data/input/Booksample/relations/relationspdf.pdf",
    "Naven": "data/input/Booksample/Naven/Naven.pdf",
    "PSE": "data/input/Booksample/pse/PSEpdf.pdf",
}

# 実測済みの正解（仕様書 §2 参照）。境界まで厳密に検証する。
EXPECTED = {
    "corfra": {"3 Place": 77, "4 Things": 93, "7 Knowing": 153,
               "8 Anonymous Introduction": 171},
    "relations": {"1. Experimentations, English and Otherwise": 36,
                  "2. Registers of Comparison": 56,
                  "3. Expansion and Contraction": 82,
                  "4. The Dissimilar and the Different": 106,
                  "5. Enlightenment Dramas": 128,
                  "6. Kinship Unbound": 150},
}

# corfra / relations は章数まで確定している。
EXPECTED_CHAPTER_COUNT = {
    "corfra": 10,
    "relations": 12,
}

# Naven: 17章に分割されることが確定済みの正解（I-25 対応で回復）。
NAVEN_EXPECTED_CHAPTER_COUNT = 17

# Naven: 境界まで確定済みの正解15章（Chap. VII / XII を除く全章）。
NAVEN_EXPECTED_GOOD = {
    "Preface to the Second Edition": 12,
    "Foreword": 14,
    "Chap. I. METHODS OF PRESENTATION": 30,
    "Chap. II. THE NAVEN CEREMONIES": 35,
    "Chap. III. THE CONCEPTS OF STRUCTURE AND FUNCTION": 52,
    "Chap. IV. CULTURAL PREMISES RELEVANT TO THE WAU-LAUA RELATIONSHIP": 64,
    "Chap. V. SORCERY AND VENGEANCE": 83,
    "Chap. VI. STRUCTURAL ANALYSIS OF THE WAU-LAUA RELATIONSHIP": 103,
    "Chap. VIII. PROBLEMS AND METHODS OF APPROACH": 137,
    "Chap. IX. THE ETHOS OF IATMUL CULTURE: THE MEN": 152,
    "Chap. X. THE ETHOS OF IATMUL CULTURE: THE WOMEN": 171,
    "Chap. XI. ATTITUDES TOWARDS DEATH": 181,
    "Chap. XIII. ETHOLOGICAL CONTRAST, COMPETITION AND SCHISMOGENESIS": 200,
    "Chap. XIV. THE EXPRESSION OF ETHOS IN NAVEN": 227,
    "Chap. XV. THE EIDOS OF IATMUL CULTURE": 247,
}

# Naven: 境界が既知に誤っている2章（I-26）。「存在すること」だけを検証し、
# 境界の正誤は検証しない（境界の誤りは既知の欠陥として別集計する）。
NAVEN_KNOWN_BAD_TITLES = {
    "Chap. VII. THE SOCIOLOGY OF NAVEN": "I-26",
    "Chap. XII. THE PREFERRED TYPES": "I-26",
}

# PSE: outline 棄却(I-24)後、175章への破綻は回避されるがこれを大きく
# 下回ることのみを確定済み正解とする（境界自体は I-27 により信頼できない
# ため厳密な章数は固定しない）。
PSE_MAX_PLAUSIBLE_CHAPTER_COUNT = 20


def _match_key(title: str) -> str:
    """章タイトルの照合キー。先頭の章番号（OCR 崩れを含む）と記号を除去する。"""
    t = re.sub(r'^[\s\d IVXivx/.:|\[\]-]+', '', title)
    t = re.sub(r'[^\w\s]', ' ', t)
    return ' '.join(t.lower().split())


class Recorder:
    """regression（新規の退行）と known_defect（既知の欠陥）を分けて集計する。"""

    def __init__(self):
        self.regressions: list[str] = []
        self.known_defects: list[str] = []

    def ok(self, msg: str):
        print(f"  OK  {msg}")

    def regression(self, msg: str):
        self.regressions.append(msg)
        print(f"  NG  [regression] {msg}")

    def known_defect(self, defect_id: str, msg: str):
        self.known_defects.append(f"{defect_id}: {msg}")
        print(f"  --  [known defect {defect_id}] {msg}")


def _find_by_title(chapters, title: str):
    key = _match_key(title)
    matches = [c for c in chapters if _match_key(c["title"]) == key]
    return matches[0] if matches else None


def verify_exact_boundaries(rec: Recorder, name: str, chapters):
    """corfra / relations: 境界まで厳密検証（従来ロジックを維持）。"""
    for title, expected_idx in EXPECTED.get(name, {}).items():
        match = _find_by_title(chapters, title)
        if match is None:
            rec.regression(f"'{title}' が章リストに無い")
            continue
        actual = match["page_range"][0] - 1
        if actual == expected_idx:
            rec.ok(f"'{title}' 期待idx{expected_idx} 実際idx{actual}")
        else:
            rec.regression(f"'{title}' 期待idx{expected_idx} 実際idx{actual}")

    expected_count = EXPECTED_CHAPTER_COUNT.get(name)
    if expected_count is not None:
        if len(chapters) == expected_count:
            rec.ok(f"章数 {len(chapters)} (期待{expected_count})")
        else:
            rec.regression(f"章数 {len(chapters)} が期待{expected_count}と不一致")


def verify_naven(rec: Recorder, chapters):
    """Naven: 章数17 + 境界確定済み15章 + 既知不良2章(I-26, 存在のみ検証)。"""
    if len(chapters) == NAVEN_EXPECTED_CHAPTER_COUNT:
        rec.ok(f"章数 {len(chapters)} (期待{NAVEN_EXPECTED_CHAPTER_COUNT})")
    else:
        rec.regression(
            f"章数 {len(chapters)} が期待{NAVEN_EXPECTED_CHAPTER_COUNT}と不一致"
        )

    for title, expected_idx in NAVEN_EXPECTED_GOOD.items():
        match = _find_by_title(chapters, title)
        if match is None:
            rec.regression(f"'{title}' が章リストに無い")
            continue
        actual = match["page_range"][0] - 1
        if actual == expected_idx:
            rec.ok(f"'{title}' 期待idx{expected_idx} 実際idx{actual}")
        else:
            rec.regression(f"'{title}' 期待idx{expected_idx} 実際idx{actual}")

    for title, defect_id in NAVEN_KNOWN_BAD_TITLES.items():
        match = _find_by_title(chapters, title)
        if match is None:
            # 存在自体が消えた場合は既知の境界誤りを超える新規の退行。
            rec.regression(f"'{title}' が章リストに無い（{defect_id} の前提が崩れている）")
            continue
        actual = match["page_range"][0] - 1
        rec.known_defect(
            defect_id,
            f"'{title}' は存在するが境界は未検証（実際idx{actual}、リガチャ崩れによる既知の誤り）",
        )


def verify_pse(rec: Recorder, chapters, total_pages: int):
    """PSE: I-24 修正の生存確認 + C2 クランプの効果確認。境界の正誤は
    I-27 により未検証（既知の欠陥として別集計する）。
    """
    if len(chapters) < PSE_MAX_PLAUSIBLE_CHAPTER_COUNT:
        rec.ok(
            f"章数 {len(chapters)} は175から大きく乖離（I-24 outline 棄却が機能）"
        )
    else:
        rec.regression(
            f"章数 {len(chapters)} が {PSE_MAX_PLAUSIBLE_CHAPTER_COUNT} 以上。"
            f"outline 棄却(I-24)が機能していない可能性"
        )

    # C2: 全章の頁範囲が文書内に収まっていること。
    out_of_range = [
        c for c in chapters
        if not (1 <= c["page_range"][0] <= total_pages and 1 <= c["page_range"][1] <= total_pages)
    ]
    if not out_of_range:
        rec.ok(f"全{len(chapters)}章の頁範囲が文書範囲内 (1-{total_pages})")
    else:
        for c in out_of_range:
            rec.regression(
                f"'{c['title']}' の頁範囲 {c['page_range']} が文書範囲外 "
                f"(1-{total_pages})。C2 クランプが機能していない"
            )

    # C2: 同一頁範囲を持つ章が無いこと（索引頁等の複製バグの再発検知）。
    seen: dict[tuple, str] = {}
    duplicates_found = False
    for c in chapters:
        rng = c["page_range"]
        if rng in seen:
            duplicates_found = True
            rec.regression(
                f"'{c['title']}' と '{seen[rng]}' が同一頁範囲 {rng} を持つ。"
                f"C2 が防ぐべき索引頁複製バグの再発"
            )
        else:
            seen[rng] = c["title"]
    if not duplicates_found:
        rec.ok("重複する頁範囲の章は無い（索引頁複製バグは再発していない）")

    rec.known_defect(
        "I-27",
        "個々の章境界の正誤は検証していない（ランニングヘッダーが書名のため"
        "コンテンツスキャンが機能せず境界は信頼できない。詳細は "
        "troubleshooting_log.md 参照）",
    )


def main() -> int:
    api_key = None
    import os
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    out_root = PROJECT_ROOT / "state" / "_verify_chapters"
    rec = Recorder()

    for name, rel in BOOKS.items():
        path = str(PROJECT_ROOT / rel)
        print(f"\n===== {name} =====")
        splitter = PDFSplitter(api_key=api_key)
        chapters = splitter.split(path, out_root / name)
        print(f"  章数: {len(chapters)}")

        for ch in chapters:
            rng = ch.get("page_range")
            print(f"    {ch['title'][:40]:42s} {rng} role={ch['role']}")

        if name in EXPECTED_CHAPTER_COUNT:
            verify_exact_boundaries(rec, name, chapters)
        elif name == "Naven":
            verify_naven(rec, chapters)
        elif name == "PSE":
            with fitz.open(path) as doc:
                total_pages = len(doc)
            verify_pse(rec, chapters, total_pages)

    print(f"\n===== 集計 =====")
    print(f"regression（新規の退行）: {len(rec.regressions)} 件")
    for msg in rec.regressions:
        print(f"  - {msg}")
    print(f"known defect（既知の欠陥・記録のみ）: {len(rec.known_defects)} 件")
    for msg in rec.known_defects:
        print(f"  - {msg}")

    return 1 if rec.regressions else 0


if __name__ == "__main__":
    sys.exit(main())
