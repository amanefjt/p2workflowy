# Spec B 実装 Plan（VLM 修理・実ルート記録・Docling 正式化・ルーティング明示化）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 機能停止中の VLM スライディング OCR を修理し（I-15）、書籍モードで偶然動いている Docling＋TOC フォールバック経路を設計された正式経路に昇格させ（I-16）、Phase 1 のルーティングを明示化・記録する。

**Architecture:** 正本スペックは `docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md`。鍵は「Phase 1 が**実際に使ったルート**を記録し、Phase 3 は指定値（pdf_mode）ではなく実ルートで分岐する」こと。Docling ルートの書籍は role 見出しを Markdown 化して既存の `structure_nodes_by_markdown` を再利用する（DRY）。

**Tech Stack:** Python 3.12 / venv（`./venv/bin/python`）/ pytest / PyMuPDF / Docling / Gemini VLM

## Global Constraints

- **着手時期**: 翻訳コンテキスト Stage 1（`plans/2026-07-10-translation-context-stage1.md`）の実装と比較読み・モデル A/B が終わってから（翻訳品質ベースラインの保護）
- **行番号は 2026-07-10 時点のもの**。Stage 1 実装後はズレている可能性が高いので、編集前に必ず該当コードを grep で再特定する
- テスト実行は `./venv/bin/python -m pytest tests/unit/ -q`（全合格を維持）
- **削除の前には再 grep で参照ゼロを確認**し、削除コミットと挙動変更コミットを分ける
- コミットメッセージは日本語。`core/` 変更のため最終タスクで管理ログ（I-15/I-16 の対応済み化）を追記
- **判断保留ポイント**（⚠️）で迷ったら Agent ツールを `model: "fable"` で単発起動して相談（スペックのパスと質問を渡す）

---

### Task 1: VLM 二重定義バグの修理（I-15）

**Files:**
- Modify: `core/engine/p1_ingest/ocr_manager.py:214-226`（互換用 `process_page_vlm` の削除）
- Test: 新規 `tests/unit/test_ocr_manager.py`

**Interfaces:**
- Produces: `OCRManager.process_page_vlm(self, current_img, prev_img=None, page_idx=0, session_dir=None) -> str` が唯一の定義になる（呼び出し元 `pdf_ingester.py:67` と整合）

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/unit/test_ocr_manager.py`:

```python
import inspect


def test_process_page_vlm_is_defined_once_with_image_signature():
    """二重定義（I-15）の再発防止。生存シグネチャが呼び出し元と一致すること。"""
    from core.engine.p1_ingest.ocr_manager import OCRManager
    params = list(inspect.signature(OCRManager.process_page_vlm).parameters)
    assert params[0] == "self"
    assert params[1] == "current_img", f"実際のシグネチャ: {params}"
    for name in ("prev_img", "page_idx", "session_dir"):
        assert name in params, f"missing param: {name}"


def test_source_has_single_definition():
    import core.engine.p1_ingest.ocr_manager as m
    src = inspect.getsource(m)
    assert src.count("def process_page_vlm") == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_ocr_manager.py -v`
Expected: FAIL（後着定義 `(pdf_path, page_num)` が生存しているため `params[1] == "current_img"` が落ちる）

- [ ] **Step 3: 修理（削除）を実施**

1. 削除前の再確認: `grep -rn "process_page_vlm" core/ tests/ server.py main.py` — 呼び出し元が `pdf_ingester.py` の画像引数版のみであること。想定外の呼び出しがあれば中断して fable advisor へ。
2. `ocr_manager.py` の**2 つ目**の `process_page_vlm`（「（互換用）1ページを Gemini VLM OCR で処理する。」の docstring を持つ、`pdf_path: str, page_num: int` 版。2026-07-10 時点 :214-226）をメソッドごと削除する。
3. `grep -n "VLM_PROMPT" core/engine/p1_ingest/ocr_manager.py` — 削除したメソッドだけが `self.VLM_PROMPT` を使っていた場合、その属性定義も同コミットで削除する（使われていれば残す）。

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 5: コミット**

```bash
git add core/engine/p1_ingest/ocr_manager.py tests/unit/test_ocr_manager.py
git commit -m "fix: process_page_vlm の二重定義を解消し VLM スライディング OCR を復旧（I-15）"
```

---

### Task 2: Phase 1 実ルートの記録

**Files:**
- Modify: `core/phase1_preprocessor.py`（`_run_phase1_pdf` / `_run_phase1_text`）
- Test: `tests/unit/test_json_pipeline.py` または新規 `tests/unit/test_phase1_route.py`

**Interfaces:**
- Produces: セッションディレクトリ（`phase1_preprocessor.json` と同じ場所）に `phase1_route.json` = `{"route": "docling" | "vlm" | "text"}`。読み出し用のモジュールレベル関数 `load_phase1_route(session_dir: Path) -> str | None`（ファイルがなければ `None`＝旧セッション互換）。Task 3 が消費する

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/unit/test_phase1_route.py`:

