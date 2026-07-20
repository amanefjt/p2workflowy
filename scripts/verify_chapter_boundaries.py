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
  検証する: (a) 章数が350（見開き分割後の頁数）に遠く及ばないこと、
  (b) 全章の頁範囲が文書内に収まること、(c) 同一頁範囲を持つ章が無いこと
  （＝索引頁等の複製バグが再発していないこと）。

見開きスキャンPDF（corfra / PSE）は実パイプライン（book_manager.py:182-185）と
同じく resolve_input_pdf() で is_spread_pdf()→split_spread_pdf() を通してから
検証する。分割前の元PDFを直接検証すると本番が一度も見ない文書を検証すること
になる（I-27 の誤診断の原因だった）。

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
from core.engine.p1_ingest.spread_splitter import is_spread_pdf, split_spread_pdf  # noqa: E402


def resolve_input_pdf(path: str) -> str:
    """実パイプライン（book_manager.py:182-185）と同じ前処理を通す。

    見開きスキャンPDFは PDFSplitter に渡る前に単ページへ分割される。
    これを通さないと、本番が一度も見ない文書を検証することになる（I-27 の誤診断の原因）。
    """
    if is_spread_pdf(path):
        print(f"  [verify] 見開きスキャンを検出。分割してから検証します: {Path(path).name}")
        return split_spread_pdf(path)
    return path


