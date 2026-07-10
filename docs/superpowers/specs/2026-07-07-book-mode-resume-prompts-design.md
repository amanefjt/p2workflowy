# 書籍モードのレジュメ／Summary系プロンプト整理：設計（Spec A）

**ステータス**: **置換済み（superseded, 2026-07-10）** — 本スペックの内容は `2026-07-10-translation-context-architecture-design.md` の Stage 1 に統合・拡張された（レジュメ配線の両モード統一・ウィンドウ連続化を追加）。実装はそちらを正とする。（旧ステータス: 設計確定・実装未着手）
**起案日**: 2026-07-07
**起案者**: shufujita（brainstorming via Claude）
**位置づけ**: 下流（プロンプト整理）の実装スペック。上流の VLM 適応ルーティング（Spec B, 未起案）とは疎結合。着手はこちらを先行させる。
**関連**: `troubleshooting_log.md` I-9〜I-14 / `requirements_log.md` 2026-07-07 の2エントリ

---

## Context

`core/coreprompts.json` に Summary 系プロンプトが複数（`GLOBAL_SUMMARY_PROMPT` / `SECTION_SUMMARY_PROMPT` / `SUMMARY_PROMPT` / `SUMMARY_PROMPT_ronbun`）あり、用途が曖昧で「もう使っていないものがあるのでは」という疑問が発端。書籍モードの情報受け渡し（Phase 0→2→3→4→統合）をコードで全面監査した結果、**意図と実装の乖離が複数**判明した（`troubleshooting_log.md` I-9〜I-14）。本スペックはその対策設計。

### ユーザーの意図（正典）

> 書籍全体のレジュメを作り、各章はそれを背景に踏まえつつ**あたかも一つの論文であるかのように**レジュメ化し、書籍レジュメ＋章レジュメの両者を踏まえて各章を翻訳する。

### 監査で判明した現状（書籍モード）

```
Phase 0 (book_manager, PDFが健全な時のみ)
  全書籍テキスト → GLOBAL_SUMMARY_PROMPT → global_resume（＋KEYWORD→glossary）
  → global_context.json に保存。使い道は最終エクスポートの巻頭表示のみ

各章 run_pipeline(is_book=True, pdf_mode="full_vlm", resume_content=None)
  ★ global_resume は章に渡らない（I-9: resume_content=None で断絶）
  ├ Phase 1: VLM OCR → チャンク（Markdown見出し入り）
  ├ Phase 2: generate_resume が GLOBAL_SUMMARY_PROMPT を1章に流用（I-10）
  │    → 章resume_content（構造化には未使用）
  │    ＋ DNA・キーワード抽出
  ├ Phase 3: structure_nodes_by_markdown で構造化（I-11: 章resume不使用）
  └ Phase 4: 節ごとに generate_section_resume（I-12: 粒度・スロット名が乖離）
       book_meta_reference ← Phase2章resume（書籍全体レジュメではない）
       → 節resume → 翻訳コンテキスト

統合 integrate_to_book: 各章出力ファイルを積み上げ＋巻頭にglobal_resume
  ★ add_chapter / _generate_consolidated_resume / integrate は死コード（I-13）

Phase 5: resume_content を各章「## レジュメ」として出力に描画（I-14）
```

### 重要な前提（検証済み）

- **書籍モードの構造化は章レジュメに依存しない。** 常に `full_vlm` で `structure_nodes_by_markdown`（または ChapterParser/TOC フォールバック）が担う。`extract_headings_from_resume` は Paper Mode 専用。したがって書籍モードでは Phase 2 章レジュメと Phase 4 節レジュメが**冗長な二重生成**。
- **上流の VLM ルート分岐（Spec B）とは疎結合。** レジュメは Phase 1 チャンクのテキストを消費するだけで、そのチャンクを VLM/Docling どちらで作っても透明。routing を将来変えても本スペックの設計は変わらない。
- **Paper Mode は本スペックの変更対象外**（`SUMMARY_PROMPT_ronbun` で章レジュメ→見出し抽出という既存挙動を維持）。

---

## ゴール / 非ゴール

**ゴール**
- 書籍モードの章レジュメを **1 本に統合**し、ユーザーの意図（書籍レジュメ＋章レジュメで翻訳）を素直に実装する。
- Summary 系プロンプトの役割・命名・配置を明確化し、用途不明・死コードを解消する。
- プロンプト数を**増やさず、むしろ減らす**。

**非ゴール**
- VLM 適応ルーティング（Spec B）。full_vlm 固定は本スペックでは触らない。
- Paper Mode の挙動変更。
- 深読モード（別 SPEC.md）との統合。

---

## 採用する設計

書籍モードの章レジュメを **Phase 2 に 1 本**へ集約する（Phase 4 の節ごと生成は廃止）。

