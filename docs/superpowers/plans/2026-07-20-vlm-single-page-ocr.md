# VLM 単ページ OCR ＋ テキスト文脈（I-21）実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VLM ルートの OCR を「2ページ結合画像→右だけ抽出」から「現ページ1枚の画像＋前ページのネイティブテキスト文脈」に戻し、図版ページで直前ページを二重に書き起こす I-21 を構造的に解消する。

**Architecture:** `ocr_manager.py::process_page_vlm` が受け取るのを `prev_img`（画像）から `prev_context_text`（文字列）へ変え、2-up 結合（`_merge_images_horizontal`）を廃止して現ページ画像のみを VLM に渡す。前ページ文脈は `pdf_ingester.py` が前ページの物理ページのネイティブテキスト末尾から組み立てる。1画像に対象ページしか入らないため、隣ページの書き起こしが構造的に起きなくなる。

**Tech Stack:** Python 3.12+ / PyMuPDF (`fitz`) / PIL / Gemini VLM (`core.llm_client.call_gemini_async`) / pytest / asyncio

**設計spec:** `docs/superpowers/specs/2026-07-20-vlm-single-page-ocr-design.md`

## Global Constraints

- VLM に渡す画像は**常に現ページ1枚のみ**。1画像に2ページを結合しない
- 前ページ文脈は**ネイティブテキスト**（`fitz` から即時取得）で渡し、VLM 出力は文脈に使わない（並列処理 `asyncio.gather`＋semaphore を直列化させない）
- 図版ページ**検出ヒューリスティックは実装しない**（構造を潰す方針。文字数等でページ種別を判定しない）
- プロンプトは `ocr_manager.py` の `OCRManager` クラス定数として持つ（既存 `VLM_FRONT_MATTER_PROMPT` 等と同じ場所）
- 定数はクラス先頭にまとめる（マジックナンバー直書き禁止）
- 外部ライブラリ追加禁止（`fitz` / `PIL` / 標準ライブラリのみ）
- コミットメッセージは日本語、末尾に必ず空行を挟んで `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- テスト: `source venv/bin/activate && python3 -m pytest tests/unit/ -q`（着手前 381 件）
- `core/` 変更コミットには `docs/management/` ログ追記が必要（Task 4 でまとめて可、hook が注意喚起）
- `golden-verification` は本計画では実施しない（同一ブランチの I-26/I-27 と合わせ I-21 完了後に1回。spec §3.3）

## File Structure

| ファイル | 責務 | 変更 |
|---|---|---|
| `core/engine/p1_ingest/ocr_manager.py` | 単ページ VLM OCR とプロンプト | `process_page_vlm` 改修・`VLM_SINGLE_PAGE_PROMPT` 追加・`_merge_images_horizontal` 削除・FRONT_MATTER 単ページ化 |
| `core/engine/p1_ingest/pdf_ingester.py` | スライディングウィンドウのタスク生成 | 画像→物理ページマップ・前文脈テキスト・タスク `range(0,N)`・結果格納整理 |
| `tests/unit/test_ocr_manager.py` | OCRManager テスト | シグネチャ検査を新シグネチャに更新＋単ページ挙動テスト追加 |
| `tests/unit/test_pdf_ingester_context.py`（新規） | 前文脈組み立てテスト | 画像→物理ページマップ・前文脈末尾抽出 |
| `docs/management/*.md` | 記録 | I-21 解決・判断根拠 |

---

### Task 1: ocr_manager を単ページ OCR ＋ テキスト文脈に戻す

spec §1.2, §1.3, §1.5。

**Files:**
- Modify: `core/engine/p1_ingest/ocr_manager.py`（`process_page_vlm` 157-197、`_merge_images_horizontal` 199-212、`VLM_FRONT_MATTER_PROMPT` 59-70、`VLM_CONTINUITY_PROMPT`/`VLM_PROMPT` 73-85）
- Modify: `tests/unit/test_ocr_manager.py`（シグネチャ検査 30-48）

**Interfaces:**
- Produces:
  - `OCRManager.process_page_vlm(self, current_img: Image.Image, prev_context_text: str = "", page_idx: int = 0, session_dir: Optional[Path] = None) -> str`
  - `OCRManager.VLM_SINGLE_PAGE_PROMPT`（`{prev_context}` を含むテンプレート文字列）
  - `OCRManager.CONTEXT_TAIL_CHARS`（定数。pdf_ingester と共有する前文脈末尾長）

- [ ] **Step 1: 既存シグネチャテストを新シグネチャへ更新する（失敗する状態にする）**

`tests/unit/test_ocr_manager.py` の `TestProcessPageVlmSignature` を次に置き換える。
`prev_img` → `prev_context_text` へ変わり、単ページ処理（結合を呼ばない）を検査する。

```python
class TestProcessPageVlmSignature:
    def test_signature_uses_prev_context_text(self):
        """process_page_vlm は前ページを画像ではなくテキスト文脈で受け取る（I-21）。"""
        sig = inspect.signature(OCRManager.process_page_vlm)
        assert list(sig.parameters.keys()) == [
            "self", "current_img", "prev_context_text", "page_idx", "session_dir",
        ]

    @pytest.mark.asyncio
    async def test_call_pattern_succeeds(self):
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        result = await manager.process_page_vlm(
            img, prev_context_text="", page_idx=0, session_dir=None
        )
        assert result == "# Heading\nBody text"
        manager._call_gemini_raw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_image_not_merged(self):
        """VLM には現ページ画像1枚だけが渡り、2-up 結合されない（I-21 の核心）。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 20), color="white")
        await manager.process_page_vlm(
            img, prev_context_text="前ページ末尾テキスト", page_idx=2, session_dir=None
        )
        # _call_gemini_raw の第1引数（content list）の画像が入力画像と同一寸法であること
        # （結合していれば幅が倍化する）
        call_args = manager._call_gemini_raw.await_args.args[0]
        passed_img = call_args[0]
        assert passed_img.size == (10, 20), "結合されず現ページ画像がそのまま渡るべき"

    @pytest.mark.asyncio
    async def test_prev_context_injected_into_prompt(self):
        """page_idx>=1 では前文脈がプロンプトに差し込まれる。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        await manager.process_page_vlm(
            img, prev_context_text="...ending mid sentence and", page_idx=3, session_dir=None
        )
        prompt = manager._call_gemini_raw.await_args.args[0][1]
        assert "...ending mid sentence and" in prompt
        assert "{prev_context}" not in prompt, "プレースホルダが未置換で残ってはならない"

    @pytest.mark.asyncio
    async def test_page0_uses_front_matter_prompt(self):
        """先頭ページ(page_idx==0)は FRONT_MATTER プロンプトを使う。"""
        manager = _make_ocr_manager()
        img = Image.new("RGB", (10, 10), color="white")
        await manager.process_page_vlm(img, prev_context_text="", page_idx=0, session_dir=None)
        prompt = manager._call_gemini_raw.await_args.args[0][1]
        assert prompt == OCRManager.VLM_FRONT_MATTER_PROMPT
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_ocr_manager.py -v`
Expected: FAIL（`prev_img` 前提の現行実装なので新シグネチャ検査・単ページ検査が落ちる）

- [ ] **Step 3: `VLM_SINGLE_PAGE_PROMPT` と `CONTEXT_TAIL_CHARS` を追加する**

`core/engine/p1_ingest/ocr_manager.py` の `VLM_CONTINUITY_PROMPT`（73-82行）と
`VLM_PROMPT` エイリアス（85行）を削除し、代わりに次を追加する。`VLM_BASE_RULES` は
既存のクラス定数（36-56行）を流用する。`{prev_context}` は f-string 中で `{{prev_context}}`
と二重にして**リテラルのまま残し**、実行時に `.replace()` で差し込む。

```python
    # --- 前ページ文脈として渡すネイティブテキスト末尾の最大長 ---
    CONTEXT_TAIL_CHARS = 500

    # --- 2ページ目以降：現ページ1枚＋前ページのテキスト文脈（I-21） ---
    # 2-up 結合をやめ、現ページ画像1枚のみを渡す。前ページはテキスト文脈として
    # 継続判定にのみ使い、決して繰り返させない。図版ページ（キャプションのみ）にも対応する。
    VLM_SINGLE_PAGE_PROMPT = f"""<task>
これは書籍または論文の**1ページ**の画像です。このページに印刷されているテキストのみを
Markdown 形式で抽出してください。
</task>

<previous_page_context>
{{prev_context}}
</previous_page_context>

<specific_rules>
- 上の <previous_page_context> は**直前ページの末尾**のテキストです。これは、このページの
  1行目が前ページの続きなのか新しい見出しなのかを判断するためだけに使ってください。
  **この文脈テキストを出力に繰り返してはいけません。**
- 前ページから文章が物理的・論理的に続いている場合、このページ冒頭に見出しタグ `# ` を
  付けてはいけません。新しい章や節がこのページ内で始まる場合のみ `# ` を付与してください。
- **図版ページの注意**: このページが写真・図表など画像主体で、キャプションだけしか印刷
  テキストが無いことがあります。その場合はキャプションのみを出力し、本文が無ければ空を
  返してください。画像の内容を描写せず、印刷されている文字だけを抽出してください。
</specific_rules>
{VLM_BASE_RULES}"""
```

- [ ] **Step 4: `VLM_FRONT_MATTER_PROMPT` を単ページ用に修正する**

現行（59-70行）は「**左側が1ページ目、右側が2ページ目です。両方の内容を出力してください。**」
という 2-up 前提の文言を含む。先頭ページ1枚のみを扱うよう次に修正する（該当の1文を置換）。

現行の該当行:
```
**左側が1ページ目、右側が2ページ目です。両方の内容を出力してください。**
```
を次に置換:
```
**これは1ページ目の画像です。このページの内容を出力してください。**
```

- [ ] **Step 5: `process_page_vlm` を単ページ＋文脈に書き換える**

`core/engine/p1_ingest/ocr_manager.py:157-197` を次に置き換える。

```python
    async def process_page_vlm(
        self, current_img: Image.Image, prev_context_text: str = "",
        page_idx: int = 0, session_dir: Optional[Path] = None,
    ) -> str:
        """現ページ1枚を OCR する。前ページはテキスト文脈として渡す（I-21）。

        2-up 結合はしない。1画像に対象ページしか入らないため、図版ページ等で
        隣ページを書き起こす失敗モードが構造的に起きない。
        """
        async with self.semaphore:
            # プロンプト選択: 先頭ページは front-matter、以降は単ページ＋文脈
            if page_idx == 0:
                prompt_text = self.VLM_FRONT_MATTER_PROMPT
            else:
                prompt_text = self.VLM_SINGLE_PAGE_PROMPT.replace(
                    "{prev_context}", prev_context_text or "（前ページなし）"
                )

            # キャッシュキー: 単一画像バイト列 ＋ 文脈テキスト
            # （文脈が変われば出力も変わりうるため画像だけでは不十分）
            img_byte_arr = io.BytesIO()
            current_img.save(img_byte_arr, format="PNG")
            key_src = img_byte_arr.getvalue() + prompt_text.encode("utf-8")
            img_hash = hashlib.md5(key_src).hexdigest()

            if img_hash in self.cache:
                print_log(f"  [OCRManager] Cache hit: Page {page_idx}")
                return self.cache[img_hash]

            if session_dir:
                debug_dir = session_dir / "debug_vlm"
                debug_dir.mkdir(parents=True, exist_ok=True)
                current_img.save(debug_dir / f"page_{page_idx:03d}_vlm_input.png")

            result = await self._call_gemini_raw([current_img, prompt_text])

            if result:
                self.cache[img_hash] = result
                self._save_cache()

            return result
```

- [ ] **Step 6: `_merge_images_horizontal` を削除する**

`core/engine/p1_ingest/ocr_manager.py:199-212` の `_merge_images_horizontal` メソッド
全体を削除する（Step 5 で呼び出し元が消えた。他に呼び出し元が無いことは Task 冒頭の
grep で確認済み）。削除後、`grep -rn "_merge_images_horizontal" core/ tests/` が
空になることを確認する。

- [ ] **Step 7: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_ocr_manager.py -v`
Expected: PASS（新シグネチャ・単ページ・文脈差し込み・page0 プロンプトの各テスト）

- [ ] **Step 8: コミット**

```bash
git add core/engine/p1_ingest/ocr_manager.py tests/unit/test_ocr_manager.py
git commit -m "$(printf 'fix: VLM を単ページ OCR ＋テキスト文脈に戻す（I-21）\n\n2-up 画像結合(_merge_images_horizontal)を廃止し、現ページ1枚のみを\nVLM に渡す。前ページは VLM_SINGLE_PAGE_PROMPT の {prev_context} に\nネイティブテキストとして差し込み継続判定にのみ使う（繰り返させない）。\n図版ページはキャプションのみ/空を返す旨をプロンプトに明記。1画像に\n対象ページしか入らないため隣ページの書き起こしが構造的に起きない。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 2: pdf_ingester を単ページ画像＋前文脈テキストで駆動する

spec §1.4, §2。

**Files:**
- Modify: `core/engine/p1_ingest/pdf_ingester.py`（画像構築 43-59、スライディングタスク 62-98、結果格納 100-112）
- Create: `tests/unit/test_pdf_ingester_context.py`

**Interfaces:**
- Consumes: `OCRManager.process_page_vlm(current_img, prev_context_text=, page_idx=, session_dir=)`、`OCRManager.CONTEXT_TAIL_CHARS`
- Produces:
  - `build_prev_contexts(native_texts: List[str], image_src_page: List[int], tail_chars: int) -> List[str]`（各論理ページの前文脈テキスト。純関数として切り出しテスト可能にする）

- [ ] **Step 1: 前文脈組み立ての純関数テストを書く**

`tests/unit/test_pdf_ingester_context.py`:

```python
"""pdf_ingester の前文脈組み立て（I-21）のテスト。"""

from core.engine.p1_ingest.pdf_ingester import build_prev_contexts


def test_first_page_has_empty_context():
    # 物理ページのネイティブテキスト
    native = ["page zero text", "page one text", "page two text"]
    # 各論理画像がどの物理ページ由来か（分割なし=1:1）
    src = [0, 1, 2]
    ctx = build_prev_contexts(native, src, tail_chars=100)
    assert ctx[0] == ""  # 先頭は前文脈なし


def test_context_is_previous_physical_page_tail():
    native = ["A" * 50, "B" * 50, "C" * 50]
    src = [0, 1, 2]
    ctx = build_prev_contexts(native, src, tail_chars=10)
    assert ctx[1] == "A" * 10   # 前ページ(物理0)の末尾10字
    assert ctx[2] == "B" * 10


def test_spread_split_halves_share_physical_page_text():
    # 物理ページ0が2分割 → 論理画像0,1がともに物理0由来。物理1は論理2。
    native = ["phys0", "phys1"]
    src = [0, 0, 1]
    ctx = build_prev_contexts(native, src, tail_chars=100)
    assert ctx[0] == ""       # 先頭
    assert ctx[1] == "phys0"  # 前の論理(物理0)の末尾
    assert ctx[2] == "phys0"  # 前の論理(物理0の右半分)の由来テキスト


def test_tail_shorter_than_limit_returns_whole():
    native = ["short"]
    src = [0, 0]
    ctx = build_prev_contexts(native, src, tail_chars=100)
    assert ctx[1] == "short"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_pdf_ingester_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_prev_contexts'`

- [ ] **Step 3: `build_prev_contexts` を実装する**

`core/engine/p1_ingest/pdf_ingester.py` のモジュール関数として追加する
（`run_pdf_ingestion_async` の前など、モジュールスコープ）。

```python
def build_prev_contexts(
    native_texts: List[str], image_src_page: List[int], tail_chars: int
) -> List[str]:
    """各論理ページの「前ページ文脈」を組み立てる（I-21）。

    論理ページ j の文脈は、直前の論理ページ j-1 の由来物理ページ
    （image_src_page[j-1]）のネイティブテキスト末尾 tail_chars 字。
    先頭ページ（j==0）は前文脈なし（空文字）。

    見開き分割された半ページは物理ページ全体のテキストを共有する（近似。
    文脈は継続判定のヒントにすぎず抽出対象ではないため許容）。
    """
    contexts: List[str] = []
    for j in range(len(image_src_page)):
        if j == 0:
            contexts.append("")
            continue
        prev_phys = image_src_page[j - 1]
        text = native_texts[prev_phys] if 0 <= prev_phys < len(native_texts) else ""
        contexts.append(text[-tail_chars:] if tail_chars > 0 else text)
    return contexts
```

- [ ] **Step 4: テストを実行して通ることを確認する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/test_pdf_ingester_context.py -v`
Expected: PASS（全4ケース）

- [ ] **Step 5: 画像構築ループで物理ページマップとネイティブテキストを収集する**

`core/engine/p1_ingest/pdf_ingester.py:43-59`（`images = []` から総論理ページ数の
print_log まで）を次に置き換える。`image_src_page` と `native_texts` を作る。

```python
        # 1. 全ページの画像を先にリスト化（高速。見開き分割を含む）
        #    あわせて、各論理画像がどの物理ページ由来か（image_src_page）と
        #    各物理ページのネイティブテキスト（native_texts）を収集する（I-21 前文脈用）。
        images = []
        image_src_page = []
        native_texts = []
        for i in range(total_pages):
            page = doc[i]
            native_texts.append(page.get_text())
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # 見開き判定と分割 (LtoR)
            if is_book and SpreadSplitter.is_spread(img):
                print_log(f"  [Ingester] 見開き検出 (Physical Page {i+1}): 分割を実行します。")
                split_pages = SpreadSplitter.split_spread_ltr(img)
                images.extend(split_pages)
                image_src_page.extend([i] * len(split_pages))
            else:
                images.append(img)
                image_src_page.append(i)

        # 分割後の総論理ページ数
        total_logical_pages = len(images)
        print_log(f"  [Ingester] 総論理ページ数: {total_logical_pages}")

        # 各論理ページの前ページ文脈（前ページのネイティブテキスト末尾）
        prev_contexts = build_prev_contexts(
            native_texts, image_src_page, OCRManager.CONTEXT_TAIL_CHARS
        )
```

- [ ] **Step 6: スライディングタスクを単ページ＋文脈に変更する**

`core/engine/p1_ingest/pdf_ingester.py` の `_vlm_slice_job` 定義（62-83行）と
タスク生成（85-92行）を次に置き換える。`prev_img` を `prev_context_text` に変え、
タスクを `range(0, N)` にして先頭ページも独立タスク化する。

```python
        completed_count = 0
        async def _vlm_slice_job(lc_idx: int, curr_img: Image.Image, prev_context_text: str):
            nonlocal completed_count
            try:
                vlm_res = await ocr.process_page_vlm(
                    curr_img, prev_context_text=prev_context_text,
                    page_idx=lc_idx, session_dir=session_dir,
                )
                if not vlm_res:
                    raise ValueError("VLM returned empty output.")
            except Exception as e:
                print_log(f"  [Ingester] VLM 失敗 (Page {lc_idx}): {e}. ネイティブPDFテキストにフォールバック。")
                native_text = ""
                try:
                    phys = image_src_page[lc_idx] if lc_idx < len(image_src_page) else lc_idx
                    if phys < len(native_texts):
                        native_text = native_texts[phys].strip()
                except Exception:
                    pass
                vlm_res = native_text if native_text else "[VLM抽出失敗]"

            completed_count += 1
            if state:
                p = int((completed_count / total_logical_pages) * 100) if total_logical_pages else 100
                state.update_status(1, f"VLM 単ページOCR中... ({completed_count}/{total_logical_pages})", p)
            return lc_idx, vlm_res

        # 全ページのタスクを生成（先頭ページも独立。前文脈は prev_contexts から）
        tasks = [
            _vlm_slice_job(i, images[i], prev_contexts[i])
            for i in range(total_logical_pages)
        ]
```

- [ ] **Step 7: 結果格納の変則（idx==1 特別扱い）を素直な形に整理する**

`core/engine/p1_ingest/pdf_ingester.py:100-112`（`results.sort` 後の for ループ）を
次に置き換える。先頭ページが独立タスクになったため「page_0_1」変則を廃止する。

```python
            results.sort(key=lambda x: x[0])

            for idx, text in results:
                all_elements.append({
                    "text": text,
                    "page_idx": idx,
                    "role": "vlm_page_source",
                    "id": f"page_{idx}",
                })
            if results:
                preview = results[0][1][:100].strip()
                print_log(f"  [Ingester] 先頭ページ解析完了。先頭 100 文字: {preview}...")
```

- [ ] **Step 8: `total_logical_pages == 1` の分岐を確認・整理する**

タスク生成を `range(total_logical_pages)` に統一したため、旧
`if total_logical_pages == 1: tasks.append(_vlm_slice_job(1, images[0], None))` の
特別分岐は不要。Step 6 の置換で該当分岐が消えていることを確認する
（1ページPDFなら `range(1)` = タスク1件、先頭ページとして前文脈なしで処理される）。

- [ ] **Step 9: 全単体テストを実行する**

Run: `source venv/bin/activate && python3 -m pytest tests/unit/ -q`
Expected: 全件 PASS（着手前 381 ＋ 本タスクの新規テスト）。

`pdf_ingester` を import する既存テスト（`test_phase1_route.py` 等）が
`build_prev_contexts` 追加や関数シグネチャ変更で落ちないことを確認する。落ちた場合は
呼び出し側の期待に合わせて修正する（`process_page_vlm` を直接呼ぶ既存テストがあれば
`prev_context_text=` へ更新）。

- [ ] **Step 10: コミット**

```bash
git add core/engine/p1_ingest/pdf_ingester.py tests/unit/test_pdf_ingester_context.py
git commit -m "$(printf 'fix: pdf_ingester を単ページ画像＋前文脈テキストで駆動（I-21）\n\nスライディングウィンドウの各タスクに前ページ画像ではなく前ページの\nネイティブテキスト末尾(build_prev_contexts)を渡す。画像→物理ページ\nマップで見開き分割時のインデックスずれに対応。タスクを range(0,N) に\nして先頭ページも独立処理し、idx==1 の page_0_1 変則を廃止。並列処理は\n維持（ネイティブテキストは即時取得）。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 3: 実PDF 受け入れ検証（corfra 図版ページの重複解消）

spec §3.1。これが本修正の成否そのもの。

**Files:**
- なし（検証のみ。必要なら使い捨てスクリプトを `$CLAUDE_JOB_DIR/tmp` に置く）

**Interfaces:**
- Consumes: Task 1・2 の実装

- [ ] **Step 1: corfra の1章を VLM ルートで処理する**

Run:
```bash
source venv/bin/activate && python3 main.py data/input/Booksample/corfra/corfrapdf.pdf --book --max-chapters 3
```
（`--pdf-mode` 未指定。規則②により見開きスキャン→VLM ルートが選ばれる。VLM 実呼び出しの
ため API コストが発生する。1章のみに絞るため `--max-chapters 3`＝前付け＋実章1で止める。）
Expected: 正常終了し `corfrapdf_p2.md`（または該当セッションの出力）が生成される。

- [ ] **Step 2: 重複が解消したことを確認する**

出力 `_p2.md` の「1 Arbitrary Location」章について、次を確認する。

Run:
```bash
grep -c "scorn on their keenness" <出力_p2.md のパス>
grep -c "A Corsican Whole" <出力_p2.md のパス>
```
Expected: それぞれ **英語原文で1、日本語訳で1**（原文＋訳で計2、修正前は計4）。
出力形式により数え方が変わるため、実際の出現箇所を目視し「同一段落が連続して2回」
出ていないことを確認する。修正前の症状（byte 同一の 1,375 字段落が2連続）が
無ければ合格。

- [ ] **Step 3: 図版ページの VLM 出力を確認する**

該当セッションの `state/<session_id>/phase1_preprocessor.json` を開き、図版ページ
（元 page_idx=3 付近、"A crucetta" キャプションのページ）に対応する chunk の text が、
前ページ本文の再転写ではなくキャプション相当（`figure i.i.` 等）または空であることを確認する。

Run:
```bash
source venv/bin/activate && python3 -c "
import json, glob
f = sorted(glob.glob('state/*/phase1_preprocessor.json'))[-1]
d = json.load(open(f))
chunks = d if isinstance(d, list) else d.get('chunks', d.get('elements', []))
for c in chunks:
    t = (c.get('text') or '')
    if 'crucetta' in t.lower() or 'corsica' in t.lower():
        print(c.get('page_idx'), repr(t[:120]))
