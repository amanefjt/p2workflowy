# 書籍モード 章分割精度の改善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `PDFSplitter` の章分割で発生している3件の不具合（I-22 誤マッチ / I-24 outline 無検査 / I-25 TOC 窓固定）を修正し、構造の異なる4冊すべてで章境界が正しく決まるようにする。

**Architecture:** 探索の骨格（`_apply_content_scan` の窓、Route 1→2→3 の優先順位）は保つ。判定部を「先勝ち」から「候補スコアリング」へ差し替え、一致行の**隣接行が裸のページ番号か章マーカーか**でランニングヘッダーを識別する。上流の Route 2 に妥当性検査、Route 3 に目次ページ探索を追加し、下流の修正に到達できるようにする。

**Tech Stack:** Python 3.12 / PyMuPDF (`fitz`) / pytest / `unittest.mock`

## Global Constants

新規定数は `PDFSplitter` クラス先頭にまとめて定義する（プロジェクト方針: ハードコードを避ける）。

| 定数 | 値 | 用途 |
|---|---|---|
| `OUTLINE_MIN_PAGES_PER_CHAPTER` | `3` | 1章あたり平均頁数の下限（唯一の頁密度指標。当初あったエントリ数比の指標は同じ量の裏返しで到達不能な死にコードだったため Task 1 レビューで削除） |
| `OUTLINE_LABEL_SEQ_RATIO` | `0.5` | 連番ラベルとみなす割合 |
| `TOC_SEARCH_PAGES` | `30` | 目次ページを探索する範囲 |
| `TOC_SAMPLE_PAGES` | `8` | 目次発見時に LLM / VLM に渡すページ数 |
| `TOC_FALLBACK_PAGES` | `15` | 目次が見つからない場合の退避窓（Task 2 レビューで追加。8 に縮小すると従来15頁窓より狭くなり既存書籍が退行するため） |
| `HEADING_SCAN_LINES` | `15` | Pass 1 で走査する行数 |
| `JOINED_SCAN_LINES` | `5` | Pass 2 で結合する行数 |
| `SCORE_HEADER_PENALTY` | `-100` | ランニングヘッダー判定の減点 |
| `SCORE_CHAPTER_MARKER` | `+30` | 章マーカー隣接の加点 |
| `SCORE_SPARSE_PAGE_CHARS` | `1500` | この文字数未満のページに加点 |

## Global Constraints

- 既存の探索窓 `logical-5 … logical+49` は変更しない
- `exact` / `joined` という一致の種類を順位付けに使ってはならない（Naven で優先順位が反転するため）
- Task 4（結合照合のゲート撤廃）は Task 3（スコアリング）なしに単独でコミットしてはならない — corfra の `3 Place` が退行する
- 実 PDF 検証で VLM フルランを行わない。Route 3 の LLM TOC 抽出のみ（キャッシュ有効）
- 仕様書: `docs/superpowers/specs/2026-07-19-chapter-splitting-accuracy-design.md`

## File Structure

| ファイル | 責務 | 変更 |
|---|---|---|
| `core/engine/p1_ingest/pdf_splitter.py` | 章分割の全ロジック（387行、単一クラス） | 定数追加・メソッド追加・`_apply_content_scan` / `_extract_toc` / `_get_chapters_from_outline` 改修 |
| `tests/unit/test_pdf_splitter.py` | 単体テスト（既存 fixture パターンを踏襲） | テストクラス追加 |
| `docs/management/troubleshooting_log.md` | 不具合記録 | I-24 / I-25 追記、I-22 に原因確定を追記 |
| `docs/management/requirements_log.md` | 仕様変更の判断根拠 | 判定方式変更の根拠を追記 |

`pdf_splitter.py` は387行で単一責務（章分割）に収まっており、分割はしない。

---

## Task 1: Route 2 outline の妥当性検査（I-24）

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py`（定数追加、`_is_plausible_outline` 新規、`_get_chapters_from_outline:132-153` 改修）
- Test: `tests/unit/test_pdf_splitter.py`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces: `_is_plausible_outline(entries: List[Tuple[str, int]], total_pages: int) -> bool` — 章目次として妥当なら True

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_pdf_splitter.py` の末尾に追加:

```python
# ============================================================
# _is_plausible_outline (I-24)
# ============================================================

class TestIsPlausibleOutline:
    def test_rejects_one_entry_per_page(self):
        """PSEpdf.pdf 実測: 175頁に175件の f1…f175 は章目次ではない。"""
        s = make_splitter()
        entries = [(f"f{i+1}", i + 1) for i in range(175)]
        assert s._is_plausible_outline(entries, total_pages=175) is False

    def test_rejects_sequential_page_labels(self):
        """比が閾値内でも、連番ラベル形式なら棄却する。"""
        s = make_splitter()
        entries = [(f"f{i*20+1}", i * 20 + 1) for i in range(9)]
        assert s._is_plausible_outline(entries, total_pages=300) is False

    def test_accepts_normal_chapter_outline(self):
        s = make_splitter()
        entries = [
            ("Preface", 1), ("1. Introduction", 12), ("2. Methods", 45),
            ("3. Results", 88), ("4. Discussion", 130), ("Appendix", 170),
        ]
        assert s._is_plausible_outline(entries, total_pages=200) is True

    def test_rejects_too_few_pages_per_chapter(self):
        s = make_splitter()
        entries = [(f"Chapter {i}", i * 2 + 1) for i in range(20)]
        assert s._is_plausible_outline(entries, total_pages=45) is False

    def test_empty_entries_rejected(self):
        s = make_splitter()
        assert s._is_plausible_outline([], total_pages=100) is False


class TestOutlineGuardIntegration:
    def test_garbage_outline_returns_none(self):
        """棄却された outline は None を返し Route 3 へ落とす。"""
        s = make_splitter()
        toc = [(1, f"f{i+1}", i + 1) for i in range(175)]
        doc = make_mock_doc(["text"] * 175, toc=toc)
        assert s._get_chapters_from_outline(doc) is None

    def test_valid_outline_still_works(self):
        s = make_splitter()
        toc = [(1, "Preface", 1), (1, "1. Introduction", 20), (1, "2. Methods", 60)]
        doc = make_mock_doc(["text"] * 100, toc=toc)
        result = s._get_chapters_from_outline(doc)
        assert result is not None
        assert len(result) == 3
        assert result[0]["start_page"] == 0
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py::TestIsPlausibleOutline -v`
Expected: FAIL — `AttributeError: 'PDFSplitter' object has no attribute '_is_plausible_outline'`