```python
import json
from pathlib import Path

from core.phase1_preprocessor import save_phase1_route, load_phase1_route


def test_route_roundtrip(tmp_path):
    save_phase1_route(tmp_path, "docling")
    assert load_phase1_route(tmp_path) == "docling"
    assert json.loads((tmp_path / "phase1_route.json").read_text())["route"] == "docling"

def test_missing_route_returns_none(tmp_path):
    assert load_phase1_route(tmp_path) is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_phase1_route.py -v`
Expected: FAIL（関数未定義）

- [ ] **Step 3: 実装**

`core/phase1_preprocessor.py` にモジュールレベル関数を追加:

```python
ROUTE_FILE_NAME = "phase1_route.json"

def save_phase1_route(session_dir: str | Path, route: str) -> None:
    """Phase 1 が実際に使った取り込みルートを記録する（Phase 3 の分岐が参照する）。"""
    path = Path(session_dir) / ROUTE_FILE_NAME
    path.write_text(json.dumps({"route": route}, ensure_ascii=False), encoding="utf-8")

def load_phase1_route(session_dir: str | Path) -> str | None:
    """記録された実ルートを返す。旧セッション（記録なし）は None。"""
    path = Path(session_dir) / ROUTE_FILE_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("route")
    except Exception:
        return None
```

呼び出し配線（いずれも `save_state` が真のときのみ）:
- `_run_phase1_pdf` の Docling 成功 return 直前（`save_chunks_to_json` の隣）: `save_phase1_route(Path(state_path).parent, "docling")`
- `_run_phase1_pdf` の VLM ルート完了時（`run_pdf_ingestion` 後のチャンク保存箇所）: `save_phase1_route(Path(state_path).parent, "vlm")`
- `_run_phase1_text` の保存箇所: `save_phase1_route(Path(state_path).parent, "text")`

各箇所は `grep -n "save_chunks_to_json\|state_path" core/phase1_preprocessor.py` で再特定すること。

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 5: コミット**

```bash
git add core/phase1_preprocessor.py tests/unit/test_phase1_route.py
git commit -m "feat: Phase 1 の実ルート（docling/vlm/text）をセッションに記録"
```

---

### Task 3: Phase 3 を実ルート分岐に変更し、Docling 書籍の role 構造化を正式化

**Files:**
- Modify: `core/phase3_structure.py:49-71`（Route C 分岐）
- Modify: `core/engine/p3_structure/tree_builder.py`（アダプタ関数追加）
- Test: `tests/unit/test_phase3_structure.py`

**Interfaces:**
- Consumes: `load_phase1_route`（Task 2）、既存 `structure_nodes_by_markdown(chunks, is_book, toc_list)`
- Produces: `roles_to_markdown_chunks(chunks: List[RawChunk]) -> List[RawChunk]`（`tree_builder.py`。role=h1/h2 のチャンク text に `# `/`## ` を前置した**コピー**を返す。p はそのまま）。Phase 3 の Markdown 構造化は「実ルート=vlm」または「書籍×実ルート=docling（アダプタ経由）」で発火し、旧セッション（route 記録なし）は従来どおり `pdf_mode=="full_vlm"` で発火

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_phase3_structure.py` に追記:

```python
def test_roles_to_markdown_chunks_prefixes_headings():
    from core.models import RawChunk
    from core.engine.p3_structure.tree_builder import roles_to_markdown_chunks
    chunks = [
        RawChunk(id="c0", text="Chapter One", role="h1", seq_index=0.0),
        RawChunk(id="c1", text="Section A", role="h2", seq_index=1.0),
        RawChunk(id="c2", text="body text", role="p", seq_index=2.0),
        RawChunk(id="c3", text="# Already MD", role="h1", seq_index=3.0),
    ]
    out = roles_to_markdown_chunks(chunks)
    assert out[0].text == "# Chapter One"
    assert out[1].text == "## Section A"
    assert out[2].text == "body text"
    assert out[3].text == "# Already MD"      # 二重付与しない
    assert chunks[0].text == "Chapter One"     # 元のチャンクは破壊しない
    assert out[2] is chunks[2]                 # p チャンクはコピー不要
