# 翻訳コンテキスト Stage 2：統合用語レイヤー（Spec）

**ステータス**: 設計確定・実装未着手
**起案日**: 2026-07-11
**起案者**: shufujita（brainstorming via Claude）
**位置づけ**: 正本 `docs/superpowers/specs/2026-07-10-translation-context-architecture-design.md`（4層モデル）の **Stage 2「②術語＝統合用語レイヤー」** を具体化する下位スペック。正本の「Stage 2 方針：統合用語レイヤー」を土台に、コード監査で判明した実装の実像に合わせて精緻化する。
**関連**: `requirements_log.md`（2026-07-11 Stage 1 実装完了・A/B 完了）/ `troubleshooting_log.md` I-17（レジュメ MAX_TOKENS 修正）/ `docs/model_optimization.md` §5（トークン収支）/ `docs/translation_review_checklist.md`（比較読み）

---

## Context

### Stage 1 の到達点（前提）

- Stage 1 実装・main マージ済み（2026-07-11, 211 tests）。「レジュメを踏まえて翻訳する」を論文・書籍両モードで初めて実体化。翻訳プロンプトの `<resume_content>` に実レジュメが届き、ウィンドウは連続 ~2,000 字化。
- モデル A/B 完了。**ハイブリッド（レジュメのみ `gemini-3.5-flash`・他 lite）を採用決定**（Arm B の訳質が良い、藤田氏判定）。用途別ルーティング（`DEFAULT_MODEL_RESUME`）は実装済み（commit 3c747ba）だが、既定化は未実施。
- A/B 観測（Stage 2 設計入力）: 3.5-flash レジュメは 11,450 字と目標「4000〜5000 字」を超過。ただし「これくらい必要な内容だったのかもしれない」という所感。
- Stage 1 比較読みで確認された積み残し: **語彙平準化**（`displace →「ずらす」` 等、術語の特殊用法が標準訳へ平坦化される）。Stage 2 の直接の標的。

### コード監査で判明した Stage 2 の実像

正本は「glossary と local_definitions を単一の用語レイヤーに統合する」と記すが、コードを読むと**機構はより小さく、根本原因は型の固定にある**。

- `KEYWORD_EXTRACTION_PROMPT`（論文 Phase 2 / 書籍 Phase 0）は**既に `{en, ja, definition}` を抽出済み**。論文モードでは `phase2_meta.json` の `keywords_data` に、書籍モードでは `global_glossary.csv` の `definition` 列（`book_manager.py:136` の `DictWriter`）に、定義が保存されている。
- ところが glossary パイプライン全体が `dict[str, str]`（en→ja）型に固定されており、**定義が 2 箇所で捨てられている**:
  1. `core/config.py::load_glossary_csv` — CSV の 3 列目（definition）を読まず、`{en: ja}` のみ返す。
  2. `core/phase4_translate.py:96-98` — `keywords_data` を `master_glossary[en] = kw.get("ja")` と平坦化し、definition を落とす。
- 結果、翻訳プロンプトの `<glossary>` セクション（`format_glossary`, `core/engine/p4_translate/prompt_builder.py:17-24`）には **訳語対応 `- en: ja` だけ**が届き、「なぜその訳語か（定義・特殊用法）」は一切届いていない。

**したがって Stage 2 の中核は「新しい抽出を足す」ことではなく、用語レイヤーの型を構造化型に広げ、既に抽出済みの定義を翻訳プロンプトまで配線し、抽出プロンプトの積極性を調整すること**である。

### ユーザーの意図（正典・Stage 2 部分）

> 理解に必要な情報のうち「術語の定義・特殊用法」を翻訳に届ける。glossary（訳語対応）と local_definitions（論文内の術語定義）を単一の用語レイヤーに統合し、定義がある語は定義文も翻訳の背景として渡す。狙いは語彙平準化への直接対策。

---

## 決定事項（brainstorming 2026-07-11）

