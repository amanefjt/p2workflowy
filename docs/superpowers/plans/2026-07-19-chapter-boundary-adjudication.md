# 章境界の検算・逸脱検出・LLM 裁定 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 書籍PDFの章境界確定に、TOC の検算（層1）・逸脱検出（層2）・LLM 裁定（層3）を追加し、I-26（Naven の誤着地2章）と I-27（PSE の TOC 系統的ずれ）を解消する。

**Architecture:** 既存の照合ループ（`_apply_content_scan` 内の `_classify_match` / `_score_candidate` / `_rescue_by_local_offset`）は一切変更しない。その前段に層1、後段に層2＋層3を新規モジュールとして追加し、`pdf_splitter.py` は配線のみを担う。

**Tech Stack:** Python 3.12+ / PyMuPDF (`fitz`) / Gemini API (`core.llm_client`) / pytest

**設計spec:** `docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md`

## Global Constraints

- 既存の照合ループ（`_classify_match` / `_score_candidate` / `_rescue_by_local_offset` / 探索窓）の**既存入力に対する振る舞いを変えない**。
  判定ロジック（`_classify_match` / `_score_candidate` / `_rescue_by_local_offset`）の中身は変更禁止。
  `_apply_content_scan` には、層1が新たに生む入力（`start_page=None`）への分岐と、
  層2へ渡す情報（`matched` / `start_page_logical`）の記録を追加してよい。
  ただし **`start_page` が数値である従来の入力に対しては、探索窓の式・採用される候補・
  ログ出力のすべてが従来と同一でなければならない**。これはテストで担保する（Task 5 Step 5）。
  （2026-07-19 ユーザー判断: 当初は「探索窓を変更しない」と書いていたが、
  層1が参照先を失った章に `None` を渡す以上ループ側の対応が不可避であり、
  制約を「既存入力に対する振る舞い不変」へ緩めた。）
- 層1は **Route 3（LLM TOC 抽出）のみ**に適用する。Route 1（ローカル TOC ファイル＝手動修正済み）と Route 2（ネイティブ outline＝物理頁直参照）には適用しない
- 定数はモジュール先頭にまとめる。マジックナンバーを関数内に直書きしない
- 外部ライブラリを追加しない（`fitz` / 標準ライブラリのみ）
- LLM が使えない場合（API キーなし・例外・不正な返り値）は必ず既存挙動以上に劣化しない経路へ落ちる
- コミットメッセージは日本語（技術用語・識別子は英語のまま）
- `core/` を変更するコミットでは `docs/management/` のログ追記が必要（`.claude/hooks/check_management_logs.sh` が注意喚起する）

## File Structure

| ファイル | 責務 |
|---|---|
| `core/engine/p1_ingest/page_number_map.py`（新規） | 印刷頁番号の回収と、印刷頁→物理頁オフセットの推定 |
| `core/engine/p1_ingest/toc_verifier.py`（新規） | TOC エントリの検算と shift 補正（層1） |
| `core/engine/p1_ingest/boundary_adjudicator.py`（新規） | 逸脱検出（層2）と LLM 裁定（層3） |
| `core/engine/p1_ingest/pdf_splitter.py`（変更） | 上記3モジュールの配線のみ |
| `core/coreprompts.json`（変更） | 層3のプロンプト `CHAPTER_OPENER_ADJUDICATION_PROMPT` を追加 |
| `scripts/verify_chapter_boundaries.py`（変更） | 見開き分割を通す。期待値定数を再取得 |
| `tests/unit/test_page_number_map.py`（新規） | 層1前半 |
| `tests/unit/test_toc_verifier.py`（新規） | 層1後半 |
| `tests/unit/test_boundary_adjudicator.py`（新規） | 層2・層3 |

---

### Task 1: 検証ハーネスを実パイプラインと一致させる

spec §4.1。これを先に行わないと以降のすべての検証が無効になる。

**Files:**
- Modify: `scripts/verify_chapter_boundaries.py`

**Interfaces:**
- Consumes: なし
- Produces: `resolve_input_pdf(path: str) -> str` — 見開きなら分割済みPDFのパスを返す

- [ ] **Step 1: 現状のスクリプト冒頭とBOOKS定数を確認する**

Run: `sed -n '1,60p' scripts/verify_chapter_boundaries.py`
Expected: `BOOKS` 辞書と `from core.engine.p1_ingest.pdf_splitter import PDFSplitter` が見える

- [ ] **Step 2: 見開き分割を通すヘルパーを追加する**

`scripts/verify_chapter_boundaries.py` の import 群の直後に追加する。

```python
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
```

- [ ] **Step 3: `main()` で PDFSplitter に渡す前に通す**

`scripts/verify_chapter_boundaries.py:245-246` 付近を次のように変更する。

```python
        splitter = PDFSplitter(api_key=api_key)
        chapters = splitter.split(resolve_input_pdf(path), out_root / name)
```

`verify_pse` に渡す `total_pages` も分割後の文書から取る必要がある。
`main()` 内の PSE 分岐を次のように変更する。

```python
        elif name == "PSE":
            with fitz.open(resolve_input_pdf(path)) as doc:
                total_pages = len(doc)
            verify_pse(rec, chapters, total_pages)
```

- [ ] **Step 4: 実行してベースラインを取得する**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py 2>&1 | tee /tmp/baseline_after_harness_fix.txt`
Expected: corfra 10章・PSE 13章・Naven 17章・relations 16章が表示される。
PSE で「範囲外」「スキップ」の警告が出ないこと（分割前は3章スキップされていた）。

- [ ] **Step 5: 期待値定数を実測値へ更新する**

Step 4 の出力を見て、`EXPECTED_CHAPTER_COUNT` と各書籍の期待頁範囲定数を実測値へ書き換える。
PSE と corfra は文書が変わった（175→350頁、106→212頁）ため全面的に取り直す。

**注意**: ここで書き込む値は「現状の出力」であり「正しい章境界」ではない。
PSE の正解は Task 2 で別途定義する。この定数は回帰検出用のスナップショットである。
その旨をコメントで明記すること。

```python
# 注意: この定数は「現時点の出力」のスナップショットであり、正解ではない。
# 回帰（意図しない変化）の検出のみに使う。正しい章境界は
# PSE_GROUND_TRUTH（Task 2 で追加）を参照。
```

- [ ] **Step 6: 再実行して retro が 0 件になることを確認する**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py`
Expected: `regression（新規の退行）: 0 件`

- [ ] **Step 7: コミット**

```bash
git add scripts/verify_chapter_boundaries.py
git commit -m "fix: 検証スクリプトに見開き分割を通し実パイプラインと一致させる

verify_chapter_boundaries.py は元PDFを直接 PDFSplitter.split() に
渡していたが、実パイプラインは book_manager.py:182-185 で
is_spread_pdf()→split_spread_pdf() を先に通す。corfra と PSE は
is_spread=True のため、本番が一度も見ない文書で検証されていた。

期待値定数も分割後の文書（PSE 175→350頁、corfra 106→212頁）で
取り直した。この定数は正解ではなく回帰検出用スナップショットである
ことをコメントで明記した。"
```

---

### Task 2: PSE の正解データを確定させる

spec §4.2 の未確定分（Ch9 / Ch1-II / Writing societies）を目視で確定させ、
正解データを検証スクリプトに埋め込む。

**Files:**
- Modify: `scripts/verify_chapter_boundaries.py`

**Interfaces:**
- Consumes: Task 1 の `resolve_input_pdf()`
- Produces: `PSE_GROUND_TRUTH: Dict[str, int]` — 章タイトル→真の扉頁（1-indexed 物理頁）

- [ ] **Step 1: 未確定3章の扉頁を探す**

Run:
```bash
source venv/bin/activate && python3 -c "
import fitz, re
d = fitz.open('data/input/Booksample/PSE/PSEpdf_split.pdf')
for i in range(len(d)):
    lines = [l.strip() for l in d[i].get_text('text').split('\n') if l.strip()]
    if not lines:
        continue
    if re.match(r'^Chapter\s+\d+$', lines[0]) or lines[0].startswith('Writing societies'):
        print(f'P{i+1} ({len(d[i].get_text().strip())}字):', lines[:4])
" 2>&1 | grep -v MuPDF
```
Expected: `Chapter 9` と `Writing societies` を含む頁が表示される。
`Chapter 1 The Ethnographic Effect II` は "Chapter 1" として2回目に出現する頁を探す。

見つからない章がある場合は、その章のランニングヘッダー文字列で全頁を検索し、
ヘッダーが最初に出現する頁の1つ手前を扉頁候補として目視確認する。

Run:
```bash
source venv/bin/activate && python3 -c "
import fitz
d = fitz.open('data/input/Booksample/PSE/PSEpdf_split.pdf')
for pat in ['What is Intellectual Property', 'The Ethnographic Effect II', 'Writing societies']:
    hits = [i+1 for i in range(len(d)) if pat.lower() in d[i].get_text('text').lower()]
    print(f'{pat!r}: {hits[:8]}')
" 2>&1 | grep -v MuPDF
```

- [ ] **Step 2: 正解データ定数を追加する**

`scripts/verify_chapter_boundaries.py` の定数群に追加する。
Step 1 で確定した3章の値を `<Step 1 で確定>` の位置に入れる。

```python
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
    "Chapter 9 What is Intellectual Property after?": <Step 1 で確定>,
    "Chapter 10 Puzzles of Scale": 217,
    "Chapter 1 The Ethnographic Effect II": <Step 1 で確定>,
    "Writing societies, writing persons": <Step 1 で確定>,
}

# Naven の正解データ（誤着地2章のみ・1-indexed 物理頁）。
# オフセット30から導出し目視確認した値。I-26 の対象章。
NAVEN_GROUND_TRUTH = {
    "Chap. VII. THE SOCIOLOGY OF NAVEN": 116,
    "Chap. XII. THE PREFERRED TYPES": 190,
}
```

