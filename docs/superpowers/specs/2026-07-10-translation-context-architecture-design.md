# 翻訳コンテキスト供給アーキテクチャ：4層モデルと段階実装（Spec A 改訂版）

**ステータス**: 設計確定・実装未着手（Stage 1 から段階実装）
**起案日**: 2026-07-10
**起案者**: shufujita（brainstorming via Claude）
**位置づけ**: `2026-07-07-book-mode-resume-prompts-design.md`（Spec A）を**置換**する上位スペック。Spec A の書籍モード改修を Stage 1 に取り込みつつ、対象を「両モードの翻訳コンテキスト供給」へ拡張する。
**関連**: `troubleshooting_log.md` I-9〜I-14 / `docs/model_optimization.md` §5（2026-07-10 トークン収支調査）/ `2026-07-10-translation-context-research-notes.md`（ウィンドウ設計の文献調査）/ 2026-05 人文系翻訳品質向上プロジェクト（G4 保留分の再起動を含む）

---

## Context

### 監査の核心（2026-07-10 再監査で確定）

p2workflowy の設計思想は「レジュメ・中心概念など**理解のための情報を踏まえて翻訳する**」だが、現行コードではこれが**どちらのモードでも実現されていない**。

- **論文モード**: Phase 2 レジュメは Phase 3 見出し抽出と出力冒頭表示のみに使われ、翻訳プロンプトの `{resume_content}` は**常に空文字**（`phase4_translate.py:32` — 配線漏れ）。
- **書籍モード**: Phase 4 で節レジュメを別途生成して注入するが、背景は章レジュメ止まりで、書籍全体レジュメは `resume_content=None`（`book_manager.py:211` — 「全体要約で章要約が上書きされる」という誤解に基づく意図的断絶）。

翻訳に実際に届いている文脈は「glossary」と「直近 3 件×200 字のスライディングウィンドウ」のみ。

### トークン・コスト制約の再確認（`model_optimization.md` §5）

- 使用モデル（`gemini-3.5-flash` / `gemini-3.1-flash-lite`）はともに入力上限 1,048,576 tok。現状の翻訳 1 バッチは中央値 ~2,100〜2,800 tok で、**入力側の制約は事実上ない**（約 200 倍の余裕）。
- レジュメ（~5,500 字）毎バッチ注入 = +$0.09〜0.14/論文、ウィンドウ 10 件×500 字化 = +$0.07〜0.11/論文。**「3 件×200 字」という切り詰めにトークン上の根拠はもうない**。
- `gemini-3.5-flash` は GA 化で $1.50/$9.00 に 3 倍値上げ（Lite 比 6 倍）→ モデル戦略（後述）の動機。

### ユーザーの意図（正典・拡張版）

> 理解に必要な情報（レジュメ・中心概念・術語の定義・論証構造）を踏まえた上で翻訳する。書籍では、全体レジュメを背景に各章を一つの論文のようにレジュメ化し、両者を踏まえて章を翻訳する。この構造を論文モード・書籍モードで統一する。

---

## 採用アーキテクチャ：翻訳コンテキストの 4 層モデル

翻訳プロンプトに流す文脈を 4 層に整理し、これを両モード共通の標準形とする。

| 層 | 内容 | 論文モード | 書籍モード | 実装 |
|---|---|---|---|---|
| ① 大域 | レジュメ | 論文レジュメ | 書籍レジュメ＋章レジュメ | **Stage 1** |
| ② 術語 | 統合用語レイヤー（glossary ＋ local_definitions、定義が優先） | 論文内定義＋glossary | 書籍全体＋章の用語 | **Stage 2** |
| ③ 論証位置 | argument_tree による「この節の論証上の役割」 | 節ごと条件注入 | 章ごと生成・節ごと注入 | **Stage 3** |
| ④ 局所 | スライディングウィンドウ（**連続した**直前訳文） | 直前 ~2,000 字 | 同左 | **Stage 1** |

- ①④は全バッチに一律注入（トークン試算上、条件注入で節約する必要なし）。③のみ節単位の条件注入（2026-05 の「条件注入」原則）。
- ②は「local_definition ＝ 定義文つき・高優先度の glossary エントリ」とみなして単一レイヤーに統合する（詳細は Stage 2 方針）。