| 論点 | 決定 | 根拠 |
|---|---|---|
| **① 抽出の積極性** | **中庸＋特殊用法込み** | 明示定義語に加え、本文で標準訳とズレる特殊用法の語も定義付きで拾う。displace 型平準化の主因は「定義されていないが特殊に使う語」であり、これを拾わないと平準化に効かない。件数上限＋「定義できない語は空」で暴発を抑える。 |
| **② ハイブリッド既定化** | **Stage 2 で既定化** | A/B で採用決定済み。`DEFAULT_MODEL: lite` ＋ `DEFAULT_MODEL_RESUME: gemini-3.5-flash` の 2 値変更。`model_optimization.md` 同時更新。 |
| **③ レジュメ目標長** | **据え置き（Stage 2 後に再評価）** | 「1 Stage につき文脈源の変更は 1 種類」原則。用語レイヤーとレジュメ短縮を同一 Stage で変えると比較読みで効果が切り分けられない。Stage 2 比較読み後に「用語レイヤーがあるならレジュメを短くできるか」を別途検証。 |

---

## アーキテクチャ

### 中核：用語レイヤーを構造化型に広げ、1 ユニットへ隔離する

glossary パイプラインの `dict[str, str]` 固定を解き、定義・ソースを運ぶ構造化型を導入する。Stage 2 の変更を**単一の隔離されたユニット**に閉じ込め、単体テスト可能にする。

**新モジュール `core/engine/p4_translate/term_layer.py`**

```
@dataclass
class TermEntry:
    en: str
    ja: str
    definition: str | None = None
    source: str = "local"   # "local" | "glossary_csv" | "book"

def build_term_layer(
    keywords_data: list[dict],      # phase2_meta.json 由来（en/ja/definition）
    user_glossary: dict[str, str],  # load_glossary_csv 由来（ユーザー訳語）
    book_glossary: list[dict] | None = None,  # 書籍モードの global_glossary
) -> list[TermEntry]:
    ...

def format_term_layer(entries: list[TermEntry]) -> str:
    ...  # 現 format_glossary を置換
```

- 現在 `phase4_translate.py:89-98` にインラインで散らばる用語集組み立て（`load_glossary_csv` → `keywords_data` マージ）を `build_term_layer` に集約する。
- `format_glossary`（`prompt_builder.py`）は `format_term_layer` に置換。`TranslationPromptBuilder` は `dict[str,str]` の代わりに `list[TermEntry]` を受け取る。
- `load_glossary_csv`（`config.py`）は **ユーザー訳語 en→ja を返す現状のまま**（後方互換・依存最小化）。定義は `keywords_data`／書籍 `global_glossary` から供給する。書籍モードで global_glossary.csv の definition 列を通す配線は「判断保留 ②」で実装方式を確定する。

### データモデル：フィールド別マージ（正本の精緻化）

正本の優先度表記 `local_definition ＞ ユーザー glossary.csv ＞ 書籍全体用語集 ＞ 章キーワード` は「エントリ丸ごとの優先」と読めるが、それだとユーザーが選んだ訳語を本文語が上書きしてしまい、ユーザー glossary.csv の権威性と矛盾する。**フィールド別マージ**に確定する:

- **dedup キー**: `en.lower()`
- **訳語 ja**: `glossary_csv > 抽出`（ユーザー glossary.csv が最優先。既存 `merge_with_glossary` の挙動を維持）
- **定義 definition**: 本文抽出（`source="local"`）のみが供給。書籍モードでは `book_glossary`（`source="book"`）の定義も採用。ユーザー glossary.csv は訳語のみで定義を持たない（CSV 2 列運用が主）。
- **source の優先**（同一 en が複数ソースに出た場合の定義の採否）: `local > book`。ユーザー glossary_csv は ja のみ寄与し definition 競合に関与しない。

### 抽出プロンプト（積極性＝中庸＋特殊用法込み）

`KEYWORD_EXTRACTION_PROMPT` を改修（新プロンプトは増やさない）:

1. **抽出範囲**: 明示的に定義・導入された専門用語に**加えて**、本文で標準的な日本語訳とズレる特殊用法・著者特有の含意で使われている語も抽出対象に含める。
2. **定義の付与**: その語がこの文献で持つ特定の含意を `definition` に簡潔に記す（1 文・目安 60 字程度、Plan で上限確定）。
3. **グラウンディング**: 定義・特殊用法が本文から特定できない語は `definition` を空（`null` または `""`）にする（既存 DNA/抽出プロンプトの「無理に値を作らない」方針を踏襲、幻覚抑制）。
4. **件数上限**: 暴発（glossary 長大化・トークン増・幻覚）を防ぐため上限を明示（具体値は Plan の判断保留 ①で NST を用いて確定）。

これにより `displace` のような語が「ここでは物理的移動ではなく、確立した秩序・位置からの『転位・置き換え』の意」といった定義付きで抽出され、翻訳プロンプトに届く。

### 注入・描画

`format_term_layer`:

- セクション見出しは「# 用語集 (Glossary)」を維持。説明文に「定義が付された語は、この文献での特定の含意を示す。訳語はこれを踏まえて選ぶこと」を追加。
- **定義なし**: `- en → ja`
- **定義あり**: `- en → ja：{definition}`
- **順序**: 定義あり（高価値・特殊用法）を先頭に、定義なしを後に並べる。
- トークン: 中庸抽出（数十語、うち定義付きは一部）で増分は入力上限 1,048,576 tok に対し無視可能（正本 §5、約 200 倍余裕）。

### モデル既定・レジュメ長（決定の反映）

- **`core/coreprompts.json`**: `DEFAULT_MODEL: gemini-3.5-flash → gemini-3.1-flash-lite`、`DEFAULT_MODEL_RESUME: "" → gemini-3.5-flash`。`@lru_cache` のため変更後プロセス再起動。
- **`docs/model_optimization.md`**: ハイブリッド既定化を反映（CLAUDE.md の「実装とドキュメントは同時更新」ルール）。
- **レジュメ字数指示**: `SUMMARY_PROMPT_ronbun` / `CHAPTER_SUMMARY_PROMPT` の分量指示は**変更しない**（論点③据え置き）。
- **Web/無料パスの含意**: `DEFAULT_MODEL_RESUME` が設定されると `get_default_model("resume")`（`llm_client.py:43-47`）はティア非追従で 3.5-flash を返す。無料でもレジュメは 3.5-flash になる。無料枠（目安 ~10 RPM / ~250 RPD）に対しレジュメ呼び出しは論文 1 回・書籍 1＋章数回で収まる想定（正本 §モデル戦略）。管理者パスコード経由でサーバ側キーを使う場合の無料枠消費は判断保留 ⑤として Plan に明記。

---

## コード変更点（サマリ）

1. **`core/engine/p4_translate/term_layer.py`（新設）**: `TermEntry` / `build_term_layer` / `format_term_layer`。
2. **`core/phase4_translate.py`**: 用語集組み立てインライン（:89-98）を `build_term_layer` 呼び出しに置換。`TranslationPromptBuilder` へ `list[TermEntry]` を渡す。
3. **`core/engine/p4_translate/prompt_builder.py`**: `format_glossary` を `format_term_layer` 利用へ置換。`glossary` 属性の型を `list[TermEntry]` に。
4. **`core/coreprompts.json`**: `KEYWORD_EXTRACTION_PROMPT` 改修（積極性・定義・グラウンディング・件数上限）。`DEFAULT_MODEL` / `DEFAULT_MODEL_RESUME` の 2 値変更。
5. **書籍モードの定義配線**: `global_glossary`（定義付き）を `build_term_layer` の `book_glossary` へ供給。実装方式（`load_glossary_csv` 拡張 vs term_layer 側で CSV 直読み vs 状態オブジェクト経由）は判断保留 ②で確定。
6. **`docs/model_optimization.md`**: ハイブリッド既定化を反映。
7. **管理ログ**: `requirements_log.md` / `troubleshooting_log.md` に Stage 2 実装を追記（`core/` 変更を含むため）。

---

## テスト / 検証