- [ ] **Step 3: 定数を追加**

`pdf_splitter.py` の `class PDFSplitter:` 直下、`CACHE_PATH` の隣に追加:

```python
    # --- Route 2 (outline) 妥当性検査 (I-24) ---
    OUTLINE_MAX_ENTRY_RATIO = 0.10
    OUTLINE_MIN_PAGES_PER_CHAPTER = 3
    OUTLINE_LABEL_SEQ_RATIO = 0.5
```

- [ ] **Step 4: `_is_plausible_outline` を実装**

`_get_chapters_from_outline` の直前に追加:

```python
    def _is_plausible_outline(
        self, entries: List[tuple], total_pages: int
    ) -> bool:
        """outline が章目次として妥当かを検査する (I-24)。

        スキャンソフトはページラベル（f1, f2, ... ）を outline として
        埋め込むことがある。これを章目次として採用すると 1頁=1章 の
        分割が発生するため、明らかに章目次でないものを棄却する。
        """
        if not entries or total_pages <= 0:
            return False

        # 指標A: エントリ数がページ数に対して多すぎる
        if (len(entries) / total_pages) > self.OUTLINE_MAX_ENTRY_RATIO:
            print_log(
                f"  [Splitter] outline 棄却: {len(entries)}件/{total_pages}頁 "
                f"= 比{len(entries)/total_pages:.2f} が上限{self.OUTLINE_MAX_ENTRY_RATIO}超"
            )
            return False

        # 指標B: 1章あたり平均頁数が少なすぎる
        if (total_pages / len(entries)) < self.OUTLINE_MIN_PAGES_PER_CHAPTER:
            print_log(f"  [Splitter] outline 棄却: 1章あたり平均頁数が過小")
            return False

        # 指標C: 連番ページラベル形式（共通接頭辞 + 数字のみ）が大半
        label_like = 0
        for title, _ in entries:
            t = title.strip()
            if re.fullmatch(r'[A-Za-z]{0,3}\d{1,4}', t):
                label_like += 1
        if (label_like / len(entries)) > self.OUTLINE_LABEL_SEQ_RATIO:
            print_log(
                f"  [Splitter] outline 棄却: 連番ページラベル形式が "
                f"{label_like}/{len(entries)} 件"
            )
            return False

        return True
```

- [ ] **Step 5: `_get_chapters_from_outline` に検査を差し込む**

`pdf_splitter.py:144-145` の `if not entries: return None` の直後に追加:

```python
        if not entries:
            return None

        if not self._is_plausible_outline(entries, len(doc)):
            return None
```

- [ ] **Step 6: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py -v`
Expected: PASS（既存テストを含め全件）

- [ ] **Step 7: 実 PDF で確認**

```bash
source venv/bin/activate && python3 -c "
import fitz, sys; sys.path.insert(0,'.')
from core.engine.p1_ingest.pdf_splitter import PDFSplitter
s=PDFSplitter.__new__(PDFSplitter)
d=fitz.open('data/input/Booksample/pse/PSEpdf.pdf')
print('結果:', s._get_chapters_from_outline(d))
"
```
Expected: `outline 棄却: 175件/175頁` のログが出て `結果: None`

- [ ] **Step 8: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py tests/unit/test_pdf_splitter.py
git commit -m "fix: PDF ネイティブ outline の妥当性検査を追加（I-24）

PSEpdf.pdf のようにスキャンソフトがページラベル f1…f175 を outline
として埋め込む PDF で、1頁=1章の175分割が発生していた。エントリ数比・
1章あたり頁数・連番ラベル形式の3指標で棄却し Route 3 へ落とす。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: TOC ページ探索（I-25）

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py`（定数追加、`_find_toc_pages` 新規、`_extract_toc:290-327` / `_extract_toc_vlm:329-387` 改修）
- Test: `tests/unit/test_pdf_splitter.py`

**Interfaces:**
- Consumes: Task 1 の定数配置パターン
- Produces: `_find_toc_pages(doc) -> List[int]` — LLM に渡すべきページ index のリスト（0-indexed、昇順）

- [ ] **Step 1: 失敗するテストを書く**

