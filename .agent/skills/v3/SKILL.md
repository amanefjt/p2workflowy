---
name: p2workflowy-v2
description: >
  p2workflowy V2 の開発・デバッグ・改修に使用する。
  PDF → 日本語翻訳パイプラインの全フェーズ（Phase 0〜5）、
  Book Mode / Paper Mode の挙動、coreprompts.json の編集、
  Gemini API クライアントのティア管理、および Unlabeled Section バグの修正を
  依頼するときにロードする。
---

# p2workflowy V2 開発スキル（完全版）

## 1. プロジェクト概要とディレクトリ構成

**p2workflowy V2** は、英語PDF（学術論文・書籍）を日本語に翻訳し、
Markdown と Workflowy 形式で構造化出力するパイプラインである。

### ディレクトリレイアウト

```
p2workflowy/              ← PROJECT_ROOT
├── .env                  ← GEMINI_API_KEY / GOOGLE_API_KEY
├── core/                 ← CORE_DIR: Pythonパッケージ本体
│   ├── __init__.py
│   ├── coreprompts.json  ← 全LLMプロンプト・モデル・用途別設定
│   ├── config.py         ← 設定管理・SessionState
│   ├── models.py         ← RawChunk / TreeNode
│   ├── llm_client.py     ← Gemini APIラッパー（用途別モデル取得）
│   ├── pdf_ingester.py   ← Phase 0
│   ├── phase1_preprocess.py
│   ├── phase2_meta.py
│   ├── phase3_structure.py
│   ├── phase4_translate.py
│   ├── phase5_export.py
│   └── pipeline.py       ← オーケストレーター
├── data/
│   └── glossary.csv      ← {英語,日本語} の用語集
├── tests/                ← ユニットテスト群
├── web/                  ← Web UI 静的アセット（HTML/CSS/JS）
├── state/                ← STATE_DIR: セッションごとの中間ファイル
├── archive/              ← 非推奨・アーカイブ済みファイル
├── main.py               ← CLIエントリーポイント
├── server.py             ← FastAPI Webサーバー
└── probe_fonts.py        ← PDFフォント構造確認スクリプト
```

**セッションID:** デフォルトは入力ファイルのステム名（`Path(input_path).stem`）。
`state/` 直下の古いセッションは `MAX_STATE_SESSIONS=10` を超えると mtime 順に自動削除される。

---

## 2. 処理フロー全体図

```
[PDF / TXT / DOCX ファイル]
          |
  Phase 0: pdf_ingester.py
    PyMuPDF 高速抽出 + Gemini VLM OCR ハイブリッド
    → state/{id}/extracted_from_pdf.txt
          |
  Phase 1: phase1_preprocess.py
    ノイズ除去・段落化・チャンク化・用語保護
    → state/{id}/phase1_clean.json  (List[RawChunk])
          |
  Phase 2: phase2_meta.py
    LLMによるレジュメ生成・キーワード抽出・Glossary マージ
    → state/{id}/phase2_meta.json  ({resume_content, keywords_data})
          |
  Phase 3: phase3_structure.py
    構造化ツリー構築
    Book:  PyMuPDF フォント解析 → LLM TOC 補正
    Paper: レジュメ見出し照合 (match_heading)
    → state/{id}/phase3_structure.json  (List[TreeNode])
    → state/{id}/phase3_sections.json   (Dict[section_key, List[dict]])
          |
  Phase 4: phase4_translate.py
    非同期バッチ翻訳・Book Mode はセクション要約も生成
    → state/{id}/phase4_translation.json  (List[TreeNode])
    ※ Phase 3 structure.json を翻訳後に上書きする（英語ツリーの確定版）
          |
  Phase 5: phase5_export.py
    Markdown / Workflowy 形式でファイル出力
    → {入力ファイルと同ディレクトリ}/{title}_p2.md
    → {入力ファイルと同ディレクトリ}/{title}_p2.txt
```

---