```
Phase 0: BOOK_SUMMARY_PROMPT（旧GLOBAL, 全書籍）→ book_resume
   │  ★ book_manager が各章に book_resume を渡す（resume_content 断絶を復活）
   ▼
各章 run_pipeline(is_book=True)
   ├ Phase 2: CHAPTER_SUMMARY_PROMPT（新）
   │    入力 = 章の全文 ＋ book_resume（<book_context> 背景）
   │    → 章レジュメ 1 本
   │    用途① Phase4 翻訳コンテキスト  用途② Phase5「## レジュメ」表示
   │    ＋ DNA・キーワード抽出（現状維持）
   ├ Phase 3: VLM Markdown 構造化（現状維持・レジュメ不使用）
   └ Phase 4: 翻訳コンテキスト = book_resume ＋ 章レジュメ
        ★ generate_section_resume / SECTION_SUMMARY_PROMPT は廃止
   ▼
統合 integrate_to_book（現状維持）＋ 死コード削除
```

これで Paper Mode（Phase 2 レジュメ→翻訳文脈）と書籍モードの構造が**揃う**。書籍固有の追加は「book_resume を背景に織り込む」ことだけ。

### なぜ Phase 2 に集約するか（Phase 4 生成としない理由）

- 書籍モードでは章レジュメは構造化に不要なので、生成タイミングを Phase 3 の後にする必要がない。
- Phase 2（meta 生成フェーズ）にレジュメを置くのが自然で、Phase 5 の描画配線（`phase2_meta.json` を読む）も現状のまま。
- Paper Mode と同じ「Phase 2 レジュメ → 翻訳文脈」構造に統一でき、書籍固有の分岐が減る。

### 「章全文を見る」ことの担保（旧・節ごとの弱点の解消）

現状の Phase 2 は `_sample_text` で head+tail サンプルを渡している。書籍モードでは章は書籍全体より十分小さいので、**章全文**を渡して節間の論理接続を捉えられるようにする（サンプリングは書籍モードの章に対しては原則無効化。上限文字数を超える極端な章のみ従来のサンプリングにフォールバック）。

---

## プロンプト最終形

| プロンプト | 変更 | 用途 |
|---|---|---|
| `BOOK_SUMMARY_PROMPT` | `GLOBAL_SUMMARY_PROMPT` をリネーム | Phase 0：全書籍レジュメ（全書籍専用に限定） |
| `SUMMARY_PROMPT_ronbun` | 変更なし | Paper Mode Phase 2：論文レジュメ→見出し抽出 |
| `CHAPTER_SUMMARY_PROMPT` | **新設** | 書籍モード Phase 2：章 1 本（章全文＋book_resume 背景） |
| ~~`SECTION_SUMMARY_PROMPT`~~ | **削除** | （`generate_section_resume` 廃止に伴い） |
| ~~`SUMMARY_PROMPT`~~ | **削除**（フォールバック参照の整理を伴う, 下記） | 汎用フォールバック |

> `SUMMARY_PROMPT` は現状 `phase2_meta.py:66,69` の両分岐で `prompts.get("...", prompts["SUMMARY_PROMPT"])` のフォールバックとして参照されている。書籍分岐（:66）は `CHAPTER_SUMMARY_PROMPT` 使用に変わるため参照が消える。論文分岐（:69）は `SUMMARY_PROMPT_ronbun` を必須参照（`prompts["SUMMARY_PROMPT_ronbun"]`）に単純化すればフォールバックが不要になり、`SUMMARY_PROMPT` を削除できる。`SUMMARY_PROMPT_ronbun` は常に存在するためフォールバックは実質発火しておらず、削除は安全。

**JSON 内の配置**：Summary 系キーを階層順に並べ替える（BOOK → CHAPTER → 論文 → 汎用）。`coreprompts.json` は JSON でコメント不可のため、順序と命名で意味を表現する。

### `CHAPTER_SUMMARY_PROMPT` の要件

- フレーム：「これは**書籍の一章**である。`<book_context>` を**背景知識**として踏まえつつ、**この章のみ**を一つの論文のようにレジュメ化せよ」と明示（I-9 の旧「全体要約で上書き」を文言で封じる）。
- スロット：`{expertise}` / `{book_context}`（=book_resume, 空なら「なし」）/ `{context_guide}` / `{text}`（=章全文）。
- 長文構成：`{text}` を `<source_document>` タグで囲みプロンプト先頭近くへ、ルール類は末尾へ（`requirements_log.md` 2026-06-07 の long-context 方針を踏襲）。
- grounding：見出しは `<source_document>` の表記と一字一句照合する確認指示（既存 Summary 系と同様）。
- 出力：常体・4000〜5000 字目安・Phase 5 が描画できる Markdown 見出し形式。

---

## コード変更点（Phase 順に段階実装）

各ステップは独立にテスト可能。Phase 0→2→4→統合の順で「ちょっとずつ」進める。