```

（`RawChunk` のコンストラクタ引数は `core/models.py` を確認して合わせること）

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_phase3_structure.py -k roles_to_markdown -v`
Expected: FAIL（関数未定義）

- [ ] **Step 3: アダプタと分岐を実装**

(a) `core/engine/p3_structure/tree_builder.py` に追加:

```python
import copy

def roles_to_markdown_chunks(chunks):
    """Docling 等が role 属性で示す見出しを、Markdown 記法（# / ##）に変換したコピーを返す。

    structure_nodes_by_markdown（Route C）を Docling ルートの書籍でも再利用するためのアダプタ。
    role が h1/h2 以外、またはすでに # で始まるチャンクはそのまま返す。
    """
    out = []
    for c in chunks:
        if c.role in ("h1", "h2") and not c.text.lstrip().startswith("#"):
            c2 = copy.copy(c)
            c2.text = ("# " if c.role == "h1" else "## ") + c.text
            out.append(c2)
        else:
            out.append(c)
    return out
```

(b) `core/phase3_structure.py` の Route C 分岐（2026-07-10 時点 :49-71）を置換:

```python
    # --- 実ルートの取得（旧セッションは None → 従来の pdf_mode 分岐にフォールバック） ---
    from .phase1_preprocessor import load_phase1_route
    route = load_phase1_route(Path(phase1_state_path).parent)

    # --- Route C: Markdown 構造化 ---
    # 発火条件: 実ルート=vlm（VLM が Markdown 見出しを生成）
    #           または 書籍×実ルート=docling（role 見出しを Markdown 化して再利用）
    #           または 実ルート記録なし×pdf_mode=full_vlm（後方互換）
    chunks_for_md = chunks
    markdown_route = (route == "vlm") or (route is None and pdf_mode == "full_vlm")
    if is_book and route == "docling":
        chunks_for_md = roles_to_markdown_chunks(chunks)
        markdown_route = True
        print_log("  [Phase 3] Docling ルート: role 見出しを Markdown 化して構造化します")

    if markdown_route:
        has_markdown_headers = any(re.match(r'^#\s+', c.text.strip()) for c in chunks_for_md)
        if has_markdown_headers:
            print_log("  [Phase 3] Route C: Markdown 構造化を実行します")
            toc_list = []
            if is_book:
                toc_path = Path(structure_state_path).parent / "phase3_toc.json"
                if toc_path.exists():
                    with open(toc_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                        toc_list = [entry["title"] for entry in cached_data.get("toc", [])]
                else:
                    toc_list = extract_toc_from_chunks(chunks_for_md, api_key=api_key, model=model)

            tree, sections_dict = structure_nodes_by_markdown(chunks_for_md, is_book=is_book, toc_list=toc_list)
            if save_state:
                save_tree_to_json(tree, str(structure_state_path))
                with open(sections_state_path, "w", encoding="utf-8") as f:
                    json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            return tree, sections_dict
        else:
            print_log("  [Phase 3] Markdown 見出しが未検出のため標準構造化へフォールバックします。")
```

（`roles_to_markdown_chunks` の import を `phase3_structure.py` 冒頭の tree_builder import 行に追加。以降の TOC/ChapterParser フォールバック :77- は無変更）

⚠️ **判断保留ポイント**: Docling の role=h2 が書籍の「節」粒度と合わない実データが出た場合（章がすべて h1 扱いになる等）、粒度マッピングの調整は fable advisor に相談。

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 5: コミット**

```bash
git add core/phase3_structure.py core/engine/p3_structure/tree_builder.py tests/unit/test_phase3_structure.py
git commit -m "feat: Phase 3 を実ルート分岐に変更し Docling 書籍の role 構造化を正式化（I-16）"
```

---

### Task 4: 書籍モードのルーティング明示化（full_vlm ハードコード廃止）

**Files:**
- Modify: `core/book_manager.py:169-174, 214`
- Modify: `main.py:184` 付近（書籍分岐の pdf_mode 解決）
- Test: `tests/unit/test_book_manager.py`