## 3. データモデル

### RawChunk（phase1 → phase3 入力）

```python
@dataclass
class RawChunk:
    id: int | str        # 段落の連番インデックス
    text: str            # クレンジング済みテキスト
    seq_index: float     # ソート用シーケンス番号（通常 id と同値）
```

### TreeNode（phase3〜5 共通）

```python
@dataclass
class TreeNode:
    id: str | int
    text: str
    role: str            # "h1" | "h2" | "h3" | "p"
    seq_index: float
    children: List[TreeNode] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # Book Mode では metadata["summary"] に章レジュメを格納
```

**role の意味:**

| role | 意味 | Book Mode | Paper Mode |
|---|---|---|---|
| `h1` | PART（部） | あり | なし |
| `h2` | 章 (Chapter) | あり | なし |
| `h3` | 節 (Section) | Phase 4 で生成 | Phase 3 で生成 |
| `p`  | 本文段落 | あり | あり |

### section_key のフォーマット（Phase 3 → Phase 4 の受け渡し）

```
"{node.id}|{heading_title}"
例: "15000|Introduction"
    "unlabeled_0|[Unlabeled Section]"
```

**このフォーマットを変更する場合は `phase3_structure.py` と `phase4_translate.py` の両方を同時に修正する。片方だけ変えると全セクションが翻訳されなくなる。**

---

## 4. SessionState：ファイルパス管理

```python
state = SessionState(input_path, session_id=session_id)

# 各フェーズの中間ファイルパス
state.phase1            # phase1_clean.json
state.phase2            # phase2_meta.json
state.phase3_structure  # phase3_structure.json
state.phase3_sections   # phase3_sections.json
state.phase4            # phase4_translation.json
state.metrics_csv       # ttft_metrics.csv
state.status_json       # status.json

# 進捗更新（CLI ログ + status.json 書き込み）
state.update_status("本文を翻訳中...", 70)

# 古いセッションのクリーンアップ（Pipeline完了後に呼ぶ）
state.cleanup_old_sessions()
```

---

## 5. CLI インターフェース

```bash
python main.py [file...] [オプション]
```

### 主要オプション

| フラグ | 説明 | デフォルト |
|---|---|---|
| `--book` | Book Mode で実行（PDF必須） | Paper Mode |
| `--hybrid-pdf` | PDF 処理をハイブリッドモードで | full_vlm（全ページVLM） |
| `--free` | 無料ティアとして実行（並列数制限） | paid |
| `--resume N` | フェーズ N から再開（1〜5） | 1から |
| `--resume-only` | 各章レジュメ + 英語原文のみ（翻訳なし） | off |
| `--structure-only` | Phase 3 まで実行して停止 | off |
| `--ronbun` | RonbunNihongo モード（日本語訳のみ出力） | p2workflowy |
| `--model` | モデル名を明示指定 | coreprompts.json の DEFAULT_MODEL |
| `--thinking Low\|High` | Thinking Level | High |
| `--title` | 出力ファイルのタイトル（単一ファイルのみ有効） | ファイル名ステム |
| `--glossary` | glossary.csv のパス | data/glossary.csv |

### Book Mode の実行例

```bash
# 基本（有料APIキー使用）
python main.py mybook.pdf --book

# 無料枠で実行（レート制限に配慮）
python main.py mybook.pdf --book --free

# Phase 3 でいったん停止してログ確認
python main.py mybook.pdf --book --structure-only

# Phase 4 から再開
python main.py mybook.pdf --book --resume 4

# TOCキャッシュを消して Phase 3 から再実行
rm state/mybook/phase3_toc.json
python main.py mybook.pdf --book --resume 3
```

---

## 6. Gemini API クライアント詳細

### モデル選択（2026年3月現在）