### ロードマップと評価原則

```
Stage 1（レジュメ配線＋ウィンドウ拡大＋死コード整理）
  → 比較読み（translation_review_checklist.md, NST ゴールデン）
  → モデル A/B（3.5-flash vs 3.1-flash-lite, 新パイプライン上で）
  → Stage 2（統合用語レイヤー, 別スペック起案）
  → 比較読み
  → Stage 3（argument_tree, スキーマ実験→別スペック起案）
```

- **1 Stage につき文脈源の変更は 1 種類**（Stage 1 の①④同梱のみ例外：同方向・低リスク・コスト無視可のため、ユーザー判断で同梱確定）。
- 評価は**比較読み**（自動メトリック不使用、2026-05 の原則踏襲）。
- Stage 2 以降は前 Stage の比較読み結果を設計入力とする（例: 語彙平準化が解消済みなら Stage 2 の抽出範囲を縮小できる）。

---

## Stage 1 スペック（実装対象）

Spec A の書籍モード改修を継承し、論文モード配線とウィンドウ拡大を加える。

### 目標状態

```
【論文モード】
Phase 2: SUMMARY_PROMPT_ronbun → resume_content（現状維持）
Phase 4: 翻訳コンテキスト = 論文レジュメ（★新規配線）

【書籍モード】
Phase 0: BOOK_SUMMARY_PROMPT（旧 GLOBAL_SUMMARY_PROMPT）→ book_resume
   ★ book_manager が各章に book_resume を渡す（断絶復活）
各章 Phase 2: CHAPTER_SUMMARY_PROMPT（新設）
   入力 = 章全文 ＋ book_resume（<book_context> 背景）→ 章レジュメ 1 本
各章 Phase 4: 翻訳コンテキスト = book_resume ＋ 章レジュメ
   ★ generate_section_resume / SECTION_SUMMARY_PROMPT は廃止

【共通】
Phase 4 ウィンドウ: 連続した直前訳文 ~2,000 字（現行「3 件×200 字の断片」方式を廃止）
TRANSLATION_PROMPT: 未配線の {context_guide} スロットを削除
```

### プロンプト最終形

| プロンプト | 変更 | 用途 |
|---|---|---|
| `BOOK_SUMMARY_PROMPT` | `GLOBAL_SUMMARY_PROMPT` をリネーム | Phase 0：全書籍レジュメ専用 |
| `CHAPTER_SUMMARY_PROMPT` | **新設** | 書籍 Phase 2：章 1 本（章全文＋book_resume 背景） |
| `SUMMARY_PROMPT_ronbun` | 変更なし | 論文 Phase 2：論文レジュメ→見出し抽出 |
| `TRANSLATION_PROMPT` | `{context_guide}` スロット削除 | Phase 4（`{resume_content}` の意味づけを「上位レジュメ」に明確化） |
| ~~`SECTION_SUMMARY_PROMPT`~~ | **削除** | （`generate_section_resume` 廃止に伴い） |
| ~~`SUMMARY_PROMPT`~~ | **削除** | 汎用フォールバック（発火実績なし） |

`CHAPTER_SUMMARY_PROMPT` の要件は Spec A の記載（「書籍の一章として、book_context を背景に、この章のみを一つの論文のようにレジュメ化」フレーム・long-context 構成・grounding・常体 4000〜5000 字・Markdown 見出し形式）をそのまま踏襲する。

### コード変更点

