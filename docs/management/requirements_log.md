# Requirements Log: P2Workflowy V3 (Golden Rewrite)

## 会話履歴とユーザー要望の集約

### 2026-04-01 - 04-03: Golden Rewrite 初期フェーズ
- **JSONからの脱却**: Fragile な JSON 出力を廃止し、XMLタグベースのパースへ移行。
- **TierManagerの実装**: 有料/無料版の制限に合わせてバッチサイズを自動調整。
- **Book Modeの確立**: 大規模PDFを章ごとに分割し、並列翻訳した後に統合。

### 2026-04-04: 最終堅牢化と API仕様最適化（TTFTペーシング）
- **Phase 4 直列化の禁止**: Gemini 3 Flash Preview の長大コンテキスト（Thinking: High）発火時に発生する「約4分のサーバー側一斉塩漬け現象」を回避するため、直列化による順次待機（Context Chaining等）は絶対に採用しない（アーキテクチャ制約）。
- **並列相殺 (Global Optimum) の確立**: `max_concurrent_sections = 4` での一斉並列処理（Scatter-Gather）を標準とし、待機時間を並列で一括消化するアプローチを唯一の正解とする。

> **[2026-05-11 訂正]** 上記「約4分の塩漬け現象」は 2026-04-04 時点の一時的な外れ値（サーバー障害 × Phase 1 分割バグの複合）であり、定常現象ではなかった。実測（AL論文 17バッチ × 8回）での avg TTFT は 14〜31s。direct serialization（concurrent=1）が約50%遅いという事実は実証済みのため「直列化を避ける」という結論は変わらないが、理由は「240秒ストールの相殺」ではなく「並列化によるスループット向上」が正しい。詳細は `docs/model_optimization.md` Section 3 参照。
- **Book Mode パス解決の修正**: 統合時に出力ファイルを見失うバグ（`_export` サブディレクトリの問題）を解消。
- **ティア伝播の修正**: `--lite` フラグが Phase 0 (Global Scan) に適用されない問題を、モデル解決の遅延タイミング化によって解決。
- **コード監査**: `main.py`, `book_manager.py`, `state_integrator.py` の引数フローの整合性を確保。
- **スモークテスト**: `chap3relations.pdf` にて書籍モード完走を確認。

### 2026-05-15: ronbunnihongo モード確立・デプロイ構成修正

- **ronbunnihongo モードの仕様確定**: `export_mode="ronbunnihongo"` は、p2workflowy の通常出力から「日本語翻訳（レジュメ＋日本語本文）のみを Markdown で出力」するモード。英語ツリーおよび Workflowy テキスト（`.txt`）は生成されない。内部的には Phase 1〜4 は通常モードと同一処理を実行し、Phase 5 の出力分岐のみ異なる。出力ファイルは `_ronbun.md`。
- **URL ルーティング修正**: Cloudflare Pages で `/ronbunnihongo` にアクセスすると `index.html` が返されていた問題を修正。`web/_redirects` に `/ronbunnihongo /ronbun.html 200`（リライト）を追加。`server.py` にも `@app.get("/ronbunnihongo")` ルートを追加（HF Spaces 側）。
- **デプロイ構成**: GitHub (`origin`) と Hugging Face Spaces (`hf`) は**別リモートで独立管理**。GitHub マージ後に `git push hf main:main` を手動実行することで HF Spaces に反映。Cloudflare Pages は GitHub の `main` ブランチへのマージで自動デプロイ。
- **セキュリティ監査**: 全コードベースを対象に監査を実施。重大な脆弱性なし。`_safe_upload_path()`・`secrets.compare_digest()`・CORS 制限・UUID バリデーションの各実装は正常。

### 2026-06-07: coreprompts.json 要約・翻訳・抽出系プロンプトの構造改善

Anthropic 公式の long-context prompting tips・hallucination 低減ガイドに基づき、`core/coreprompts.json` の主要プロンプトを再構成した。

