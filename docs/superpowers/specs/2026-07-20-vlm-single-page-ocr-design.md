# VLM 単ページ OCR ＋ テキスト文脈（I-21）設計

- 日付: 2026-07-20
- 対象: `core/engine/p1_ingest/ocr_manager.py`、`core/engine/p1_ingest/pdf_ingester.py`
- 関連課題: I-21（VLM スライディング OCR が図版ページを直前ページの内容で埋める重複）
- 同一ブランチ先行分: `2026-07-19-chapter-boundary-adjudication-design.md`（I-26/I-27、完了）

## 0. 問題の本質

現在の VLM ルートは、ページ N を OCR する際に前ページ N-1 と N を横に貼り合わせた
2-up 画像 `[N-1 | N]` を作り、VLM に「右半分（N）だけ読め、左は文脈」と指示している
（`ocr_manager.py::_merge_images_horizontal` ＋ `VLM_CONTINUITY_PROMPT`）。

**確実な構造的原因**: 1枚の画像に2ページ分が入っており、抽出対象（右）が図版ページで
ほぼ空白・隣（左）が本文でびっしり、という状態になると、VLM は「右だけ」の指示に反して
左（N-1）を書き起こす。結果、N-1 の本文が N の出力として二重に出る。

実測（corfra `1 Arbitrary Location` 章・見開き分割後の単ページ章PDF）:

| 2-up 画像 | 左（idx2・文脈） | 右（idx3・抽出対象） |
|---|---|---|
| `[本文 \| 図版]` | 3327 字の本文 | 22 字（キャプションのみ） |

VLM が左を書き起こし、`chunk_9`（page_idx=2）と `chunk_12`（page_idx=3）が
byte-for-byte 同一の 1,375 字になった。同一章内で2箇所（"scorn on their keenness"
段落、"A Corsican Whole" 見出し）発生しており、図版ページを含む章では構造的に再現する。

**なぜ「なぜ VLM が左を書くか」に依存した対策を採らないか**: VLM が指示を無視する
挙動の理由は推定に留まる。推定に依存した対策（プロンプト強化・図版ページ検出）は脆い。
確実なのは構造条件（1画像に2ページ入れて対象が空だと隣を書く）なので、対策は
**構造を潰す**——1画像に対象ページしか入れない——を採る。

**真因は実装が元設計から逸脱していたこと**: 元々の設計意図は「画像は N ページのみ渡し、
N-1 は画像ではなく OCR 済みテキストをヒントとして渡す」だった。2-up 画像結合は
この意図からの逸脱であり、本スペックは実装を元設計へ戻す修正である。

## 1. 設計

### 1.1 方針

VLM に渡す画像を**常に現ページ1枚だけ**にする。前ページの文脈は second image では
なく**テキスト**（前ページのネイティブテキスト末尾）でプロンプトに渡す。画像の中に
対象ページしか存在しないため、隣を書き起こすこと自体が物理的に不可能になる。図版
ページも短いページも将来の未知のケースも、検出ヒューリスティックなしで一律に直る。

**なぜ検出ヒューリスティックを使わないか**: 当初「図版ページを検出して単ページ化」を
検討したが、真の引き金は「図版ページであること」ではなく「抽出対象が隣より極端に
文字が少ないこと」である。図版ページはその極端な例にすぎず、章の最初/最後の短い
ページも文字は少ない。「文字数が少ない＝図版」という検出は的を外している。構造を
潰せば検出は不要になる。

**なぜ文脈にネイティブテキストを使うか（VLM 出力ではなく）**: 前ページの VLM 出力を
文脈にすると「N の処理に N-1 の出力が必要」という依存が生じ、現在の並列処理
（`asyncio.gather` ＋ semaphore=10）が直列化する（書籍 OCR で実測 ~10 倍遅くなりうる）。
ネイティブテキストは `fitz` から即座に取れるため並列を維持できる。継続判定に必要なのは
「前ページが文の途中で終わったか」程度であり、スキャン由来で崩れていても切れ目は判る
ため、ネイティブテキスト末尾で十分。

### 1.2 `ocr_manager.py`

`process_page_vlm` のシグネチャを変更する。

```python
async def process_page_vlm(
    self, current_img: Image.Image, prev_context_text: str = "",
    page_idx: int = 0, session_dir: Optional[Path] = None,
) -> str:
```