| 用途 | 推奨モデル | RPD（無料） | 備考 |
|---|---|---|---|
| **Web UI 既定** | `gemini-3.1-flash-lite-preview` | **500** | Thinking:High 必須 |
| **CLI / 高品質** | `gemini-3-flash-preview` | 20 | 有料版推奨 |
| **Legacy** | `gemini-2.0-flash` | × | 現在は終了 |

`DEFAULT_MODEL` は `coreprompts.json` で設定する。

### thinking_level の適用条件

```python
# llm_client.py の実装（同期・非同期共通）
if thinking_level and model and (
    "gemini-3" in model.lower() or "thinking" in model.lower()
):
    config_kwargs["thinking_config"] = types.ThinkingConfig(
        thinking_level=level.upper()  # "LOW" | "HIGH"
    )
```

`gemini-2.x` 系には適用されない。`gemini-3.x` 系でのみ有効。

`generate_section_resume`（章要約生成）は速度優先で `thinking_level="Low"` を固定使用している。
翻訳バッチ（`translate_batch`）は呼び出し元から渡された `thinking_level` をそのまま使う。

### TierManager：自動ダウンシフト

```python
tier_manager = TierManager()  # シングルトン

# 429 エラー検出時に llm_client.py が自動呼び出し
tier_manager.downgrade()
# → current_tier = FREE, was_downgraded = True
```

### apply_tier_settings の実数値

```python
# FREE ティア（--free フラグまたは自動ダウンシフト後）
rate_limiter = AsyncLimiter(1, 4.0)   # 1リクエスト / 4秒
semaphore    = asyncio.Semaphore(1)
settings     = {"max_batch_chunks": 3, "max_batch_chars": 1500}

# PAID ティア（デフォルト CLI）
rate_limiter = AsyncLimiter(100, 60.0)  # 100リクエスト / 60秒
semaphore    = asyncio.Semaphore(15)
settings     = {"max_batch_chunks": 5, "max_batch_chars": 2500}
```

### コスト目安（Gemini 3.1 Flash-Lite, 6万字論文）

| 項目 | コスト（$1=150円換算） |
|---|---|
| 入力トークン（約16万tk） | ~1.8円 |
| 出力トークン（約2万tk） | ~4.5円 |
| **合計** | **約6.3円** |

### デバッグ用ファイル

LLM リクエストのたびに `state/debug_prompt.txt` に最後のプロンプト全文が上書きされる。
マッチングが失敗する場合、このファイルで実際に送信されたプロンプトを確認する。

---

## 7. translate_batch のパース処理

`translate_batch` は以下の3段階フォールバックでレスポンスをパースする。

```
レスポンス
  |
  +-- Step A: JSON配列形式の検出
  |   re.search(r"\[\s*\{.*\}\s*\]", response, re.DOTALL)
  |   キーの正規化: "id" / "chunk_id", "trans" / "text" / "translation"
  |   chunk_id の "chunk_" プレフィックスを removeprefix で除去
  |
  +-- Step B: XMLタグ形式の検出（正常クローズ）
  |   rf"<(?:chunk[ _-]*)?{cid}>(.*?)(?=</(?:chunk[ _-]*)?{cid}>|...)"
  |   <chunk_123>, <chunk 123>, <chunk-123>, <123> すべてに対応
  |
  +-- Step C: 開始タグのみ（出力打ち切り対策）
  |   rf"<(?:chunk_)?{cid}>\s*(.*)"
  |   3000文字未満のみ採用（次のチャンク取り込み防止）
  |
  +-- 最終失敗: "【翻訳失敗】\n{原文テキスト}" を格納してパイプライン継続
```

---

## 8. Phase 0: PDF 取り込みロジック

### 事前品質診断 (Pre-flight Check)
`pipeline.py` にて、PDFのテキスト品質を診断し、Route C (Full VLM) へ自動転送するか判定する。
- **指標**: 異常記号密度（3%以上）または単語断片化（8%以上）。
- **処置**: 破損検知時は `pdf_mode="full_vlm"` に固定（Bipolar Routing/Dirty）。