**Interfaces:**
- Consumes: Task 2/3（実ルート記録と Phase 3 の実ルート分岐が先に入っていること。これがないと full_vlm 廃止で Phase 3 Route C が壊れる）
- Produces: 章の `run_pipeline(..., pdf_mode=<ユーザー明示指定 or "hybrid">)`。ユーザーが `--pdf-mode` を指定しなければ Phase 1 の自動判定（Docling viable → Docling、スキャン → VLM）に委ねる

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_book_manager.py` に追記（Task 2 of Stage 1 と同じ harness を流用）:

```python
def test_run_respects_user_pdf_mode(tmp_path):
    # ユーザーが pdf_mode="full_vlm" を明示 → そのまま章へ
    captured = _run_book_manager_with_mocks(tmp_path, pipeline_kwargs={"pdf_mode": "full_vlm"})
    assert captured["pdf_mode"] == "full_vlm"

def test_run_defaults_to_auto_routing(tmp_path):
    # 未指定 → "hybrid"（Phase 1 の自動判定に委ねる）
    captured = _run_book_manager_with_mocks(tmp_path, pipeline_kwargs={})
    assert captured["pdf_mode"] == "hybrid"
```

（`_run_book_manager_with_mocks` は Stage 1 Task 2 で書いたモック harness をヘルパー関数に抽出して共用する。抽出時は既存テストも同ヘルパーに寄せる）

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_book_manager.py -k pdf_mode -v`
Expected: FAIL（現状は常に `full_vlm`）

- [ ] **Step 3: 実装**

(a) `core/book_manager.py`:
- `explicit_keys`（:169-172）から `"pdf_mode"` を外し、代わりに章ループ前で取り出す:

```python
        # 章処理の pdf_mode: ユーザー明示指定があれば尊重、なければ Phase 1 の自動判定に委ねる。
        # （旧実装はここで full_vlm に固定していたが、I-15/I-16 により実態と乖離していた）
        requested_pdf_mode = pipeline_kwargs.pop("pdf_mode", None)
        chapter_pdf_mode = requested_pdf_mode or "hybrid"
        try:
            from .engine.p1_ingest.docling_ingester import is_docling_viable
            expected = "docling" if is_docling_viable(pdf_for_splitting) else "vlm"
            print_log(f"  [BookManager] ルーティング判定（書籍単位・参考値）: {expected} "
                      f"(pdf_mode={chapter_pdf_mode})")
        except Exception:
            pass
```

- 章ループの `run_pipeline(...)` の `pdf_mode="full_vlm",` を `pdf_mode=chapter_pdf_mode,` に変更。

(b) `main.py` 書籍分岐（2026-07-10 時点 :184）: `pdf_mode=args.pdf_mode if args.pdf_mode else "hybrid"` を `pdf_mode=args.pdf_mode` に変更（未指定 None を BookManager まで素通しし、上記 `or "hybrid"` で解決する。「明示指定かどうか」の情報を BookManager が受け取れるようにするため）。

（注: 見開きスキャン PDF は Phase 0 で分割された画像 PDF になるため `is_docling_viable=False` → 自動的に VLM ルートに入る。スペックの規則②（spread→VLM）は明示的な分岐を書かなくてもこの経路で満たされる）

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 5: コミット**

```bash
git add core/book_manager.py main.py tests/unit/test_book_manager.py
git commit -m "feat: 書籍モードの pdf_mode 固定を廃止しユーザー指定と自動判定に委ねる"
```

---

### Task 5: VLM 実動作確認とコスト実測（要 GEMINI_API_KEY）

**Files:**
- Modify: `docs/model_optimization.md`（VLM 実測の追記）

- [ ] **Step 1: スキャン相当 PDF の確認**

```bash
./venv/bin/python -c "
from core.engine.p1_ingest.docling_ingester import is_docling_viable
print(is_docling_viable('data/input/Booksample/corfra/corfrapdf_split.pdf'))
"
```

Expected: `False`（分割済み見開き＝画像 PDF）。`True` の場合は他のスキャン PDF を探すか、任意の画像化 PDF をスクラッチで作って代用する。

- [ ] **Step 2: VLM 経路のスモーク実行**

スクラッチディレクトリに以下を作って実行:

```python
# scratch_vlm_smoke.py
import os
from core.engine.p1_ingest.pdf_ingester import run_pdf_ingestion

elements = run_pdf_ingestion(
    "data/input/Booksample/corfra/corfrapdf_split.pdf",
    api_key=os.environ["GEMINI_API_KEY"],
    pdf_mode="full_vlm",
    max_pages=3,
)
for e in elements[:5]:
    print(e.get("role"), repr(e.get("text", ""))[:120])
```