```python
# ============================================================
# _find_toc_pages (I-25)
# ============================================================

class TestFindTocPages:
    def test_finds_toc_beyond_fixed_window(self):
        """Naven.pdf 実測: 目次が idx 16 にあり従来の15頁窓の外。"""
        s = make_splitter()
        texts = ["front matter"] * 16 + ["TABLE\nOF CONTENTS\nChap. I. METHODS"] \
            + ["contents cont."] * 8 + ["body"] * 100
        doc = make_mock_doc(texts)
        pages = s._find_toc_pages(doc)
        assert 16 in pages

    def test_finds_toc_near_front(self):
        """corfra 実測: 目次は idx 3。"""
        s = make_splitter()
        texts = ["cover", "title", "copyright", "Contents\n1 Arbitrary Location\n9"] \
            + ["body"] * 50
        doc = make_mock_doc(texts)
        assert 3 in s._find_toc_pages(doc)

    def test_falls_back_to_leading_pages_when_absent(self):
        """目次が見つからない場合は先頭から既定頁数を返す。"""
        s = make_splitter()
        doc = make_mock_doc(["body text"] * 50)
        pages = s._find_toc_pages(doc)
        assert pages == list(range(s.TOC_SAMPLE_PAGES))

    def test_includes_pages_following_toc(self):
        """目次は複数頁にまたがるため後続頁も含める。"""
        s = make_splitter()
        texts = ["x"] * 5 + ["Contents\nChapter 1"] + ["y"] * 40
        pages = s._find_toc_pages(make_mock_doc(texts))
        assert 5 in pages and 6 in pages

    def test_does_not_exceed_document_length(self):
        s = make_splitter()
        doc = make_mock_doc(["Contents\nChapter 1", "b"])
        assert all(0 <= p < 2 for p in s._find_toc_pages(doc))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py::TestFindTocPages -v`
Expected: FAIL — `AttributeError: ... '_find_toc_pages'`

- [ ] **Step 3: 定数を追加**

Task 1 で追加した定数群の下に:

```python
    # --- Route 3 TOC ページ探索 (I-25) ---
    TOC_SEARCH_PAGES = 30
    TOC_SAMPLE_PAGES = 8
```

- [ ] **Step 4: `_find_toc_pages` を実装**

`_extract_toc` の直前に追加:

```python
    TOC_HEADING_RE = re.compile(
        r'^\s*(TABLE\s+OF\s+CONTENTS|CONTENTS|目\s*次)\s*$',
        re.IGNORECASE | re.MULTILINE,
    )

    def _find_toc_pages(self, doc: fitz.Document) -> List[int]:
        """目次ページを探索し、LLM に渡すページ index を返す (I-25)。

        従来は先頭15頁固定だったが、Naven.pdf のように目次が idx 16-24 に
        ある書籍では窓外となり TOC を取得できなかった。目次見出しを探索し、
        見つかった位置から後続頁を含めて返す。見つからない場合は従来どおり
        先頭から既定頁数を返す。
        """
        limit = min(self.TOC_SEARCH_PAGES, len(doc))
        for i in range(limit):
            text = doc[i].get_text()
            # 先頭数行に目次見出しがあるページを目次とみなす
            head = "\n".join(text.split("\n")[:5])
            if self.TOC_HEADING_RE.search(head):
                end = min(i + self.TOC_SAMPLE_PAGES, len(doc))
                print_log(f"  [Splitter] 目次ページ検出: idx {i}-{end-1}")
                return list(range(i, end))

        return list(range(min(self.TOC_SAMPLE_PAGES, len(doc))))
```

- [ ] **Step 5: `_extract_toc` を改修**

`pdf_splitter.py:296-298` の固定窓を置き換える:

```python
        text_samples = ""
        for i in self._find_toc_pages(doc):
            text_samples += f"--- Page {i+1} ---\n" + doc[i].get_text() + "\n"
```

- [ ] **Step 6: `_extract_toc_vlm` を改修**

`pdf_splitter.py:343-347` の固定窓を置き換える:

```python
        images = []
        for i in self._find_toc_pages(doc):
            pix = doc[i].get_pixmap(dpi=150)
            img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
```

- [ ] **Step 7: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py -v`
Expected: PASS（全件）

- [ ] **Step 8: 実 PDF で確認**

```bash
source venv/bin/activate && python3 -c "
import fitz, sys; sys.path.insert(0,'.')
from core.engine.p1_ingest.pdf_splitter import PDFSplitter
s=PDFSplitter.__new__(PDFSplitter)
for n,p in [('corfra','data/input/Booksample/corfra/corfrapdf_split.pdf'),
            ('relations','data/input/Booksample/relations/relationspdf.pdf'),
            ('Naven','data/input/Booksample/Naven/Naven.pdf'),
            ('PSE','data/input/Booksample/pse/PSEpdf.pdf')]:
    d=fitz.open(p); pages=s._find_toc_pages(d)
    sample=''.join(d[i].get_text() for i in pages)
    print(f'{n:10s} pages={pages[:3]}… 目次語含む={\"ontents\" in sample or \"CONTENTS\" in sample}')
"
```
Expected: 4冊すべて `目次語含む=True`。Naven は `pages=[16, 17, 18]…`

- [ ] **Step 9: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py tests/unit/test_pdf_splitter.py
git commit -m "fix: TOC 抽出のサンプリング窓を目次ページ探索に変更（I-25）

先頭15頁固定だったため Naven.pdf のように目次が idx 16-24 にある書籍で
TOC を取得できず、全編が単一章として処理されていた。目次見出しを探索し
その周辺頁のみを LLM に渡すことで、窓外の目次に届きつつ入力量も抑える。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 一致の分類とスコアリング（I-22 本体）

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py`（定数追加、`_parse_page_number` / `_is_chapter_marker` / `_extract_leading_numeral` / `_classify_match` / `_score_candidate` 新規、`_apply_content_scan:155-220` 改修、`_normalize_title:283-288` に `Chap` 追加）
- Test: `tests/unit/test_pdf_splitter.py`