### ルーティング判定（should_use_vlm / Bipolar Routing）
モードにより戦略が異なる：

1. **Book Mode (書籍モード)**:
   - **Bipolar Routing**: 原則としてページごとの VLM 判定を行わない。
   - Clean PDF の場合: **全ページ Python 高速抽出**（1ページ目も含む）。
   - Dirty PDF の場合: **全ページ VLM OCR** (Route C)。
2. **Paper Mode (論文モード)**:
   - **Hybrid Routing**: 以下の条件を1つでも満たすと部分的に VLM を使用：
     - 1ページ目 (page_num == 0) — 論文タイトル・アブストラクト取得のため。
     - テキスト量が `MIN_TEXT_CHARS=100` 未満。
     - ページ下部20%（`FOOTNOTE_AREA_RATIO=0.80`）に、全体中央値の60%以下（`FOOTNOTE_FONT_RATIO=0.60`）のフォントサイズのスパン（脚注）が存在する。

**キャッシュ:** `state/{id}/extracted_from_pdf.txt` が存在する場合は再抽出をスキップする。
`--resume 1` で再開してもキャッシュが使われる。強制再抽出するにはこのファイルを削除する。

### probe_fonts.py：フォント構造の確認

Book Mode で章タイトルが正しく検出されない場合の診断スクリプト。

```bash
python probe_fonts.py path/to/book.pdf
```

出力内容:
- フォントサイズ分布 TOP10（本文サイズの確認）
- 見出し候補（本文サイズの1.3倍以上またはBold、先頭30件）
- Running Header 候補（ページ上部10%以内のテキスト）

`detect_chapter_font_sizes` の `confirmed_sizes` がこのスクリプトの出力に対応しているかを確認することで、章境界の誤検出/未検出を特定できる。

---

## 9. Phase 3: 構造化ロジック詳細

### Book Mode フロー

```
extract_toc_via_llm()
    PDFの冒頭40ページをLLMに送る
    → キャッシュ: state/{id}/phase3_toc.json
    → 返り値: {"toc": [...], "body_start_page": N}

detect_chapter_font_sizes()
    全ページのフォントサイズを分布分析
    → TOCとの照合で章タイトルサイズを特定（±1.5pt の救済あり）
    → 失敗時フォールバック: 10.8〜20.0pt の全値

extract_book_chapters()
    ページをスキャン
    → Stateful Coalescing: pending_title → current_title
    → STOP_SECTIONS で Notes/References 以降を除外

extract_book_chapters() の前付け処理:
    TOC に page=-1 のエントリ（Preface/Foreword等）がある場合、
    body_start_page 以前のページも走査対象に含める。
    ただしフォントサイズ条件を使わず、TOC タイトルのテキスト一致のみで
    章境界を判定する（ハイブリッド型）。

apply_toc_titles()
    PyMuPDF検出タイトルをTOCで補正
    → ページ差 <= 10 かつタイトル部分一致の場合に上書き

build_tree() [Book Mode]
    chapters → TreeNode(h2) 変換
    → 段落はすべてフラットに子ノード(p)として追加
    → サブセクション分割は Phase 4 で行う
    
```

### Paper Mode フロー

```
extract_headings_from_resume()
    phase2 レジュメからh3見出し候補を抽出
    → メタ見出しフィルタ: "リサーチ・クエスチョン", "核心的主張" 等をスキップ
    → 角括弧 [Heading] から原文見出しを抽出
    → ローマ数字・番号を除去して headings リストを返す

structure_nodes_by_headings()
    各段落の先頭を headings リストに照合
    → match_heading(): normalize 後に startswith で前方一致
    → 一致: 新 h3 セクションを開始
### Route C (VLM Markdown) の階層補正（Demotion Logic）

Route C では、VLM がページ単位の視覚情報に基づき `# ` (Chapter) を出力しますが、文脈不足により単なる節の見出しを章と誤認することがあります。
これを防ぐため、`structure_nodes_by_markdown` 関数にて **プレフィックス・ヒューリスティック** を実施しています。