- `prev_img` を `prev_context_text`（文字列）に置き換える。
- `_merge_images_horizontal` の呼び出しを廃止し、`current_img` を単独で VLM に渡す。
- `_merge_images_horizontal` メソッドは他に呼び出し元が無くなるため削除する。
- プロンプト選択:
  - `page_idx == 0`（先頭ページ、前文脈なし）→ `VLM_FRONT_MATTER_PROMPT`（既存を単ページ用に
    調整。「左が1ページ目、右が2ページ目」等の 2-up 前提の文言を除く）。
  - `page_idx >= 1` → 新規 `VLM_SINGLE_PAGE_PROMPT`（`{prev_context}` に前ページ末尾テキストを差す）。
- キャッシュキーを「単一画像バイト列 ＋ 文脈テキスト」の MD5 にする（文脈が変われば
  出力も変わりうるため、画像だけでは不十分）。

### 1.3 新プロンプト `VLM_SINGLE_PAGE_PROMPT`

`ocr_manager.py` の `OCRManager` クラス定数として追加する（既存の
`VLM_FRONT_MATTER_PROMPT` / `VLM_CONTINUITY_PROMPT` / `VLM_BASE_RULES` と同じ場所・形式）。要旨:

- これは書籍/論文の**1ページ**の画像である。このページの印刷テキストを Markdown で抽出せよ。
- `<previous_page_context>{prev_context}</previous_page_context>` は**直前ページの末尾**である。
  これは、このページの1行目が前ページの続きか新しい見出しかを判断するためだけに使う。
  **この文脈テキストを出力に繰り返してはならない。**
- 前ページから文章が続いている場合、このページ冒頭に見出しタグ `#` を付けない。
- **図版ページの注意**: このページが図版（写真・図表）主体で、キャプションだけしか
  印刷テキストが無いことがある。その場合はキャプションのみを出力し、本文が無ければ
  空を返せ。画像の内容を描写してはならない。印刷されている文字だけを抽出せよ。
- 既存の `VLM_BASE_RULES`（見出し判定・本文/脚註の扱い・出力形式）は流用する。

### 1.4 `pdf_ingester.py`

スライディングウィンドウのタスク生成を変更する。

- 画像構築ループで、各論理画像の**物理ページ対応マップ**を持つ。非分割ページは
  `image_src_page[i] = 物理ページ i`。見開き分割ページは両半分が同じ物理ページを指す。
- 各論理ページ i の**前文脈テキスト**を、`image_src_page[i-1]` の物理ページの
  ネイティブテキスト末尾（例: 末尾 500 字程度、定数化）とする。書籍章 PDF は
  画像↔物理ページが 1:1 のため正確。見開き分割の半ページでは物理ページ全体の
  テキストを近似的に使う（文脈ヒントのため許容）。
- タスクを `range(0, N)` に変更する（現在は `range(1, N)`）。
  - `i == 0`: `_vlm_slice_job(0, images[0], prev_context_text="")`（前文脈なし）。
  - `i >= 1`: `_vlm_slice_job(i, images[i], prev_context_text=<前ページ末尾>)`。
- `_vlm_slice_job` のシグネチャを `(lc_idx, curr_img, prev_context_text)` に変更し、
  `process_page_vlm(curr_img, prev_context_text=prev_context_text, ...)` を呼ぶ。
- 先頭ページが独立タスクになるため、`idx == 1` を「page_0_1」として特別扱いしていた
  結果格納ロジックを、各ページ 1 タスク 1 要素の素直な形に整理する。
- 並列性（`asyncio.gather`・semaphore）は変更しない。前文脈はネイティブテキストで
  即座に用意できるため直列化しない。

### 1.5 削除・整理されるもの

- `ocr_manager.py::_merge_images_horizontal`（呼び出し元が消えるため削除）。
- 2-up 結合経路と、それを前提とした `VLM_CONTINUITY_PROMPT` の「左＝前ページ/右＝現ページ」
  の枠組み（`VLM_SINGLE_PAGE_PROMPT` で置換）。`VLM_CONTINUITY_PROMPT` / `VLM_PROMPT`
  エイリアスの扱いは実装時に呼び出し元を確認して決める（未使用なら削除、使用なら置換）。
- `pdf_ingester.py` の「idx==1 が page 0 と 1 を両方抽出」する変則処理。

## 2. データフロー