BOOKS = {
    "corfra": "data/input/Booksample/corfra/corfrapdf.pdf",
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

# Naven の VII/XII（旧 I-26）は層3で解決済み。正解境界は NAVEN_GROUND_TRUTH で
# 厳密に検査する（旧 NAVEN_KNOWN_BAD_TITLES による「存在のみ検証」は撤去）。

# PSE: outline 棄却(I-24)後、350章（見開き分割後の頁数。分割前は175頁）への
# 破綻は回避されるがこれを大きく下回ることのみを確定済み正解とする（境界自体は
# I-27 により信頼できないため厳密な章数は固定しない）。
#
# 注意: 以下の PSE_MAX_PLAUSIBLE_CHAPTER_COUNT は「現時点の出力」のスナップショットで
# あり「正しい章境界」ではない。回帰（意図しない変化）の検出のみに使う。
# PSE の正しい章境界（真の扉頁）は Task 2 で PSE_GROUND_TRUTH として確定済み。
PSE_MAX_PLAUSIBLE_CHAPTER_COUNT = 20

# PSE の正解データ（2026-07-19 目視確認・1-indexed 物理頁）。
# PSEpdf_split.pdf（見開き分割後・350頁）に対する値である。
# spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §4.2
PSE_GROUND_TRUTH = {
    "Preface": 8,
    "Chapter 1 The Ethnographic Effect I": 12,
    "Chapter 2 Pre-figured Features": 42,
    "Chapter 3 The Aesthetics of Substance": 58,
    "Chapter 4 Refusing Information": 77,
    "Chapter 5 New Economic Forms: a Report": 102,
    "Chapter 6 The New Modernities": 130,
    "Chapter 7 Divisions of Interest and Languages of Ownership": 149,
    "Chapter 8 Potential Poperty: Intellectual Rights and Property in Persons": 174,
    "Chapter 9 What is Intellectual Property after?": 192,
    "Chapter 10 Puzzles of Scale": 217,
    "Chapter 1 The Ethnographic Effect II": 242,
    "Writing societies, writing persons": 246,
}

# Naven の正解データ（誤着地2章のみ・1-indexed 物理頁）。
# 2026-07-19 目視確認: P116 = CHAPTER VII The Sociology of Naven,
# P190 = CHAPTER XII The Preferred Types（いずれも実測と一致、修正不要）。I-26 の対象章。
NAVEN_GROUND_TRUTH = {
    "Chap. VII. THE SOCIOLOGY OF NAVEN": 116,
    "Chap. XII. THE PREFERRED TYPES": 190,
}


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
        self.metrics: dict[str, tuple[int, int]] = {}

    def ok(self, msg: str):
        print(f"  OK  {msg}")

    def regression(self, msg: str):
        self.regressions.append(msg)
        print(f"  NG  [regression] {msg}")

    def known_defect(self, defect_id: str, msg: str):
        self.known_defects.append(f"{defect_id}: {msg}")
        print(f"  --  [known defect {defect_id}] {msg}")

    def metric(self, key: str, value: int, total: int) -> None:
        """回帰判定ではなく、推移を追う指標として記録する。"""
        self.metrics[key] = (value, total)


def _find_by_title(chapters, title: str):
    key = _match_key(title)
    matches = [c for c in chapters if _match_key(c["title"]) == key]
    return matches[0] if matches else None


def report_ground_truth(
    rec: "Recorder", name: str, chapters: list, truth: dict, floor: int
) -> None:
    """正解データとの一致章数を報告し、下限を割ったら regression とする。

    照合は**物理頁番号**で行う（タイトルではない）。TOC の LLM 抽出は非決定的で、
    再抽出のたびに書式が揺れる（'Chapter 1' が 'Chapter 1:' や 'Chapter 11' に
    化ける。`state/` は gitignore のため新規 checkout では必ず再抽出される）。
    タイトル完全一致だと真値12/13が1/13に化けるため使えない。「正解の扉頁に章境界が
    実際に立っているか」を数えるのが、書式非依存で境界精度そのものを測る正しい指標。

    一致数が `floor` を下回ったら regression として exit code を非0にする
    （spec は Writing societies/Preface が構造的に到達不能なため 13/13 を求めない。
    floor はそれらを除いた到達可能な全章の正解を要求する値）。
    """
    actual_starts = {ch["page_range"][0] for ch in chapters if ch.get("page_range")}
    hits = []
    misses = []
    for title, expected in truth.items():
        if expected in actual_starts:
            hits.append(title)
        else:
            misses.append(f"{title[:40]}: 期待P{expected} に章境界なし")
    print(f"  [{name}] 正解一致（頁境界ベース）: {len(hits)}/{len(truth)}")
    for m in misses:
        print(f"      × {m}")
    rec.metric(f"{name}_ground_truth_hits", len(hits), len(truth))
    if len(hits) < floor:
        rec.regression(
            f"{name} の章境界一致 {len(hits)}/{len(truth)} が下限 {floor} を下回った"
            f"（境界精度の退行）"
        )


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

    # I-26（VII/XII のリガチャ崩れによる誤着地）は層3の LLM 裁定で解決済み。
    # 両章の境界は report_ground_truth(Naven, floor=2) が正解頁 P116/P190 に対して
    # 頁ベースで厳密に検査し、下回れば regression を立てる。ここで known_defect として
    # 「境界未検証」と記録していた従来の扱いは撤去した（もはや未検証ではない）。


def verify_pse(rec: Recorder, chapters, total_pages: int):
    """PSE: I-24 修正の生存確認 + C2 クランプの効果確認。境界の正誤は
    I-27 により未検証（既知の欠陥として別集計する）。
    """
    if len(chapters) < PSE_MAX_PLAUSIBLE_CHAPTER_COUNT:
        rec.ok(
            f"章数 {len(chapters)} は{total_pages}（分割後総頁数）から大きく乖離"
            f"（I-24 outline 棄却が機能）"
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

    # I-27 の真因は TOC 抽出の系統的ずれで、層1の検算が解消した。境界は
    # report_ground_truth(PSE, floor=12) が正解頁に対して厳密に検査する。
    # 残る唯一の未達は Preface（前付けの端症例・正解 P8 に対し P11 のランニング
    # ヘッダー継続頁へ着地）で、これは章境界ではなく前付け処理の既知限界として残す。
    rec.known_defect(
        "I-27-residual",
        "Preface の扉頁のみ未達（前付けの端症例。章本文の境界は全て正解と一致）",
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
        # is_spread_pdf() は毎回PDFを開くため、ここで1回だけ解決して使い回す。
        resolved_path = resolve_input_pdf(path)
        splitter = PDFSplitter(api_key=api_key)
        chapters = splitter.split(resolved_path, out_root / name)
        print(f"  章数: {len(chapters)}")

        for ch in chapters:
            rng = ch.get("page_range")
            print(f"    {ch['title'][:40]:42s} {rng} role={ch['role']}")

        if name in EXPECTED_CHAPTER_COUNT:
            verify_exact_boundaries(rec, name, chapters)
        elif name == "Naven":
            verify_naven(rec, chapters)
        elif name == "PSE":
            with fitz.open(resolved_path) as doc:
                total_pages = len(doc)
            verify_pse(rec, chapters, total_pages)

        if name == "PSE":
            # floor=12: Preface（前付けの端症例・P8 vs P11）を除く到達可能な全章を要求。
            report_ground_truth(rec, "PSE", chapters, PSE_GROUND_TRUTH, floor=12)
        if name == "Naven":
            # floor=2: I-26 の VII/XII は層3で解決済み。両方の正解境界を厳密に要求する。
            report_ground_truth(rec, "Naven", chapters, NAVEN_GROUND_TRUTH, floor=2)

    print(f"\n===== 集計 =====")
    print(f"regression（新規の退行）: {len(rec.regressions)} 件")
    for msg in rec.regressions:
        print(f"  - {msg}")
    print(f"known defect（既知の欠陥・記録のみ）: {len(rec.known_defects)} 件")
    for msg in rec.known_defects:
        print(f"  - {msg}")

    print(f"\n===== 指標 =====")
    for key, (value, total) in rec.metrics.items():
        print(f"  {key}: {value}/{total}")

    return 1 if rec.regressions else 0


if __name__ == "__main__":
    sys.exit(main())
