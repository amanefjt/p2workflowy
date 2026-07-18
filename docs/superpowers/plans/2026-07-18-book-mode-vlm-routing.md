# Spec B: 書籍モード Phase 1 入力ルーティング修理・公式化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VLM スライディング OCR の二重定義バグ（I-15）を修理し、書籍モードの `pdf_mode` 無視バグ（I-16）を解消して、書籍単位ルーティング規則（①明示指定 → ②見開きスキャン=VLM → ③Docling可能=Docling → ④それ以外=VLM）を公式の経路として実装する。Phase 1 が実際に使ったルートを記録し、Phase 3 はその実ルートを見て構造化方式（VLM Markdown 正規表現 / Docling role構造化 / ChapterParser・TOCフォールバック）を切り替える。

**Architecture:** `core/engine/p1_ingest/ocr_manager.py` の二重定義削除 → `core/phase1_preprocessor.py` が `pdf_mode` を尊重し実ルートを `phase1_route.json` に記録 → `core/book_manager.py` が書籍単位で1回だけルーティングを判定し章パイプラインへ明示的に渡す → `core/phase3_structure.py` が実ルート参照で分岐し、書籍×Docling実ルートの場合は新設の `structure_nodes_by_role`（`tree_builder.py`）で Docling の role 見出しを直接構造化する。

**Tech Stack:** Python 3.12+ / pytest, pytest-asyncio（既存導入済み） / 既存の PyMuPDF (`fitz`), Docling, Gemini VLM 呼び出し経路をそのまま使用。新規外部依存は追加しない。

## Global Constraints

- 外部ライブラリの新規追加は行わない（既存の `fitz`/`docling`/`PIL` 等で完結する設計）。
- 定数・設定値はファイル先頭にまとめる（本計画では新規マジックナンバーの導入なし）。
- コミットメッセージは日本語（技術用語・識別子は英語のまま）。`frugal-commit` skill を使う。
- 二重定義の削除は「削除直前の再 grep」「削除コミット分離」の原則に従う（Spec A 由来、CLAUDE.md 参照）。
- `core/` を変更するコミットでは `docs/management/troubleshooting_log.md`・`docs/management/requirements_log.md` への追記を行う（`.claude/hooks/check_management_logs.sh` が確認する）。
- 破壊的操作（既存出力ファイルの上書き等）は事前確認する。実 PDF 検証（Task 7/8）は API コストが発生するため、フルラン前に必ずスモークテストで確認する。
- 各タスックの完了ごとに `python3 -m pytest tests/unit/ -q` を実行し全合格を確認してからコミットする。

---

## Task 1: I-15 修理 — `OCRManager.process_page_vlm` の二重定義解消

**Files:**
- Modify: `core/engine/p1_ingest/ocr_manager.py:214-226`（削除対象: pdf_path 引数版の重複定義）
- Test: `tests/unit/test_ocr_manager.py`（新規作成）

**Interfaces:**
- Consumes: なし（既存クラス `OCRManager` の内部整理のみ）
- Produces: `OCRManager.process_page_vlm(self, current_img: Image.Image, prev_img: Optional[Image.Image] = None, page_idx: int = 0, session_dir: Optional[Path] = None) -> str` — 唯一生存するシグネチャ。以降のタスクはこれを前提にしない（呼び出し元 `pdf_ingester.py:67` は変更不要、既にこのシグネチャで呼んでいる）。

- [ ] **Step 1: 削除直前の再 grep で依存を確認する**

```bash
grep -rn "process_page_vlm\|_merge_images_horizontal\|VLM_PROMPT" core/ --include="*.py" | grep -v __pycache__
```

Expected: `process_page_vlm` の呼び出し元は `pdf_ingester.py:67` の1箇所のみ、`_merge_images_horizontal` の呼び出し元は `ocr_manager.py` 内の157行目付近（削除しない側）のみであることを確認する。もし他の呼び出し元が見つかった場合は本タスクを中断しユーザーに報告する。

- [ ] **Step 2: 失敗するテストを書く**

`tests/unit/test_ocr_manager.py` を新規作成:

```python
"""
OCRManager.process_page_vlm の二重定義バグ（I-15）に対する回帰テスト。

クラス内に同名メソッドが2つ定義されていると Python は後者のみを生存させる。
唯一の呼び出し元 pdf_ingester.py:67 は画像引数版のシグネチャで呼ぶため、
pdf_path 引数版が生存していると毎回 TypeError になる。
"""

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from core.engine.p1_ingest.ocr_manager import OCRManager


def _make_ocr_manager() -> OCRManager:
    """API 初期化や環境変数を経由せずに OCRManager インスタンスを作る。"""
    manager = OCRManager.__new__(OCRManager)
    manager.semaphore = asyncio.Semaphore(1)
    manager.cache = {}
    manager._save_cache = lambda: None
    manager._call_gemini_raw = AsyncMock(return_value="# Heading\nBody text")
    return manager


class TestProcessPageVlmSignature:
    def test_signature_matches_pdf_ingester_call_site(self):
        """pdf_ingester.py:67 は (curr_img, prev_img=, page_idx=, session_dir=) で呼ぶ。
        生存すべきシグネチャはこれと一致する画像引数版でなければならない。"""
        sig = inspect.signature(OCRManager.process_page_vlm)
        assert list(sig.parameters.keys()) == [
            "self", "current_img", "prev_img", "page_idx", "session_dir",
        ]

    @pytest.mark.asyncio
    async def test_call_with_pdf_ingester_call_pattern_succeeds(self):
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")

        result = await manager.process_page_vlm(
            img, prev_img=None, page_idx=0, session_dir=None
        )

        assert result == "# Heading\nBody text"
        manager._call_gemini_raw.assert_awaited_once()
```

- [ ] **Step 3: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_ocr_manager.py -v
```

Expected: `test_signature_matches_pdf_ingester_call_site` は現在のシグネチャが `["self", "pdf_path", "page_num"]` のため FAIL。`test_call_with_pdf_ingester_call_pattern_succeeds` は `TypeError: process_page_vlm() got an unexpected keyword argument 'prev_img'` で FAIL。

- [ ] **Step 4: pdf_path 引数版の重複定義を削除する**

`core/engine/p1_ingest/ocr_manager.py` を Read で開き、214行目 `async def process_page_vlm(self, pdf_path: str, page_num: int) -> str:` から始まるブロック（`_call_gemini_raw` 呼び出しで終わる、`_merge_images_horizontal` の直後・`_call_gemini_raw` メソッド定義の直前にある2つ目の定義）を Edit で削除する。以下の内容と一致することを確認してから削除する:

```python
    async def process_page_vlm(self, pdf_path: str, page_num: int) -> str:
        """（互換用）1ページを Gemini VLM OCR で処理する。"""
        async with self.semaphore:
            doc = fitz.open(pdf_path)
            try:
                page = doc[page_num]
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            finally:
                doc.close()

            # VLM 呼び出し
            return await self._call_gemini_raw([img, self.VLM_PROMPT])
```

削除後、157行目の画像引数版 `process_page_vlm` が唯一の定義として残ることを確認する。クラス内の他のメソッド（`_merge_images_horizontal`, `_call_gemini_raw`, `VLM_PROMPT` クラス変数等）は変更しない。

- [ ] **Step 5: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_ocr_manager.py -v
```

Expected: 2件とも PASS。

- [ ] **Step 6: 全体テストスイートを実行し既存テストに影響がないことを確認する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: 既存の全テストが PASS（この変更は `ocr_manager.py` 内部の重複削除のみで、他モジュールの契約は変えていない）。

- [ ] **Step 7: 単独コミット**