1. **coreprompts.json**
   - `GLOBAL_SUMMARY_PROMPT` → `BOOK_SUMMARY_PROMPT` にリネーム。
   - `CHAPTER_SUMMARY_PROMPT` を新設。
   - `SECTION_SUMMARY_PROMPT` を削除。
   - `SUMMARY_PROMPT` を削除。あわせて `phase2_meta.py:69` の論文分岐フォールバックを `prompts["SUMMARY_PROMPT_ronbun"]` に単純化（書籍分岐 :66 は次項で `CHAPTER_SUMMARY_PROMPT` に置換され参照が消える）。`_apply_prefix_to_ids` を含む state_integrator 死コード群は参照無しを確認済み（下記 5）。
   - キー順を階層順に整理。
   - ※ `@lru_cache` のためプロセス再起動が必要（CLAUDE.md 既知事項）。

2. **Phase 0（book_manager.py）**
   - `_generate_global_context` / キャッシュ読込の `GLOBAL_SUMMARY_PROMPT` 参照を `BOOK_SUMMARY_PROMPT` に更新。
   - 章ループの `run_pipeline(..., resume_content=None)`（:211）を `resume_content=self.global_resume` に変更（I-9 の断絶復活）。

3. **Phase 2（phase2_meta.py）**
   - 書籍モード分岐（:64-69）を `GLOBAL_SUMMARY_PROMPT` → `CHAPTER_SUMMARY_PROMPT` に変更。
   - `resume_context`（=book_resume）を `<book_context>` に注入する構築に更新。
   - 書籍モードの章テキストは原則サンプリングせず全文投入（上限超のみフォールバック）。
   - `metrics_metadata` の `section="global_resume"` を実態に合う名称へ（例 `chapter_resume`）。

4. **Phase 4（phase4_translate.py / llm_client.py）**
   - `generate_section_resume`（llm_client.py:519）と、その呼び出し（process_section_modular 内 `if is_book:` ブロック）を**削除**。
   - 翻訳コンテキストを `book_resume ＋ 章レジュメ` にする配線。`book_resume` を `run_phase4` に新規引数として渡す（現状 Phase 4 は未受領、`pipeline.py` の `run_phase4(...)` 呼び出しに追加）。
   - `SECTION_SUMMARY_PROMPT` 依存の除去。`llm_client.py:540` の 300 字フォールバックは削除。

5. **統合（state_integrator.py）**
   - 死コード削除：`add_chapter` / `_generate_consolidated_resume` / `integrate` / `run_integration_test` と、それらだけが使う `chapter_resumes` / `chapter_titles` / `self.chapters` / `_apply_prefix_to_ids`（`add_chapter` 内と自己再帰のみで使用＝死コード確定済み）。未定義の `BookExporter` 参照も消える。
   - 本番経路 `integrate_to_book` は変更なし（引き続き `global_resume` を巻頭に）。

6. **Phase 5（phase5_export.py）**
   - 変更なし想定（`phase2_meta.json` の `resume_content` を読み描画）。章レジュメの供給元が Phase 2 のままなので配線は保たれる。実装後に章「## レジュメ」が出力に残ることを確認（I-14）。

---

## テスト / 検証

- **単体**: `tests/unit/` の該当テスト更新。プロンプトキー名変更・`generate_section_resume` 削除に伴う参照を修正。新設 `CHAPTER_SUMMARY_PROMPT` のロード・スロット注入の単体テスト追加。
- **回帰**: `python3 -m pytest tests/unit/ -q` 全合格を維持。
- **ゴールデン検証**: `golden-verification` skill に従い、書籍サンプル（`data/input/Booksample/`）で完走確認。各章に「## レジュメ」が残り、書籍レジュメが巻頭に付くこと、参照除外/Appendix 保持などのエクスポート不変条件を確認。
- **意図の確認**: 章レジュメが book_resume の文脈を踏まえた内容になっているか（断絶復活の効果）を出力で目視。

---

## リスク / 留意

- **削除の安全性（重要）**: 本スペックは削除が多い（`SECTION_SUMMARY_PROMPT` / `generate_section_resume` / `SUMMARY_PROMPT` / state_integrator 死コード群）。静的解析の誤りは削除で最も痛いため、(1) **削除直前に再 grep で参照ゼロを再確認**する（コードは経緯で変わりうる）、(2) **削除コミットと挙動変更コミットを分離**して revert しやすくする。なお削除の根拠（死コード・冗長二重生成）はコードの論理＋grep で確定済みであり、経験的実行は確度を上げる任意の保険であって正しさのゲートではない。
- **章全文投入のトークン増**: 章が極端に大きい場合の上限とフォールバックを設ける。
- **book_resume 未生成時**: Phase 0 がスキップされた（PDF 破損等で `global_resume=""`）ケースでは `<book_context>` を「なし」にして章単独レジュメとして成立させる。
- **プロセス再起動**: プロンプト変更は `@lru_cache` により再起動必須。
- **管理ログ追記**: `core/` 変更を含むため、実装コミットで `troubleshooting_log.md` / `requirements_log.md` の追記漏れに注意（`check_management_logs.sh` が警告）。

---

## スコープ外（別課題）

- **Spec B（VLM 適応ルーティング）**: 書籍章処理の full_vlm 固定見直し。コスト実測＋構造品質 A/B を伴う独立スペック。Spec A 完了後のクリーンな下流の上で着手が望ましい。疎結合につき手戻りなし。