- **要約系（`GLOBAL_SUMMARY_PROMPT` / `SECTION_SUMMARY_PROMPT` / `SUMMARY_PROMPT` / `SUMMARY_PROMPT_ronbun`）**: 投入テキストは論文最大50万字・書籍最大150万字（Anthropic の言う「2万トークン超の長文書」に該当）であるため、`{text}` を `<source_document>` タグで囲んでプロンプト先頭近くへ移動し、詳細な構成・記述・フォーマットルールは本文ブロックの後（生成直前）にまとめる構成に変更（本文先頭化により応答品質が最大30%向上するという知見に基づく）。あわせて「`# [Original Heading]` / `## 英語節タイトル` を出力前に `<source_document>` の表記と一字一句照合する」という確認指示を追加（grounding 強化・幻覚抑制）。さらに「節と節の間の論理的接続（前節からの展開・次節への接続）を明示する」指示を追加した。
- **翻訳系（`TRANSLATION_PROMPT`）**: `{chunk_json}` の上限は `parallel_translator.py::DEFAULT_MAX_BATCH_CHARS = 11000`（約3,000〜7,000トークン）であり「2万トークン超」の閾値に届かないため、**本文先頭化は適用しなかった**（ルールを把握してから本文を変換するという翻訳タスクの性質上、効果も薄いと判断）。代わりに `<source_chunks>` `<resume_content>` `<glossary>` `<previous_translation>` 等の XML タグで「参考情報（背景文脈）」と「翻訳対象」を明確に分離し、境界の曖昧さを解消するに留めた。Core ルール・hedge/booster の few-shot 例・Strict Tag Protocol の内容は変更していない（過一般化リスクを避けるため）。
- **抽出系（`DNA_EXTRACTION_PROMPT` / `TEXT_STRUCTURE_EXTRACTION_PROMPT` / `TOC_EXTRACTION_PROMPT`）**: 入力規模が小さい（1ページ分のチャンク・最大120チャンク・冒頭15ページ）ため構造はほぼ維持しつつ、`<page1_chunks>` `<source_text>` `<toc_source_pages>` タグで入力データの境界を明示し、「id は `<page1_chunks>` 内に実在する値のみ使用する」「該当する情報が見つからない場合は無理に値を作らず `null` / 空配列を返す」というグラウンディング・ルールを追加した（Anthropic の hallucination 低減ガイドにある "Allow Claude to say I don't know" を JSON 抽出向けに翻案）。
- **判断基準（恒久指針）**: 長文を投入するプロンプトを改修する際は「本文を先頭へ」を機械的に適用せず、まず該当変数（`{text}` 等）の実際の投入上限文字数をコードで確認し、2万トークン規模に達するかどうかで適用可否を判断する。届かない場合は XML タグによる境界明示など、並び替えを伴わない改善に留める。
- **副産物として発見した実装バグ2件**: `meta_analyzer.py` の VLM ヒント連結順序の問題、および `TOC_EXTRACTION_PROMPT` の二重波括弧エスケープの問題。詳細は `troubleshooting_log.md` の I-6・I-7 を参照。

### 2026-06-10: 深読モード（Deep Reading Mode）仕様確定

藤田氏へのインタビュー（AskUserQuestion 6 ラウンド）により新機能「深読モード」の仕様を確定し、`SPEC.md`（リポジトリルート）として文書化した。

- **コンセプト**: 「人文系テクストを『読めた』とは何か」の再定義。(1) 問いの構造レイヤー（二層問いスキーマ: 個別の問い×理論の問い、レジュメ 1・2 を置換）、(2) 論証地図（要所 5〜10 箇所の精読マーク＋論証注釈、ヨミの緩急）、(3) 解釈開示ノート（分岐・翻訳不可能性・訳注、論文あたり最大 5 件）。
- **主要判断**: オプトイン（Web トグル＋ `--deep`）／コスト上限 +30%／根拠なき推論の出力禁止／独立フェーズ追加（`phase_deep.json`、失敗時は通常出力に縮退）／Phase 4 プロンプトは 2 ルート化し通常ルートはバイト同一を維持／論文モードのみ先行。
- **実装前の必須検証**: 二層問いスキーマの妥当性（プロンプト実験＋藤田氏レビュー）と翻訳品質干渉の A/B。詳細は `SPEC.md` §10〜11。

### 2026-07-04: 全体リファクタリング（挙動不変）