```bash
git add core/engine/p1_ingest/ocr_manager.py tests/unit/test_ocr_manager.py
git commit -m "$(cat <<'EOF'
fix: OCRManager.process_page_vlm の二重定義を解消しVLM OCR経路を復旧（I-15）

同一クラス内に process_page_vlm が二重定義され、Pythonの規則で後方定義
（pdf_path引数版）が常に生存していた。唯一の呼び出し元 pdf_ingester.py:67
は画像引数版のシグネチャで呼ぶため毎ページ TypeError → ネイティブテキスト
へ静かにフォールバックしていた（ファイル初出コミット a4c7fa4 から）。
pdf_path引数版を削除し、呼び出し元と一致する画像引数版を正とする。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: I-16 修理（下層）— `phase1_preprocessor.py` の `pdf_mode` 尊重と実ルート記録

**Files:**
- Modify: `core/models.py`（末尾に route 用ヘルパー3関数を追加）
- Modify: `core/config.py:92`（`SessionState` に `phase1_route` パスを追加）
- Modify: `core/phase1_preprocessor.py`（`force_vlm` 判定・実ルート記録の配線）
- Test: `tests/unit/test_phase1_route.py`（新規作成）

**Interfaces:**
- Consumes: なし
- Produces:
  - `core.models.phase1_route_path(phase1_state_path: str) -> str` — `phase1_preprocessor.json` のパスから同階層の `phase1_route.json` パスを導出する。Task 4 が使用。
  - `core.models.save_route_to_json(route: str, path: str) -> None`
  - `core.models.load_route_from_json(path: str) -> Optional[str]` — ファイル未存在時は `None`（テキストルート・旧セッション）。Task 4 が使用。
  - `phase1_preprocessor.py` は PDF ルートで `"docling"` / `"vlm"` / `"native_fallback"` のいずれかを `phase1_route_path(state_path)` に保存する。

- [ ] **Step 1: 失敗するテストを書く（models.py の route ヘルパー）**

`tests/unit/test_phase1_route.py` を新規作成:

```python
"""
Phase 1 の実ルート記録（I-16 対応）に関するユニットテスト。
"""

from core.models import phase1_route_path, save_route_to_json, load_route_from_json


class TestPhase1RoutePath:
    def test_derives_sibling_path(self, tmp_path):
        phase1_path = tmp_path / "session123" / "phase1_preprocessor.json"
        result = phase1_route_path(str(phase1_path))
        assert result == str(tmp_path / "session123" / "phase1_route.json")


class TestSaveLoadRoute:
    def test_round_trip(self, tmp_path):
        route_path = str(tmp_path / "phase1_route.json")
        save_route_to_json("docling", route_path)
        assert load_route_from_json(route_path) == "docling"

    def test_load_missing_file_returns_none(self, tmp_path):
        missing_path = str(tmp_path / "does_not_exist.json")
        assert load_route_from_json(missing_path) is None
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_phase1_route.py -v
```

Expected: `ImportError: cannot import name 'phase1_route_path' from 'core.models'` で FAIL。

- [ ] **Step 3: `core/models.py` にヘルパー関数を追加する**

`core/models.py` の `load_chunks_from_json`（143-146行目）の直後に追加:

```python
def phase1_route_path(phase1_state_path: str) -> str:
    """phase1_preprocessor.json のパスから同階層の phase1_route.json パスを導出する。"""
    from pathlib import Path
    return str(Path(phase1_state_path).parent / "phase1_route.json")


def save_route_to_json(route: str, path: str) -> None:
    """Phase 1 が実際に使用した入力ルート（docling/vlm/native_fallback）を記録する。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"route": route}, f, ensure_ascii=False)


def load_route_from_json(path: str) -> Optional[str]:
    """Phase 1 の実ルート記録を読み込む。ファイルが存在しない場合は None（テキストルート・旧セッション）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("route")
    except FileNotFoundError:
        return None
```

- [ ] **Step 4: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_phase1_route.py -v
```

Expected: 3件とも PASS。

- [ ] **Step 5: `SessionState` に `phase1_route` パスを追加する**

`core/config.py:92` の直後に1行追加:

```python
        self.phase1_preprocessor = self.session_dir / "phase1_preprocessor.json"
        self.phase1_route = self.session_dir / "phase1_route.json"
```

- [ ] **Step 6: `phase1_preprocessor.py` で `pdf_mode` を尊重し実ルートを記録する**

`core/phase1_preprocessor.py:12` の import に `phase1_route_path`, `save_route_to_json` を追加:

```python
from .models import RawChunk, save_chunks_to_json, phase1_route_path, save_route_to_json
```

141行目の Docling 分岐条件を変更（`force_vlm` 判定を追加）:

```python
    # Docling ルート: デジタルPDFかつ max_pages 未指定（部分処理モードでない）かつ VLM 強制指定でない
    force_vlm = pdf_mode == "full_vlm"
    if max_pages is None and not force_vlm and is_docling_viable(pdf_path):
        try:
            chunks = docling_pdf_to_chunks(pdf_path)
            if chunks:
                print_log(f"  [Phase 1 PDF] Docling ルート: {len(chunks)} チャンクを生成。")
                for c in chunks:
                    c.text = c.text.strip()
                if save_state:
                    save_chunks_to_json(chunks, str(state_path))
                    save_route_to_json("docling", phase1_route_path(str(state_path)))
                    print_log(f"  [Phase 1 PDF] State 保存: {state_path}")
                return chunks
            print_log("  [Phase 1 PDF] Docling が空を返したため VLM にフォールバック。")
        except Exception as e:
            print_log(f"  [Phase 1 PDF] Docling エラー ({e})、VLM にフォールバック。")
```

156-179行目の VLM/物理ルート部分を変更（`actual_route` を判定し保存する）:

```python
    # VLM ルート（フォールバック）
    print_log("  [Phase 1 PDF] VLM ルートで処理します。")
    elements = run_pdf_ingestion(
        pdf_path, api_key=api_key, state=state,
        pdf_mode=pdf_mode, model=model,
        is_book=is_book, heavy_ocr=heavy_ocr,
        max_pages=max_pages,
    )

    if elements and elements[0].get("role") == "vlm_page_source":
        chunks = Formatter.logical_split(elements)
        actual_route = "vlm"
        print_log(f"  [Phase 1 PDF] VLM ルート: {len(chunks)} チャンクを生成。")
    else:
        chunks = Formatter.smart_unwrap(elements)
        actual_route = "native_fallback"
        print_log(f"  [Phase 1 PDF] 物理ルート: {len(chunks)} チャンクを生成。")

    for c in chunks:
        c.text = c.text.strip()

    if save_state:
        save_chunks_to_json(chunks, str(state_path))
        save_route_to_json(actual_route, phase1_route_path(str(state_path)))
        print_log(f"  [Phase 1 PDF] State 保存: {state_path}")

    return chunks
```

- [ ] **Step 7: I-16 の下層修理を確認するテストを追加する**

`tests/unit/test_phase1_route.py` に追記:

```python
from unittest.mock import patch
from core.phase1_preprocessor import _run_phase1_pdf


class TestForceVlmRespectsPdfMode:
    def test_full_vlm_mode_skips_docling_even_if_viable(self, tmp_path):
        """I-16: pdf_mode='full_vlm' 指定時は is_docling_viable()=True でも Docling をスキップする。"""
        state_path = tmp_path / "phase1_preprocessor.json"
        fake_elements = [{"role": "vlm_page_source", "text": "# Chapter\nBody"}]

        with patch("core.phase1_preprocessor.is_docling_viable", return_value=True), \
             patch("core.phase1_preprocessor.docling_pdf_to_chunks") as mock_docling, \
             patch("core.phase1_preprocessor.run_pdf_ingestion", return_value=fake_elements):
            _run_phase1_pdf(
                "dummy.pdf", str(state_path),
                pdf_mode="full_vlm", save_state=True,
            )

        mock_docling.assert_not_called()
        from core.models import phase1_route_path, load_route_from_json
        assert load_route_from_json(phase1_route_path(str(state_path))) == "vlm"

    def test_hybrid_mode_uses_docling_when_viable(self, tmp_path):
        """既存動作の回帰確認: pdf_mode='hybrid'（既定）かつ Docling 可能ならDoclingルート。"""
        state_path = tmp_path / "phase1_preprocessor.json"
        from core.models import RawChunk

        with patch("core.phase1_preprocessor.is_docling_viable", return_value=True), \
             patch("core.phase1_preprocessor.docling_pdf_to_chunks",
                   return_value=[RawChunk(id="0", text="Title", role="h1", seq_index=0.0)]):
            _run_phase1_pdf(
                "dummy.pdf", str(state_path),
                pdf_mode="hybrid", save_state=True,
            )

        from core.models import phase1_route_path, load_route_from_json
        assert load_route_from_json(phase1_route_path(str(state_path))) == "docling"
```