1. **coreprompts.json**: 上記リネーム・新設・削除。Summary 系キーを階層順（BOOK → CHAPTER → 論文）に整理。`TRANSLATION_PROMPT` から `{context_guide}` セクションを除去。`@lru_cache` のため変更後プロセス再起動。
2. **Phase 0（book_manager.py）**: `GLOBAL_SUMMARY_PROMPT` 参照を `BOOK_SUMMARY_PROMPT` に更新。章ループ `run_pipeline(..., resume_content=None)`（:211）を `resume_content=self.global_resume` に変更。誤解に基づく既存コメント（:204）を削除。
3. **Phase 2（phase2_meta.py）**: 書籍分岐（:64-69）を `CHAPTER_SUMMARY_PROMPT` に変更し、`resume_context`（=book_resume）を `<book_context>` に注入。書籍モードの章テキストは原則サンプリングせず全文投入（上限超のみ従来サンプリングにフォールバック）。論文分岐のフォールバックを `prompts["SUMMARY_PROMPT_ronbun"]` 必須参照に単純化。`metrics_metadata` の `section="global_resume"` を `chapter_resume` に改称。
4. **Phase 4（pipeline.py / phase4_translate.py / llm_client.py）**:
   - `generate_section_resume`（llm_client.py:519）とその呼び出し（`process_section_modular` の `if is_book:` ブロック）、300 字フォールバック（llm_client.py:540 付近）を削除。
   - `run_phase4` に翻訳コンテキスト引数を追加し、`pipeline.py` から渡す：論文モード = Phase 2 の `resume_content`、書籍モード = `book_resume ＋ 章レジュメ` の連結（見出し付きで区別。book_resume 空なら章レジュメのみ）。
   - `translate_batch` の `{resume_content}` にこれを注入。`context_guide` 引数と `.format()` の該当箇所を削除。
5. **ウィンドウの連続化・拡大（p4_translate/prompt_builder.py）**: `format_previous_translation` を「直近 3 件の `role=="p"` を各 200 字に切り抜く」方式から、「**確定済み訳文を末尾から遡り、段落を丸ごと（切り抜きなし）合計 ~2,000 字まで連結**する」方式に変更。上限はファイル先頭の定数（例 `WINDOW_MAX_CHARS = 2000`）。`role=="p"` フィルタは維持（見出し・注は文体連続性の参照にならないため）。根拠: 断片方式は研究・実務に先行例がなく、連続文脈＋要約＋用語台帳が標準形（詳細は `2026-07-10-translation-context-research-notes.md`）。既訳を文脈に使う方式のエラー伝播（訳語ブレの増幅）リスクは Stage 2 の用語レイヤーが対策になる。
6. **統合（state_integrator.py）**: 死コード削除（`add_chapter` / `_generate_consolidated_resume` / `integrate` / `run_integration_test` / `_apply_prefix_to_ids` / `chapter_resumes` / `chapter_titles` / `self.chapters`、未定義 `BookExporter` 参照）。本番経路 `integrate_to_book` は変更なし。
7. **Phase 5（phase5_export.py）**: 変更なし想定（`phase2_meta.json` の `resume_content` を描画する配線は保たれる）。

### テスト / 検証

- **単体**: プロンプトキー変更・関数削除・`run_phase4` シグネチャ変更に伴うテスト更新。`CHAPTER_SUMMARY_PROMPT` のロード・スロット注入、翻訳コンテキスト連結（book_resume 有無 × モード）の単体テスト追加。`python3 -m pytest tests/unit/ -q` 全合格維持。
- **ゴールデン検証**: `golden-verification` skill に従い AL/NST（論文・構造回帰なし）と `Booksample`（書籍・完走＋各章「## レジュメ」＋巻頭書籍レジュメ）を確認。エクスポート不変条件（References 除外・Appendix 保持）維持。
- **比較読み**: `docs/translation_review_checklist.md` に基づき、NST で Stage 1 前後の訳文を比較。レジュメ文脈の効果（語彙・文脈整合）とウィンドウ拡大の効果を確認。ここでの結果が Stage 2 の設計入力・モデル A/B の土台になる。

### リスク / 留意

- **削除の安全性**: Spec A のリスク節を踏襲——(1) 削除直前に再 grep で参照ゼロを再確認、(2) 削除コミットと挙動変更コミットを分離。
- **レジュメ品質の翻訳への波及**: レジュメが翻訳の入力になるため、Phase 2 の品質要求が上がる。レジュメ生成失敗・空の場合は「なし」で縮退し従来動作に一致させる。
- **論文モードの挙動変化**: 翻訳出力が変わるのは意図どおりだが、ゴールデン理想出力との比較は「構造」で行い、訳文の質は比較読みで判定する（理想出力の訳文と一致しないこと自体は不合格ではない）。
- **管理ログ**: `core/` 変更を含むため `requirements_log.md` / `troubleshooting_log.md` への追記を実装コミットに含める。