- [ ] **Step 3: Naven の正解2章を目視確認する**

Run:
```bash
source venv/bin/activate && python3 -c "
import fitz
d = fitz.open('data/input/Booksample/Naven/Naven.pdf')
for i in [114, 115, 116, 188, 189, 190]:
    lines = [l.strip() for l in d[i].get_text('text').split('\n') if l.strip()]
    print(f'P{i+1} ({len(d[i].get_text().strip())}字):', lines[:5])
" 2>&1 | grep -v MuPDF
```
Expected: P116 に `CHAPTER` / `VII` / `The Sociology of N aven`、
P190 に `CHAPTER` / `XII` / `The Pref erred Types` に相当する行が現れる。
異なる場合は実測値で `NAVEN_GROUND_TRUTH` を修正すること。

- [ ] **Step 4: 正解との一致数を報告する検証関数を追加する**

```python
def report_ground_truth(rec: "Recorder", name: str, chapters: list, truth: dict) -> None:
    """正解データとの一致章数を報告する（regression ではなく指標として記録）。

    章境界の改善は「一致章数が増える」ことで評価する。減った場合のみ regression とする。
    """
    by_title = {ch["title"]: ch["page_range"][0] for ch in chapters if ch.get("page_range")}
    hits = []
    misses = []
    for title, expected in truth.items():
        actual = by_title.get(title)
        if actual == expected:
            hits.append(title)
        else:
            misses.append(f"{title[:40]}: 期待P{expected} 実際P{actual}")
    print(f"  [{name}] 正解一致: {len(hits)}/{len(truth)}")
    for m in misses:
        print(f"      × {m}")
    rec.metric(f"{name}_ground_truth_hits", len(hits), len(truth))
```

`Recorder` に `metric()` が無い場合は追加する。

```python
    def metric(self, key: str, value: int, total: int) -> None:
        """回帰判定ではなく、推移を追う指標として記録する。"""
        self.metrics[key] = (value, total)
```

`Recorder.__init__` に `self.metrics = {}` を追加し、`main()` の集計出力に加える。

```python
    print(f"\n===== 指標 =====")
    for key, (value, total) in rec.metrics.items():
        print(f"  {key}: {value}/{total}")
```

- [ ] **Step 5: `main()` から呼び出す**

PSE と Naven の分岐で `report_ground_truth` を呼ぶ。

```python
        if name == "PSE":
            report_ground_truth(rec, "PSE", chapters, PSE_GROUND_TRUTH)
        if name == "Naven":
            report_ground_truth(rec, "Naven", chapters, NAVEN_GROUND_TRUTH)
```

- [ ] **Step 6: 実行して現状の一致数を記録する**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py 2>&1 | tail -30`
Expected: `PSE_ground_truth_hits: N/13` と `Naven_ground_truth_hits: 0/2` が出る。
この N が改善前のベースラインになる。出力を控えておくこと。

- [ ] **Step 7: コミット**

```bash
git add scripts/verify_chapter_boundaries.py
git commit -m "test: PSE と Naven の章境界正解データを追加

目視で確定した真の扉頁を PSE_GROUND_TRUTH / NAVEN_GROUND_TRUTH として
定義し、一致章数を指標として報告する。回帰判定ではなく推移を追う指標
として扱い、減少した場合のみ問題とする。"
```

---

### Task 3: 印刷頁番号の回収とオフセット推定（層1前半）

spec §2.2 手順1-2、§1.2。

**Files:**
- Create: `core/engine/p1_ingest/page_number_map.py`
- Create: `tests/unit/test_page_number_map.py`
- Modify: `core/engine/p1_ingest/pdf_splitter.py`（`_parse_page_number` を新モジュールへ委譲）

**Interfaces:**
- Consumes: なし
- Produces:
  - `parse_page_number(text: str) -> Optional[int]`
  - `harvest_printed_page(page_text: str) -> Optional[int]`
  - `estimate_offset(doc) -> Optional[int]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_page_number_map.py`:

```python
"""
page_number_map のユニットテスト

テスト対象:
  - parse_page_number: 行を頁番号として解釈（OCR 崩れ耐性）
  - harvest_printed_page: 頁のヘッダー/フッターから印刷頁番号を1つ得る
  - estimate_offset: 文書全体から最頻オフセット（物理idx - 印刷頁）を推定
"""

import pytest
from unittest.mock import MagicMock

from core.engine.p1_ingest.page_number_map import (
    parse_page_number,
    harvest_printed_page,
    estimate_offset,
)


def make_mock_doc(page_texts: list[str]) -> MagicMock:
    doc = MagicMock()
    pages = []
    for t in page_texts:
        p = MagicMock()
        p.get_text.return_value = t
        pages.append(p)
    doc.__len__.return_value = len(pages)
    doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
    return doc


class TestParsePageNumber:
    def test_plain_digits(self):
        assert parse_page_number("147") == 147

    def test_ocr_corrupted_digits(self):
        # 'I'→1, 'l'→1, 'O'→0 等の誤読に耐える
        assert parse_page_number("3 I") == 31
        assert parse_page_number("l72") == 172

    def test_rejects_pure_roman_numeral(self):
        # 数字を1文字も含まない文字列は頁番号として扱わない（章マーカーとの誤認防止）
        assert parse_page_number("XIII") is None
        assert parse_page_number("I") is None

    def test_rejects_long_string(self):
        assert parse_page_number("Property, Substance and Effect") is None

    def test_rejects_out_of_range(self):
        assert parse_page_number("99999") is None
        assert parse_page_number("0") is None


class TestHarvestPrintedPage:
    def test_recto_title_then_number(self):
        # corfra / PSE の recto 形式: 'Knowing | 147'
        text = "Divisions of Interest\n137\n本文が続く……\n"
        assert harvest_printed_page(text) == 137

    def test_verso_number_then_title(self):
        # verso 形式: 頁番号が先
        text = "144\nProperty, Substance and Effect\n本文が続く……\n"
        assert harvest_printed_page(text) == 144

    def test_rejects_page_with_conflicting_numbers(self):
        # 同一頁から異なる数値が読めた場合は棄却する（誤読の混入を防ぐ）
        text = "12\nTitle\n本文\n99\n"
        assert harvest_printed_page(text) is None

    def test_returns_none_for_empty_page(self):
        assert harvest_printed_page("") is None

    def test_returns_none_when_no_number(self):
        assert harvest_printed_page("Title\n本文だけの頁\n") is None


class TestEstimateOffset:
    def test_constant_offset(self):
        # 物理 idx 2,3,4 に印刷頁 1,2,3 → オフセット +1
        doc = make_mock_doc([
            "表紙\n", "扉\n",
            "1\nTitle\n", "2\nTitle\n", "3\nTitle\n", "4\nTitle\n",
        ])
        assert estimate_offset(doc) == 1

    def test_ignores_outliers(self):
        # 年号など（1958）が混じっても最頻値は揺らがない
        doc = make_mock_doc([
            "1958\n奥付\n",
            "1\nTitle\n", "2\nTitle\n", "3\nTitle\n", "4\nTitle\n",
        ])
        assert estimate_offset(doc) == 1

    def test_stepped_offsets_picks_most_common(self):
        # 部扉ごとに段が変わる場合、最も多い段を採る（relations 型）
        doc = make_mock_doc([
            "1\nT\n", "2\nT\n", "3\nT\n",       # offset 0（idx0→印刷1 は -1）
            "3\nT\n", "4\nT\n",                  # offset +... 段が変わる
        ])
        result = estimate_offset(doc)
        assert result is not None

    def test_returns_none_when_no_page_numbers(self):
        doc = make_mock_doc(["本文だけ\n", "本文だけ\n"])
        assert estimate_offset(doc) is None
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_page_number_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.engine.p1_ingest.page_number_map'`

- [ ] **Step 3: 実装する**

`core/engine/p1_ingest/page_number_map.py`:

```python
"""印刷頁番号の回収と、印刷頁→物理頁オフセットの推定。

書籍の TOC が持つ論理頁（＝紙面に印刷された頁番号）と PDF の物理頁との
写像は、PDF の作られ方に依存する。この写像を、ヘッダー/フッターに印字された
頁番号から実測で推定する。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.2
"""

import re
from collections import Counter
from typing import Any, Optional

# 頁番号として受け付ける文字列の最大長・値域
PAGE_NUMBER_MAX_LEN = 8
PAGE_NUMBER_MIN_VALUE = 1
PAGE_NUMBER_MAX_VALUE = 9999

# ヘッダー/フッターとして走査する行数（先頭 N 行と末尾 N 行）
HEADER_FOOTER_LINES = 2

# オフセット推定に必要な最小の投票数。これ未満なら推定を諦める。
MIN_OFFSET_VOTES = 5

# OCR で数字と誤読されやすい文字の対応表。
# pdf_splitter.PDFSplitter._OCR_DIGIT_MAP と同一の内容を持つ（委譲元）。
OCR_DIGIT_MAP = str.maketrans(
    {'I': '1', 'l': '1', '|': '1', 'i': '1', 'r': '1',
     'O': '0', 'o': '0', 'S': '5', 'B': '8'}
)

# 行をトークンへ割る区切り（空白と縦罫）
_TOKEN_SPLIT_RE = re.compile(r'[\s|]+')


def parse_page_number(text: str) -> Optional[int]:
    """行を頁番号として解釈する。OCR 崩れ（'3 I'→31, 'l72'→172）に耐える。

    数字を1文字も含まない文字列（ローマ数字 'XIII' や 'I'）は頁番号として
    扱わない。章マーカーとの誤認を防ぐため。
    """
    t = text.strip()
    if not t or len(t) > PAGE_NUMBER_MAX_LEN:
        return None
    if not any(c.isdigit() for c in t):
        return None
    normalized = t.translate(OCR_DIGIT_MAP).replace(' ', '')
    if normalized.isdigit() and PAGE_NUMBER_MIN_VALUE <= int(normalized) <= PAGE_NUMBER_MAX_VALUE:
        return int(normalized)
    return None


def harvest_printed_page(page_text: str) -> Optional[int]:
    """頁のヘッダー/フッター領域から印刷頁番号を1つ回収する。

    recto は 'Knowing | 147'（タイトル→番号）、verso は '144 | 書名'（番号→タイトル）
    という交互配置が組版の慣習である。したがって各行の先頭トークンと末尾トークンの
    両方を候補として見る。

    同一頁から異なる数値が読めた場合は None を返す。本文中の数字や年号を
    誤って拾うより、その頁を投票から外すほうが安全である。
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    if not lines:
        return None

    candidates = []
    for line in lines[:HEADER_FOOTER_LINES] + lines[-HEADER_FOOTER_LINES:]:
        tokens = _TOKEN_SPLIT_RE.split(line)
        if not tokens:
            continue
        for token in (tokens[0], tokens[-1]):
            value = parse_page_number(token)
            if value is not None:
                candidates.append(value)

    if not candidates:
        return None
    if len(set(candidates)) != 1:
        return None
    return candidates[0]


def estimate_offset(doc: Any) -> Optional[int]:
    """文書全体から `物理 idx − 印刷頁` の最頻値を推定する。

    中央値ではなく最頻値を使う。relations のように部扉ごとにオフセットが
    階段状に変わる書籍では、中央値が実在しない中間値になりうるのに対し、
    最頻値は必ず実在する段のいずれかを選ぶ。

    投票数が MIN_OFFSET_VOTES 未満の場合は None を返す（推定を諦める）。
    """
    votes: Counter = Counter()
    for idx in range(len(doc)):
        printed = harvest_printed_page(doc[idx].get_text("text"))
        if printed is None:
            continue
        votes[idx - printed] += 1

    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    if count < MIN_OFFSET_VOTES:
        return None
    return offset
```

- [ ] **Step 4: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_page_number_map.py -v`
Expected: PASS（全ケース）

`test_stepped_offsets_picks_most_common` と `test_constant_offset` は投票数が
`MIN_OFFSET_VOTES=5` に満たない可能性がある。失敗する場合はテスト側の頁数を増やして
5票以上になるようにすること（定数を下げてはならない。実データでの誤推定を防ぐ閾値である）。

- [ ] **Step 5: `PDFSplitter._parse_page_number` を新モジュールへ委譲する**

重複実装を残さないため、`core/engine/p1_ingest/pdf_splitter.py:367-385` の
`_OCR_DIGIT_MAP` と `_parse_page_number` を次に置き換える。

```python
    def _parse_page_number(self, line: str) -> Optional[int]:
        """行を頁番号として解釈する。実装は page_number_map に委譲する。

        照合ループ（_classify_match / _rescue_by_local_offset）から呼ばれるため、
        挙動は従来と完全に同一でなければならない。
        """
        from .page_number_map import parse_page_number
        return parse_page_number(line)
```

- [ ] **Step 6: 既存テストが全て通ることを確認する（挙動不変の証明）**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_pdf_splitter.py -v`
Expected: PASS（91件すべて）。1件でも落ちたら委譲で挙動が変わっている。

Run: `source venv/bin/activate && python3 -m pytest tests/unit/ -q`
Expected: 全件 PASS

- [ ] **Step 7: コミット**

```bash
git add core/engine/p1_ingest/page_number_map.py tests/unit/test_page_number_map.py core/engine/p1_ingest/pdf_splitter.py
git commit -m "feat: 印刷頁番号の回収とオフセット推定モジュールを追加

ヘッダー/フッターから印刷頁番号を回収し、物理頁との最頻オフセットを
推定する page_number_map を追加。実測での回収率は PSE 302/350、
corfra 179/212、Naven 206/380、relations 153/282。

中央値ではなく最頻値を採る。relations のように段が複数ある書籍で、
中央値は実在しない中間値になりうるため。

PDFSplitter._parse_page_number は重複を避けて新モジュールへ委譲した
（既存91件のテストで挙動不変を確認）。"
```

---

### Task 4: TOC の検算と shift 補正（層1後半）

spec §2.2 手順3-4。

**Files:**
- Create: `core/engine/p1_ingest/toc_verifier.py`
- Create: `tests/unit/test_toc_verifier.py`

**Interfaces:**
- Consumes: `page_number_map.estimate_offset`
- Produces:
  - `count_title_matches(doc, entries, offset, shift, normalize) -> int`
  - `detect_toc_shift(doc, entries, offset, normalize) -> int`
  - `apply_shift(entries, shift) -> List[Dict[str, Any]]`
  - `verify_and_fix_toc(doc, entries, normalize) -> List[Dict[str, Any]]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_toc_verifier.py`:

```python
"""
toc_verifier のユニットテスト

テスト対象:
  - count_title_matches: 予測位置にタイトルがある章の数
  - detect_toc_shift: TOC エントリと頁番号の対応ずれの検出
  - apply_shift: 検出した shift の適用
  - verify_and_fix_toc: 上記を束ねた入口
"""

import pytest
from unittest.mock import MagicMock

from core.engine.p1_ingest.toc_verifier import (
    count_title_matches,
    detect_toc_shift,
    apply_shift,
    verify_and_fix_toc,
)


def normalize(text: str) -> str:
    """PDFSplitter._normalize_title と同等の簡易版（テスト用）。"""
    import re
    t = re.sub(r'^(?:Chapter|Chap\.?|Part)\s+[\dIVXivx]+\s*[.:]?\s*', '', text, flags=re.I)
    t = re.sub(r'^[\dIVXivx]+[.:]?\s+', '', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    return ' '.join(t.lower().split())


def make_mock_doc(page_texts: list[str]) -> MagicMock:
    doc = MagicMock()
    pages = []
    for t in page_texts:
        p = MagicMock()
        p.get_text.return_value = t
        pages.append(p)
    doc.__len__.return_value = len(pages)
    doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
    return doc


class TestCountTitleMatches:
    def test_counts_entries_landing_on_their_own_title(self):
        # オフセット +2: 論理頁1→物理idx3、論理頁3→物理idx5
        doc = make_mock_doc([
            "表紙\n", "扉\n", "1\n本文\n",
            "Alpha Chapter\n本文\n",       # idx3
            "4\n本文\n",
            "Beta Chapter\n本文\n",        # idx5
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 1},
            {"title": "Beta Chapter", "start_page": 3},
        ]
        assert count_title_matches(doc, entries, offset=2, shift=0, normalize=normalize) == 2

    def test_shift_moves_which_number_each_entry_uses(self):
        # 各エントリが「次のエントリの頁番号」を持っている（PSE 型のずれ）
        doc = make_mock_doc([
            "x\n", "x\n", "x\n",
            "Alpha Chapter\n本文\n",       # idx3 が Alpha の真の位置
            "x\n",
            "Beta Chapter\n本文\n",        # idx5 が Beta の真の位置
        ])
        # Alpha には Beta の頁(3)が、Beta にはさらに次の頁(5)が入っている
        entries = [
            {"title": "Alpha Chapter", "start_page": 3},
            {"title": "Beta Chapter", "start_page": 5},
        ]
        # shift=-1 で Alpha は「1つ前のエントリの頁番号」を使う…が先頭には無い。
        # Beta は Alpha の頁番号(3)を使い、offset=2 で idx5 に着地して一致する。
        assert count_title_matches(doc, entries, offset=2, shift=-1, normalize=normalize) == 1
        assert count_title_matches(doc, entries, offset=2, shift=0, normalize=normalize) == 0


class TestDetectTocShift:
    def test_detects_off_by_one(self):
        doc = make_mock_doc([
            "x\n", "x\n", "x\n",
            "Alpha Chapter\n本文\n",
            "x\n",
            "Beta Chapter\n本文\n",
            "x\n",
            "Gamma Chapter\n本文\n",
            "x\n",
            "Delta Chapter\n本文\n",
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 3},
            {"title": "Beta Chapter", "start_page": 5},
            {"title": "Gamma Chapter", "start_page": 7},
            {"title": "Delta Chapter", "start_page": 9},
        ]
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == -1

    def test_returns_zero_when_toc_is_correct(self):
        doc = make_mock_doc([
            "x\n", "x\n",
            "Alpha Chapter\n本文\n",      # idx2
            "x\n",
            "Beta Chapter\n本文\n",       # idx4
            "x\n",
            "Gamma Chapter\n本文\n",      # idx6
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 0},
            {"title": "Beta Chapter", "start_page": 2},
            {"title": "Gamma Chapter", "start_page": 4},
        ]
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == 0

    def test_returns_zero_when_evidence_is_thin(self):
        # 一致が少なく判断材料が乏しい場合は賭けに出ない
        doc = make_mock_doc(["x\n", "x\n", "Alpha\n"])
        entries = [{"title": "Alpha", "start_page": 0}]
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == 0


class TestApplyShift:
    def test_shift_zero_returns_unchanged(self):
        entries = [{"title": "A", "start_page": 1}, {"title": "B", "start_page": 2}]
        assert apply_shift(entries, 0) == entries

    def test_negative_shift_takes_previous_entry_page(self):
        entries = [
            {"title": "A", "start_page": 10},
            {"title": "B", "start_page": 20},
            {"title": "C", "start_page": 30},
        ]
        result = apply_shift(entries, -1)
        # A は参照先が無いので頁番号を持たない扱い（None）にする
        assert result[0]["start_page"] is None
        assert result[1]["start_page"] == 10
        assert result[2]["start_page"] == 20

    def test_preserves_other_fields(self):
        entries = [
            {"title": "A", "start_page": 10, "role": "chapter"},
            {"title": "B", "start_page": 20, "role": "chapter"},
        ]
        result = apply_shift(entries, -1)
        assert result[1]["role"] == "chapter"
        assert result[1]["title"] == "B"


class TestVerifyAndFixToc:
    def test_returns_original_when_offset_cannot_be_estimated(self):
        doc = make_mock_doc(["本文だけ\n", "本文だけ\n"])
        entries = [{"title": "A", "start_page": 1}]
        assert verify_and_fix_toc(doc, entries, normalize) == entries

    def test_returns_original_for_empty_entries(self):
        doc = make_mock_doc(["1\n", "2\n"])
        assert verify_and_fix_toc(doc, [], normalize) == []
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_toc_verifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.engine.p1_ingest.toc_verifier'`