- [ ] **Step 8: テストを実行し確認する**

```bash
python3 -m pytest tests/unit/test_phase1_route.py -v
```

Expected: 5件とも PASS。

- [ ] **Step 9: 全体テストスイートを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: 全合格（`pdf_mode` 既定値 `"hybrid"` かつ `force_vlm=False` は既存の分岐条件と等価なため、既存の Docling/VLM 経路テストに回帰なし）。

- [ ] **Step 10: コミット**

```bash
git add core/models.py core/config.py core/phase1_preprocessor.py tests/unit/test_phase1_route.py
git commit -m "$(cat <<'EOF'
fix: Phase1がpdf_modeを尊重し実ルートを記録するように修正（I-16下層）

phase1_preprocessor.py の Docling 分岐が pdf_mode を無視していたため、
full_vlm を明示指定してもデジタルPDFは常にDoclingルートに入っていた。
force_vlm 判定を追加して尊重するとともに、Phase1が実際に使ったルート
（docling/vlm/native_fallback）を phase1_route.json に記録する。
Phase3側の実ルート参照（次コミット）の前提となる。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: I-16 修理（上層）— 書籍単位ルーティング規則（①〜④）の実装

**Files:**
- Modify: `core/book_manager.py`（`_decide_book_pdf_mode` 追加・`run()` の配線変更）
- Modify: `main.py:184`（`pdf_mode` の `None` コレースを廃止）
- Modify: `server.py:143`（同上）
- Test: `tests/unit/test_book_manager.py`（新規クラス2つ追加）

**Interfaces:**
- Consumes: `core.engine.p1_ingest.spread_splitter.is_spread_pdf`, `core.engine.p1_ingest.docling_ingester.is_docling_viable`（既存）
- Produces: `core.book_manager._decide_book_pdf_mode(explicit_pdf_mode: Optional[str], is_spread: bool, is_docling_ok: bool) -> tuple[str, str]` — 戻り値は `(pdf_mode, reason)`。`BookManager.run()` はこの戻り値を各章の `run_pipeline(..., pdf_mode=...)` に渡す（Task 2 の `force_vlm` 判定と組み合わさって書籍単位ルーティングが機能する）。

- [ ] **Step 1: 失敗するテストを書く（純粋関数 `_decide_book_pdf_mode`）**

`tests/unit/test_book_manager.py` の末尾に追記:

```python
# ============================================================
# 書籍単位ルーティング規則（①〜④）
# ============================================================

class TestDecideBookPdfMode:
    def test_explicit_pdf_mode_takes_priority(self):
        """規則①: ユーザー明示指定は他の判定より優先される。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode("full_vlm", is_spread=False, is_docling_ok=True)
        assert (mode, reason) == ("full_vlm", "explicit_pdf_mode")

    def test_spread_pdf_forces_vlm_even_if_docling_viable(self):
        """規則②>③: 見開きスキャンはDocling可能でもVLMを優先する。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode(None, is_spread=True, is_docling_ok=True)
        assert (mode, reason) == ("full_vlm", "spread_pdf")

    def test_docling_viable_non_spread_uses_hybrid(self):
        """規則③: 見開きでなくDocling可能ならDoclingルート（hybrid=Docling優先）。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode(None, is_spread=False, is_docling_ok=True)
        assert (mode, reason) == ("hybrid", "docling_viable")

    def test_non_viable_non_spread_falls_back_to_vlm(self):
        """規則④: 見開きでもDocling可能でもない（劣化スキャン等）ならVLM。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode(None, is_spread=False, is_docling_ok=False)
        assert (mode, reason) == ("full_vlm", "docling_not_viable")
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_book_manager.py::TestDecideBookPdfMode -v
```

Expected: `ImportError: cannot import name '_decide_book_pdf_mode'` で4件とも FAIL。

- [ ] **Step 3: `_decide_book_pdf_mode` を実装する**

`core/book_manager.py` の `RESUME_MODEL_SAFE_CHAR_LIMIT`（20行目）の直後、`class BookManager:` の直前に追加:

```python
def _decide_book_pdf_mode(
    explicit_pdf_mode: Optional[str], is_spread: bool, is_docling_ok: bool
) -> tuple[str, str]:
    """書籍単位のルーティング規則（①〜④）を判定する。

    ① ユーザーが pdf_mode を明示指定 → それを尊重
    ② 見開きスキャン → VLM ルート（Docling の読み順が未検証のため保守的に優先）
    ③ Docling 可能（デジタルPDF）→ Docling ルート
    ④ それ以外（スキャン等）→ VLM ルート

    戻り値は (pdf_mode, reason)。reason は routing_decision.json とログに記録する。
    """
    if explicit_pdf_mode is not None:
        return explicit_pdf_mode, "explicit_pdf_mode"
    if is_spread:
        return "full_vlm", "spread_pdf"
    if is_docling_ok:
        return "hybrid", "docling_viable"
    return "full_vlm", "docling_not_viable"
```

- [ ] **Step 4: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_book_manager.py::TestDecideBookPdfMode -v
```

Expected: 4件とも PASS。

- [ ] **Step 5: `BookManager.run()` に配線する（失敗する統合テストを先に書く）**

`tests/unit/test_book_manager.py` に追記（`TestGlobalResumeHandoff` クラスの直後）:

```python
# ============================================================
# 書籍単位ルーティングの run() 配線（I-16 上層）
# ============================================================

def _run_with_routing_mocks(manager, is_spread, is_docling_ok, **run_kwargs):
    """is_spread_pdf / is_docling_viable をモックして manager.run() を実行し、
    run_pipeline に渡された kwargs を captured で返す。"""
    ch_pdf = Path(manager.input_path).parent / "ch1.pdf"
    ch_pdf.write_bytes(b"%PDF-1.4 dummy")
    splitter = MagicMock()
    splitter.split.return_value = [{"title": "Ch1", "path": str(ch_pdf), "role": "chapter"}]

    captured = {}

    def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return []

    with patch("core.book_manager.PDFSplitter", return_value=splitter), \
         patch("core.book_manager.apply_tier_settings"), \
         patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
         patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=is_spread), \
         patch("core.engine.p1_ingest.spread_splitter.split_spread_pdf", return_value=str(ch_pdf)), \
         patch("core.engine.p1_ingest.docling_ingester.is_docling_viable", return_value=is_docling_ok), \
         patch("core.pipeline.run_pipeline", side_effect=fake_run_pipeline):
        try:
            manager.run(max_chapters=1, **run_kwargs)
        except Exception:
            pass  # 統合フェーズ以降の失敗は本テストの関心外
    return captured


class TestBookLevelRouting:
    def _make_ready_manager(self, tmp_path, title, fp):
        manager = make_manager(tmp_path)
        manager.book_title = title
        manager.fingerprint = fp
        manager.session_dir = tmp_path / "book_sessions" / f"{title}_{fp}"
        manager.session_dir.mkdir(parents=True, exist_ok=True)
        (manager.session_dir / "global_context.json").write_text(
            json.dumps({"resume": "R", "glossary": [], "book_title": title}),
            encoding="utf-8",
        )
        return manager

    def test_explicit_pdf_mode_is_respected_not_discarded(self, tmp_path):
        """I-16: 明示指定された pdf_mode が pop されて捨てられず章パイプラインに渡る。"""
        manager = self._make_ready_manager(tmp_path, "routebook1", "fp1")
        captured = _run_with_routing_mocks(
            manager, is_spread=False, is_docling_ok=True, pdf_mode="full_vlm"
        )
        assert captured.get("pdf_mode") == "full_vlm"

    def test_default_docling_viable_uses_hybrid_not_hardcoded_full_vlm(self, tmp_path):
        """I-16: pdf_mode 未指定・非見開き・Docling可能な書籍は hybrid（Doclingルート）になる。"""
        manager = self._make_ready_manager(tmp_path, "routebook2", "fp2")
        captured = _run_with_routing_mocks(manager, is_spread=False, is_docling_ok=True)
        assert captured.get("pdf_mode") == "hybrid"

        routing_file = manager.session_dir / "routing_decision.json"
        assert routing_file.exists()
        data = json.loads(routing_file.read_text(encoding="utf-8"))
        assert data["pdf_mode"] == "hybrid"
        assert data["reason"] == "docling_viable"

    def test_spread_pdf_forces_full_vlm_even_if_docling_viable(self, tmp_path):
        """I-16: 見開きスキャンはDocling可能でもVLMルートを優先する（規則②>③）。"""
        manager = self._make_ready_manager(tmp_path, "routebook3", "fp3")
        captured = _run_with_routing_mocks(manager, is_spread=True, is_docling_ok=True)
        assert captured.get("pdf_mode") == "full_vlm"
```