"
```
Expected: キャプション行のみ（前ページの長い本文が再出現していない）。

- [ ] **Step 4: 結果を記録する**

Step 2/3 の結果（重複が消えたか、図版ページ出力の中身）を Task 4 のドキュメント記述用に控える。
重複がまだ出る場合は**コミット済みの Task 1/2 は保持しつつ**、原因を報告して先に進まない
（プロンプトが効いていない/文脈が誤って繰り返されている等の切り分け）。

---

### Task 4: ドキュメントの更新

spec §4。`.claude/hooks/check_management_logs.sh` が要求する管理ログ更新を含む。

**Files:**
- Modify: `docs/management/troubleshooting_log.md`（I-21 エントリ 261-282 に解決注記）
- Modify: `docs/management/requirements_log.md`
- Modify: `docs/ARCHITECTURE.md`（VLM ルート説明にスライディング 2-up の記述があれば単ページ方式へ）

- [ ] **Step 1: troubleshooting_log の I-21 を解決として更新する**

`docs/management/troubleshooting_log.md` の I-21 エントリ末尾に、`I-22` と同じ形式の
解決ブロックを追加する（Task 3 の実測値を反映）。

```markdown
> **[2026-07-20 解決]** ブランチ `feature/chapter-boundary-adjudication` で解決。真因は
> **実装が元設計から逸脱していたこと**だった。元設計の意図は「画像は現ページのみ渡し、
> 前ページは OCR テキストをヒントとして渡す」だったが、実装は 2-up 画像結合
> (`_merge_images_horizontal`) になっており、図版ページ（抽出対象=右がほぼ空白・隣=左が
> 本文だらけ）で VLM が「右だけ」の指示に反して左（前ページ）を書き起こしていた。
> 対策は挙動（なぜ VLM が左を書くか）ではなく**構造**を潰す方針を採り、VLM に渡す画像を
> 常に現ページ1枚だけにした。前ページ文脈は second image ではなくネイティブテキストで
> 渡す（`build_prev_contexts` → `VLM_SINGLE_PAGE_PROMPT` の {prev_context}）。1画像に
> 対象ページしか入らないため、隣ページの書き起こしが構造的に起きない。図版ページ検出の
> ヒューリスティックは採らなかった（真の引き金は「図版であること」ではなく「対象が隣より
> 極端に文字が少ないこと」で、章の短い頁も巻き込む雑な signal になるため。構造を潰せば
> 検出は不要）。文脈にネイティブテキストを使うのは並列処理を直列化させないため。実PDF検証で
> corfra「1 Arbitrary Location」章の重複段落・重複見出しが解消したことを確認。詳細は
> `docs/superpowers/specs/2026-07-20-vlm-single-page-ocr-design.md`。
```

- [ ] **Step 2: requirements_log に判断根拠を追記する**

`docs/management/requirements_log.md` に追記する。

```markdown
## 2026-07-20: VLM 単ページ OCR ＋ テキスト文脈（I-21 解決）