- [ ] **Step 3: 実装する**

`core/engine/p1_ingest/toc_verifier.py`:

```python
"""TOC エントリの検算と、エントリ↔頁番号の系統的ずれ（shift）の補正。

LLM による TOC 抽出（Route 3）は、目次頁のテキスト層が列単位で出力される
書籍で、エントリと頁番号を1つずらして対応付けることがある（PSE で実測）。
このずれは下流のどの精緻化でも回復できないため、上流で検出・補正する。

検算の方法は「予測物理頁にそのエントリのタイトルが実在するか」である。
予測は `論理頁 + オフセット`（オフセットは page_number_map が実測から推定）。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.2
"""

from typing import Any, Callable, Dict, List, Optional

from core.config import print_log
from .page_number_map import estimate_offset

# 試す shift の候補。実測では PSE の −1 以外に必要なずれは観測されていない。
SHIFT_CANDIDATES = (-1, 0, 1)

# 検算でタイトルを探す行数（章扉のタイトルは冒頭に現れる）
VERIFY_HEAD_LINES = 12

# タイトル比較に使う先頭文字数。全長一致を求めると、扉頁で改行された
# タイトルの後半が欠ける場合に落ちるため、先頭のみを見る。
VERIFY_TITLE_PREFIX = 25

# shift 補正を適用する条件。最良 shift の一致数がこの件数以上あり、
# かつ次点の SHIFT_DOMINANCE_RATIO 倍以上であること。
# 判断材料が乏しい書籍で賭けに出ないための安全弁である。
SHIFT_MIN_MATCHES = 3
SHIFT_DOMINANCE_RATIO = 2.0


def count_title_matches(
    doc: Any,
    entries: List[Dict[str, Any]],
    offset: int,
    shift: int,
    normalize: Callable[[str], str],
) -> int:
    """shift を適用したときに、予測物理頁に自分のタイトルが実在する章の数。"""
    matches = 0
    for i, entry in enumerate(entries):
        source = i + shift
        if not (0 <= source < len(entries)):
            continue
        logical = entries[source].get("start_page")
        if logical is None:
            continue
        predicted = int(logical) + offset
        if not (0 <= predicted < len(doc)):
            continue

        norm_title = normalize(entry.get("title", ""))
        if not norm_title:
            continue

        lines = [l.strip() for l in doc[predicted].get_text("text").split("\n") if l.strip()]
        head = normalize(" ".join(lines[:VERIFY_HEAD_LINES]))
        if norm_title[:VERIFY_TITLE_PREFIX] in head:
            matches += 1
    return matches


def detect_toc_shift(
    doc: Any,
    entries: List[Dict[str, Any]],
    offset: int,
    normalize: Callable[[str], str],
) -> int:
    """エントリと頁番号の対応ずれを検出する。ずれが無ければ 0 を返す。

    実測（2026-07-19）では PSE のみ shift=−1 が 12対2 で勝ち、
    corfra・Naven・relations は shift=0 が勝った（誤検出ゼロ）。
    """
    scores = {s: count_title_matches(doc, entries, offset, s, normalize)
              for s in SHIFT_CANDIDATES}
    best_shift = max(scores, key=lambda s: scores[s])
    best = scores[best_shift]

    if best_shift == 0:
        return 0
    if best < SHIFT_MIN_MATCHES:
        return 0

    runner_up = max(v for s, v in scores.items() if s != best_shift)
    if runner_up > 0 and best < runner_up * SHIFT_DOMINANCE_RATIO:
        return 0
    return best_shift


def apply_shift(entries: List[Dict[str, Any]], shift: int) -> List[Dict[str, Any]]:
    """shift を適用し、各エントリに参照先エントリの頁番号を割り当てる。

    参照先が範囲外になるエントリの start_page は None にする。
    その章は論理頁を持たないものとして、下流のコンテンツスキャンに委ねられる。
    """
    if shift == 0:
        return entries

    fixed = []
    for i, entry in enumerate(entries):
        source = i + shift
        new_entry = dict(entry)
        if 0 <= source < len(entries):
            new_entry["start_page"] = entries[source].get("start_page")
        else:
            new_entry["start_page"] = None
        fixed.append(new_entry)
    return fixed


def verify_and_fix_toc(
    doc: Any,
    entries: List[Dict[str, Any]],
    normalize: Callable[[str], str],
) -> List[Dict[str, Any]]:
    """TOC を検算し、系統的ずれがあれば補正して返す（層1の入口）。

    オフセットを推定できない書籍では何もしない（元のまま返す）。
    """
    if not entries:
        return entries

    offset = estimate_offset(doc)
    if offset is None:
        print_log("  [TOCVerifier] 印刷頁番号が乏しく写像を推定できません。検算をスキップします。")
        return entries

    shift = detect_toc_shift(doc, entries, offset, normalize)
    if shift == 0:
        print_log(f"  [TOCVerifier] TOC 検算: ずれなし（推定オフセット {offset:+d}）")
        return entries

    print_log(
        f"  [TOCVerifier] TOC のエントリと頁番号が {shift:+d} ずれています。"
        f"補正します（推定オフセット {offset:+d}）。"
    )
    return apply_shift(entries, shift)
```

- [ ] **Step 4: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_toc_verifier.py -v`
Expected: PASS（全ケース）

- [ ] **Step 5: コミット**

```bash
git add core/engine/p1_ingest/toc_verifier.py tests/unit/test_toc_verifier.py
git commit -m "feat: TOC の検算と系統的ずれの補正を追加（層1）

予測物理頁（論理頁 + 推定オフセット）にそのエントリのタイトルが実在
するかで TOC を検算し、エントリと頁番号の対応が1つずれている場合に
補正する。

実測では PSE のみ shift=-1 が 12対2 で勝ち、corfra・Naven・relations は
shift=0 が勝つ（誤検出ゼロ）。判断材料が乏しい場合は補正しない
（SHIFT_MIN_MATCHES / SHIFT_DOMINANCE_RATIO）。"
```

---

### Task 5: 層1を Route 3 に配線する

spec §2.2 適用範囲。

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py:100-108`（Route 3 の分岐）
- Modify: `tests/unit/test_pdf_splitter.py`

**Interfaces:**
- Consumes: `toc_verifier.verify_and_fix_toc`
- Produces: なし（配線のみ）

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_pdf_splitter.py` の末尾に追加する。

```python
class TestTocVerifierWiring:
    """層1（TOC 検算）が Route 3 にのみ掛かることを確認する。"""

    def test_route3_calls_verify_and_fix_toc(self, tmp_path):
        s = make_splitter()
        s.cache = {"dummyhash_toc": [{"title": "A", "start_page": 1, "role": "chapter"}]}
        doc = make_mock_doc(["1\nA\n", "2\n本文\n"])

        with patch("fitz.open", return_value=doc), \
             patch.object(s, "_get_pdf_hash", return_value="dummyhash"), \
             patch.object(s, "_get_chapters_from_outline", return_value=None), \
             patch.object(s, "_apply_content_scan", return_value=[]), \
             patch("core.engine.p1_ingest.toc_verifier.verify_and_fix_toc") as mock_verify:
            mock_verify.return_value = [{"title": "A", "start_page": 1, "role": "chapter"}]
            s.split("dummy.pdf", tmp_path)

        assert mock_verify.called, "Route 3 では層1が呼ばれなければならない"

    def test_route2_does_not_call_verify_and_fix_toc(self, tmp_path):
        """ネイティブ outline は物理頁を直接持つため検算を掛けてはならない。"""
        s = make_splitter()
        doc = make_mock_doc(["A\n", "本文\n"])
        outline_toc = [{"title": "A", "start_page": 0, "role": "chapter"}]

        with patch("fitz.open", return_value=doc), \
             patch.object(s, "_get_chapters_from_outline", return_value=outline_toc), \
             patch("core.engine.p1_ingest.toc_verifier.verify_and_fix_toc") as mock_verify:
            s.split("dummy.pdf", tmp_path)

        assert not mock_verify.called, "Route 2 で層1が呼ばれてはならない"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_pdf_splitter.py::TestTocVerifierWiring -v`
Expected: FAIL — `test_route3_calls_verify_and_fix_toc` が
`assert mock_verify.called` で落ちる

- [ ] **Step 3: 配線する**

`core/engine/p1_ingest/pdf_splitter.py` の Route 3 分岐（`if llm_toc:` の直前）を
次のように変更する。

```python
            if llm_toc:
                # 層1: TOC の検算と系統的ずれの補正。
                # Route 3（LLM 抽出）のみに掛ける。Route 1（手動修正済みTOC）と
                # Route 2（ネイティブ outline・物理頁直参照）には掛けない。
                from .toc_verifier import verify_and_fix_toc
                llm_toc = verify_and_fix_toc(doc, llm_toc, self._normalize_title)
                toc_data = self._apply_content_scan(doc, llm_toc)