- [ ] **Step 6: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_book_manager.py::TestBookLevelRouting -v
```

Expected: 3件とも FAIL（現状 `pdf_mode` は常に `"full_vlm"` ハードコードのため、`test_default_docling_viable_uses_hybrid_not_hardcoded_full_vlm` の `"hybrid"` 期待値と `routing_decision.json` 未存在で失敗する）。

- [ ] **Step 7: `book_manager.py` の `run()` に配線する**

`core/book_manager.py` の154-164行目（「1. PDF 分割」セクション）を変更:

```python
        # 1. PDF 分割
        # 見開きスキャンPDFは分割してから章分割・処理に渡す
        from .engine.p1_ingest.spread_splitter import is_spread_pdf, split_spread_pdf
        from .engine.p1_ingest.docling_ingester import is_docling_viable

        pdf_for_splitting = str(self.input_path)
        is_spread = is_spread_pdf(pdf_for_splitting)
        if is_spread:
            print_log("  [BookManager] 見開きスキャンPDFを検出。単ページに分割します...")
            pdf_for_splitting = split_spread_pdf(pdf_for_splitting)

        # 書籍単位のルーティング決定（①〜④）: ユーザー明示指定 pop はここで一度だけ行う
        explicit_pdf_mode = pipeline_kwargs.pop("pdf_mode", None)
        is_docling_ok = is_docling_viable(str(self.input_path))
        book_pdf_mode, routing_reason = _decide_book_pdf_mode(explicit_pdf_mode, is_spread, is_docling_ok)
        print_log(
            f"  [BookManager] 入力ルーティング決定: pdf_mode={book_pdf_mode} "
            f"(理由: {routing_reason}, spread={is_spread}, docling_viable={is_docling_ok}, "
            f"explicit={explicit_pdf_mode})"
        )
        routing_path = self.session_dir / "routing_decision.json"
        routing_path.write_text(
            json.dumps({
                "pdf_mode": book_pdf_mode,
                "reason": routing_reason,
                "is_spread": is_spread,
                "is_docling_viable": is_docling_ok,
                "explicit_pdf_mode": explicit_pdf_mode,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        model_to_use = self.model or get_default_model("default")
        splitter = PDFSplitter(api_key=self.api_key, model=model_to_use)
        chapters = splitter.split(pdf_for_splitting, self.session_dir / "chapters")
```

181-186行目の `explicit_keys` から `"pdf_mode"` を除去する（既に上で pop 済みのため重複を避ける）:

```python
        explicit_keys = [
            "glossary_path", "thinking_level", "tier",
            "heavy_ocr", "max_pages", "api_key", "model", "resume_from"
        ]
        for key in explicit_keys:
            pipeline_kwargs.pop(key, None)
```

225行目の `pdf_mode="full_vlm",` を変更:

```python
                    pdf_mode=book_pdf_mode,
```

- [ ] **Step 8: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_book_manager.py -v
```

Expected: 全件 PASS（既存の `TestChapterResume`, `TestChapterFailure`, `TestGlobalResumeHandoff`, `TestBookSessionCleanup` を含む）。

- [ ] **Step 9: CLI・Web の `None` コレースを廃止する**

`main.py:184` を変更:

```python
                pdf_mode=args.pdf_mode,
```

（変更前: `pdf_mode=args.pdf_mode if args.pdf_mode else "hybrid",`。`args.pdf_mode` は `--pdf-mode` 未指定時 `None` のままなので、`BookManager._decide_book_pdf_mode` が規則②〜④で自動判定できるようになる。）

`server.py:143` を変更:

```python
                    pdf_mode=None,
```

（変更前: `pdf_mode="hybrid",`。Web UI には書籍モード用の `pdf_mode` 選択 UI がなく、常に自動判定に委ねるのが正しい。）

- [ ] **Step 10: 全体テストスイートを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: 全合格。

- [ ] **Step 11: コミット**

```bash
git add core/book_manager.py main.py server.py tests/unit/test_book_manager.py
git commit -m "$(cat <<'EOF'
fix: 書籍単位ルーティング規則(①〜④)を実装しpdf_mode破棄を解消（I-16上層）

BookManager.run() は pipeline_kwargs から pdf_mode を pop するだけで値を
一切参照せず、章処理は常に pdf_mode="full_vlm" 固定だった。書籍単位で
1回だけ判定する _decide_book_pdf_mode を追加し、①明示指定 ②見開きスキャン
=VLM ③Docling可能=Docling ④それ以外=VLM の優先順位で決定・記録する。
main.py/server.py も pdf_mode 未指定時の "hybrid" コレースを廃止し、
「既定のまま」と「明示指定」を区別できるようにした。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Phase 3 の Route C 発火条件を実ルート参照に変更

**Files:**
- Modify: `core/phase3_structure.py:15,50`
- Test: `tests/unit/test_phase3_structure.py`（新規クラス追加）

**Interfaces:**
- Consumes: `core.models.load_route_from_json`, `core.models.phase1_route_path`（Task 2 で追加済み）
- Produces: `run_phase3()` の Route C 分岐は `pdf_mode` 指定値でなく `load_route_from_json(phase1_route_path(phase1_state_path))` の実ルートを見る。`pdf_mode` パラメータ自体は Task 5 で使わなくなるが、シグネチャ互換のため残す（呼び出し元 `pipeline.py` の変更は不要）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_phase3_structure.py` の末尾に追記:

```python
# ============================================================
# I-16: Route C 発火条件の実ルート参照化
# ============================================================

class TestRunPhase3ActualRouteDispatch:
    def test_vlm_route_triggers_route_c_even_with_hybrid_pdf_mode(self, tmp_path):
        """実ルート=vlm が記録されていれば、pdf_mode='hybrid' でも Route C（Markdown構造化）が発火する。"""
        from core.phase3_structure import run_phase3
        from core.models import RawChunk, save_chunks_to_json, save_route_to_json, phase1_route_path

        phase1_path = tmp_path / "phase1_preprocessor.json"
        chunks = [
            RawChunk(id="0", text="# Chapter One", role="p", seq_index=0.0),
            RawChunk(id="1", text="Body text.", role="p", seq_index=1.0),
        ]
        save_chunks_to_json(chunks, str(phase1_path))
        save_route_to_json("vlm", phase1_route_path(str(phase1_path)))

        tree, sections = run_phase3(
            phase1_state_path=str(phase1_path),
            phase2_state_path=str(tmp_path / "phase2_meta.json"),
            structure_state_path=str(tmp_path / "phase3_structure.json"),
            sections_state_path=str(tmp_path / "phase3_sections.json"),
            is_book=True,
            input_path=None,
            api_key=None,
            pdf_mode="hybrid",
        )

        assert len(tree) == 1
        assert tree[0].text == "Chapter One"

    def test_docling_route_does_not_trigger_route_c(self, tmp_path):
        """実ルート=docling では # Markdown が無いため Route C は発火せず後続分岐へ進む。"""
        from core.phase3_structure import run_phase3
        from core.models import RawChunk, save_chunks_to_json, save_route_to_json, phase1_route_path

        phase1_path = tmp_path / "phase1_preprocessor.json"
        chunks = [RawChunk(id="0", text="Plain body text.", role="p", seq_index=0.0)]
        save_chunks_to_json(chunks, str(phase1_path))
        save_route_to_json("docling", phase1_route_path(str(phase1_path)))

        # Book Mode かつ role 見出しが無いため Task 5 の Route D も発火せず、
        # ChapterParser/TOC フォールバック（input_path 必須）に進む。
        # input_path=None なので例外になることをもって「Route C を通らなかった」ことを確認する。
        with pytest.raises(Exception):
            run_phase3(
                phase1_state_path=str(phase1_path),
                phase2_state_path=str(tmp_path / "phase2_meta.json"),
                structure_state_path=str(tmp_path / "phase3_structure.json"),
                sections_state_path=str(tmp_path / "phase3_sections.json"),
                is_book=True,
                input_path=None,
                api_key=None,
                pdf_mode="hybrid",
            )
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_phase3_structure.py::TestRunPhase3ActualRouteDispatch -v
```

Expected: `test_vlm_route_triggers_route_c_even_with_hybrid_pdf_mode` は FAIL（現状 `pdf_mode == "full_vlm"` のみを見るため `pdf_mode="hybrid"` では Route C に入らず、`tree` が空か例外になる）。`test_docling_route_does_not_trigger_route_c` は現状の分岐でもたまたま同じ結果になり得るため、Step 1 時点で PASS していても構わない（後続変更で壊れないことの回帰テストとして機能する）。

- [ ] **Step 3: `phase3_structure.py` を変更する**

`core/phase3_structure.py:15` の import に `load_route_from_json`, `phase1_route_path` を追加:

```python
from .models import TreeNode, load_chunks_from_json, load_route_from_json, phase1_route_path, save_tree_to_json
```

49-51行目を変更:

```python
    # --- Route C: VLM Markdown 構造化（Phase1 の実ルートが vlm の場合のみ）---
    actual_route = load_route_from_json(phase1_route_path(str(phase1_state_path)))
    if actual_route == "vlm":
        has_markdown_headers = any(re.match(r'^#\s+', c.text.strip()) for c in chunks)
```

70-71行目のフォールバックログを変更:

```python
        else:
            print_log("  [Phase 3] 実ルート=vlm ですが Markdown 見出しが未検出です。標準構造化へフォールバックします。")
```

- [ ] **Step 4: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_phase3_structure.py -v
```

Expected: 全件 PASS（既存の `TestStructureNodesByHeadings` 等も含め回帰なし）。

- [ ] **Step 5: 全体テストスイートを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: 全合格。

- [ ] **Step 6: コミット**

```bash
git add core/phase3_structure.py tests/unit/test_phase3_structure.py
git commit -m "$(cat <<'EOF'
fix: Phase3のRoute C発火条件をpdf_mode指定値から実ルート参照に変更（I-16）

phase3_structure.py:50 は pdf_mode=="full_vlm" という指定値のみを見ており、
Phase1が実際にDoclingへフォールバックした場合でもVLM前提のRoute Cが
（見出し検出できず）空振りしていた。Phase1が記録した実ルート
（phase1_route.json）を参照するように変更し、指定値と実績の前提不一致を解消。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Docling role 見出しの書籍 Phase 3 配線（新設 `structure_nodes_by_role`）

**Files:**
- Modify: `core/engine/p3_structure/tree_builder.py`（`structure_nodes_by_role` 新設）
- Modify: `core/phase3_structure.py`（Route D 分岐の追加）
- Test: `tests/unit/test_phase3_structure.py`（新規クラス追加）

**Interfaces:**
- Consumes: `core.engine.p3_structure.heading_matcher.normalize_heading`（既存）
- Produces: `core.engine.p3_structure.tree_builder.structure_nodes_by_role(chunks: List[RawChunk], toc_list: List[str] | None = None) -> tuple[List[TreeNode], Dict[str, List[dict]]]` — Docling が付与した `role="h1"`（章）/`role="h2"`（節）/`role="p"`（本文）を使ってツリーを構築する。書籍モード専用（`structure_nodes_by_markdown` の role 版、`is_book` 引数は取らず常に book 相当の `role="h3"` を割り当てる）。

- [ ] **Step 1: 失敗するテストを書く（`structure_nodes_by_role` 単体）**

`tests/unit/test_phase3_structure.py` の末尾に追記:

```python
# ============================================================
# structure_nodes_by_role（Docling role 見出しの書籍構造化）
# ============================================================

class TestStructureNodesByRole:
    def _chunk(self, id, text, role, seq):
        from core.models import RawChunk
        return RawChunk(id=id, text=text, role=role, seq_index=seq)

    def test_h1_becomes_chapter_h2_becomes_section(self):
        from core.engine.p3_structure.tree_builder import structure_nodes_by_role
        chunks = [
            self._chunk("0", "Chapter One", "h1", 0.0),
            self._chunk("1", "Section A", "h2", 1.0),
            self._chunk("2", "Body text.", "p", 2.0),
        ]
        tree, sections = structure_nodes_by_role(chunks)

        assert len(tree) == 1
        assert tree[0].text == "Chapter One"
        assert tree[0].role == "h3"
        assert tree[0].children[0].text == "Section A"
        assert tree[0].children[0].children[0].text == "Body text."

    def test_toc_demotes_h1_not_in_toc(self):
        """TOCにない h1 見出しは h3（節相当）へ降格し、直前の章の子ではなくトップレベルに置かれる。"""
        from core.engine.p3_structure.tree_builder import structure_nodes_by_role
        chunks = [self._chunk("0", "Fake Chapter", "h1", 0.0)]
        tree, sections = structure_nodes_by_role(chunks, toc_list=["Real Chapter"])

        assert len(tree) == 1
        assert tree[0].text == "Fake Chapter"
        assert tree[0].role == "h3"

    def test_body_before_any_heading_creates_unlabeled_section(self):
        from core.engine.p3_structure.tree_builder import structure_nodes_by_role
        chunks = [self._chunk("0", "Orphan text", "p", 0.0)]
        tree, sections = structure_nodes_by_role(chunks)

        assert len(tree) == 1
        assert tree[0].text == "[Unlabeled Section]"
        assert tree[0].children[0].text == "Orphan text"

    def test_sections_dict_keys_match_tree(self):
        from core.engine.p3_structure.tree_builder import structure_nodes_by_role
        chunks = [
            self._chunk("0", "Chapter One", "h1", 0.0),
            self._chunk("1", "Body.", "p", 1.0),
        ]
        tree, sections = structure_nodes_by_role(chunks)

        section_key = f"{tree[0].id}|Chapter One"
        assert section_key in sections
        assert sections[section_key] == [{"id": "1", "text": "Body.", "role": "p"}]
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_phase3_structure.py::TestStructureNodesByRole -v
```

Expected: `ImportError: cannot import name 'structure_nodes_by_role'` で4件とも FAIL。

- [ ] **Step 3: `structure_nodes_by_role` を実装する**

`core/engine/p3_structure/tree_builder.py` の末尾（`structure_nodes_by_markdown` の後）に追加:

```python
def structure_nodes_by_role(
    chunks: List[RawChunk],
    toc_list: List[str] | None = None,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    書籍モード専用: Docling が付与した role（h1=章, h2=節, p=本文）を使って
    TreeNode の親子構造を構築する。structure_nodes_by_markdown の role 版。

    TOC（目次）リストが存在する場合、role="h1" の見出しが TOC に含まれるか検証し、
    含まれない場合は自動的に節（h3）へ降格（Demote）させる。
    """
    import difflib

    tree: List[TreeNode] = []
    sections_dict: Dict[str, List[dict]] = {}

    current_h2: Optional[TreeNode] = None
    current_h3: Optional[TreeNode] = None
    unlabeled_key = "unlabeled_0|[Unlabeled Section]"
    current_section_key: str = unlabeled_key

    norm_toc = [normalize_heading(t) for t in (toc_list or [])]

    def is_valid_chapter(title: str) -> bool:
        if not norm_toc:
            return True  # TOCがない場合はDoclingのroleを信じる
        norm_title = normalize_heading(title)
        if norm_title in norm_toc:
            return True
        for t in norm_toc:
            if norm_title in t or t in norm_title:
                return True
            ratio = difflib.SequenceMatcher(None, norm_title, t).ratio()
            if ratio > 0.85:
                return True
        return False

    for chunk in chunks:
        text = chunk.text.strip()
        if not text:
            continue

        if chunk.role == "h1":
            if not is_valid_chapter(text):
                node = TreeNode(id=chunk.id, text=text, role="h3", seq_index=chunk.seq_index, children=[])
                if current_h2 is not None:
                    current_h2.children.append(node)
                else:
                    tree.append(node)
                sections_dict.setdefault(current_section_key, []).append(
                    {"id": node.id, "text": node.text, "role": "h3"}
                )
                current_h3 = node
                continue

            node = TreeNode(id=chunk.id, text=text, role="h3", seq_index=chunk.seq_index, children=[])
            tree.append(node)
            current_section_key = f"{chunk.id}|{text}"
            sections_dict[current_section_key] = []
            current_h2 = node
            current_h3 = None

        elif chunk.role == "h2":
            node = TreeNode(id=chunk.id, text=text, role="h3", seq_index=chunk.seq_index, children=[])
            if current_h2 is not None:
                current_h2.children.append(node)
            else:
                tree.append(node)
            sections_dict.setdefault(current_section_key, []).append(
                {"id": node.id, "text": node.text, "role": "h3"}
            )
            current_h3 = node

        else:
            node = TreeNode(id=chunk.id, text=text, role="p", seq_index=chunk.seq_index)
            if current_h2 is None:
                current_h2 = TreeNode(
                    id="unlabeled_0", text="[Unlabeled Section]",
                    role="h3", seq_index=chunk.seq_index, children=[],
                )
                tree.append(current_h2)
                current_section_key = unlabeled_key
                sections_dict[current_section_key] = []
            parent = current_h3 or current_h2
            parent.children.append(node)
            sections_dict.setdefault(current_section_key, []).append(
                {"id": node.id, "text": node.text, "role": "p"}
            )

    return tree, sections_dict
```

- [ ] **Step 4: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_phase3_structure.py::TestStructureNodesByRole -v
```

Expected: 4件とも PASS。

- [ ] **Step 5: Route D の統合テストを書く（`run_phase3` への配線）**

`tests/unit/test_phase3_structure.py` の `TestRunPhase3ActualRouteDispatch` クラスに追記:

```python
    def test_docling_route_with_role_headings_uses_route_d(self, tmp_path):
        """実ルート=docling かつ role 見出しがあれば Route D（role構造化）が発火し、
        ChapterParser（PDF再解析、input_path 必須）を経由しない。"""
        from core.phase3_structure import run_phase3
        from core.models import RawChunk, save_chunks_to_json, save_route_to_json, phase1_route_path

        phase1_path = tmp_path / "phase1_preprocessor.json"
        chunks = [
            RawChunk(id="0", text="Chapter One", role="h1", seq_index=0.0),
            RawChunk(id="1", text="Body text.", role="p", seq_index=1.0),
        ]
        save_chunks_to_json(chunks, str(phase1_path))
        save_route_to_json("docling", phase1_route_path(str(phase1_path)))

        tree, sections = run_phase3(
            phase1_state_path=str(phase1_path),
            phase2_state_path=str(tmp_path / "phase2_meta.json"),
            structure_state_path=str(tmp_path / "phase3_structure.json"),
            sections_state_path=str(tmp_path / "phase3_sections.json"),
            is_book=True,
            input_path=None,  # ChapterParser経路に落ちたら input_path 必須でエラーになるはず
            api_key=None,
            pdf_mode="hybrid",
        )

        assert len(tree) == 1
        assert tree[0].text == "Chapter One"
        assert tree[0].children[0].text == "Body text."
```

このテストを追加した時点で `test_docling_route_does_not_trigger_route_c`（Task 4）は矛盾する（role見出しがある場合はRoute Dへ進むため、あちらのテストは role 見出しの無いケースに限定されている）。両テストの chunks 定義が異なる（本テストは `role="h1"`、Task 4 のテストは `role="p"` のみ）ことを確認する。

- [ ] **Step 6: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_phase3_structure.py::TestRunPhase3ActualRouteDispatch::test_docling_route_with_role_headings_uses_route_d -v
```

Expected: FAIL（Route D 未実装のため `input_path=None` で ChapterParser 分岐に落ちて例外、または空ツリーになる）。

- [ ] **Step 7: `phase3_structure.py` に Route D を追加する**

`core/phase3_structure.py:17-20` の import に `structure_nodes_by_role` を追加:

```python
from .engine.p3_structure.tree_builder import (
    build_tree,
    structure_nodes_by_markdown,
    structure_nodes_by_role,
)
```

Route C ブロック（Task 4 で変更した箇所、71行目付近）の直後・73行目 `anchors = {"metadata_ids": []}` の直前に新設:

```python
    # --- Route D: Docling role 見出し構造化（書籍モードかつ実ルートが docling の場合）---
    if is_book and actual_route == "docling" and chunks:
        role_headings_present = any(c.role in ("h1", "h2") for c in chunks)
        if role_headings_present:
            print_log("  [Phase 3] Route D: Docling role 見出し構造化 を実行します")
            toc_list = []
            toc_path = Path(structure_state_path).parent / "phase3_toc.json"
            if toc_path.exists():
                with open(toc_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    toc_list = [entry["title"] for entry in cached_data.get("toc", [])]
            else:
                toc_list = extract_toc_from_chunks(chunks, api_key=api_key, model=model)

            tree, sections_dict = structure_nodes_by_role(chunks, toc_list=toc_list)
            if save_state:
                save_tree_to_json(tree, str(structure_state_path))
                with open(sections_state_path, "w", encoding="utf-8") as f:
                    json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            return tree, sections_dict
        else:
            print_log("  [Phase 3] 実ルート=docling ですが role 見出しが未検出です。ChapterParser/TOCフォールバックへ進みます。")
```

- [ ] **Step 8: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_phase3_structure.py -v
```

Expected: 全件 PASS（Task 4 の `test_docling_route_does_not_trigger_route_c` も含め、role 見出しの有無で Route D の発火/フォールバックが正しく分岐する）。

- [ ] **Step 9: 全体テストスイートを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: 全合格。

- [ ] **Step 10: コミット**

```bash
git add core/engine/p3_structure/tree_builder.py core/phase3_structure.py tests/unit/test_phase3_structure.py
git commit -m "$(cat <<'EOF'
feat: Doclingのrole見出しを書籍Phase3に配線するRoute Dを新設

これまで書籍モードのPhase3はDoclingが成功していてもrole見出しを一切
使わず、ChapterParserがPDFをフォント統計から再解析していた（Docling出力
は実質破棄されていた）。structure_nodes_by_markdown のrole版として
structure_nodes_by_role を新設し、実ルート=docling かつ role見出しが
存在する場合はこれを使う。role見出しが乏しい場合は従来のChapterParser/
TOCフォールバックを維持する（Spec B 設計どおり）。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: ドキュメント正常化

**Files:**
- Modify: `/Users/shufujita/Code/p2workflowy/CLAUDE.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/management/troubleshooting_log.md`
- Modify: `docs/management/requirements_log.md`

**Interfaces:**
- Consumes: Task 1〜5 の実装内容
- Produces: なし（ドキュメントのみ、自動テストなし）

- [ ] **Step 1: `CLAUDE.md` の設計原則を更新する**

以下の一文（現在の暫定注記を含む段落）を置き換える:

old:
```
- **入力ルーティングと判断優先順位**: デジタル PDF（`is_docling_viable()`=True）は Docling ルートが正式経路で、VLM OCR はスキャン PDF（見開き含む）用。VLM 経路内の判断優先順位は `VLM の論理役割判断 > 物理証拠（フォント・座標） > 幾何的ヒント` で、OCR 補正は「VLM が特定した位置の Native テキストで肉付けする」方針を守る。※2026-07-10 時点、VLM 経路は機能停止中（`troubleshooting_log.md` I-15/I-16）。修理と経路の公式化は Spec B（`docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md`）で対応予定。
```

new:
```
- **入力ルーティングと判断優先順位**: 書籍は書籍単位で1回だけ判定する（①ユーザーが `pdf_mode` を明示指定→それを尊重、②見開きスキャン→VLM、③デジタルPDF（`is_docling_viable()`=True）→Docling、④それ以外→VLM）。論文（非書籍）PDF は Phase 1（`phase1_preprocessor.py`）が同じ優先順位で1文書ごとに判定する。Phase 1 が実際に使ったルート（`docling`/`vlm`/`native_fallback`）は `phase1_route.json` に記録され、Phase 3 は `pdf_mode` 指定値ではなくこの実ルートを見て構造化方式（VLM Markdown 正規表現 / Docling role 構造化 / ChapterParser・TOC フォールバック）を切り替える。VLM 経路内の判断優先順位は `VLM の論理役割判断 > 物理証拠（フォント・座標） > 幾何的ヒント` で、OCR 補正は「VLM が特定した位置の Native テキストで肉付けする」方針を守る。（Spec B 実装済み: `docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md`）
```

- [ ] **Step 2: `docs/ARCHITECTURE.md` §3 を更新する**

「入力ルーティングの自動判定」の段落の直後に新しい段落を追加する（見出し文言はファイルの実際の記述に合わせて調整可、内容は以下を含める）:

```
書籍モードでは `BookManager` が書籍のオリジナル PDF に対して1回だけルーティングを判定し（①明示指定 ②見開きスキャン=VLM ③Docling可能=Docling ④それ以外=VLM）、判定結果を book session の `routing_decision.json` に記録します。Phase 1 が実際に使用したルート（`docling`/`vlm`/`native_fallback`）は `phase1_route.json` に記録され、Phase 3 は `pdf_mode` の指定値ではなくこの実ルートを参照して構造化方式を切り替えます。デジタル書籍（Docling ルート）では Docling が付与した role 見出し（h1=章, h2=節）を `structure_nodes_by_role` で直接構造化に使い、role 見出しが乏しい場合のみ `ChapterParser`/TOC 抽出のフォールバックに委ねます。
```

- [ ] **Step 3: `troubleshooting_log.md` の I-15/I-16 を対応済みとして更新する**

`docs/management/troubleshooting_log.md:199`（I-15 の「対策方針」行）の直後に追加:

```
- **対応済み（2026-07-18, Spec B 実装）**: `ocr_manager.py` の pdf_path 引数版（旧 :214）を削除し、呼び出し元 `pdf_ingester.py:67` と一致する画像引数版（旧 :157）を正とした。
```

`docs/management/troubleshooting_log.md:205`（I-16 の「対策方針」行）の直後に追加:

```
- **対応済み（2026-07-18, Spec B 実装）**: `phase1_preprocessor.py` が `pdf_mode` を尊重するよう修正し、実ルートを `phase1_route.json` に記録。`BookManager` に書籍単位ルーティング（①〜④）を実装し `pdf_mode` の pop・破棄を解消。`phase3_structure.py` の Route C 発火条件を実ルート参照に変更し、Docling ルート×書籍モードでは新設の `structure_nodes_by_role` が role 見出しを直接構造化する（従来の ChapterParser/TOC フォールバックは role 見出しが乏しい場合のみ使用）。
```

- [ ] **Step 4: `requirements_log.md` に実装完了エントリを追加する**

`docs/management/requirements_log.md` の末尾に追記（既存エントリのフォーマットに合わせる）:

```
## 2026-07-18: Spec B（書籍モード Phase 1 入力ルーティング修理・公式化）実装完了

`docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md` を実装。I-15（VLM二重定義バグ）・I-16（pdf_mode無視バグ）を修理し、書籍単位ルーティング規則（①明示指定 ②見開き=VLM ③Docling可能=Docling ④それ以外=VLM）と実ルート記録（`phase1_route.json`）を公式化。Docling の role 見出しを書籍 Phase 3 に配線する `structure_nodes_by_role` を新設し、これまで実質破棄されていた Docling 出力を書籍モードの本文構造化に直接使うようにした。CLAUDE.md・ARCHITECTURE.md の設計原則も実態に合わせて更新。実 PDF 検証は corfrapdf.pdf（見開き×Docling可能の優先順位検証）→ Naven.pdf（見開きでない純スキャン、VLM初回稼働確認）→ relations/AL/NST（既存回帰確認）の順で実施予定（Task 7〜9、golden-verification 実行記録を参照）。
```

- [ ] **Step 5: コミット**

```bash
git add CLAUDE.md docs/ARCHITECTURE.md docs/management/troubleshooting_log.md docs/management/requirements_log.md
git commit -m "$(cat <<'EOF'
docs: Spec B実装完了に合わせてCLAUDE.md・ARCHITECTURE.md・管理ログを更新

VLM経路機能停止中の暫定注記を、書籍単位ルーティング規則・実ルート記録・
Docling role構造化を含む正式記述に差し替え。troubleshooting_log.md の
I-15/I-16を対応済みとして記録し、requirements_log.mdにSpec B実装完了の
経緯を追記した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: VLM 実動作確認（corfrapdf.pdf、見開き×Docling可能の優先順位検証）

**Files:** なし（コード変更なし、実 PDF での動作検証）

**Interfaces:**
- Consumes: Task 1〜5 の実装
- Produces: `state/book_sessions/corfrapdf_*/` 配下の実処理結果、`data/input/Booksample/corfra/corfrapdf_p2.md`/`.txt`（再生成）

### 本タスクを最初に行う理由

事前に両書籍のページ形状を実測した結果:

- **corfrapdf.pdf**: 106ページ・62MB。ページ aspect比 1.28（横長＝見開きスキャン相当、`SPREAD_ASPECT_THRESHOLD=1.1` を超える）かつ埋め込みテキスト層あり（page5で6,286字抽出）。つまり `is_spread_pdf()=True` かつ `is_docling_viable()=True` の両方が成立する可能性が高い、**規則②（見開き=VLM）が規則③（Docling可能=Docling）より優先されるべき**という spec が名指しで注意しているケース（design doc L64「corfra/pse が該当」）そのもの。加えて既存出力（`corfrapdf_p2.md`, 2026-05-09生成）という比較材料がある。
- **Naven.pdf**: 380ページ・94MB。ページ aspect比 0.60（縦長＝見開きでない）かつ埋め込みテキスト層なし（page5でテキスト抽出0字＝純スキャン）。規則④（それ以外=VLM）の素直なケースで、優先順位の検証にはならない。規模も大きくコストが高い。

したがって、規則②>③の優先順位ロジックを安く・確実に検証できる corfrapdf.pdf を先に実行する。API コストが発生するため、まず1章だけのスモークテストで確認してからフルランに進む。

- [ ] **Step 1: 1章だけのスモークテストを実行する**

```bash
source venv/bin/activate
python3 main.py data/input/Booksample/corfra/corfrapdf.pdf --book --max-chapters 1
```

`--pdf-mode` は指定しない（規則②〜④の自動判定を検証するため）。

- [ ] **Step 2: 見開き優先の判定結果をログ・状態ファイルで確認する**

```bash
find state/book_sessions -maxdepth 1 -iname "corfrapdf_*" -newer docs/superpowers/plans/2026-07-18-book-mode-vlm-routing.md
```

上記で得たセッションディレクトリを `<BOOK_SESSION>` として:

```bash
cat state/book_sessions/<BOOK_SESSION>/routing_decision.json
```

Expected: `is_spread: true`, `is_docling_viable: true`, `reason: "spread_pdf"`, `pdf_mode: "full_vlm"`。もし `is_docling_viable` が `false` だった場合は規則④が代わりに発火して同じ `full_vlm` になるため、規則②の優先順位自体は検証できない点に注意し、その場合はログにその旨を記録する。

```bash
cat state/<BOOK_SESSION_ch1>/phase1_route.json
grep -c "\[VLM抽出失敗\]" state/<BOOK_SESSION_ch1>/phase1_preprocessor.json
```

Expected: `phase1_route.json` の `route` が `"vlm"`（`"native_fallback"` ならまだ壊れている＝I-15/I-16未解消の兆候）。`[VLM抽出失敗]` の出現回数が0件。

```bash
python3 -c "
import json
data = json.load(open('state/<BOOK_SESSION_ch1>/phase1_preprocessor.json'))
markdown_chunks = [c for c in data if c['text'].strip().startswith('#')]
print(f'Markdown見出しチャンク数: {len(markdown_chunks)}')
print(markdown_chunks[:3])
"
```

Expected: Markdown見出し（`#`/`##`）を含むチャンクが1件以上存在する（VLMが `# Heading` 形式で見出しを返している証拠）。

- [ ] **Step 3: スモークテストの結果をユーザーに報告し、フルラン実行の了承を得る**

- [ ] **Step 4: フルランを実行する（ユーザー了承後）**

```bash
python3 main.py data/input/Booksample/corfra/corfrapdf.pdf --book
```

注意: `data/input/Booksample/corfra/corfrapdf_p2.md`（2026-05-09生成）は I-15/I-16 修理**前**の産物（実際には native_fallback または Docling+TOCフォールバック経由）。今回の出力と単純比較して差分が出ても regression ではなく、修理後の設計どおりの経路で生成された初回の正しい出力である可能性が高い。差分は文字列比較ではなく `golden-verification` skill の構造品質チェックで評価する。

- [ ] **Step 5: golden-verification skill のチェックリストに従い出力を検証する**

`golden-verification` skill を呼び出し、生成された `corfrapdf_p2.md`/`.txt` の構造品質（見出し階層・章立て・本文欠落の有無）を確認する。

- [ ] **Step 6: コスト実測を記録する**

処理ログから VLM 呼び出し回数・概算トークン数を確認し、`docs/model_optimization.md` に実測値を追記する（Section 3 相当）。

- [ ] **Step 7: 結果をユーザーに報告し、Naven.pdf に進んでよいか確認を得る**

corfrapdf.pdf の結果（ルーティング判定・VLM動作・構造品質）をまとめてユーザーに報告し、**明示的な確認を得てから Task 8（Naven.pdf）に進む**。問題が見つかった場合はここで止め、Task 8 には進まない。

```bash
git add docs/model_optimization.md
git commit -m "$(cat <<'EOF'
docs: corfrapdf.pdf VLM実行(見開き優先ルーティング)のコスト実測を記録

Spec B実装後、見開き×Docling可能という優先順位検証ケース(corfrapdf.pdf)
におけるVLM呼び出し回数・概算トークン数の実測値を記録した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: VLM 実動作確認（Naven.pdf、大規模スキャン書籍の純VLM経路）

**前提:** Task 7（corfrapdf.pdf）の結果をユーザーが確認し、次に進む承認を得てから着手する。

**Files:** なし（コード変更なし、実 PDF での動作検証）

**Interfaces:**
- Consumes: Task 1〜7 の実装・検証結果
- Produces: `state/book_sessions/Naven_*/` 配下の実処理結果、`data/input/Booksample/Naven/Naven_p2.md`/`.txt`（初回生成）

Naven.pdf はテキスト層のない純スキャン書籍（380ページ・94MB）で、規則④（それ以外=VLM）の素直なケース。I-15 修理後、VLM スライディング OCR が**実質初回稼働**する検証としてはこちらが本命（`docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md` L51 参照）。規模が大きいため、まず1章だけのスモークテストで確認する。

- [ ] **Step 1: 1章だけのスモークテストを実行する**

```bash
python3 main.py data/input/Booksample/Naven/Naven.pdf --book --max-chapters 1
```

- [ ] **Step 2: VLM が実際に呼ばれたことをログ・状態ファイルで確認する**

Task 7 Step 2 と同様の確認（`routing_decision.json` の `reason` は `"docling_not_viable"` になるはず、`phase1_route.json`, Markdown見出しチャンクの有無、`[VLM抽出失敗]` が0件）を行う。

- [ ] **Step 3: スモークテストの結果をユーザーに報告し、フルラン実行の了承を得る**

Naven.pdf は大型書籍のため、フルランは時間・APIコストが相応にかかる。

- [ ] **Step 4: フルランを実行する（ユーザー了承後）**

```bash
python3 main.py data/input/Booksample/Naven/Naven.pdf --book
```

- [ ] **Step 5: golden-verification skill のチェックリストに従い出力を検証する**

- [ ] **Step 6: コスト実測を記録する**

```bash
git add docs/model_optimization.md
git commit -m "$(cat <<'EOF'
docs: Naven.pdf VLM実行(純スキャン書籍・初回稼働)のコスト実測を記録

Spec B実装後の初回VLM稼働（Naven.pdf、テキスト層のない純スキャン書籍）
における VLM 呼び出し回数・概算トークン数の実測値を記録した。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: 結果をユーザーに報告する**

---

## Task 9: relations.pdf・論文モード（AL/NST）の回帰確認

**前提:** Task 8（Naven.pdf）の結果をユーザーが確認してから着手する。

**Files:** なし（コード変更なし、実 PDF・既存回帰スイートでの検証）

**Interfaces:**
- Consumes: Task 1〜8 の実装・検証結果

- [ ] **Step 1: relations.pdf で Docling 正式化の回帰確認を行う**

spec の検証方針（relations = 単ページ・純デジタル、見開き要因を排除できるため Docling 正式化の重点検証対象）に従い、既存の relations 出力（フルラン完了済み、memory 参照）が Task 3〜5 の変更後も正しく処理されることを確認する:

```bash
python3 main.py data/input/Booksample/relations/relationspdf.pdf --book --max-chapters 1
```

Expected: `routing_decision.json` の `reason` が `"docling_viable"`（見開きでなくDocling可能なため）、`pdf_mode` が `"hybrid"`。Route D（`structure_nodes_by_role`）が発火していることをログで確認する。

- [ ] **Step 2: 論文モード（AL/NST）の回帰確認を行う**

```bash
python3 main.py data/input/paperplain/NST/NSTsample.txt --lite
python3 main.py data/input/paperpdf/AL/*.pdf --lite
```

`golden-verification` skill のチェックリストに従い、既存の理想出力との構造一致を確認する（Task 2 の `force_vlm` 判定追加が論文モードのデフォルト経路に影響しないことの確認）。

- [ ] **Step 3: 全体テストスイートの最終確認**

```bash
python3 -m pytest tests/unit/ -v
```

Expected: 全件 PASS。

- [ ] **Step 4: 検証結果を troubleshooting_log.md に追記してコミットする**

golden-verification の結果（各書籍・論文の構造品質確認結果、見つかった問題があれば新規Issue番号を付与）を `docs/management/troubleshooting_log.md` に追記する。

```bash
git add docs/management/troubleshooting_log.md
git commit -m "$(cat <<'EOF'
docs: Spec B ゴールデン検証結果を記録（corfra/Naven/relations/AL/NST）

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 実行順序メモ

Task 1〜2 は独立して並行可能（ocr_manager.py と phase1_preprocessor.py は別ファイル）。Task 3 は Task 2 完了後（`_decide_book_pdf_mode` の結果を Task 2 の `force_vlm` 判定と組み合わせて使うため）。Task 4〜5 は Task 2 完了後（`phase1_route_path`/`load_route_from_json` に依存）、Task 3 とは独立。Task 6（ドキュメント）は Task 1〜5 完了後。

実 PDF 検証（Task 7〜9）は Task 1〜6 すべて完了後、**corfrapdf.pdf → Naven.pdf → relations/論文回帰の順に1冊ずつ**実施する。各書籍の完了後は次に進む前に必ずユーザーへ結果を報告し、確認を得てから次のタスクに着手する（Task 7→8、Task 8→9 のいずれも承認ゲートあり）。corfrapdf.pdf を先にするのは、見開き×Docling可能という規則②>③の優先順位を検証できる唯一のサンプルであり、かつ Naven.pdf（380ページ・94MB）より小さく安く・過去の処理実績で比較もできるため。