**Interfaces:**
- Consumes: Task 1・2 の定数配置パターン
- Produces:
  - `_parse_page_number(line: str) -> Optional[int]` — OCR 崩れに耐える頁番号パース
  - `_extract_leading_numeral(title: str) -> Optional[str]` — TOC タイトルの先頭章番号
  - `_is_chapter_marker(line: str, chapter_numeral: Optional[str]) -> bool`
  - `_classify_match(page_text: str, norm_title: str, chapter_numeral: Optional[str]) -> Optional[str]` — `"header"` / `"title"` / `None`
  - `_score_candidate(page_text: str, kind: str, chapter_numeral: Optional[str]) -> int`

- [ ] **Step 1: 失敗するテストを書く**

```python
# ============================================================
# _parse_page_number / _is_chapter_marker (I-22)
# ============================================================

class TestParsePageNumber:
    def test_plain_number(self):
        assert make_splitter()._parse_page_number("29") == 29

    def test_ocr_i_for_one(self):
        """Naven 実測: '3 I' は 31 の OCR 崩れ。"""
        assert make_splitter()._parse_page_number("3 I") == 31

    def test_ocr_r_for_one(self):
        """Naven 実測: 'r 72' は 172 の OCR 崩れ。"""
        assert make_splitter()._parse_page_number("r 72") == 172

    def test_roman_numeral_is_not_page_number(self):
        """章マーカー 'XIII' を頁番号と誤認してはならない。"""
        assert make_splitter()._parse_page_number("XIII") is None

    def test_bare_i_is_not_page_number(self):
        """数字を1文字も含まない文字列は頁番号ではない。"""
        assert make_splitter()._parse_page_number("I") is None

    def test_prose_is_not_page_number(self):
        assert make_splitter()._parse_page_number("In this distributed process") is None


class TestIsChapterMarker:
    def test_chapter_word(self):
        assert make_splitter()._is_chapter_marker("CHAPTER", None) is True

    def test_matching_arabic_numeral(self):
        """corfra 実測: 扉頁は '4' / 'Things'。"""
        assert make_splitter()._is_chapter_marker("4", "4") is True

    def test_matching_roman_numeral(self):
        """Naven 実測: 扉頁は 'CHAPTER' / 'XIII'。"""
        assert make_splitter()._is_chapter_marker("XIII", "XIII") is True

    def test_non_matching_number_is_not_marker(self):
        """Naven 実測: 本文頁の '29' は第III章の章マーカーではない。"""
        assert make_splitter()._is_chapter_marker("29", "III") is False


# ============================================================
# _classify_match (I-22)
# ============================================================

class TestClassifyMatch:
    def test_same_line_running_header_is_header(self):
        """corfra 実測: 'Knowing | 147' はランニングヘッダー。"""
        s = make_splitter()
        text = "Knowing | 147\nwoman drove it fast, with her sunglasses\nhand. As we snaked"
        assert s._classify_match(text, "knowing", None) == "header"

    def test_adjacent_line_running_header_is_header(self):
        """Naven 実測: タイトルと頁番号が別行のヘッダー。"""
        s = make_splitter()
        text = "The Concepts of Structure and Function\n29\nnot more so than is the use"
        assert s._classify_match(text, "the concepts of structure and function", "III") == "header"

    def test_chapter_number_adjacent_is_title(self):
        """corfra 実測: 扉頁 '4' / 'Things'。"""
        s = make_splitter()
        text = "4\nThings\nThe difference between ambiguity"
        assert s._classify_match(text, "things", "4") == "title"

    def test_multiline_title_page_is_title(self):
        """Naven 実測: 'CHAPTER' / 'XIII' / 2行に割れたタイトル。"""
        s = make_splitter()
        text = ("CHAPTER\nXIII\nEthological Contrast, Competition\n"
                "and Schismogenesis\nT\nHE foregoing description")
        kind = s._classify_match(
            text, "ethological contrast competition and schismogenesis", "XIII")
        assert kind == "title"

    def test_body_prose_prefix_is_not_title_page(self):
        """corfra 実測: 本文行 'things, and it is against them…' で誤検出しない。"""
        s = make_splitter()
        text = ("Place | 81\nIn this distributed process, people are helped by\n"
                "things, and it is against them that we measure\nthe terrain can")
        assert s._classify_match(text, "things", "4") != "title"

    def test_no_match_returns_none(self):
        s = make_splitter()
        assert s._classify_match("unrelated body text\nmore text", "knowing", None) is None


# ============================================================
# 候補スコアリング (I-22)
# ============================================================

class TestCandidateScoring:
    def test_title_page_outranks_earlier_body_match(self):
        """corfra 実測: idx89(本文) より idx93(扉) を選ぶ。"""
        s = make_splitter()
        body = ("Place | 81\nIn this distributed process, people are helped\n"
                "things, and it is against them that we measure\n" + "x " * 800)
        title_pg = "4\nThings\nThe difference between ambiguity and clarity"
        pages = ["filler"] * 80 + [body] + ["filler"] * 3 + [title_pg] + ["filler"] * 30
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [{"title": "4 Things", "start_page": 85}])
        assert result[0]["start_page"] == 84

    def test_joined_match_does_not_beat_standalone_title(self):
        """corfra 実測: '3 Place' が本文結合一致(idx73)へ退行しない。"""
        s = make_splitter()
        body = "Mystery | 65\nslowly snakes up the tall stone house\n" + "y " * 900
        title_pg = "3\nPlace\nThis then, may be a way out of the dichotomy"
        pages = ["filler"] * 64 + [body] + ["filler"] * 3 + [title_pg] + ["filler"] * 40
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [{"title": "3 Place", "start_page": 69}])
        assert result[0]["start_page"] == 68

    def test_monotonic_ordering_enforced(self):
        """後続章が前章より前に着地してはならない。"""
        s = make_splitter()
        pages = ["filler"] * 20 + ["1\nAlpha\nbody"] + ["filler"] * 20 + ["2\nBeta\nbody"] \
            + ["filler"] * 20
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [
            {"title": "1 Alpha", "start_page": 21},
            {"title": "2 Beta", "start_page": 42},
        ])
        assert result[0]["start_page"] < result[1]["start_page"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py::TestParsePageNumber tests/unit/test_pdf_splitter.py::TestClassifyMatch -v`