- デッドコード削除: p3_structure 孤児3モジュール / pdf_ingester 旧ルート / LayoutEngine / text_utils 旧関数
- spread_splitter 二系統をノド検出コア共有で統合（信頼性判定はレンジベース基準に一本化）
- phase3_structure(1360行) を run_phase3 専任ファサードに縮小、アルゴリズムは engine/p3_structure/ の heading_matcher / tree_builder / toc_extractor / chapter_extractor へ移設
- chapter_parser のファサード逆 import を解消
- pdf_ingester / pdf_splitter を engine/p1_ingest へ移動、v3 命名除去、core/base 解消、meta_analyzer を p2_meta へ
- web フロント共通関数を common.js へ抽出
- 根拠: CLAUDE.md 責務境界（ファサード=オーケストレーション専任）との乖離解消

### 2026-07-04: Phase 3 見出し判定基準を「レジュメ ∪ Phase1 role」に拡張（I-8 対応）

`docs/management/troubleshooting_log.md` I-8（テキストルートで末尾見出し Conclusion が本文に格下げされ欠落）の根本対策として、Phase 3 の見出し判定基準を変更した。

- **変更前**: `core/phase3_structure.py:146` の `extract_headings_from_resume(resume_content)` のみが見出しリストの生成元。Phase 2 が生成する要約（レジュメ）の箇条書きに見出し名の言及が漏れると、Phase 1 が `role="h1"/"h2"` として正しく検出済みのチャンクであっても Phase 3 で本文（`role="p"`）扱いのまま復元されない設計だった（2026-05-07 コミット `e345ffe` で「レジュメの見出しリストを唯一の基準にする」に一本化されて以来の状態）。
- **変更後**: `core/engine/p3_structure/heading_matcher.py` に純関数 `merge_role_headings(role_headings, resume_headings)` を追加し、`phase3_structure.py` でレジュメ由来のリストに Phase 1 の `role="h1"/"h2"` チャンクのテキストを合成してから `build_tree` に渡すようにした。`tree_builder.py` / `heading_matcher.py` 側の既存マッチングロジック（見出しリストが唯一の基準、というアーキテクチャ）自体は変更していない。
- **判断根拠**: レジュメの悉皆性は要約 LLM の品質・モデル Tier（`--lite` 等）に依存し保証できない。Phase 1 の role 判定は決定論的な専用 LLM 呼び出し（`TextStructureExtractor.extract_headings()`）の結果であり、これを見出しリストに合流させることでレジュメの取りこぼしをモデル Tier に依存せず補完できる。Phase 2 の要約プロンプトに悉皆性の指示を追加する対策（候補 (b)）は、確率的な改善に留まるため今回は見送った。
- **スコープ**: PDF ルート（Book Mode・Route C の Markdown 構造化）には影響しない。Paper Mode（テキスト入力＋非 full_vlm PDF）の見出し判定にのみ適用。
- **検証**: `tests/unit/test_heading_matcher.py` / `test_phase3_structure.py` に単体・回帰テスト計6件を追加（197件全合格）。`data/input/paperplain/NST/NSTsample.txt --lite` の実行で `Conclusion` セクションの復元を実地確認済み。詳細は `troubleshooting_log.md` の「I-8 対応済み（2026-07-04）」を参照。

### 2026-07-07: 書籍モードのレジュメ／プロンプト整理の方針確定（Spec A 起案・実装未着手）

「`coreprompts.json` の Summary 系プロンプトの用途が不明」という藤田氏の問いを起点に、書籍モードの情報受け渡しをコードで全面監査した。判明した意図と実装の乖離、および対策方針を確定した。詳細な調査結果は `troubleshooting_log.md` の I-9〜I-14、対策設計は `docs/superpowers/specs/2026-07-07-book-mode-resume-prompts-design.md`（Spec A）を参照。