- **単体**: `term_layer` の `build_term_layer`（フィールド別マージ・dedup・source 優先・book_glossary 有無）と `format_term_layer`（定義有無 × 順序）の単体テスト新設。`phase4_translate` / `prompt_builder` の型変更に伴うテスト更新。既存 glossary 関連テスト更新。`python3 -m pytest tests/unit/ -q` 全合格維持。
- **ゴールデン検証**: `golden-verification` skill に従い AL/NST（論文・**構造回帰なし**）を確認。エクスポート不変条件（References 除外・Appendix 保持）維持。用語レイヤーは翻訳の背景注入のみで構造には影響しないことを確認。
- **比較読み**: `docs/translation_review_checklist.md` に基づき、NST で Stage 2 前後（**ハイブリッド固定**）の訳文を比較。`displace` 等の平準化語が定義注入で改善するかを重点確認。この結果が (a) Stage 3（argument_tree）の設計入力、(b) レジュメ長再評価（論点③の宿題）、(c) 抽出積極性の微調整、の土台になる。

---

## リスク / 留意

- **抽出の暴発**: 「中庸＋特殊用法込み」は保守的抽出より件数・幻覚リスクが上がる。件数上限＋「定義できない語は空」で抑える。失敗時（抽出過多・的外れ定義）は既存 glossary 動作（訳語のみ）に縮退でき、翻訳が壊れることはない。
- **型変更の波及**: `dict[str,str]` → `list[TermEntry]` は `phase4_translate` / `prompt_builder` に波及。`load_glossary_csv` は現状維持で後方互換を保つことで波及を最小化。
- **書籍モードの検証**: 定義配線（判断保留 ②）は書籍モードに固有。Booksample での完走＋用語レイヤーへの定義注入を実地確認する（比較読みは論文 NST が主）。
- **モデル既定変更の影響**: 有料パスが「全部 3.5-flash」→「ハイブリッド」に変わり、翻訳出力が変化する。これは A/B で採用済みの意図した変化。ゴールデンは構造で判定し、訳質は比較読みで判定する。
- **管理ログ**: `core/` 変更を含むため `requirements_log.md` / `troubleshooting_log.md` への追記を実装コミットに含める（`.claude/hooks/check_management_logs.sh` が注意喚起）。

---

## スコープ外

- **Stage 3（argument_tree）**: 論証位置レイヤー。スキーマ実験先行。別スペック。
- **レジュメ長締め直し**: 論点③据え置き。Stage 2 比較読み後に再評価。
- **章間の確定訳語フィードバック機構**: 章の並列処理と衝突するため見送り（正本のスコープ外を踏襲）。用語レイヤー統合で必要性を再評価。
- **Spec B（VLM 適応ルーティング）**: 疎結合につき独立。

---

## 判断保留ポイント（実装 Plan で明記・実装セッションが迷わないための一覧）

1. **抽出件数上限の具体値**: `KEYWORD_EXTRACTION_PROMPT` の上限（例 20 / 30 / 40 語）。NST で試走し、平準化カバレッジと glossary 長のバランスで確定。
2. **書籍 global_glossary の定義配線方式**: `global_glossary.csv` の definition 列（3 列目）を term_layer へ通す方法。候補: (a) `load_glossary_csv` を 3 列対応に拡張、(b) `term_layer` 側で CSV を直読み、(c) `book_manager` の状態オブジェクト（`self.global_glossary`）を pipeline 経由で渡す。後方互換・依存最小の観点で選定。
3. **format の並び順**: 「定義あり先頭」を既定とするが、source 順・アルファベット順との比較は軽微。実装デフォルトを置き、比較読みで必要なら調整。
4. **定義文の長さ上限**: プロンプトでの目安（1 文・~60 字）と、逸脱時の扱い（切り詰めるか否か）。
5. **Web 無料枠のレジュメ 3.5-flash 消費**: 管理者パスコード経由でサーバ側キーを使う無料モードで、レジュメが 3.5-flash 無料枠を消費する点の許容判断。ユーザー自身のキー利用時は当人の枠なので問題なし。