```

- [ ] **Step 4: `_apply_content_scan` が `start_page=None` を受けられるようにする**

`apply_shift` は参照先の無いエントリの `start_page` を `None` にする。
`_apply_content_scan:257` の `int(entry.get("start_page", 1))` はこれで落ちる。

`core/engine/p1_ingest/pdf_splitter.py:257` を次に変更する。

```python
            raw_logical = entry.get("start_page")
            if raw_logical is None:
                # 層1の shift 補正で参照先が無かった章。論理頁のヒントを持たないため
                # 文書全体を探索窓とし、本文照合のみで位置を決める。
                logical_page = 1
                has_logical_hint = False
            else:
                logical_page = int(raw_logical)
                has_logical_hint = True
```

続いて探索窓の決定（`:262-263`）を次に変更する。

```python
            # 探索範囲: 論理ページ前後を広めに取り可変オフセットに対応
            if has_logical_hint:
                search_start = max(0, logical_page - 5)
                search_end = min(total_pages - 1, logical_page + 49)
            else:
                search_start = max(0, last_found_phys + 1)
                search_end = total_pages - 1
```

**注意**: これは探索窓の「変更」ではない。`has_logical_hint` が真の場合の式は
従来と完全に同一である。偽の場合は従来なら存在しなかった入力への対応である。

- [ ] **Step 5: 既存入力に対する振る舞い不変をテストで担保する**

Global Constraints は「既存入力に対する振る舞いを変えない」を要求する。
`start_page` が数値である従来の入力では、探索窓の式が従来と完全に同一であることを
明示的に検証する。`tests/unit/test_pdf_splitter.py` に追加する。

```python
class TestSearchWindowInvariance:
    """start_page が数値の場合、探索窓は従来の式と完全に同一でなければならない。"""

    def test_numeric_logical_page_uses_original_window(self):
        s = make_splitter()
        # 論理頁50 → 従来の窓は idx45..99（logical-5 … logical+49、総頁数でクリップ）
        doc = make_mock_doc(["x\n"] * 200)
        scanned = []

        original_getitem = doc.__getitem__

        def record(idx):
            scanned.append(idx)
            return original_getitem(idx)

        doc.__getitem__ = MagicMock(side_effect=record)
        s._apply_content_scan(doc, [{"title": "NotPresent", "start_page": 50, "role": "chapter"}])

        assert min(scanned) == 45, "探索開始が logical-5 でない"
        assert max(scanned) == 99, "探索終了が logical+49 でない"

    def test_none_logical_page_scans_from_previous_chapter(self):
        s = make_splitter()
        doc = make_mock_doc(["x\n"] * 20)
        scanned = []
        original_getitem = doc.__getitem__

        def record(idx):
            scanned.append(idx)
            return original_getitem(idx)

        doc.__getitem__ = MagicMock(side_effect=record)
        s._apply_content_scan(doc, [{"title": "NotPresent", "start_page": None, "role": "chapter"}])

        assert min(scanned) == 0
        assert max(scanned) == 19
```

**注意**: `_apply_content_scan` は末尾で層2・層3を呼ぶ（Task 8 で配線）。
Task 5 の時点ではまだ配線されていないため、このテストはそのまま通る。
Task 8 実装後にこのテストが落ちる場合は、層3が候補頁を読んだことによる
`scanned` の汚染である。その場合は `_adjudicate_boundaries` を
`patch.object(s, "_adjudicate_boundaries", side_effect=lambda doc, r: r)` で
無効化してから計測するようテストを修正すること。

- [ ] **Step 6: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_pdf_splitter.py -v`
Expected: PASS（既存91件＋新規4件）

- [ ] **Step 7: 実PDF 4冊で層1の効果を確認する**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py 2>&1 | tail -40`
Expected:
- PSE のログに `[TOCVerifier] TOC のエントリと頁番号が -1 ずれています` が出る
- corfra / Naven / relations は `ずれなし` が出る
- `PSE_ground_truth_hits` が Task 2 のベースラインより**増える**
- `regression（新規の退行）: 0 件`

一致数が増えない場合は先に進まず、`detect_toc_shift` の実データでの挙動を調べること。

- [ ] **Step 8: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py tests/unit/test_pdf_splitter.py
git commit -m "feat: 層1（TOC 検算）を Route 3 に配線する

LLM TOC 抽出の結果に対してのみ検算・shift 補正を掛ける。Route 1
（手動修正済みTOC）と Route 2（ネイティブ outline）は対象外。

shift 補正で頁番号の参照先を失った章（start_page=None）は論理頁の
ヒントを持たないため、前章の直後から文書末尾までを探索窓として
本文照合のみで位置を決める。ヒントがある場合の窓の式は従来と同一。"
```

---

### Task 6: 逸脱検出（層2）

spec §2.3。

**Files:**
- Create: `core/engine/p1_ingest/boundary_adjudicator.py`
- Create: `tests/unit/test_boundary_adjudicator.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `ChapterPlacement` データクラス（`index`, `title`, `logical_page`, `start_page`, `matched`）
  - `flag_suspects(placements) -> Set[int]`
  - `interpolated_offset(placements, index) -> Optional[int]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_boundary_adjudicator.py`:

```python
"""
boundary_adjudicator のユニットテスト

テスト対象:
  - flag_suspects: 前後の確定章の双方とオフセットが食い違う章の検出（層2）
  - interpolated_offset: 前後の確定章から期待オフセットを補間
"""

import pytest

from core.engine.p1_ingest.boundary_adjudicator import (
    ChapterPlacement,
    flag_suspects,
    interpolated_offset,
)


def placements(specs):
    """(logical, physical, matched) の列から ChapterPlacement 列を作る。"""
    return [
        ChapterPlacement(index=i, title=f"Ch{i}", logical_page=lg,
                         start_page=ph, matched=m)
        for i, (lg, ph, m) in enumerate(specs)
    ]


class TestFlagSuspects:
    def test_flat_offsets_flag_nothing(self):
        """corfra 型: 全章が同じオフセット。"""
        p = placements([(10, 19, True), (20, 29, True), (30, 39, True), (40, 49, True)])
        assert flag_suspects(p) == set()

    def test_stepped_offsets_flag_nothing(self):
        """relations 型: 部扉ごとに段が変わる。前か後の一方と一致すれば通過。"""
        p = placements([
            (10, 24, True),   # +14
            (20, 32, True),   # +12
            (30, 42, True),   # +12
            (40, 50, True),   # +10
            (50, 60, True),   # +10
        ])
        assert flag_suspects(p) == set()

    def test_single_deviant_is_flagged(self):
        """Naven 型: 1章だけ前後の双方と食い違う。"""
        p = placements([
            (10, 40, True),   # +30
            (20, 50, True),   # +30
            (30, 61, True),   # +31 ← 逸脱
            (40, 70, True),   # +30
            (50, 80, True),   # +30
        ])
        assert flag_suspects(p) == {2}

    def test_large_deviant_is_flagged(self):
        """Naven XII 型: 逸脱幅が大きい場合も同じ規則で捕まる。"""
        p = placements([
            (10, 40, True), (20, 50, True), (30, 67, True), (40, 70, True), (50, 80, True),
        ])
        assert flag_suspects(p) == {2}

    def test_fallback_chapters_always_flagged(self):
        """フォールバックに落ちた章は無条件に要審査。"""
        p = placements([(10, 19, True), (20, 19, False), (30, 39, True)])
        assert 1 in flag_suspects(p)

    def test_fallback_chapters_not_used_as_reference(self):
        """フォールバック章は前後の参照に使わない。"""
        p = placements([
            (10, 19, True),    # +9
            (20, 20, False),   # フォールバック（参照に使わない）
            (30, 39, True),    # +9
            (40, 49, True),    # +9
        ])
        # index1 は要審査だが、index2 は前(index0)・後(index3)ともに +9 で通過する
        suspects = flag_suspects(p)
        assert 1 in suspects
        assert 2 not in suspects

    def test_first_chapter_never_flagged(self):
        """前の確定章が無い章は評価対象外（前付けの誤検知を防ぐ）。"""
        p = placements([(10, 11, True), (20, 29, True), (30, 39, True)])
        # index0 のオフセットは +1 で他と違うが、前が無いので対象外
        assert 0 not in flag_suspects(p)

    def test_last_chapter_never_flagged(self):
        p = placements([(10, 19, True), (20, 29, True), (30, 45, True)])
        assert 2 not in flag_suspects(p)

    def test_empty_input(self):
        assert flag_suspects([]) == set()


class TestInterpolatedOffset:
    def test_uses_surrounding_confirmed_chapters(self):
        p = placements([(10, 19, True), (20, 20, False), (30, 39, True)])
        assert interpolated_offset(p, 1) == 9

    def test_averages_when_neighbours_differ(self):
        p = placements([(10, 22, True), (20, 20, False), (30, 40, True)])
        # 前 +12、後 +10 → 平均 +11
        assert interpolated_offset(p, 1) == 11

    def test_falls_back_to_single_neighbour(self):
        p = placements([(10, 19, True), (20, 20, False)])
        assert interpolated_offset(p, 1) == 9

    def test_returns_none_without_any_confirmed_neighbour(self):
        p = placements([(10, 10, False), (20, 20, False)])
        assert interpolated_offset(p, 0) is None
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_boundary_adjudicator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.engine.p1_ingest.boundary_adjudicator'`

- [ ] **Step 3: 実装する**

`core/engine/p1_ingest/boundary_adjudicator.py`:

```python
"""章境界の逸脱検出（層2）と LLM 裁定（層3）。

層2は「前の確定章と次の確定章の双方とオフセットが食い違う章」を要審査とする。
逸脱幅の閾値は使えない — relations の正当な段差は 2、Naven の誤りは 1 であり、
大小で正誤を区別できないため（実測 2026-07-19）。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.3, §2.4
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Set

from core.config import print_log


@dataclass
class ChapterPlacement:
    """コンテンツスキャンが決めた1章の配置。"""

    index: int
    title: str
    logical_page: int          # TOC 由来の論理頁（1-indexed）
    start_page: int            # 決定した物理頁（0-indexed）
    matched: bool              # True=本文照合が成立、False=フォールバック

    @property
    def offset(self) -> int:
        return self.start_page - self.logical_page


def _confirmed_neighbour(
    placements: List[ChapterPlacement], index: int, step: int
) -> Optional[ChapterPlacement]:
    """index から step 方向へ進み、最初に見つかる確定章を返す。"""
    i = index + step
    while 0 <= i < len(placements):
        if placements[i].matched:
            return placements[i]
        i += step
    return None


def flag_suspects(placements: List[ChapterPlacement]) -> Set[int]:
    """要審査の章の index 集合を返す（層2）。

    規則:
      - フォールバックに落ちた章は無条件に要審査
      - 前の確定章と次の確定章の**双方と**オフセットが食い違う章は要審査

    前または次の確定章が存在しない章（前付け・最終章）は評価できないため
    対象外とする。これにより、ローマ数字など別の頁体系を持つ前付けの
    誤検知が自動的に消える。
    """
    suspects: Set[int] = set()

    for p in placements:
        if not p.matched:
            suspects.add(p.index)
            continue

        prev = _confirmed_neighbour(placements, p.index, -1)
        nxt = _confirmed_neighbour(placements, p.index, +1)
        if prev is None or nxt is None:
            continue

        if p.offset != prev.offset and p.offset != nxt.offset:
            suspects.add(p.index)

    return suspects


def interpolated_offset(
    placements: List[ChapterPlacement], index: int
) -> Optional[int]:
    """前後の確定章から期待オフセットを補間する。

    両側にあれば平均、片側のみならその値、どちらも無ければ None。
    """
    prev = _confirmed_neighbour(placements, index, -1)
    nxt = _confirmed_neighbour(placements, index, +1)

    if prev is not None and nxt is not None:
        return round((prev.offset + nxt.offset) / 2)
    if prev is not None:
        return prev.offset
    if nxt is not None:
        return nxt.offset
    return None
```

- [ ] **Step 4: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_boundary_adjudicator.py -v`
Expected: PASS（全ケース）

- [ ] **Step 5: コミット**

```bash
git add core/engine/p1_ingest/boundary_adjudicator.py tests/unit/test_boundary_adjudicator.py
git commit -m "feat: 章境界の逸脱検出を追加（層2）

前の確定章と次の確定章の双方とオフセットが食い違う章のみを要審査と
する。閾値を持たない規則である点が重要で、relations の正当な段差(2)と
Naven の誤り(1)を取り違えない。

フォールバック章は無条件に要審査とし、かつ参照には使わない。前後
いずれかの確定章が無い章（前付け・最終章）は評価対象外とすることで、
別の頁体系を持つ前付けの誤検知が自動的に消える。"
```

---

### Task 7: LLM 裁定（層3）

spec §2.4、§2.5。

**Files:**
- Modify: `core/coreprompts.json`
- Modify: `core/engine/p1_ingest/boundary_adjudicator.py`
- Modify: `tests/unit/test_boundary_adjudicator.py`

**Interfaces:**
- Consumes: `ChapterPlacement`, `flag_suspects`, `interpolated_offset`
- Produces: `BoundaryAdjudicator.adjudicate(doc, placements, pdf_hash) -> List[ChapterPlacement]`

- [ ] **Step 1: プロンプトを追加する**

`core/coreprompts.json` に次のキーを追加する（JSON なので実際は1行の文字列にする）。

```
"CHAPTER_OPENER_ADJUDICATION_PROMPT": "<candidate_pages>\n{pages}\n</candidate_pages>\n\n---\n\n上記は書籍PDFの連続する物理ページで、各ページの冒頭部分のテキストです。\n\nこの中から、章「{title}」が実際に始まるページ（章扉ページ）を1つ特定してください。\n\n【判定の手がかり】\n1. 章扉ページは、章番号（'CHAPTER'、'8'、'XII' 等）と章タイトルが本文より上に置かれ、その下から本文が始まる。\n2. ランニングヘッダー（各ページ上部に繰り返し印字される章名・書名と頁番号の行）は章扉ではない。章扉と紛らわしいので注意すること。\n3. OCR の崩れでタイトルに不自然な空白が入ることがある（'N aven'、'Pref erred'）。表記の完全一致にこだわらず、意味で判断すること。\n4. 章扉ページには印刷された頁番号が無いことが多い。\n\n【現在の推定】\n現在このシステムは物理ページ {current} を章の開始位置と推定しています。これが正しいと判断できる場合は page に {current} を返してください。候補の中に章扉が見当たらない場合は page に null を返してください。無理に選ばないこと。\n\n【出力形式 (JSON Only)】\n{\n  \"page\": <物理ページ番号 または null>,\n  \"reason\": \"判断の根拠を1文で\"\n}\n\n---\n解説や挨拶は一切不要です。純粋な JSON のみを出力してください。"
```

Run: `source venv/bin/activate && python3 -c "
from core.config import load_coreprompts
p = load_coreprompts()
assert 'CHAPTER_OPENER_ADJUDICATION_PROMPT' in p
print('OK:', len(p['CHAPTER_OPENER_ADJUDICATION_PROMPT']), '文字')
"`
Expected: `OK: <数値> 文字`（JSON が壊れていないこと）

- [ ] **Step 2: 失敗するテストを書く**

`tests/unit/test_boundary_adjudicator.py` の末尾に追加する。

```python
from unittest.mock import MagicMock, patch
from core.engine.p1_ingest.boundary_adjudicator import BoundaryAdjudicator


def make_mock_doc(page_texts: list[str]) -> MagicMock:
    doc = MagicMock()
    pages = []
    for t in page_texts:
        p = MagicMock()
        p.get_text.return_value = t
        pages.append(p)
    doc.__len__.return_value = len(pages)
    doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
    return doc


class TestAdjudicate:
    def _run(self, placements_, doc, response=None, raises=False, api_key="k"):
        adj = BoundaryAdjudicator(api_key=api_key, model="m", cache={}, save_cache=lambda: None)
        target = "core.engine.p1_ingest.boundary_adjudicator.call_gemini"
        if raises:
            with patch(target, side_effect=RuntimeError("API down")):
                return adj.adjudicate(doc, placements_, "hash")
        with patch(target, return_value=response):
            return adj.adjudicate(doc, placements_, "hash")

    def test_valid_response_moves_the_boundary(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": 60, "reason": "章扉"}')
        assert result[2].start_page == 60

    def test_null_response_keeps_matched_chapter_as_is(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": null, "reason": "判断不能"}')
        assert result[2].start_page == 61

    def test_null_response_uses_interpolated_offset_for_fallback_chapter(self):
        """フォールバック章では機械照合の値（オフセット0）に留まってはならない。"""
        p = placements([
            (10, 19, True),    # +9
            (20, 20, False),   # フォールバック（オフセット0 = I-27 の症状）
            (30, 39, True),    # +9
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": null, "reason": "判断不能"}')
        assert result[1].start_page == 29, "論理頁20 + 補間オフセット9 = 29 になるべき"

    def test_out_of_range_response_is_rejected(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        # 前章50・次章70 の区間外を返した場合は棄却する
        result = self._run(p, doc, response='{"page": 95, "reason": "でたらめ"}')
        assert result[2].start_page == 61

    def test_monotonicity_violation_is_rejected(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": 50, "reason": "前章と同じ"}')
        assert result[2].start_page == 61

    def test_malformed_response_is_rejected(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response="これはJSONではない")
        assert result[2].start_page == 61

    def test_exception_falls_back_safely(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, raises=True)
        assert result[2].start_page == 61

    def test_no_api_key_skips_llm_entirely(self):
        p = placements([
            (10, 19, True), (20, 20, False), (30, 39, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        adj = BoundaryAdjudicator(api_key=None, model="m", cache={}, save_cache=lambda: None)
        with patch("core.engine.p1_ingest.boundary_adjudicator.call_gemini") as mock_llm:
            result = adj.adjudicate(doc, p, "hash")
        assert not mock_llm.called
        # API キーが無くてもフォールバック章は補間オフセットで改善される
        assert result[1].start_page == 29

    def test_no_suspects_returns_input_unchanged(self):
        p = placements([(10, 19, True), (20, 29, True), (30, 39, True)])
        doc = make_mock_doc(["x\n"] * 100)
        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)
        with patch("core.engine.p1_ingest.boundary_adjudicator.call_gemini") as mock_llm:
            result = adj.adjudicate(doc, p, "hash")
        assert not mock_llm.called
        assert [x.start_page for x in result] == [19, 29, 39]
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_boundary_adjudicator.py -k Adjudicate -v`
Expected: FAIL — `ImportError: cannot import name 'BoundaryAdjudicator'`

- [ ] **Step 4: 実装する**

`core/engine/p1_ingest/boundary_adjudicator.py` の末尾に追加する。
冒頭の import に `import json` と `from core.llm_client import call_gemini` を加える。