Expected:
- ログに「VLM 失敗 (Page N): ... TypeError」が**出ない**（修理前は毎ページ出ていた）
- 出力テキストに `[VLM抽出失敗]` が含まれない
- テキストに Markdown 見出し（`# `）が含まれる（VLM プロンプトが Markdown を返す設計のため）

- [ ] **Step 3: コスト実測を記録**

Step 2 実行時のログ/メトリクス（`state/` のメトリクスやコンソール出力）から、VLM 1 呼び出しあたりの入出力トークンを読み取り、`docs/model_optimization.md` §5 の末尾に「VLM 1 ページあたり実測（2026-XX-XX、gemini-3.1-flash-lite・thinking LOW）」として追記する。読み取れない場合は「N ページで X 秒・課金ダッシュボード読み値」でも可（概算であることを明記）。

- [ ] **Step 4: コミット**

```bash
git add docs/model_optimization.md
git commit -m "docs: VLM OCR 復旧後の 1 ページあたり実測コストを記録"
```

---

### Task 6: E2E 検証・ドキュメント正常化・管理ログ

**Files:**
- Modify: `CLAUDE.md`（設計原則の暫定注記を正式記述へ）
- Modify: `docs/ARCHITECTURE.md` §3（入力ルーティングの記述更新）
- Modify: `docs/management/troubleshooting_log.md` / `requirements_log.md`

- [ ] **Step 1: 論文モードのゴールデン検証（回帰なし確認）**

`golden-verification` skill を invoke し、AL/NST（PDF 版含む）で構造回帰がないことを確認:

```bash
./venv/bin/python main.py data/input/paperpdf/NST/NSTpdf.pdf --lite
```

Expected: 完走し、`state/<session>/phase1_route.json` が `docling`（NST はデジタル PDF）、見出し構成が理想出力と一致。

- [ ] **Step 2: 書籍モードの重点検証（relations）**

```bash
./venv/bin/python main.py data/input/Booksample/relations/relationspdf.pdf --book --lite
```

Expected: 各章の `phase1_route.json` が `docling`、Phase 3 ログに「Docling ルート: role 見出しを Markdown 化」が出る。完走した出力の章・節構成を、（あれば）過去の出力と比較して劣化がないことを目視確認。

- [ ] **Step 3: CLAUDE.md / ARCHITECTURE.md の正式化**

- `CLAUDE.md` 設計原則の「※2026-07-10 時点、VLM 経路は機能停止中…」の暫定注記を削除し、次の正式記述に差し替え:

```
- **入力ルーティングと判断優先順位**: デジタル PDF（`is_docling_viable()`=True）は Docling ルートが正式経路、スキャン PDF（見開き含む）は VLM OCR ルート。Phase 1 は実際に使ったルートを `phase1_route.json` に記録し、Phase 3 は指定値ではなく実ルートで構造化方式を選ぶ。VLM 経路内の判断優先順位は `VLM の論理役割判断 > 物理証拠（フォント・座標）> 幾何的ヒント`、OCR 補正は「VLM が特定した位置の Native テキストで肉付けする」方針を守る。
```

- `docs/ARCHITECTURE.md` §3「入力ルーティングの自動判定」を同趣旨で更新（書籍モードも同じ自動判定に乗ること、実ルート記録の存在を追記）。

- [ ] **Step 4: 管理ログ追記**

- `troubleshooting_log.md`: I-15 / I-16 に「対応済み（2026-XX-XX, Spec B 実装）」を追記（I-8 対応済みの書式に倣う）。
- `requirements_log.md`: Spec B 実装完了エントリ（VLM 復旧・Docling 正式化・ルーティング明示化。スキャン書籍の実測コストの要点を含む）。

- [ ] **Step 5: 最終確認とコミット**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

```bash
git add CLAUDE.md docs/ARCHITECTURE.md docs/management/
git commit -m "docs: 入力ルーティングの正式化を反映し I-15/I-16 を対応済みに更新"
```

- [ ] **Step 6: 完了報告**

superpowers:verification-before-completion に従い、検証結果（ルート判定ログ・VLM スモーク・ゴールデン）を明示して報告。**残課題（A/B 課題として登録済み）**: 見開き×テキスト層クリーンの本（corfra/pse タイプ）を Docling に載せる実験、章単位ルーティングの要否。