- **判定基準**: 見出しが `Chapter`, `Part`, `Preface`, `Introduction` 等、特定のチャプター用プレフィックス（`VALID_CHAPTER_PREFIXES`）で始まっているか。
- **降格処理**: プレフィックスを持たない `# ` 見出しは、自動的に `h3` (節) へと降格（Demote）され、直前の章の子要素として組み込まれます。
- **適用条件**: `is_book=True` かつ `pdf_mode="full_vlm"` の場合。

### normalize_heading（全照合処理の基盤）

```python
def normalize_heading(text: str) -> str:
    # \b なしだと "Introduction" の I が剥がれるバグが発生するため必須
    t = re.sub(r'^(?:Chapter\s+)?(?:[IVXLCDM]+\b|[\d\.]+)\s*[:\.]?\s*', '', text, flags=re.I)
    t = re.sub(r'[^\w\s]', '', t)
    return " ".join(t.lower().split())
```

---

## 10. 修正済みの重大バグ

**根本原因:** `SECTION_SUMMARY_PROMPT` が LLM に verbatim コピーを強制できず、
LLM が意訳した見出しを返す → `match_heading` の `startswith` が永遠に失敗する。

```
実際の本文の節:      "An Introduction to the Book"
LLM が生成した見出し: "An Introduction to Relations"  ← 不一致 → Unlabeled
```

**修正1（最優先）: coreprompts.json の `SECTION_SUMMARY_PROMPT` の見出し指示を置き換える**

```
【厳守】各見出しを必ず `# [Original English Heading]` という形式で記述すること。
角括弧内は本文に実際に存在する見出し文字列を一字一句そのままコピーすること。
言い換え・意訳・日本語化は厳禁。本文に存在しない見出しはこの形式で出力しないこと。
```

**修正2: `normalize_heading` に `\b` を追加**（BUG-002 と同じ）

**修正3（安全網）: Phase 4 の `process_section` 内に全落ちフォールバックを追加**

```python
# phase4_translate.py: structure_nodes_by_headings の呼び出し直後
ch_tree, _ = structure_nodes_by_headings(chunk_nodes, ch_headings, exclude_keywords)

if (
    len(ch_tree) == 1
    and ch_tree[0].text == "[Unlabeled Section]"
    and len(ch_tree[0].children) > 0
):
    print_log(f"  [Phase 4] サブセクション構造化失敗: フラットにフォールバック ({section_name[:40]})")
    ch_tree = ch_tree[0].children  # h3 ラッパーを外して p ノードリストに戻す
```

### BUG-002: `normalize_heading` の `\b` 欠落

`Introduction` → `ntroduction` に壊れる問題。

```python
# 修正前（バグあり）
re.sub(r'^(?:Chapter\s+)?(?:[IVXLCDM]+|[\d\.]+)\s*...', '', text, flags=re.I)
#                         ^^^^^^^^^^^^ \b なし