`core/engine/p1_ingest/ocr_manager.py` / `pdf_ingester.py` を、VLM に現ページ1枚のみ渡す
方式へ戻した（2-up 画像結合を廃止）。

- **なぜ構造を潰すか（検出ヒューリスティックを採らないか）**: I-21 の真の引き金は「図版
  ページであること」ではなく「抽出対象ページが隣の文脈ページより極端に文字が少ないこと」。
  図版ページはその極端例にすぎず、章の最初/最後の短い頁も文字が少ない。「文字数が少ない=
  図版」という検出は的を外す。VLM がなぜ隣を書き起こすかの挙動理由も推定に留まり、推定
  依存の対策（プロンプト強化・図版検出）は脆い。確実な構造条件（1画像に2ページ入れて対象が
  空だと隣を書く）を潰す＝1画像に対象ページしか入れない、が最も堅牢。
- **なぜ文脈にネイティブテキストを使うか（VLM 出力でなく）**: 前ページの VLM 出力を文脈に
  すると N←N-1 の依存で並列処理が直列化し書籍 OCR が実測 ~10 倍遅くなりうる。ネイティブ
  テキストは fitz から即時取得でき並列を維持できる。継続判定に必要なのは「前ページが途中で
  終わったか」程度でネイティブテキスト末尾で足りる。