Expected: FAIL — `AttributeError: ... '_parse_page_number'`

- [ ] **Step 3: 定数を追加**

```python
    # --- コンテンツスキャンの判定 (I-22) ---
    HEADING_SCAN_LINES = 15
    JOINED_SCAN_LINES = 5
    SCORE_HEADER_PENALTY = -100
    SCORE_CHAPTER_MARKER = 30
    SCORE_SPARSE_PAGE_CHARS = 1500
    SCORE_SPARSE_BONUS = 20
```

- [ ] **Step 4: 補助メソッドを実装**

`_matches_heading` の直前に追加:

```python
    # OCR で数字と誤読されやすい文字の対応表
    _OCR_DIGIT_MAP = str.maketrans(
        {'I': '1', 'l': '1', '|': '1', 'i': '1', 'r': '1',
         'O': '0', 'o': '0', 'S': '5', 'B': '8'}
    )
    _ROMAN_RE = re.compile(r'^[IVXLC]+$', re.IGNORECASE)

    def _parse_page_number(self, line: str) -> Optional[int]:
        """行を頁番号として解釈する。OCR 崩れ（'3 I'→31, 'r 72'→172）に耐える。

        数字を1文字も含まない文字列（ローマ数字 'XIII' や 'I'）は
        頁番号として扱わない。章マーカーとの誤認を防ぐため。
        """
        t = line.strip()
        if not t or len(t) > 8:
            return None
        if not any(c.isdigit() for c in t):
            return None
        normalized = t.translate(self._OCR_DIGIT_MAP).replace(' ', '')
        if normalized.isdigit() and 1 <= int(normalized) <= 9999:
            return int(normalized)
        return None

    def _extract_leading_numeral(self, title: str) -> Optional[str]:
        """TOC タイトルの先頭章番号を取り出す（'4 Things' → '4'）。"""
        m = re.match(
            r'^\s*(?:Chap(?:ter)?\.?|Part|Section)?\s*([\dIVXLCivxlc]+)[.:]?\s+',
            title,
        )
        return m.group(1) if m else None

    def _is_chapter_marker(self, line: str, chapter_numeral: Optional[str]) -> bool:
        """行が章マーカー（'CHAPTER' / その章の番号）かを判定する。

        単なる数字を無条件に章マーカーとすると本文頁の頁番号と区別
        できないため、TOC 由来の章番号と一致する場合のみ真とする。
        """
        t = line.strip()
        if not t or len(t) > 12:
            return False
        if t.upper().rstrip('.') in ('CHAPTER', 'CHAP', 'PART'):
            return True
        if chapter_numeral:
            return t.strip('.').upper() == chapter_numeral.strip('.').upper()
        return False

    def _classify_match(
        self, page_text: str, norm_title: str, chapter_numeral: Optional[str]
    ) -> Optional[str]:
        """ページが章扉かランニングヘッダーかを判定する (I-22)。

        判別軸は一致行の隣接行である。裸の頁番号が隣接していれば
        ランニングヘッダー、章マーカーが隣接していれば章扉とみなす。
        exact / joined という一致の種類は判定に使わない（Naven では
        本文頁のヘッダーが exact、章扉が joined になり順位が反転するため）。
        """
        if not norm_title:
            return None

        lines = [l.strip() for l in page_text.split("\n")]
        nonempty = [l for l in lines if l]
        if not nonempty:
            return None

        # Pass 1: 行単位
        for pos, line in enumerate(nonempty[:self.HEADING_SCAN_LINES]):
            line_norm = self._normalize_title(line)
            if line_norm == norm_title:
                pass
            elif line_norm.startswith(norm_title + " "):
                rest = line_norm[len(norm_title):].strip()
                if self._parse_page_number(rest) is not None:
                    return "header"   # 'Knowing | 147'
                if not self._is_chapter_marker(rest, chapter_numeral):
                    continue          # 本文行の前方一致は一致とみなさない
            else:
                continue

            neighbors = []
            if pos > 0:
                neighbors.append(nonempty[pos - 1])
            if pos + 1 < len(nonempty):
                neighbors.append(nonempty[pos + 1])

            if any(self._is_chapter_marker(n, chapter_numeral) for n in neighbors):
                return "title"
            if any(self._parse_page_number(n) is not None for n in neighbors):
                return "header"
            return "title"

        # Pass 2: 冒頭数行の結合（複数行に割れたタイトル）
        #
        # 部分文字列一致にしてはならない。短いタイトルが本文を掴むため
        # （'things' は "…people are helped by things, and it is against…"
        # に含まれてしまう）。章扉はタイトルで始まる性質を使い、先頭の
        # 章マーカー（'CHAPTER' / 'XIII' / '8'）を剥がしてから前方一致を見る。
        joined_norm = self._normalize_title(" ".join(nonempty[:self.JOINED_SCAN_LINES]))
        tokens = joined_norm.split()
        while tokens and self._is_chapter_marker(tokens[0], chapter_numeral):
            tokens.pop(0)
        if tokens and " ".join(tokens).startswith(norm_title):
            return "title"

        return None

    def _score_candidate(
        self, page_text: str, kind: str, chapter_numeral: Optional[str]
    ) -> int:
        """候補ページを章扉らしさで採点する (I-22)。"""
        score = 0
        if kind == "header":
            score += self.SCORE_HEADER_PENALTY
        head = [l.strip() for l in page_text.split("\n") if l.strip()][:self.JOINED_SCAN_LINES]
        if any(self._is_chapter_marker(l, chapter_numeral) for l in head):
            score += self.SCORE_CHAPTER_MARKER
        if len(page_text.strip()) < self.SCORE_SPARSE_PAGE_CHARS:
            score += self.SCORE_SPARSE_BONUS
        return score
```