# 修正後
re.sub(r'^(?:Chapter\s+)?(?:[IVXLCDM]+\b|[\d\.]+)\s*...', '', text, flags=re.I)
```

---

## 11. coreprompts.json プロンプト仕様

### プロンプト一覧と用途

| キー | 用途 | 使用フェーズ | Thinking |
|---|---|---|---|
| `GLOBAL_SUMMARY_PROMPT` | 書籍全体レジュメ（8000字目標） | Phase 2 (is_book=True) | High |
| `SUMMARY_PROMPT` | 論文レジュメ（標準、gemini-3.x用） | Phase 2 (is_book=False) | High |
| `SUMMARY_PROMPT_ronbun` | 論文レジュメ（厳格フォーマット、gemini-2.x用） | Phase 2 優先使用 | — |
| `SECTION_SUMMARY_PROMPT` | 章ごとの詳細レジュメ | Phase 4 (is_book=True) | **Low（固定）** |
| `KEYWORD_EXTRACTION_PROMPT` | 専門用語抽出（JSON配列出力） | Phase 2 | High |
| `TRANSLATION_PROMPT` | 本文翻訳（サイトトランスレーション） | Phase 4 | High |
| `EXCLUDE_SECTION_KEYWORDS` | 翻訳除外セクション名リスト | Phase 3 | — |
| `DEFAULT_MODEL` | CLI/高品質用デフォルトモデル | llm_client.py | — |
| `DEFAULT_MODEL_FREE` | Web UI/無料ティア用モデル | llm_client.py | — |
| `DEFAULT_MODEL_VLM` | OCR/VLM 用モデル | pdf_ingester.py | — |

### プロンプト変数一覧

| 変数 | 使用プロンプト | 内容 |
|---|---|---|
| `{expertise}` | 全プロンプト | 専門分野（例: "文化人類学"） |
| `{context_guide}` | SUMMARY系 | 処理指示の補足 |
| `{text}` | SUMMARY系, KEYWORD, SECTION_SUMMARY | 対象テキスト本文 |
| `{section_name}` | SECTION_SUMMARY, TRANSLATION | セクション名 |
| `{resume_content}` | SECTION_SUMMARY, TRANSLATION | 書籍全体レジュメ（コンテキスト） |
| `{glossary_content}` | TRANSLATION | 翻訳用語集 |
| `{previous_translation}` | TRANSLATION | 直前の翻訳結果（スライディングウィンドウ） |
| `{chunk_json}` | TRANSLATION | `<chunk_ID>` タグ付きテキスト群 |

### SECTION_SUMMARY_PROMPT の期待出力構造

LLM にこの構造で返させること。`# [Heading]` 形式が `match_heading` の唯一の入力源。

```markdown
# 1. 全体のリサーチ・クエスチョン

# 2. 全体の核心的主張

# 3. 章内の論理展開（節・項ごとの詳細分析）

# [Original English Heading]
## 中心的な主張
## 論理展開（箇条書きで詳細に）

# [Another Original Heading]
## 中心的な主張
## 論理展開（箇条書きで詳細に）
```

### TRANSLATION_PROMPT の chunk_json 形式

```
<chunk_42>
Some English text to translate here.
</chunk_42>

<chunk_43>
Another paragraph.
</chunk_43>
```

LLM はこのタグを保持したまま翻訳結果を返す。タグの省略・変形は厳禁。
`translate_batch` がタグを解析して `TreeNode.id` と翻訳テキストを紐付ける。

---

## 12. Phase 5: 出力フォーマット仕様

### Book Mode の Markdown 構造

```markdown
# {書籍タイトル}

## 書籍全体のレジュメ
### 1. リサーチ・クエスチョン    ← shift=2 でH3
### 2. 核心的主張               ← H3
### 3. 各章の構成と理論的貢献   ← H3（is_global廃止により統一）
### {原文見出し}               ← レジュメ内の元の # + shift=2

## {章タイトル}                 ← H2（clean_heading_text で [] を除去）
### {章タイトル}のレジュメ
#### （章レジュメ内容）          ← shift=3 で H4

### English text of {章タイトル}
#### {サブセクション見出し}      ← base_level=3, h3 → H4

### {章タイトル}の日本語本文
### {サブセクション見出し}       ← base_level=2, h3 → H3
```

### tree_to_markdown の level 計算

```python
role_num = int(node.role[1:])   # "h3" → 3
level_offset = role_num - 2     # h2=0, h3=1
level = base_level + level_offset
```

- 日本語本文: `base_level=2`
- 英語本文:   `base_level=3`

### clean_heading_text の動作

```python
# "[Forward/Intro]" → "Forward/Intro"
# "# Introduction"  → "Introduction"
cleaned = re.sub(r'^[#\s\[\（]+', '', text)
cleaned = re.sub(r'[\]\）\s#]+$', '', cleaned)
```