---

## Stage 2 方針：統合用語レイヤー（着手確定・別スペックで起案）

glossary（訳語対応表）と local_definitions（論文内の術語定義）を**単一の用語レイヤー**に統合する。

- **データモデル**: `{en, ja, definition?, source, priority}`。優先度は `local_definition ＞ ユーザー glossary.csv ＞ 書籍全体用語集 ＞ 章キーワード`。
- **抽出**: 新プロンプトを増やさず、Phase 2 の既存キーワード抽出を「本文中で明示的に定義・特殊用法されている語は定義文も添える」形に拡張する。
- **注入**: 翻訳プロンプトの「# 用語集」1 セクションに一本化し、定義がある語のみ定義文を添える。
- **狙い**: 2026-05 比較読みで確認された語彙平準化（displace →「ずらす」等）への直接対策。craft 指示ではなくコンテキスト注入なので、失敗時は既存 glossary 動作に縮退する。
- **起案タイミング**: Stage 1 の比較読み後。平準化の残存度合いで抽出の積極性を調整する。

## Stage 3 方針：argument_tree（着手確定・別スペックで起案）

- **前提実験**: JSON スキーマ妥当性の小実験を先行させる（深読モード SPEC の「実装前にスキーマ妥当性実験必須」方針を踏襲）。スキーマ設計が Stage 3 スペックの最初のタスク。
- **2026-05 時点の確定事項を踏襲**: 書籍モードでは章ごとに生成／`phase2_argument_tree.json` に独立保存／Phase 4 へは節単位の**条件注入**／抽出失敗時は既存挙動に縮退。

## モデル戦略（Stage 1 完了後に実施）

- **本命はハイブリッド構成**: レジュメ生成（論文レジュメ／書籍全体レジュメ／章レジュメ）のみ `gemini-3.5-flash`、それ以外（翻訳含む）を `gemini-3.1-flash-lite`。レジュメは 1 回生成されて全翻訳バッチに配られるため強いモデルの効果が増幅され、翻訳は呼び出し回数が多いため lite 化の節約効果が最大、というレバレッジの非対称性に基づく。
- **無料モードでも成立**: 3.5-flash の無料枠（目安 ~10 RPM / ~250 RPD）に対し、レジュメ呼び出しは論文 1 回・書籍 1＋章数回で十分収まる。
- **A/B 手順**: Stage 1 実装後の新パイプライン上で「現行（全部 3.5-flash）vs ハイブリッド」の 2 アーム比較読み（NST）。ハイブリッド合格なら有料・無料両モードの既定に採用。不合格時の切り分け用に「lite 一色」アームを予備として温存。
- **実装**: TierManager（429/503 の全体ダウンシフト）とは直交する**用途別モデルルーティング**（例: `coreprompts.json` に `DEFAULT_MODEL_RESUME` キー追加）。Stage 1 には含めず、A/B のタイミングで導入する。
- **根拠**: GA 値上げで価格差 6 倍（`model_optimization.md` §5）。文脈供給（Stage 1）の強化が弱いモデルの品質を底上げする「コンテキスト設計への投資をモデル格下げの原資にする」仮説を検証する。
- **注意**: Stage 1 の前にモデルを切り替えると品質変化の原因（モデルか文脈か）が切り分け不能になるため、順序を守る。切替時は `coreprompts.json` と `docs/model_optimization.md` を同時更新（CLAUDE.md の整合ルール）。

---

## スコープ外

- **Spec B（VLM 適応ルーティング）**: 書籍章処理の full_vlm 固定見直し。疎結合につき本スペックと独立。
- **深読モード**（`docs/superpowers/specs/2026-06-10-deep-reading-mode-shelved.md`、旧ルート SPEC.md）: 棚上げ確定済み（2026-07-06）。
- **章間の確定訳語フィードバック機構**: 章の並列処理と衝突するため今回見送り（global_glossary ＋ Stage 2 統合レイヤーで代替し、必要性を再評価）。
- **スライディングウィンドウのさらなる拡大・セクション冒頭への文脈供給**: Stage 1 の比較読みで「つながりの悪さ」が残る場合のみ再検討。