- [ ] **Step 5: `_normalize_title` に `Chap` を追加**

Naven の TOC は `Chap. III. THE CONCEPTS…` 形式で、現行の正規表現は `Chapter` しか剥がせない。`pdf_splitter.py:285` を置き換える:

```python
        t = re.sub(
            r'^(?:Chapter|CHAPTER|Chap\.?|CHAP\.?|Part|PART|Section|SECTION)\s+[\dIVXivx]+\s*[.:]?\s*',
            '', text)
```

- [ ] **Step 6: `_apply_content_scan` を先勝ちからスコアリングへ差し替え**

`pdf_splitter.py:182-203` の探索ループを置き換える:

```python
            chapter_numeral = self._extract_leading_numeral(title)

            best_phys = None
            best_score = None
            best_kind = None
            for phys_idx in range(search_start, search_end + 1):
                if phys_idx <= last_found_phys:
                    continue          # 単調性: 前章より前は採らない
                raw_page = doc[phys_idx].get_text("text")

                if self._is_toc_page(raw_page, all_titles):
                    continue

                kind = self._classify_match(raw_page, norm_title, chapter_numeral)
                if kind is None:
                    continue

                score = self._score_candidate(raw_page, kind, chapter_numeral)
                if best_score is None or score > best_score:
                    best_phys, best_score, best_kind = phys_idx, score, kind
```

`best_kind` は Task 4 の局所オフセット救済で使う。

- [ ] **Step 7: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py -v`
Expected: PASS（全件。既存の `_apply_content_scan` テストが落ちる場合は期待値が先勝ち前提でないか確認し、仕様変更が正当なら期待値を更新してコメントで理由を残す）

- [ ] **Step 8: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py tests/unit/test_pdf_splitter.py
git commit -m "fix: 章見出し照合をランニングヘッダー識別＋スコアリングに変更（I-22）

_matches_heading が 'Knowing | 147' 形式のランニングヘッダーと本文行に
誤マッチし、first-match-wins で誤った候補を採用していた。一致行の隣接行
が裸の頁番号か章マーカーかで判別し、窓内の全候補を採点して最良を選ぶ。

exact/joined は順位付けに使わない。Naven では本文頁のヘッダーが exact、
章扉が joined になり優先順位が反転するため。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 局所オフセットによる救済

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py`（`_rescue_by_local_offset` 新規、`_apply_content_scan` の採用部改修）
- Test: `tests/unit/test_pdf_splitter.py`

**Interfaces:**
- Consumes: Task 3 の `_parse_page_number`、`_classify_match` が返す `best_kind`
- Produces: `_rescue_by_local_offset(doc, phys_idx, logical_page, norm_title) -> Optional[int]`

- [ ] **Step 1: 失敗するテストを書く**

```python
# ============================================================
# 局所オフセット救済 (I-22 / Knowing)
# ============================================================

class TestLocalOffsetRescue:
    def test_derives_true_start_from_running_header(self):
        """corfra 実測: idx155 の 'Knowing | 147' から論理145 → idx153。"""
        s = make_splitter()
        pages = ["filler"] * 160
        pages[155] = "Knowing | 147\nwoman drove it fast, with her sunglasses"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 155, 145, "knowing") == 153

    def test_reads_page_number_from_adjacent_line(self):
        """Naven 形式: タイトルと頁番号が別行。"""
        s = make_splitter()
        pages = ["filler"] * 80
        pages[60] = "The Concepts of Structure and Function\n31\nbody text"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(
            doc, 60, 23, "the concepts of structure and function") == 52

    def test_returns_none_when_no_page_number(self):
        s = make_splitter()
        pages = ["filler"] * 50
        pages[30] = "Knowing\nbody text without any page number"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 30, 20, "knowing") is None

    def test_returns_none_when_out_of_range(self):
        s = make_splitter()
        pages = ["filler"] * 20
        pages[10] = "Knowing | 900\nbody"
        doc = make_mock_doc(pages)
        assert s._rescue_by_local_offset(doc, 10, 5, "knowing") is None

    def test_knowing_end_to_end(self):
        """扉頁にタイトル文字が無くても正しい開始位置を得る。"""
        s = make_splitter()
        pages = ["filler"] * 200
        pages[153] = "The tree imposes the verb to be\nCollisions and Connections\nThis story starts"
        pages[155] = "Knowing | 147\nwoman drove it fast"
        doc = make_mock_doc(pages)
        result = s._apply_content_scan(doc, [{"title": "7 Knowing", "start_page": 145}])
        assert result[0]["start_page"] == 153
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py::TestLocalOffsetRescue -v`
Expected: FAIL — `AttributeError: ... '_rescue_by_local_offset'`