- **確定した課題認識（要旨）**: (1) Phase 0 の書籍全体レジュメ `global_resume` が章処理に渡らず断絶している（`resume_content=None`）。(2) 書籍 Phase 2 が全書籍用 `GLOBAL_SUMMARY_PROMPT` を 1 章に流用している。(3) 書籍モードでは章レジュメは構造化に使われず（構造は VLM Markdown が担う）、Phase 4 節レジュメと冗長に二重生成されている。(4) `state_integrator` に死コード（`BookExporter` 未定義で呼べば `NameError`）。(5) `SECTION_SUMMARY_PROMPT` の粒度（節ごと）と文言（章想定）・スロット名（`book_meta_reference` に章レジュメが入る）が乖離。
- **ユーザーの意図（正典）**: 「書籍全体のレジュメを作り、各章はそれを背景に踏まえつつ**あたかも一つの論文であるかのように**レジュメ化し、書籍レジュメ＋章レジュメの両者を踏まえて各章を翻訳する」。
- **Spec A の方針**: 書籍章レジュメを **Phase 2 に 1 本**へ統合（章全文＋`book_resume` 背景、新設 `CHAPTER_SUMMARY_PROMPT`）。`GLOBAL_SUMMARY_PROMPT` は全書籍専用にリネーム（`BOOK_SUMMARY_PROMPT`）。冗長な `generate_section_resume` / `SECTION_SUMMARY_PROMPT` は廃止。`resume_content=None` の断絶を復活。`state_integrator` の死コードと `llm_client.py:540` の 300 字フォールバック乖離を整理。プロンプトは**増えず、むしろ減る**。Phase 0→2→4→統合の順に段階実装。
- **プロンプト整理の位置づけ**: 上流（VLM ルート分岐）と下流（プロンプト整理）は**疎結合**であることを検証済み（書籍モードの構造化は routing に関わらず VLM Markdown/ChapterParser が担い、レジュメは Phase 1 チャンクのテキストを消費するだけ）。したがってどちらを先にやっても手戻りは発生しない。リスク・検証容易性・複雑性削減の観点からプロンプト整理（Spec A）を先行させる判断。

### 2026-07-07: 【候補改善・保留】書籍モードの VLM 適応ルーティング（Spec B, 未起案）

書籍章処理が常に `pdf_mode="full_vlm"` に固定されている（`book_manager.py:214`）点について、藤田氏の対象が**デジタル書籍・スキャン書籍を同程度**扱うことを踏まえ、適応ルーティングの導入を候補改善として登録する。**本日は実装も詳細スペック化も行わない。**

- **現状の意図性**: full_vlm 固定は `CLAUDE.md` 設計原則「複雑なレイアウトでは Route C を優先し中途半端な混在モードは避ける」と整合する意図的判断だが、その根拠は当時のテスト corpus が**見開きスキャン書籍中心**（`SpreadSplitter`・`corfrapdf` 等）だった経緯に引きずられた可能性が高い。
- **改善余地**: 論文モードは既に `is_docling_viable()` で適応ルーティング（綺麗なデジタル→Docling、スキャン→VLM）している。書籍も同様にすれば、デジタル書籍で 10〜50 倍規模のコスト差を削減できる余地がある。
- **着手条件**: 構造抽出品質に直結する上流変更のため、**コスト実測＋構造品質の A/B**（デジタル/スキャン両サンプル）を必須とする（深読モードで定めた「実装前に A/B 必須」方針と同様）。着手時に Spec B として起案する。
- **Spec A との関係**: 疎結合。Spec A 完了後のクリーンな下流の上で着手するのが望ましい。

### 2026-07-10: 翻訳コンテキスト供給の 4 層モデル方針確定（Spec A 改訂）・Spec B 起案

書籍モードの翻訳コンテキスト供給を再考する議論の中で、**「レジュメを踏まえて翻訳する」という設計思想が論文・書籍どちらのモードでも実装されていない**ことが判明した（論文モード: `{resume_content}` スロットへの配線漏れで常に空。書籍モード: `resume_content=None` による意図的だが誤った断絶。詳細は `troubleshooting_log.md` I-9 と新スペックの Context 節）。これを受け、以下を確定した。