```python
# 層3: LLM 裁定の設定
ADJUDICATION_HEAD_LINES = 15       # 各候補頁から見せる行数（_classify_match と同じ）
ADJUDICATION_MAX_PAGES = 32        # 候補区間の上限（トークン量の上限を決める）


class BoundaryAdjudicator:
    """要審査の章について、LLM に章扉頁を裁定させる（層3）。

    LLM が結論を出せない場合の基準値は、その章がどう要審査になったかで分ける。
      - 照合が成立した章 → 既存の機械照合の結果を維持する（実在の一致であり安全）
      - フォールバック章 → 論理頁 + 補間オフセット（機械照合の値はオフセット0で
        あり、それ自体が I-27 の症状であるため維持してはならない）
    """

    def __init__(self, api_key: Optional[str], model: str, cache: dict, save_cache):
        self.api_key = api_key
        self.model = model
        self.cache = cache
        self._save_cache = save_cache

    def adjudicate(
        self, doc: Any, placements: List[ChapterPlacement], pdf_hash: str
    ) -> List[ChapterPlacement]:
        """要審査の章を裁定し、更新した配置リストを返す。"""
        suspects = flag_suspects(placements)
        if not suspects:
            return placements

        print_log(f"  [Adjudicator] 要審査の章: {len(suspects)}件")
        result = list(placements)

        for index in sorted(suspects):
            target = result[index]
            lower, upper = self._interval(result, index, len(doc))
            if lower > upper:
                continue

            decided = None
            if self.api_key:
                decided = self._ask_llm(doc, target, lower, upper, pdf_hash)

            if decided is None:
                decided = self._baseline(result, index, len(doc))

            if decided is None or decided == target.start_page:
                continue
            if not (lower <= decided <= upper):
                continue

            print_log(
                f"  [Adjudicator] 境界を補正: '{target.title[:40]}' "
                f"物理P{target.start_page + 1} → P{decided + 1}"
            )
            result[index] = ChapterPlacement(
                index=target.index, title=target.title,
                logical_page=target.logical_page, start_page=decided,
                matched=target.matched,
            )

        return result

    def _interval(
        self, placements: List[ChapterPlacement], index: int, total_pages: int
    ) -> tuple:
        """前後の確定章に挟まれた物理頁の区間（両端含む）を返す。

        真の章扉は定義上この区間内にあるため、論理頁の正しさに依存しない。
        これにより、論理頁が誤っているために要審査になった章でも窓が正しく張れる。
        """
        prev = _confirmed_neighbour(placements, index, -1)
        nxt = _confirmed_neighbour(placements, index, +1)

        lower = prev.start_page + 1 if prev is not None else 0
        upper = nxt.start_page - 1 if nxt is not None else total_pages - 1
        upper = min(upper, lower + ADJUDICATION_MAX_PAGES - 1, total_pages - 1)
        return lower, upper

    def _baseline(
        self, placements: List[ChapterPlacement], index: int, total_pages: int
    ) -> Optional[int]:
        """LLM が結論を出せない場合の基準値（spec §2.5）。"""
        target = placements[index]
        if target.matched:
            return target.start_page

        offset = interpolated_offset(placements, index)
        if offset is None:
            return target.start_page

        candidate = target.logical_page + offset
        return max(0, min(candidate, total_pages - 1))

    def _ask_llm(
        self, doc: Any, target: ChapterPlacement, lower: int, upper: int, pdf_hash: str
    ) -> Optional[int]:
        """候補区間を見せて章扉頁を尋ねる。判断できない場合は None を返す。"""
        cache_key = f"{pdf_hash}_adjudicate_{target.index}_{target.title}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        blocks = []
        for idx in range(lower, upper + 1):
            lines = [l.strip() for l in doc[idx].get_text("text").split("\n") if l.strip()]
            body = "\n".join(lines[:ADJUDICATION_HEAD_LINES])
            blocks.append(f"--- 物理ページ {idx} ---\n{body}")

        from core.llm_client import load_coreprompts
        prompts = load_coreprompts()
        template = prompts.get("CHAPTER_OPENER_ADJUDICATION_PROMPT", "")
        if not template:
            return None
        prompt = (template
                  .replace("{pages}", "\n\n".join(blocks))
                  .replace("{title}", target.title)
                  .replace("{current}", str(target.start_page)))

        try:
            response = call_gemini(
                prompt, api_key=self.api_key, model=self.model,
                response_mime_type="application/json",
            )
            data = json.loads(response)
            page = data.get("page")
            if page is None:
                decided = None
            else:
                decided = int(page)
        except Exception as e:
            print_log(f"  [Adjudicator] 裁定エラー ('{target.title[:30]}'): {e}")
            return None

        self.cache[cache_key] = decided
        self._save_cache()
        return decided
```

- [ ] **Step 5: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_boundary_adjudicator.py -v`
Expected: PASS（全ケース）

- [ ] **Step 6: コミット**

```bash
git add core/coreprompts.json core/engine/p1_ingest/boundary_adjudicator.py tests/unit/test_boundary_adjudicator.py
git commit -m "feat: 要審査の章に対する LLM 裁定を追加（層3）

前後の確定章に挟まれた物理区間の各頁を見せ、章扉頁を裁定させる。
窓を物理区間にすることで、論理頁が誤っている章でも正しく張れる。

返り値は区間内・単調性を機械検証し、不合格・null・API キーなし・例外の
いずれでも安全側へ落ちる。基準値は経路で分ける — 照合成立章は機械照合の
結果を維持し、フォールバック章は論理頁+補間オフセットを採る。後者を
分けないと LLM 失敗時に I-27（オフセット0）へ戻ってしまう。"
```

---

### Task 8: 層2・層3を `_apply_content_scan` に配線する

spec §2.1。

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py`
- Modify: `tests/unit/test_pdf_splitter.py`

**Interfaces:**
- Consumes: `BoundaryAdjudicator`, `ChapterPlacement`
- Produces: なし（配線のみ）

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_pdf_splitter.py` の末尾に追加する。

```python
class TestAdjudicatorWiring:
    def test_content_scan_records_matched_flag(self):
        """フォールバックした章と照合成立した章が区別できること。"""
        s = make_splitter()
        doc = make_mock_doc([
            "x\n", "x\n",
            "Alpha\n本文\n",
            "x\n", "x\n", "x\n",
        ])
        llm_toc = [
            {"title": "Alpha", "start_page": 3, "role": "chapter"},
            {"title": "NotPresent", "start_page": 5, "role": "chapter"},
        ]
        result = s._apply_content_scan(doc, llm_toc)
        assert result[0].get("matched") is True
        assert result[1].get("matched") is False
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_pdf_splitter.py::TestAdjudicatorWiring -v`
Expected: FAIL — `assert None is True`

- [ ] **Step 3: `_apply_content_scan` の結果に `matched` を記録する**

`core/engine/p1_ingest/pdf_splitter.py` の `_apply_content_scan` 内、
結果を append している3箇所を次のように変更する。

照合成立の分岐（`:316`）:
```python
                results.append({**entry, "start_page": best_phys, "matched": True})
```

フォールバックの2分岐（`:351` と `:362`）:
```python
                results.append({**entry, "start_page": rescued_fallback, "matched": False})
```
```python
            results.append({**entry, "start_page": fallback, "matched": False})
```

- [ ] **Step 4: 層2・層3を呼び出す**

`_apply_content_scan` の `return results` の直前に追加する。

```python
        return self._adjudicate_boundaries(doc, results)