- [ ] **Step 3: `_rescue_by_local_offset` を実装**

`_classify_match` の直後に追加:

```python
    def _rescue_by_local_offset(
        self, doc: fitz.Document, phys_idx: int, logical_page: int, norm_title: str
    ) -> Optional[int]:
        """ランニングヘッダーから局所オフセットを求め真の開始位置を導く (I-22)。

        章扉のタイトル文字がテキスト層から欠落している書籍（corfra の
        'Knowing' 章）では、どんなテキスト照合でも扉頁に到達できない。
        照合が当たったヘッダー頁の印刷頁番号 P から局所オフセット
        (phys_idx - P) を求め、TOC の論理頁に加えることで扉頁を導出する。

        書籍全体の頁番号マップは作らない。オフセットは PDF の作られ方に
        依存し（見開きスキャンでは全巻一定、組版由来では部扉ごとに階段状）
        大域的な抽出は書式依存で脆いため、当たった1頁のみを見る。
        """
        lines = [l.strip() for l in doc[phys_idx].get_text("text").split("\n") if l.strip()]
        printed = None

        for pos, line in enumerate(lines[:self.HEADING_SCAN_LINES]):
            line_norm = self._normalize_title(line)
            if line_norm.startswith(norm_title):
                rest = line_norm[len(norm_title):].strip()
                printed = self._parse_page_number(rest)
                if printed is None and pos + 1 < len(lines):
                    printed = self._parse_page_number(lines[pos + 1])
                if printed is None and pos > 0:
                    printed = self._parse_page_number(lines[pos - 1])
                break

        if printed is None:
            return None

        predicted = logical_page + (phys_idx - printed)
        if not (0 <= predicted < len(doc)):
            return None
        return predicted
```

- [ ] **Step 4: `_apply_content_scan` の採用部に救済を差し込む**

Task 3 で書き換えたループの直後、`if best_phys is not None:` ブロックの先頭に追加:

```python
            if best_phys is not None and best_kind == "header":
                rescued = self._rescue_by_local_offset(
                    doc, best_phys, logical_page, norm_title)
                if rescued is not None and rescued > last_found_phys:
                    print_log(
                        f"  [Splitter] 局所オフセット補正: '{title}' "
                        f"物理P{best_phys+1} → P{rescued+1}"
                    )
                    best_phys = rescued
```

- [ ] **Step 5: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_pdf_splitter.py -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py tests/unit/test_pdf_splitter.py
git commit -m "fix: ランニングヘッダーからの局所オフセット救済を追加（I-22）

corfra の 'Knowing' 章は扉頁のタイトル文字がテキスト層に無く、どんな
照合でも到達できない。当たったヘッダー頁の印刷頁番号から局所オフセット
を求め、TOC 論理頁に加えて扉頁を導出する。書籍全体の頁番号マップは
書式依存で脆いため作らない。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 実 PDF 4冊による全章検証

**Files:**
- Create: `scripts/verify_chapter_boundaries.py`（検証用スクリプト。リポジトリに残す）

**Interfaces:**
- Consumes: Task 1〜4 の全変更
- Produces: なし（検証のみ）

- [ ] **Step 1: 検証スクリプトを書く**

```python
"""章分割境界の実測検証（I-22 / I-24 / I-25）。

VLM フルランは行わず PDFSplitter.split() のみを実行する。
Route 3 の LLM TOC 抽出は state/vlm_cache.json にキャッシュされる。
"""
import sys
from pathlib import Path

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

# 実測済みの正解（仕様書 §2 参照）
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


def main() -> int:
    api_key = None
    import os
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    out_root = PROJECT_ROOT / "state" / "_verify_chapters"
    failures = 0

    for name, rel in BOOKS.items():
        path = str(PROJECT_ROOT / rel)
        print(f"\n===== {name} =====")
        splitter = PDFSplitter(api_key=api_key)
        chapters = splitter.split(path, out_root / name)
        print(f"  章数: {len(chapters)}")

        for ch in chapters:
            rng = ch.get("page_range")
            print(f"    {ch['title'][:40]:42s} {rng} role={ch['role']}")

        for title, expected_idx in EXPECTED.get(name, {}).items():
            match = [c for c in chapters if c["title"] == title]
            if not match:
                print(f"  NG  '{title}' が章リストに無い")
                failures += 1
                continue
            actual = match[0]["page_range"][0] - 1
            mark = "OK " if actual == expected_idx else "NG "
            if actual != expected_idx:
                failures += 1
            print(f"  {mark}'{title}' 期待idx{expected_idx} 実際idx{actual}")

    print(f"\n不一致: {failures} 件")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 実行**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py`

Expected:
- **corfra**: 4章すべて `OK`（idx 77 / 93 / 153 / 171）
- **relations**: 6章すべて `OK`（現行と変化しないこと＝回帰なし）
- **Naven**: 章数が2件以上（I-25 修正前は単一章だった）。各章が章扉に着地していること
- **PSE**: `outline 棄却` のログが出て Route 3 に落ち、章数が175でないこと
- 最終行 `不一致: 0 件`