- **4 層モデルの採用**: 翻訳プロンプトへ流す文脈を「①大域（レジュメ）②術語（統合用語レイヤー）③論証位置（argument_tree）④局所（連続ウィンドウ）」の 4 層に整理し、両モード共通の標準形とする。正本: `docs/superpowers/specs/2026-07-10-translation-context-architecture-design.md`（Spec A を置換）。
- **Stage 1**: レジュメ配線の両モード統一＋ウィンドウの連続化（断片 3 件×200 字 → 連続 ~2,000 字。根拠: 同日付 research-notes、断片方式に先行例なし・数段落で飽和・用語一貫性は用語台帳が担うという文献知見）＋死コード/死スロット整理。
- **モデル戦略**: `gemini-3.5-flash` の GA 値上げ（$1.50/$9.00、Lite 比 6 倍。`model_optimization.md` §5）を受け、「レジュメ生成のみ 3.5-flash・他は lite」のハイブリッド構成を A/B の本命とする。切替は Stage 1 実装後の比較読みで判定（文脈とモデルの効果を切り分けるため順序厳守）。
- **Stage 2/3**: 統合用語レイヤー（glossary＋local_definitions の一本化、G4-2 再定義）と argument_tree（G4-1、スキーマ実験先行）を各 Stage の比較読み後に別スペックで起案。
- **Spec B の起案と再フレーム**: 前提調査で I-15（VLM OCR が二重定義バグで機能停止）・I-16（full_vlm 指定でも Docling 優先、書籍の実働経路は Docling＋TOC フォールバック）が判明し、「full_vlm 固定の適応ルーティング化」から「①VLM 経路修理 ②デジタル書籍の Docling 正式ルート化（role 見出し配線）③ルーティング明示化（書籍単位判定）」に再構成して起案。正本: `docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md`。実装は Stage 1 の後（比較読みベースライン保護）。

### 2026-07-11: 翻訳コンテキスト Stage 1 実装完了（レジュメ配線・ウィンドウ連続化・死コード整理）

`docs/superpowers/plans/2026-07-10-translation-context-stage1.md` を `subagent-driven-development` で実装完了（feature ブランチ `feature/translation-context-stage1`）。正本設計は `docs/superpowers/specs/2026-07-10-translation-context-architecture-design.md` の Stage 1。「レジュメを踏まえて翻訳する」を論文・書籍両モードで初めて実体化した。

- **変更点（要旨）**:
  - **プロンプト整理**: `GLOBAL_SUMMARY_PROMPT` を `BOOK_SUMMARY_PROMPT`（Phase 0 全書籍専用）へリネーム。章専用 `CHAPTER_SUMMARY_PROMPT` を新設（`{expertise}`/`{book_context}`/`{context_guide}`/`{text}`、`book_resume` を `<book_context>` 背景として注入）。冗長な `SECTION_SUMMARY_PROMPT` / `SUMMARY_PROMPT` を削除し、Summary 系を `BOOK`→`CHAPTER`→`SUMMARY_PROMPT_ronbun` に整理。
  - **配線**: Phase 0 の `global_resume` を各章 `run_pipeline()` へ復活（I-9）。両モードで Phase 2 レジュメを Phase 4 翻訳コンテキストへ配線する `build_translation_context(book_resume, document_resume, is_book)` を新設（論文=章/論文レジュメそのもの、書籍=書籍全体＋章レジュメを `【書籍全体の要約】`→`【この章の要約】` の順で結合）。論文モードの `{resume_content}` 配線漏れも同時解消。
  - **廃止**: Phase 4 節レジュメ生成 `generate_section_resume`（I-12）、`translate_batch` の `context_guide` 引数、`state_integrator` 死コード（I-13）を削除。削除は挙動変更コミットと分離。
  - **ウィンドウ連続化**: 直前訳ウィンドウを断片 3 件×200 字トリムから連続 ~2,000 字（段落丸ごと、`WINDOW_MAX_CHARS=2000`、末尾から遡り最低 1 段落保証）へ変更。根拠は `docs/superpowers/specs/2026-07-10-translation-context-research-notes.md`。
- **検証**: 単体テスト 197→211 件全合格（回帰なし、新規 14 件）。論文モードのゴールデン検証で構造回帰なし＋`<resume_content>` への実レジュメ注入を確認（`troubleshooting_log.md` の「I-9〜I-14 対応済み」参照）。
- **次ステップ（ユーザー実施・本 Plan スコープ外）**: (1) 比較読み（`docs/translation_review_checklist.md`、NST で Stage 1 前後比較）、(2) モデル A/B（現行 lite vs 「レジュメ生成のみ gemini-3.5-flash」ハイブリッド）。この結果を持って Stage 2（統合用語レイヤー）/Stage 3（argument_tree）の Spec/Plan 起案へ。