```

そして `_apply_content_scan` の直後に新メソッドを追加する。

```python
    def _adjudicate_boundaries(
        self, doc: fitz.Document, results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """層2（逸脱検出）と層3（LLM 裁定）を適用する。

        要審査の章が無ければ results をそのまま返す。したがって、現在正しい
        境界を持つ書籍（実測では corfra）は出力が一切変化しない。
        """
        from .boundary_adjudicator import BoundaryAdjudicator, ChapterPlacement

        placements = [
            ChapterPlacement(
                index=i,
                title=entry.get("title", ""),
                logical_page=int(entry.get("start_page_logical", entry.get("start_page", 0))),
                start_page=int(entry.get("start_page", 0)),
                matched=bool(entry.get("matched", False)),
            )
            for i, entry in enumerate(results)
        ]

        adjudicator = BoundaryAdjudicator(
            api_key=self.api_key, model=self.model,
            cache=self.cache, save_cache=self._save_cache,
        )
        decided = adjudicator.adjudicate(doc, placements, self._current_pdf_hash or "")

        adjusted = []
        for entry, placement in zip(results, decided):
            adjusted.append({**entry, "start_page": placement.start_page})
        return adjusted
```

- [ ] **Step 5: 論理頁を結果に持ち回す**

`ChapterPlacement.logical_page` には TOC 由来の論理頁が必要だが、
`results` の `start_page` は物理頁で上書きされている。
`_apply_content_scan` の append 3箇所に `"start_page_logical": logical_page` を加える。

照合成立の分岐:
```python
                results.append({
                    **entry, "start_page": best_phys,
                    "start_page_logical": logical_page, "matched": True,
                })
```

フォールバックの2分岐も同様に `"start_page_logical": logical_page` を加える。

- [ ] **Step 6: `_current_pdf_hash` を用意する**

`split()` の Route 3 分岐で計算している `pdf_hash` を、
`_apply_content_scan` から参照できるようにする。
`PDFSplitter.__init__` に追加する。

```python
        self._current_pdf_hash: Optional[str] = None
```

`split()` の冒頭（`doc = fitz.open(pdf_path)` の直後）に追加する。

```python
        self._current_pdf_hash = self._get_pdf_hash(pdf_path)
```

Route 3 分岐の `pdf_hash = self._get_pdf_hash(pdf_path)` は
`pdf_hash = self._current_pdf_hash` に置き換える（二重計算を避ける）。

- [ ] **Step 7: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/ -q`
Expected: 全件 PASS

`matched` / `start_page_logical` を追加したことで、`split()` の
戻り値を検査する既存テストが落ちる可能性がある。落ちた場合は、
これらのキーが増えることを許容するようテスト側を修正すること
（`==` による辞書全体比較を、必要なキーの個別検査に変える）。

- [ ] **Step 8: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py tests/unit/test_pdf_splitter.py
git commit -m "feat: 層2・層3を _apply_content_scan に配線する

照合ループの結果に matched フラグと論理頁を記録し、逸脱検出と
LLM 裁定へ渡す。要審査の章が無ければ結果をそのまま返すため、
現在正しい境界を持つ書籍は出力が一切変化しない。"
```

---

### Task 9: 実PDF 4冊での検証

spec §4.4。

**Files:**
- 変更なし（検証のみ）

**Interfaces:**
- Consumes: Task 1-8 のすべて
- Produces: 検証結果（次タスクのドキュメント記述に使う）

- [ ] **Step 1: 全単体テストを実行する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/ -q`
Expected: 全件 PASS。件数を控えておく（着手前は 322 件）。

- [ ] **Step 2: 実PDF 4冊を検証する**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py 2>&1 | tee /tmp/verify_final.txt | tail -50`

Expected:
- `regression（新規の退行）: 0 件`
- `PSE_ground_truth_hits` が Task 2 のベースラインより増えている
- `Naven_ground_truth_hits: 2/2`（VII と XII が正解に一致）

- [ ] **Step 3: corfra が不変であることを個別に確認する**

Run: `grep -A 12 '===== corfra' /tmp/verify_final.txt`
Expected: 10章、頁範囲が Task 1 で記録したベースラインと**完全に一致**する。

corfra は層1で shift=0、層2で要審査0件のはずなので、変化があってはならない。
変化がある場合は層2の誤検知か層1の誤補正である。先に進まずに原因を特定すること。

- [ ] **Step 4: 層1・層2・層3の発火をログで確認する**

Run: `grep -E 'TOCVerifier|Adjudicator' /tmp/verify_final.txt`
Expected:
- PSE: `TOC のエントリと頁番号が -1 ずれています`
- corfra / Naven / relations: `TOC 検算: ずれなし`
- Naven: `要審査の章: 2件` と、VII / XII の境界補正ログ

- [ ] **Step 5: 結果を記録する**

`/tmp/verify_final.txt` の要点（各書籍の章数・正解一致数・発火した層）を
次タスクのドキュメント記述用に控える。

---

### Task 10: ドキュメントの訂正と追記

spec §5。`.claude/hooks/check_management_logs.sh` が要求する管理ログの更新を含む。

**Files:**
- Modify: `docs/management/troubleshooting_log.md`
- Modify: `docs/management/requirements_log.md`

**Interfaces:**
- Consumes: Task 9 の検証結果
- Produces: なし

- [ ] **Step 1: I-27 の記載を訂正する**

`docs/management/troubleshooting_log.md` の I-27 の節の末尾に、
`I-22` と同じ形式の訂正ブロックを追加する。

```markdown
> **[2026-07-19 原因確定・当初診断の全面訂正]** I-27 の当初診断は2箇所で誤っていた。
> **(1) 検証が実パイプラインと異なる文書に対して行われていた。** `scripts/verify_chapter_boundaries.py`
> は元PDFを直接 `PDFSplitter.split()` に渡していたが、実パイプラインは `book_manager.py:182-185` で
> `is_spread_pdf()`→`split_spread_pdf()` を先に通す。PSE は `is_spread=True` かつ `BOOKS` が
> 元PDFを指していたため、本番が一度も見ない175頁の文書で検証されていた（実際は分割後350頁）。
> corfra も `is_spread=True` だが `BOOKS` が分割済みファイルを直接指しており、分割判定を
> 迂回してはいたが見ていた文書自体は正しかった。記録されていた主症状
> 「範囲外フォールバックによる索引頁複製」は本番では発生し得ない。
> **(2) 原因は「ランニングヘッダーが書名」ではない。** PSE の奇数頁（recto）ヘッダーは章タイトルを
> 載せている（例 P150: `Divisions of Interest` / `137`）。書名が出るのは偶数頁のみで、
> これは組版の標準的な交互配置にすぎない。真因は上流の TOC 抽出（Route 3 `_extract_toc`）が
> エントリと頁番号を1つずらして対応付けていたことである（Ch1←29 は実は Ch2 の頁）。
> 目次頁のテキスト層が列単位で出力され、Preface の頁番号 `ix` が `lX` と誤読されて
> タイトル列に紛れ、数値列が1つ足りなくなるため。
> **(3) 調査途中に観測した「PSE のオフセットは −2〜−4 で揃っている」も実在しなかった。**
> 探索窓 `logical-5 … logical+49` の左端が採用されていただけの人工物である。
> 対策は層1（TOC 検算・shift 補正）・層2（逸脱検出）・層3（LLM 裁定）。詳細は
> `docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md`。
> C2（範囲外クランプ）は防御として残すが、その正当化の根拠だった実害はハーネスの副産物だった。
```

- [ ] **Step 2: 検証ハーネスの欠陥を独立した項目として記録する**

同ファイルに新しい項目を追加する。

```markdown
### I-28. 検証スクリプトが実パイプラインと異なる文書を検証していた（修正済み）

- **事象**: `scripts/verify_chapter_boundaries.py:246` が元PDFを直接 `PDFSplitter.split()` に
  渡しており、実パイプライン（`core/book_manager.py:182-185`）が前段で行う見開き分割を
  通していなかった。`is_spread_pdf()` の実測は corfra=True / PSE=True / Naven=False /
  relations=False。ただし実害を受けたのは **PSE のみ**である。corfra は `BOOKS` 辞書で
  分割済みファイルが直接ハードコードされており、分割判定を迂回してはいたが結果として
  正しい文書を見ていた。**PSE だけが元PDF（175頁）で検証され、本番が見る350頁の文書を
  一度も検証していなかった。**
- **影響**: (1) I-27 の原因診断が誤った（詳細は I-27 の訂正ブロック）。(2) マージ済み
  I-22/I-24/I-25 が主張する「実PDF 4冊で退行0件」のうち PSE の分は無意味な検証だった
  （再測の結果、正しい文書上でも退行は無かったため結論自体は維持される）。
- **対策**: `resolve_input_pdf()` を追加し、`book_manager` と同じ前処理を通す。
  期待値定数も分割後の文書で取り直した。
- **教訓**: 検証スクリプトは「本番と同じ入力を作る」ところまで含めて本番と一致させなければ
  ならない。前処理の1段の欠落が、その上に積んだ全ての診断を無効にする。
  メモリに記録済みの教訓「検証スクリプトの assert 範囲を疑う」の、より上流での再発である。
```

- [ ] **Step 3: PSE Ch5 の誤着地を追記する**

I-26 の節の後に、本調査で新規発見した事象を追記する。

```markdown
### I-29. PSE Chapter 5 の境界が22頁ずれていた（層1で解消）

- **事象**: `Chapter 5 New Economic Forms: a Report` の真の扉頁は物理P102 だが、
  修正前の出力は P124 だった（22頁のずれ）。I-27 の調査中に正解データを目視で
  確定させる過程で発見した未記録の欠陥。
- **原因**: I-27 と同一。TOC のエントリと頁番号の1つずれにより、Ch5 は Ch6 の
  頁番号（117）を受け取っていた。
- **対策**: 層1（TOC 検算・shift 補正）で解消。
```

- [ ] **Step 4: requirements_log に判断根拠を記録する**

`docs/management/requirements_log.md` に追記する。

```markdown
## 2026-07-19: 章境界の検算・逸脱検出・LLM 裁定（層1〜層3）

`core/engine/p1_ingest/` に `page_number_map.py` / `toc_verifier.py` /
`boundary_adjudicator.py` を追加した。既存の照合ループ
（`_classify_match` / `_score_candidate` / `_rescue_by_local_offset`）は変更していない。

**なぜ逸脱幅の閾値を使わないか**: 4冊の実測で、relations の**正当な**オフセット段差は 2、
Naven の**誤り**は 1 だった。大小で正誤を区別できないため、閾値ではなく
「前後の確定章の双方と食い違うか」という構造で判定する。

**なぜ最頻値でオフセットを推定するか**: relations は部扉ごとに段が変わるため、
中央値は実在しない中間値になりうる。最頻値は必ず実在する段のいずれかを選ぶ。

**なぜ shift 検算が安全か**: 実測で PSE のみ shift=−1 が 12対2 で勝ち、
他3冊は shift=0 が勝った（誤検出ゼロ）。差が明瞭なため閾値調整を要しない。
判断材料が乏しい場合は補正しない安全弁（`SHIFT_MIN_MATCHES` /
`SHIFT_DOMINANCE_RATIO`）を設けた。

**なぜ LLM 失敗時の基準値を経路で分けるか**: フォールバック章の機械照合の値は
「オフセット0」であり、それ自体が I-27 の症状である。ここで機械照合の結果を
維持すると、LLM が失敗した瞬間に不具合へ戻る。照合が成立した章は逆に、
実在の一致であるため維持するほうが安全である。

**探索窓をオフセット中心にしなかった理由**: 層1がオフセットを推定するため技術的には
可能だが、窓は照合ループの中核であり変更すれば4冊すべての結果が動く。
手前方向のずれは層3（確定章間の物理区間を窓とする）が回収するため、
本スペックでは触らない。将来の独立した改善候補として記録する。

**`golden-verification` は未実施**。I-21（VLM OCR）完了後に1回だけ実行する
（実 API コストを2回払わないため。2026-07-19 ユーザー判断）。
```

- [ ] **Step 5: 検証結果の数値を埋める**

Task 9 の実測値（正解一致数の改善、単体テスト件数）を上記の記述に反映する。
`<数値>` のような未確定表現を残さないこと。

- [ ] **Step 6: コミット**

```bash
git add docs/management/troubleshooting_log.md docs/management/requirements_log.md
git commit -m "docs: I-27 の原因診断を全面訂正し I-28/I-29 を追記

I-27 の当初診断は2箇所で誤っていた。検証スクリプトが見開き分割を
通しておらず本番の見ない文書を検証していたこと（I-28 として独立記録）、
および原因が「ヘッダーが書名」ではなく TOC 抽出の系統的ずれだったこと。

調査中に発見した PSE Chapter 5 の22頁ずれを I-29 として追記した。
層1〜層3の判断根拠は requirements_log に記録した。"
```

---

## 完了後の扱い

本計画の完了時点では `golden-verification` を実施しない（spec §4.5）。
I-21（VLM スライディング OCR の頁重複）のスペックと実装を同一ブランチで続け、
そちらの完了後に1回だけ実行する。

マージの判断は `superpowers:finishing-a-development-branch` に従う。