- [ ] **Step 3: 分割 PDF の中身を目視確認**

```bash
source venv/bin/activate && python3 -c "
import fitz, glob
for f in sorted(glob.glob('state/_verify_chapters/corfra/*.pdf')):
    d=fitz.open(f)
    head=[l.strip() for l in d[0].get_text().split(chr(10)) if l.strip()][:2]
    tail=[l.strip() for l in d[len(d)-1].get_text().split(chr(10)) if l.strip()][:2]
    print(f.split('/')[-1][:34], len(d), '頁 | 先頭:', str(head)[:44], '| 末尾:', str(tail)[:36])
"
```

Expected: `06_4_Things.pdf` の先頭に `Place |` を含む頁が無いこと。`09_Knowing.pdf` の末尾に `8 | Anonymous` が無いこと。

- [ ] **Step 4: 全単体テストを実行**

Run: `python3 -m pytest tests/unit/ -v`
Expected: PASS（全件。既存197件以上）

- [ ] **Step 5: コミット**

```bash
git add scripts/verify_chapter_boundaries.py
git commit -m "test: 章分割境界の実測検証スクリプトを追加

corfra・relations・Naven・PSE の4冊で PDFSplitter.split() を実行し、
実測済みの正解 idx と突き合わせる。VLM フルランは行わない。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: ドキュメント更新

**Files:**
- Modify: `core/engine/p1_ingest/pdf_splitter.py`（`_apply_content_scan` の docstring）
- Modify: `docs/management/troubleshooting_log.md`
- Modify: `docs/management/requirements_log.md`

**Interfaces:**
- Consumes: Task 1〜5 の全変更と検証結果
- Produces: なし

- [ ] **Step 1: `_apply_content_scan` の docstring を訂正**

現行は「オフセットは章ごとに変動するためページ番号への依存を断つ」と述べるが、実測ではこれが正しい本と誤りの本がある。`pdf_splitter.py:158-165` を置き換える:

```python
        """LLM の論理ページ番号をコンテンツスキャンで物理ページに補正する。

        紙面ページと PDF 物理ページのオフセットの挙動は PDF の作られ方に
        依存し、事前に予測できない。実測では見開きスキャン（corfra）は
        全巻一定の +8、組版由来のデジタル PDF（relations）は部扉ごとに
        +7→+9→+11→+13 と階段状に変動した。したがってオフセットを仮定せず、
        論理ページは探索窓のヒントとしてのみ使い、本文照合で位置を決める。

        照合は _classify_match() が担う。一致行の隣接行が裸の頁番号なら
        ランニングヘッダー、章マーカーなら章扉と判別し、窓内の全候補を
        _score_candidate() で採点して最良を選ぶ（先勝ちではない）。
        照合が当たった頁がヘッダーだった場合のみ _rescue_by_local_offset()
        が局所オフセットで扉頁を導出する。
        """
```

- [ ] **Step 2: `troubleshooting_log.md` を更新**

I-22 の項に「原因確定」の追記を行い、I-24 / I-25 を新規追加する。それぞれ **事象・調査・原因・対策・再現手順・教訓** を記載する。記載内容の根拠は仕様書 §2 にすべて揃っている。

I-22 追記に必ず含める訂正:
- 「ページ順そのものが前後している」という当初の見立ては**誤読**だった。偶数頁のランニングヘッダーが章名を含まない汎用形式（`164 | Corsican Fragments`）のため、第8章の164頁が第7章の続きに見えただけである。`insert_pdf(from_page, to_page)` は連続範囲でありページ順は正常

- [ ] **Step 3: `requirements_log.md` を更新**

判定方式の変更（先勝ち → スコアリング、`exact`/`joined` を順位付けに使わない）の判断根拠を記録する。特に「Naven でヘッダーが exact・章扉が joined になり優先順位が反転する」という実測を残す。

あわせて**採用しなかった案**として大域的オフセット表を記録する（探索窓は既に真の扉頁を含むため位置決めに寄与せず、余白からの頁番号抽出は書式依存で脆い）。

- [ ] **Step 4: コミット**

```bash
git add core/engine/p1_ingest/pdf_splitter.py docs/management/
git commit -m "docs: 章分割精度の改善を管理ログに記録（I-22/I-24/I-25）

_apply_content_scan の docstring にあったオフセット前提を実測に
合わせて訂正。オフセットの挙動は PDF の作られ方に依存し予測できない。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: 完了判定

- [ ] **Step 1: 全単体テスト**

Run: `python3 -m pytest tests/unit/ -v`
Expected: 全件 PASS

- [ ] **Step 2: 実 PDF 検証の再実行**

Run: `source venv/bin/activate && python3 scripts/verify_chapter_boundaries.py`
Expected: `不一致: 0 件`

- [ ] **Step 3: `golden-verification` skill を実行**

構造検証（見出し階層・章統合・除外セクション）を行い、章分割の変更が下流の出力形式に回帰を起こしていないことを確認する。

- [ ] **Step 4: 未検証事項の確認**

以下は仕様書で「実装時に確認する」とした事項。結果を `troubleshooting_log.md` に追記する。

- PSE が Route 3 に落ちた後、章数が目次相当（Chapter 1〜10 程度）になるか。ならない場合は原因を記録し、別課題として切り出す
- Naven の各章が章扉に着地しているか。3.1 の隣接判定を実データで検証できる唯一の書籍である