```
pdf_ingester:
  画像構築（見開き分割含む）→ images[], image_src_page[]
  各 i について prev_context = tail(native_text[image_src_page[i-1]])  （i==0 は ""）
  タスク: process_page_vlm(images[i], prev_context, page_idx=i)  ← 全 i 並列
    ↓
ocr_manager.process_page_vlm:
  page_idx==0 → FRONT_MATTER プロンプト（単ページ）
  page_idx>=1 → SINGLE_PAGE プロンプト（{prev_context} 差し込み）
  current_img 単独を VLM へ（結合なし）
  キャッシュキー = md5(image_bytes + prev_context)
    ↓
  Markdown テキストを返す（前ページを繰り返さない）
```

## 3. テストと検証

### 3.1 実PDF 受け入れテスト（本修正の成否そのもの）

`data/input/Booksample/corfra/corfrapdf.pdf` を `--book --max-chapters 3` で処理し、
`1 Arbitrary Location` 章について:

- "scorn on their keenness" を含む段落が出力 `_p2.md` に**1回だけ**出現する（修正前は2回）。
- "A Corsican Whole" が**1回だけ**出現する（修正前は2回）。
- 図版ページ（元 page_idx=3）の VLM 出力が、前ページ本文ではなくキャプション相当
  （`figure i.i.` 等）または空である。`phase1_preprocessor.json` の該当 chunk で確認する。

VLM 実呼び出しを伴うため API コストが発生する。`--max-chapters 3` で 1 章のみに絞る。

### 3.2 単体テスト（`tests/unit/`）

`ocr_manager`:
- `process_page_vlm` が `_merge_images_horizontal` を呼ばず `current_img` 単独を VLM へ渡すこと
  （`call_gemini_async` をモックし、渡された画像が結合画像でない＝入力画像と同一寸法であることを検証）。
- `page_idx==0` は FRONT_MATTER プロンプト、`page_idx>=1` は SINGLE_PAGE プロンプトを選ぶこと。
- `SINGLE_PAGE` プロンプトに `prev_context_text` が差し込まれること。
- キャッシュキーが画像＋文脈から構成され、文脈が異なれば別キーになること。

`pdf_ingester`:
- 前文脈が前ページの物理ページのネイティブテキスト末尾から組まれること。
- タスクが `range(0, N)` で、先頭ページ（i==0）が前文脈なしで生成されること。
- 画像→物理ページマップが、見開き分割時に両半分を同じ物理ページへ対応させること。

### 3.3 `golden-verification`

**I-21 完了後に1回だけ実行する**（同一ブランチの I-26/I-27 と合わせ、実 API コストを
2回払わないため。2026-07-19 ユーザー判断）。継続判定が視覚（2-up）からテキスト文脈に
変わるため、見出し構造（英語 nested / 日本語 parallel・章統合・除外セクション）に退行が
無いことをここで最終確認する。

## 4. ドキュメント

- `docs/management/troubleshooting_log.md` の I-21 を解決として更新（真因＝実装が元設計から
  逸脱・2-up 結合が構造的に隣ページを書き起こしていた。対策＝単ページ画像＋テキスト文脈で
  構造を潰した）。
- `docs/management/requirements_log.md` に判断根拠（構造を潰す方針・検出ヒューリスティックを
  採らない理由・文脈にネイティブテキストを使い並列を維持する理由）を記録。
- `docs/ARCHITECTURE.md` の VLM ルート説明にスライディング 2-up の記述があれば単ページ方式へ更新。

## 5. 受容するリスク

- **継続判定の品質低下**: 前ページの視覚レイアウトが使えなくなり、テキスト文脈のみで
  1行目の見出し/継続を判断する。スキャンでネイティブテキストが皆無の書籍では文脈が
  空になり、単ページの視覚的手がかり（見出しは太字・番号付きで目立つ）のみに頼る。
  まれにページ境界で見出し誤判定が残りうるが、壊滅的な重複（本スペックが直す対象）
  よりは軽微。`golden-verification` で構造退行が無いことを確認する。
- **見開き分割半ページの文脈近似**: 分割された半ページには物理ページ全体のネイティブ
  テキストを文脈として使う（半分ずれる）。文脈は継続判定のヒントにすぎず、抽出対象
  そのものではないため実害は限定的。書籍章 PDF（VLM ルートの主対象）は 1:1 で正確。