---

## 13. デバッグ手順集

### Unlabeled Section が出たときの診断

```bash
# Step 1: セクション構成を確認（Unlabeled が何件あるか）
python3 -c "
import json
with open('state/mysession/phase3_sections.json') as f:
    s = json.load(f)
for k, v in s.items():
    print(f'{k[:60]:60s} | {len(v)} chunks')
"

# Step 2: phase2 レジュメから抽出された見出しリストを確認
python3 -c "
import json, sys
sys.path.insert(0, '.')
from core.phase3_structure import extract_headings_from_resume
with open('state/mysession/phase2_meta.json') as f:
    meta = json.load(f)
headings = extract_headings_from_resume(meta['resume_content'])
for h in headings:
    print(repr(h))
"

# Step 3: debug_prompt.txt でLLMへの実際のプロンプト内容を確認
cat state/debug_prompt.txt | head -100

# Step 4: フォント構造の確認（章タイトル検出の診断）
python probe_fonts.py input.pdf
```

### match_heading デバッグログの一時追加

```python
# phase3_structure.py の match_heading 内に追加
for head in headings:
    norm_head = normalize_heading(head)
    if not norm_head or len(norm_head) < 3:
        continue
    print_log(f"  [match] '{norm_first[:40]}' startswith '{norm_head[:40]}' -> {norm_first.startswith(norm_head)}")
    if norm_first.startswith(norm_head):
        ...
```

### API コスト計測の確認

```bash
# ヘッダー: timestamp, section, batch_id, input_chars, p_tokens, c_tokens, ttft, tps, duration
cat state/{session_id}/ttft_metrics.csv
```

---

## 14. 変更時の影響範囲マトリクス

| 変更内容 | 影響ファイル | 注意点 |
|---|---|---|
| `section_key` フォーマット | `phase3_structure.py`, `phase4_translate.py` | 両方同時変更必須 |
| `normalize_heading` | `phase3_structure.py` 全呼び出し箇所 | `\b` を維持 |
| `SECTION_SUMMARY_PROMPT` | `coreprompts.json`, `phase4_translate.py` | verbatim指示を維持 |
| `chunk_json` タグ形式 | `llm_client.py`（`translate_batch`） | パーサー正規表現も変更 |
| `TreeNode.metadata` のキー | `models.py`, `phase4_translate.py`, `phase5_export.py` | |
| `apply_tier_settings` の並列数 | `llm_client.py` | FREE=1, PAID=15 が現在値 |
| `MAX_STATE_SESSIONS` | `config.py` | デフォルト10 |
| TOC取得範囲（冒頭40ページ） | `phase3_structure.py` `extract_toc_via_llm` | 薄い書籍では削減可 |

---

## 15. 出力の正常/異常チェックリスト

### Book Mode の `_p2.md` で確認するもの

```
OK  ## {章タイトル}           → 章が H2 で列挙されている
OK  ### {章タイトル}のレジュメ → 各章にレジュメがある
OK  #### {サブセクション}      → English text 内に節が H4 で存在する
OK  ### {サブセクション}       → 日本語本文内に節が H3 で存在する

NG  ### Unlabeled Section     → BUG-001/002 が未修正
NG  #### Unlabeled Section    → 同上
NG  ## [Forward/Intro]        → TOC補正が未適用（apply_toc_titles が失敗）
NG  章が1つしかない            → STOP_SECTIONS が早期トリガーまたは chapter_sizes の検出失敗
```

### Unlabeled Section が出たときの最初の確認ポイント

1. `state/{id}/phase2_meta.json` を開き `resume_content` を確認
2. `# [` で始まる行が存在するか → **存在しない場合**: SECTION_SUMMARY_PROMPT の問題
3. 存在する場合、角括弧内のテキストが本文の節タイトルと一致するか → **不一致**: BUG-001
4. 一致している場合、`normalize_heading` 変換後も一致するか → **不一致**: BUG-002