- **元設計への回帰**: 2-up 結合は元々の設計意図（画像は現ページのみ／前ページはテキスト
  ヒント）からの実装逸脱だった。本変更は仕様の新設ではなく実装の是正である。
```

- [ ] **Step 3: ARCHITECTURE.md の VLM ルート記述を確認・更新する**

Run: `grep -n "スライディング\|2-up\|見開き結合\|process_page_vlm\|VLM ルート\|sliding" docs/ARCHITECTURE.md`
2-up 結合・スライディングウィンドウを「前ページ画像との結合」として説明している箇所が
あれば、「現ページ1枚＋前ページのネイティブテキスト文脈」に更新する。該当が無ければ変更不要
（その旨を報告）。

- [ ] **Step 4: コミット**

```bash
git add docs/management/troubleshooting_log.md docs/management/requirements_log.md docs/ARCHITECTURE.md
git commit -m "$(printf 'docs: I-21 を解決として記録（VLM 単ページ化）\n\ntroubleshooting_log の I-21 に解決注記（真因=2-up 結合が元設計から逸脱・\n図版ページで隣を書き起こし。対策=単ページ画像＋テキスト文脈で構造を潰す）。\nrequirements_log に判断根拠（検出ヒューリスティックを採らない理由・文脈に\nネイティブテキストを使い並列維持する理由）。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## 完了後の扱い

本計画の完了後、同一ブランチの I-26/I-27 と合わせて `golden-verification` を1回だけ実行する
（spec §3.3）。継続判定が視覚（2-up）からテキスト文脈に変わるため、見出し構造
（英語 nested / 日本語 parallel・章統合・除外セクション）に退行が無いことをここで最終確認する。
その後 `superpowers:finishing-a-development-branch` でマージ判断に入る。
