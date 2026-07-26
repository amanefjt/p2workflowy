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

### 2026-07-11: Stage 1 比較読み＋モデル A/B 完了 → ハイブリッド採用決定

Stage 1（レジュメ配線・ウィンドウ連続化）実装後、NST 論文で「全 lite（Arm A）」vs「レジュメのみ gemini-3.5-flash・他 lite（Arm B）」のモデル A/B を実走行・比較読みした。

- **決定**: **Arm B（ハイブリッド）を採用**。訳質が Arm A より良い（藤田氏判定）。用途別ルーティングは実装済み（`DEFAULT_MODEL_RESUME`, commit 3c747ba）。※採用の既定化（`coreprompts.json` に `DEFAULT_MODEL_RESUME=gemini-3.5-flash` を設定）は未実施——Stage 2 起案時にレジュメ目標長の調整とあわせて判断する。
- **付随バグ修正**: A/B 準備中に thinking モデルでレジュメが MAX_TOKENS 途中切断されるバグ（`max_output_tokens=8192` 過小）を発見・修正（8192→32768, I-17, commit f761f5d）。本番有料モードにも影響していた潜在バグ。
- **観測（Stage 2 設計入力）**: 3.5-flash レジュメは 11,450 字と目標「4000〜5000 字」を大きく超過（lite の約 2.6 倍）。訳質向上と引き換えに文脈長・コストが増える。→ Stage 2 起案時に (a) 用語レイヤーの抽出積極性、(b) レジュメ目標長の締め直し、を検討事項とする。
- **A/B 成果物**: `data/input/paperplain/NST/ab_stage1_model/armA_lite_p2.*` / `armB_hybrid_p2.*`（git 管理外）。
- **次**: Stage 2（統合用語レイヤー）を Fable セッションで起案（Spec＋Plan）。

### 2026-07-12: 翻訳コンテキスト Stage 2（統合用語レイヤー）実装完了

`docs/superpowers/specs/2026-07-11-translation-context-stage2-term-layer-design.md` を `subagent-driven-development` で実装完了（feature ブランチ `feature/translation-context-stage2`）。用語集パイプラインが `dict[str,str]`（en→ja）に固定され、Phase 2 が既に抽出していた `definition` が翻訳プロンプトの `<glossary>` に一度も届いていなかった欠落（詳細は `troubleshooting_log.md` I-18）を解消し、訳語と定義を両方運ぶ統合用語レイヤーへ差し替えた。

- **定義配線の復活**: `core/config.py` に 3 列（en, ja, definition）対応の `load_glossary_entries` を新設（旧 `load_glossary_csv` は dict 版として残置、呼び出し元は新関数へ移行）。`core/phase4_translate.py` の用語集組み立てを `keywords_data`（本文抽出の definition 付き）と glossary CSV（書籍は definition 列付き）の両方を材料にする方式へ差し替え、`definition` が初めて翻訳プロンプトまで到達するようにした。
- **`TermEntry` / `term_layer.py` への隔離**: 新モジュール `core/engine/p4_translate/term_layer.py` に `TermEntry(en, ja, definition, source)` と `build_term_layer(keywords_data, glossary_entries)` を実装。フィールド別マージ方針（訳語 `ja` は glossary CSV 優先、`definition` は本文抽出（local）優先・空なら CSV で補完）を単一箇所に閉じ込め、フェーズファサード側にマージロジックを持たせない設計とした。`TranslationPromptBuilder.glossary` の型を `dict[str,str]` → `list[TermEntry]` に統一（`format_glossary()` は内部で `format_term_layer()` に委譲）。
- **描画**: `format_term_layer` は定義付きエントリを `- en → ja：定義` 形式で先頭に列挙し、定義なしエントリを後続、ヘッダは `# 用語集 (Glossary)`。
- **書籍モードの定義配線**: 書籍の `global_glossary.csv` は元々 definition 列を持つため、`load_glossary_entries` を共通で通すだけで両モードの定義配線が揃った（新規のパイプライン分岐は不要、判断保留②の確定値）。
- **`KEYWORD_EXTRACTION_PROMPT` の中庸＋特殊用法込み改修**: 明示的に定義された専門用語に加え、日常語が理論的・特殊な意味で使われている場合（語彙平板化対策の代表例: displace→「ずらす」）も抽出対象に含めるよう改修。特殊な語義の根拠が本文から取れない場合は `definition` を空のまま許容し、無理な創作を防止。抽出上限は 30 件（判断保留①の確定値。比較読みの結果次第で今後調整可）。
- **ハイブリッド既定化**: Stage 1 の A/B で採用が決まっていた「レジュメ生成のみ `gemini-3.5-flash`・他は `gemini-3.1-flash-lite`」構成を `core/coreprompts.json` の既定値として確定（`DEFAULT_MODEL=gemini-3.1-flash-lite`, `DEFAULT_MODEL_RESUME=gemini-3.5-flash`、`DEFAULT_MODEL_FREE`/`DEFAULT_MODEL_VLM` は lite のまま不変）。`docs/model_optimization.md` を既定化後の状態に同期（§1 にハイブリッド構成の注記追加、§3・§5 に残っていた「lite 一本」前提の矛盾記述を修正）。
- **レジュメ長は据え置き（論点③宿題は継続保留）**: Stage 1 で観測された 3.5-flash レジュメの超過（目標 4000〜5000 字に対し実測 11,450 字）への対処（プロンプトの目標字数を締め直す等）は本 Stage では実施しない。比較読みで訳質への影響を見てから判断する対象として持ち越し。
- **書籍レジュメ routing のリグレッション修正**: ハイブリッド既定化（`DEFAULT_MODEL` を lite に変更）により、`book_manager.py` の書籍全体レジュメ生成（旧 `:72` 相当）と各章の `run_pipeline()` 呼び出し（旧 `:212` 相当）が resume 用途のモデルルーティングを経由せず `DEFAULT_MODEL`（lite）にフォールバックしてしまう回帰を発見・修正した。両箇所を `get_default_model("resume")` 経由／`self.model` 明示渡しに変更し、書籍全体・章レジュメが意図どおり `DEFAULT_MODEL_RESUME`（3.5-flash）で生成されるようにした。**この回帰は NST 論文モードの比較読みでは検出不可能**（書籍モード専用の配線のため）であり、コードレビューで先に発見できたことは Task 8 として記録済み。
- **判断保留⑤の確定**: Web 版で管理者パスコード経由のサーバー側キー（無料モード）利用時、レジュメ生成が `DEFAULT_MODEL_RESUME`（3.5-flash、無料枠なし）を消費する点は、ハイブリッド構成の訳質向上メリットを優先し**許容する**判断とする。将来的にコスト面で問題が顕在化した場合は、無料モード時のみ resume も lite にフォールバックする分岐を別途検討する。
- **検証**: 単体テスト 211 → 237 件全合格（回帰なし、新規 26 件: `load_glossary_entries` 4 / `term_layer` 10 / `TranslationPromptBuilder` 2 / `coreprompts` Stage 2 関連 2 / 書籍 resume routing 1 ほか）。ゴールデン構造回帰・書籍スモークはユーザー実施（本タスクのスコープ外、有料 API 実行を伴うため）。
- **次ステップ（ユーザー実施・本 Plan スコープ外）**: (1) 比較読み（`docs/translation_review_checklist.md`、NST で Stage 2 前後・ハイブリッド固定条件、`displace` 等の語彙平板化改善を重点確認）。(2) その結果を入力に、(a) レジュメ目標長の再評価（論点③宿題）、(b) 用語抽出の積極性微調整（抽出上限 30 件を含む、判断保留①の再検討）、(c) Stage 3（argument_tree）の Spec/Plan 起案。

### 2026-07-12: 翻訳コンテキスト Stage 3（論証位置レイヤー / argument_tree）→ 実験の末、棚上げ確定
- **背景・方針転換**: 正本（`2026-07-10-translation-context-architecture-design.md`）の Stage 3 は「LLM 生成の構造化 argument_tree を節単位で条件注入」だったが、起案時のコード監査で翻訳品質への EV が低いと判断。(1) 論文レジュメ「# 3. 各セクションの展開」が既に節ごとの主張・論理ステップ・節間接続を含み layer ① として全バッチ注入済み、(2) 文献上も大域的論証構造は翻訳品質のレバーに挙がらない、(3) 翻訳 LLM は既に「今どの節か」の手がかりの大半を持つ（節先頭バッチは見出しチャンクを見ており、`section_name` も引数として届いているがプロンプト未注入で捨てていた）。→ 正本の当初案を作らず、**レジュメの現在節スライスを `<source_chunks>` 直前に前面化する安価版**（新 LLM 呼び出しゼロ）に置換し、1 回の A/B で継続 or 棚上げを判断する salience 実験に尖らせた。Spec: `docs/superpowers/specs/2026-07-12-stage3-argument-position-salience-experiment-design.md`。
- **実装（branch 上・revert 済み）**: `TRANSLATION_PROMPT` に `<current_position>` スロット、`prompt_builder.build_current_position()`（レジュメから現在節スライスを決定的抽出）、`translate_batch` 配線、単体 16 件。実装中に初回 A/B 無効化バグ 2 件（`sections_dict` キーが `id|title` 形式／レジュメ節直下が `###` サブ見出しで始まりスライスが即空）を発見・修正し、主要 5 節で実スライス注入に成功（slice-match 5/16）。マッチ率ログを入れていたため「全節が名前のみ縮退」を検知でき、無効な A/B を避けられた。
- **A/B 結果（ユーザー・NST・ハイブリッド固定）: 明確に悪化**。症状＝冗長化・固い漢語過多・文末表現の勝手な改変・**確立訳語「世俗」（secular）が glossary を上書きされて不使用**。
- **解釈**: 末尾（recency 位置）に前面化した常体・漢語密度の高いレジュメスライスが**要約文体のスタイル見本**として作用し、翻訳をレジュメの register に引っ張った（style/lexical bleed）。要約プロンプトの狙い（論理の精緻な俯瞰）と翻訳プロンプトの狙い（自然な本文訳出）は文体要求が異なり、後者の近傍に前者を置くと干渉する。
- **判断: layer ③ は棚上げ。** 翻訳コンテキストプロジェクトは実装上 **Stage 1・2 の 3 層（①大域レジュメ／②統合用語レイヤー／④連続ウィンドウ）で完結**とする。実装コードは revert 済み（`core/` 変更なし）。再開条件があるとすれば「フルのレジュメスライス」ではなく論証役割を 1 行に圧縮した短ラベルを翻訳文体を汚さない形で注入する版だが、事前 EV は今回の負の結果でさらに低下。深読モード同様「必要が出たら再起動」。
- **付随の教訓**: (1) 要約系の生成物を翻訳プロンプトの近傍にそのまま流用すると文体干渉・glossary 上書きが起きる。(2) 節単位注入の実験では「マッチ率」を必ずログして、無効な A/B（注入ゼロ）を検知できるようにする。

### 2026-07-13: 用語"定義"レイヤーの撤去（Stage 2 の A/B 再検証 → 定義注入は無効と確定）
- **背景**: Stage 2（統合用語レイヤー）の価値にユーザーが疑問を持ち、「定義あり vs なし」を他条件完全固定で再検証。手法＝同一 session の Phase 1–3（レジュメ・抽出語・構造・ハイブリッドモデル）を固定し、Phase 4 の翻訳だけを glossary に定義を入れる/入れないで振り分け（`format_term_layer` の一時 env トグル `P2W_NO_TERM_DEFINITIONS`、両アームとも resume 経路で構造完全一致を担保）。ON=全語定義つき／OFF=全語 en→ja のみを debug_prompt と決定的再構成の両方で確認。
- **結果（n=2, NST・AL）**: **定義注入（armA）が定義なし（armB）に語彙一貫性で勝った語はゼロ**。NST 20語・AL 14語を全文集計。大半は両版が同一かほぼ同一（＝訳語はもともと安定）。揺れた語（NST: 不平等/不等価・writing through relations、AL: bounded field-site・localization）は**すべて armB の方が一貫**。AL では定義ありに副作用も観測——(1) 原注番号（脚注 6/7/8/10）の欠落、(2) 著者の造語 `sitedness` に用語集の `localization→場所性` グロスを過剰適用して原語の区別を潰す、(3) 書誌情報の過剰な日本語化。
- **機序の確定（重要・当初の推測を訂正）**: 「用語集が本文生成に届いていない実装漏れ」ではない。実 Phase 4 プロンプトを検証し、armA には20語すべてが定義つきで確かに注入されていた。効かない真因は、(1) 一貫性の仕事は **en→ja の訳語対応だけでほぼ完結**しており定義文は inert、(2) 同じ概念はレジュメ（両版に注入）が既に厚く説明済みで**定義は冗長**、(3) 多義語（displace 等）は対応表でも定義でも固定できず両版で揺れる、(4) 単発の悪化は LLM のゆらぎ。→ これは Stage 3 と同型の知見（レジュメが既に運ぶ内容の上に薄い層を足しても翻訳は動かない）。
- **判断＝Option B（定義レイヤーごと撤去）**: 定義は翻訳注入以外に消費先が無く（読者向け出力にも未描画）、その用途が無効と確定したため抽出ごと畳む。**en→ja 訳語対応（両版で機能）は維持**。変更: `format_term_layer` を en→ja のみ／`KEYWORD_EXTRACTION_PROMPT` から定義要求を除去（抽出対象の選定ガイド＝特殊用法・平準化対策は維持）／`TermEntry`・`build_term_layer` の definition 削除／`load_glossary_entries` を en→ja 2列に／`merge_with_glossary`・book_manager global_glossary から definition 除去。ハイブリッドモデル既定（`DEFAULT_MODEL_RESUME=3.5-flash`）は Stage 2 とは独立の別施策なので維持。副産物として抽出トークンも節約。
- **Stage 2 の総括**: Stage 2 が直した「定義が dict[str,str] で 2 箇所落ちていたバグ」の修正自体は妥当だったが、"翻訳レバーとしての定義注入"には価値が無かった。翻訳コンテキストは実装上、**①大域レジュメ／②-en→ja 訳語対応／④連続ウィンドウ**の3要素で完結（②の定義文・③論証位置はいずれも A/B で撤去/棚上げ）。
- **教訓**: (1) 要約系生成物（定義・論証スライス）を翻訳プロンプトへ足しても、レジュメが既にある以上ほぼ効かず副作用が出うる。(2) 「効いているはず」の介入は他条件固定 A/B で必ず実測する。(3) null 結果の解釈で「実装漏れ」を疑う前に実プロンプトを一次確認する。

### 2026-07-13: レジュメ・プロンプトの接地性強化（翻訳コンテキスト品質向上・低リスク直接適用）

- **契機**: 別 Claude セッションが「AIチャット用レジュメ」向けに提案したプロンプト改訂（ドキュメント先頭配置・XMLタグ・CoT足場・節間接続・肯定形化）を p2workflowy に取り込めるか検討。→ 提案の工夫の**大半は 2026-06-07 の long-context 対応で既に実装済み**で、提案元は p2workflowy が先行している事実を知らなかった。
- **不採用（理由付き）**: (1) 提案の唯一の新規要素 `<thinking_steps>` 逐次足場は、レジュメ生成モデルが `gemini-3.5-flash`（thinking モデル）であり、公式ガイダンス「thinking 系は細かい手順指定より高レベル指示の方が良い推論を生む」に反するため**採用せず**。(2) 提案の `### [節タイトル]` / `**中心命題：**` 形式は Phase 3 の見出し逆引き（`heading_matcher.py` が `## English heading` / `# [Original Heading]` の verbatim を基準に使う）を**破壊する**ため採用せず。(3) RQ/Thesis への「先行研究との緊張関係」追記は**ソース外の外部知識を要求し幻覚を招く**ため採用せず。レジュメは Phase 4 で翻訳コンテキストに毎ウィンドウ注入されるため、幻覚は翻訳品質を直接毒する。
- **採用＝接地性強化のみ（コード論理は無変更・A/B 不要と判断）**: レジュメの主用途＝翻訳コンテキストの品質に、壊す箇所なくほぼ確実に効く2点を `SUMMARY_PROMPT_ronbun` / `CHAPTER_SUMMARY_PROMPT` / `BOOK_SUMMARY_PROMPT` に直接適用。(H1) 「内容: 存在する情報のみを使用」を「**本文に明示された内容のみに基づいて記述し、根拠を持たない推論・評価や外部知識による補足は含めない**」へ格上げ（肯定形の接地指示）。(H2 最重要) `# 2. 核心的主張（Thesis）` の「**既存のパラダイムに対し独自の貢献**」という外部知識を招く枠付けを、「**著者/本書がソース内で示している中心的主張と結論**。先行研究やパラダイムへの言及は本文が言及する範囲に限る」へ再枠付け。※提案元とは正反対の方向。
- **不変条件の維持**: verbatim 見出しルール（`## 英語節タイトル` / `# [Original Heading]` の一字一句照合）と節間接続指示は温存。残る否定形（見出しフォーマットガード）は機械解析用の load-bearing な精密指示のため肯定形化せず据え置き（H4 は H1 の書き換えで実質的に達成）。
- **検証**: `tests/unit/` 239 passed（プロンプト文字列に依存するテストは無し）。`core/coreprompts.json` は `@lru_cache` のためランタイム反映にはプロセス再起動が必要。実装スコープはプロンプト文言のみで `core/` のコード論理は無変更。

### 2026-07-13: 書籍スモークテスト（relationspdf.pdf）で resume モデルの実効入力上限バグを発見・修正

- **背景**: 2026-07-13 のレジュメ接地性強化（commit `266091a`）後、Task 8（章レジュメ routing）と合わせた e2e 検証として `data/input/Booksample/relations/relationspdf.pdf`（282p, Docling ルート）で書籍モードのスモークテストを実施。当初計画は全章フルランだったが、ユーザー判断で先に `--max-chapters` を絞った疎通確認へ変更（前付け2つ＋実質章2つ）。
- **発見した不具合**: Phase 0（`BookManager._generate_global_context`、書籍全文スキャンからのグローバルレジュメ生成）が起動直後に `gemini-3.5-flash` の `400 INVALID_ARGUMENT` で 5 回リトライ後クラッシュ。最小再現・二分探索により、`gemini-3.5-flash` は公称入力上限（1,048,576 tok）とは無関係に実測 ~186,000〜187,000 tok（735,000字前後）超の単発リクエストで決定論的に失敗することを特定（`gemini-3.1-flash-lite` は同一入力で成功）。詳細は `troubleshooting_log.md` I-20。
- **原因**: 2026-07-12 の Stage 2 既定化・書籍レジュメ routing 修正（I-18）は NST/AL 論文規模（数万字）でのみ検証されており、書籍全文スキャンという桁違いの入力規模は当時の検証範囲外だった。
- **対策**: `core/book_manager.py` に `RESUME_MODEL_SAFE_CHAR_LIMIT=600,000` を追加し、全文がこれを超え `--model` 未指定の場合のみ resume モデルから `DEFAULT_MODEL`（lite）へフォールバック。章単位のレジュメ生成（Task 8 routing 本体）は章規模がこの閾値を大きく下回るため影響なし。`docs/model_optimization.md` に実測値の注記を追加（`gemini_models.md` は共有ドキュメントのため直接編集せず）。
- **検証**: 単体テスト239件全合格。修正後に同一書籍・同条件で再実行し、Phase 0 が lite へフォールバックして正常完了、4ユニット（Preface/Introduction/Chapter1/Chapter2）完走・統合を確認。`golden-verification` skill で非対称階層（英語nested/日本語parallel）・References等の除外・章統合のタイトル重複や見出しシフトがないことを確認、構造回帰なし。章単位レジュメでは `gemini-3.5-flash` 使用が維持されていることをログで確認（Task 8 routing 健全）。
- **フルラン追記（同日）**: 疎通確認後、Stage 3（論証位置レイヤー）が既に A/B 検証済みで棚上げ・撤去済み（今後 Relations の翻訳に影響する予定変更なし）と確認できたため、同日中に残り9ユニット（実質章3〜6・間奏部3つ・結論）のフルランを実行。完了済み4ユニットは `output_paths.json` キャッシュにより自動スキップ、12ユニット全て成功・エラーなし。統合後の `phase3_structure.json` を全章横断でスキャンし、Conclusions 章（ch12）で見出し `Theoretical Heterogeneity` が連続重複するノードを1件発見（本文冒頭が直前の見出しとほぼ同一語句で始まる段落が誤って見出しへ再分類された疑い）。全12章・全見出し中この1件のみで、今回の変更とは無関係の既存 Phase 3 見出し検出ロジックの端症例。低頻度・非破壊的（後続本文は正しく続く）のため今回は修正せず記録のみに留める。**残タスク**: Spec B（VLM ルーティング修理）は引き続き未着手。上記の見出し重複端症例は次回 Phase 3 見出し検出ロジックに触れる際の調査候補。

## 2026-07-18: Spec B（書籍モード Phase 1 入力ルーティング修理・公式化）実装完了

`docs/superpowers/specs/2026-07-10-book-mode-vlm-routing-design.md` を実装。I-15（VLM二重定義バグ）・I-16（pdf_mode無視バグ）を修理し、書籍単位ルーティング規則（①明示指定 ②見開き=VLM ③Docling可能=Docling ④それ以外=VLM）と実ルート記録（`phase1_route.json`）を公式化。Docling の role 見出しを書籍 Phase 3 に配線する `structure_nodes_by_role` を新設し、これまで実質破棄されていた Docling 出力を書籍モードの本文構造化に直接使うようにした。CLAUDE.md・ARCHITECTURE.md の設計原則も実態に合わせて更新。実 PDF 検証は corfrapdf.pdf（見開き×Docling可能の優先順位検証）→ Naven.pdf（見開きでない純スキャン、VLM初回稼働確認）→ relations/AL/NST（既存回帰確認）の順で実施予定（Task 7〜9、golden-verification 実行記録を参照）。

## 2026-07-19: 章分割精度の改善（I-22/I-24/I-25）設計判断

Spec B 完了により書籍モードで「スキャン書籍を最後まで読める」状態になったことで、章分割そのものの精度を通読で検証する機会が初めて生まれ、`PDFSplitter`（`core/engine/p1_ingest/pdf_splitter.py`）に既存不具合が3件（troubleshooting_log.md I-22/I-24/I-25）顕在化した。3件は「Route 2 (outline) 無検査採用 → Route 3 (TOC) 窓固定で目次に届かず → コンテンツスキャンの誤マッチ」という経路上で連結しており、単一スペック `docs/superpowers/specs/2026-07-19-chapter-splitting-accuracy-design.md` として扱った。

- **first-match-wins からスコアリングへの変更（判断根拠）**: 当初の `_matches_heading()` は前方一致した最初の候補で `break` する first-match-wins 設計で、corfra の奇数頁ランニングヘッダー（`Knowing | 147` が `"knowing 147".startswith("knowing ")` で誤ヒット）が章内の全奇数頁にマッチし、必ず最初の（＝多くの場合誤った）ページで確定してしまっていた。これを窓内の全候補を集めて `_score_candidate()` で採点し最良を選ぶ方式（`_apply_content_scan()`）に変更した。
- **`exact`/`joined` を順位付けに使わない（判断根拠）**: 当初は「単独行完全一致（exact）を複数行結合一致（joined）より優先する」スコアリングを想定していたが、Naven の実データがこれを反転させることを実測で確認した。Naven はランニングヘッダーのタイトルと頁番号が別行にあるため、**本文頁のランニングヘッダー行が exact になり、真の章扉はタイトルが2行に割れて joined にしかならない**（`CHAPTER` / `XIII` / `Ethological Contrast...` のように扉のタイトルが行をまたぐ）。`exact > joined` を採用すると Naven では全章がランニングヘッダーに着地する誤判定になる。corfra・relations では exact/joined の優先度が問題にならなかったため、この反転は Naven を実データに含めて初めて発見できた。→ 3冊すべてを貫く判別軸として「一致したタイトル行の隣接行が裸の頁番号（ランニングヘッダー）か、章マーカー・無し（章扉）か」を採用し、一致の種類自体は判定材料から除外した（`_classify_match()`）。
- **局所オフセット救済を「当たった頁のヘッダー1件」からのみ読む理由**: corfra の 'Knowing' 章は真の扉頁（idx153）のテキスト層に `7`/`Knowing` の見出し文字が存在せず（大見出しがOCR/抽出に載っていない）、どんな文字列照合でもこの頁には到達できない。唯一の手がかりは、当たったランニングヘッダー頁（idx155, `Knowing | 147`）から「物理頁 − 印刷頁番号」の局所オフセット（+8）を読み、TOC論理頁（145）に加算して真の扉頁（153）を逆算することだった（`_rescue_by_local_offset()`）。書籍全体の頁番号マップを事前構築する必要はなく、当たった1頁だけを見れば十分。
- **却下した代替案: 大域的オフセット表**: 「全頁の余白から印刷頁番号を抽出し書籍全体のオフセット表を作る」案を検討したが採用しなかった。(1) 既存の探索窓（論理頁-5 〜 +49）は、実測した corfra（オフセット+8で全巻一定）・relations（部扉ごとに+7→+9→+11→+13と階段状）の両方で真の扉頁を既に含んでいた。つまりオフセットは**候補の位置決めには寄与せず、複数候補間の順位付けにしか使えない**——順位付けは上記のスコアリングで代替できるため、大域オフセット表を持つ意義が薄い。(2) 余白からの頁番号抽出は書式依存で脆く、5回の試行すべてで別の破綻を起こした（帯域（余白）の閾値判定・索引の相互参照番号・章番号などが数字として誤って拾われ、印刷頁番号と混同された）。これらの理由から、局所オフセット（当たった1頁のみを見る、上記）で十分と判断し、大域表は不採用とした。
- **Route 2 の妥当性検査（I-24）の判断根拠**: outline は Route 3 より優先されるため、ページラベル等の「章目次でないもの」が無検査で採用されると下流の修正が一切効かない。「章目次らしさ」を頁密度・連番ラベル形式という低コストな2指標で検査し、疑わしければ Route 3 へ委譲する設計とした（`_is_plausible_outline()`）。
- **TOC サンプリング窓の可変化（I-25）の判断根拠**: 固定窓（先頭15頁）はテスト対象コーパスに偶然合致していただけの前提であり、目次の位置は書籍によって大きく異なりうる（Naven は idx 16-24）。単純な窓拡大は LLM への入力量を全書籍で増やしコストを上げるため、目次見出しを探索してから周辺だけを渡す方式（discovery-hit / fallback の二段構え、`_find_toc_pages()`）を採った。
- **検証・スコープ**: 単体テスト 264 → 319 件全合格。`data/input/Booksample/` 全4冊（corfra/relations/Naven/PSE）で `PDFSplitter.split()` を実行し境界を実測確認（`.superpowers/sdd/task-5-report.md`）。Task 5 の検証で新たに2件の未修正欠陥（I-26 Naven リガチャ崩れ／I-27 PSE 書名ヘッダー）を発見したが、いずれも本スペックのスコープ（I-22/I-24/I-25）とは異なる種類の不具合のため記録のみに留め、修正は別課題とした（詳細は `troubleshooting_log.md`）。

## 2026-07-20: 章境界の検算・逸脱検出・LLM 裁定（層1〜層3・I-26/I-27 解決）

ブランチ `feature/chapter-boundary-adjudication`。`core/engine/p1_ingest/` に
`page_number_map.py`（印刷頁番号の回収・オフセット推定）/ `toc_verifier.py`（TOC 検算・
shift 補正＝層1）/ `boundary_adjudicator.py`（逸脱検出＝層2・LLM 裁定＝層3）を追加。
既存の照合ループ（`_classify_match` / `_score_candidate` / `_rescue_by_local_offset`）は
判定ロジック本体を変更せず、`_apply_content_scan` に層1の前段・層2/3の後段配線のみ加えた。
仕様は `docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md`。

- **なぜ逸脱幅の閾値を使わないか**: 4冊の実測で relations の**正当な**オフセット段差は 2、
  Naven の**誤り**は 1 だった。大小で正誤を区別できないため、閾値ではなく「前後の確定章の
  双方とオフセットが食い違うか」という構造で判定する（層2 `flag_suspects`）。前後いずれかの
  確定章が無い章（前付け・最終章）は評価対象外となり、前付けの誤検知が自動的に消える。
- **なぜ最頻値でオフセットを推定するか**: relations は部扉ごとにオフセットが階段状に変わる
  ため、中央値は実在しない中間値になりうる。最頻値は必ず実在する段のいずれかを選ぶ。
- **なぜ shift 検算が安全か**: 予測物理頁（論理頁＋推定オフセット）にそのエントリのタイトルが
  実在するかを shift∈{-1,0,+1} で数え最良を採る。実測で PSE のみ shift=-1 が 12対2 で勝ち、
  他3冊は shift=0（誤検出ゼロ）。差が明瞭なため閾値調整不要。判断材料が乏しい場合は補正しない
  安全弁（`SHIFT_MIN_MATCHES` / `SHIFT_DOMINANCE_RATIO`）を設けた。層1は Route 3（LLM TOC 抽出）
  のみに適用し、Route 1（手動 TOC）/Route 2（ネイティブ outline・物理頁直参照）には掛けない。
- **なぜ LLM 失敗時の基準値を経路で分けるか**: フォールバック章の機械照合値は「オフセット0」で
  あり、それ自体が I-27 の症状である。ここで機械照合結果を維持すると LLM 失敗時に不具合へ戻る。
  よってフォールバック章は「論理頁＋補間オフセット」を、照合成立章は「元値維持」を基準値とする
  （層3 `_baseline`）。区間外/単調性違反の LLM 応答も基準値へ落とす（コードレビューで発見・修正）。
- **なぜ層3の窓を確定章間の物理区間にするか**: 論理頁が誤っているために要審査になった章（PSE 型）
  でも、前後の確定章に挟まれた物理区間なら真の扉頁を必ず含む。論理頁±αの窓は使わない。
- **探索窓をオフセット中心にしなかった理由**: 層1がオフセットを推定するため技術的には可能だが、
  窓は照合ループの中核であり変更すれば4冊すべての結果が動く。手前方向のずれは層3が回収するため
  本スペックでは触らず、既存入力に対する振る舞いを不変に保った（`TestSearchWindowInvariance`）。
- **検証指標の設計（Task 9）**: `report_ground_truth` を物理頁境界ベースの照合に直し、下限
  （PSE≥12・Naven==2）を hard regression 化した。TOC の LLM 抽出は非決定的でタイトル書式が
  実行ごとに揺れるため、タイトル完全一致では真値が化ける。mutation テストで gate の実効性を確認。
- **成果（実PDF 4冊・単体テスト 380件）**: ★I-26 解決（Naven VII=P116/XII=P190、層3 が
  リガチャ崩れ `N aven`/`Pref erred` を意味で解釈し同定）。PSE 章本文境界 12/13（唯一の未達は
  Preface の前付け端症例）。relations Notes を +45 外れ値 P236 → +8 の真の扉頁 P199 へ是正
  （層2/3 のボーナス修正）。corfra 完全不変・regression 0。TOC を全再抽出しても境界は同じ正解に収束。
- **golden-verification は本ブランチでは未実施**。I-21（VLM スライディング OCR の図版頁重複）を
  同一ブランチで対応後に1回だけ実行する（実 API コストを2回払わないため。2026-07-19 ユーザー判断）。

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
- **実PDF検証結果**: corfra「1 Arbitrary Location」章を `--max-chapters 3` で実行。
  修正前に2連続重複していた段落（"scorn on their keenness..."）は最終出力で1回のみに
  なった。図版ページ（page_idx=3, page_idx=18）は前ページ内容の再転写ではなく、それぞれ
  `FIGURE 1.1. A *crucetta*` / `FIGURE 1.2. Corsica. ...` のキャプションのみを正しく
  出力した。見出し「A Corsican Whole」は要約レイヤーと本文レイヤーに1回ずつの計2回（正しい
  構造）で、修正前のような余分な重複は無い。

## 2026-07-20: Phase 4 バッチサイズ上限を flash-lite 前提で見直し（PAID 10/11,000→18/20,000、FREE 5/6,000→8/9,000）

`core/llm_client.py::apply_tier_settings()` と `core/engine/p4_translate/parallel_translator.py::ParallelTranslator`
の `max_batch_chunks` / `max_batch_chars` を、PAID: 10チャンク/11,000字 → **18チャンク/20,000字**、
FREE: 5チャンク/6,000字 → **8チャンク/9,000字** に変更した。

- **背景**: この定数は 2026-04-03/04 の初期実装時から未変更で、選定根拠の記録も残っていなかった
  （`git log -p` 確認済み）。2026-07-11 のハイブリッド化以降 Phase 4 翻訳は PAID/FREE とも
  `gemini-3.1-flash-lite` で動くようになったが、この定数は旧モデル（`gemini-3.5-flash`）前提の
  ままだった。ユーザーからの「flash-lite の実力を踏まえて考え直そう」という指示を受けて再検討した。
- **引き上げ幅の根拠**: 出力上限 65,536tok に対し、`docs/model_optimization.md` §5.2 実測比率
  （11,000字入力→最大約13,000字≒13,000tok出力、thinking除く）から入力1字あたり最悪約1.18tokと
  見積もり、thinking＋バッファに出力予算の60%を確保する保守的前提を置いても、入力文字数の理論上限は
  約22,200字（26,214tok ÷ 1.18tok/字）。またパーサーの「1チャンク3,000字超で切り捨て」安全弁
  （`core/llm_client.py:461-466`）との掛け算（`max_batch_chunks`×3,000字）も出力上限を超えない
  範囲であることを確認した上で、この理論上限より余裕を残した PAID 20,000字/18チャンクを候補とした。
- **検証**: AL論文・NST論文それぞれで PAID候補・FREE候補を実行（計4パターン・65バッチ、実 API
  呼び出し）。全バッチで「タグ正常抽出」、3,000字切り捨て警告・`MAX_TOKENS`・翻訳失敗はいずれも
  0件。AL: PAID 11バッチ/Phase4約53秒（旧設定17バッチ相当から削減）、FREE 19バッチ/約91秒。
  NST: PAID 16バッチ（全16セクションが1バッチで収束）/約54秒、FREE 19バッチ/約93秒。翻訳品質の
  詳細レビューは今回未実施（エラー有無の確認のみで採否判断する、というユーザー判断）。
- **なぜ FREE を PAID と完全同一値にしなかったか**: バッチ失敗時は該当チャンク全体が
  `【翻訳失敗】` プレースホルダになりチャンク単位の再試行はない（`parallel_translator.py` の
  失敗フォールバック）。FREE は `TierManager` のダウンシフト対象＝429/503 に遭遇しやすいティア
  であるため、1回の失敗の被害を PAID より抑える設計判断を維持した（PAID比のバッチ規模比率は
  変更前後でおよそ同水準）。
- **変更箇所**: `core/llm_client.py::apply_tier_settings()`（PAID/FREE の `settings` 辞書）、
  `core/engine/p4_translate/parallel_translator.py::ParallelTranslator`
  （`DEFAULT_MAX_BATCH_CHUNKS`/`DEFAULT_MAX_BATCH_CHARS`）。詳細な数値根拠・検証ログは
  `docs/model_optimization.md` §5.4 参照。

## 2026-07-21: 複数APIキー運用（CLIローテーション ＋ Webアプリ並行プール）を導入

`docs/model_optimization.md` §3/§4 の実測（無料枠で1日〜45〜68本処理可能）を踏まえ、ユーザーから
「CLIは無料キー2本→有料の自動フォールバック、Webアプリは無料キー5本を同時実行スロットとして使い
6人目以降は混雑中表示にしたい」との要望があった。上位モデル（Opus）に素案をレビューさせたところ、
Web側で本当に5並行を許可する設計に変えることで露呈する、プロセスグローバル状態の競合バグを2件
発見したため、それを踏まえた修正込みで実装した。

- **前提**: Gemini APIの無料/有料はAPIキーが紐づくGCPプロジェクトの課金設定で決まり、無料枠の
  RPM/RPD/TPMはキー単位ではなくプロジェクト単位（WebSearchで確認）。複数キーで実質的に枠を
  増やすには、それぞれ別のGCPプロジェクトで発行する必要がある（同一アカウント内で複数プロジェクト
  を作ればよく、別アカウントは不要）。
- **CLI**: `core/config.py`に`GEMINI_API_KEY_FREE_1/2`を追加。`core/llm_client.py`に
  `KeyRotator`（プロセスグローバル・forward-onlyのシングルトン）を新設し、`call_gemini`/
  `call_gemini_async`のリトライループが429/503を検知した際、モデルのダウンシフトに加えて
  キー自体も次に進める。`main.py`が起動時に`key_rotator.configure([FREE_1, FREE_2,
  GEMINI_API_KEY])`を一度だけ呼ぶ。`server.py`は一切呼ばないため、Webアプリの挙動には
  影響しない（`is_configured()`が常にFalseのまま）。
- **Webアプリ**: `core/config.py`に`GEMINI_API_KEY_WEB_1..5`（`GEMINI_API_KEY_WEB_KEYS`として
  export）を追加。`server.py`の`asyncio.Semaphore(1)`による全リクエスト完全直列化＋FIFOキュー
  （`"queued"`状態・キュー位置表示）を撤去し、`WebKeyPool`（無料キー最大5本を同時実行スロット
  として貸し出す、非ブロッキングacquire）に置き換えた。空きが無い場合は待たせず即座に
  `task_status`を`"failed"`＋「混雑中」メッセージにする（既存のステータスポーリングの仕組みを
  再利用、フロントエンド変更なし）。管理者パスコード・ユーザー自身のAPIキー入力の経路はプールを
  介さず今まで通り無制限。
- **必須の副作用修正1（Opusレビューで発覚）**: `core/llm_client.py`の`_CLIENTS`（クライアント
  キャッシュ）・`_CACHED_LIMITERS`（レートリミッタキャッシュ）・`TierManager`の内部状態は
  いずれもプロセスグローバルだった。Web側の各並行パイプラインは`asyncio.to_thread`でそれぞれ
  別スレッド・別イベントループで動くため、`(tier, api_key)`キーイングだけでは、
  `reset_pipeline_state()`（`run_pipeline()`開始時に無条件実行）があるユーザーの処理開始で
  別の処理中ユーザーのキャッシュを消したり、一時的に誤ったモデル階層（バッチサイズ・レート
  制限）に切り替えてしまったりする間欠的な競合が残る。3つとも`threading.local()`ベースに
  変更した（外部API・属性アクセスは無変更）。`apply_tier_settings()`には`api_key`引数を追加し
  `(tier, api_key)`でのキーイングも別軸で実施（CLIのキーローテーションが複数キーを跨ぐ際に
  キーごとに正しい残余レートを持たせるため）。
- **必須の副作用修正2（Opusレビューで発覚）**: 当初「Web起点は`SessionState.
  cleanup_old_sessions()`（`state/`直下の全セッション横断のグローバル上限10）をスキップし
  `server.py::_cleanup_task_status()`に一本化する」という案を検討したが、書籍モードの章
  ディレクトリ（`state/<book>_<fp>_ch<N>`）は`task_id`と無関係の命名のため`_cleanup_task_status`
  では一切掃除できず、無制限にディスクリークする新たな不具合を生むことが判明した。代わりに
  章ディレクトリ自体を`state/book_sessions/<book>_<fp>/chapters_state/ch<N>/`に物理的に移設
  した（`run_pipeline()`に`state_base_dir`パラメータを追加）。`book_sessions/`は元々グローバル
  上限の対象外かつ書籍単位の独自上限（`MAX_BOOK_SESSIONS=5`）で管理されるため、章が個別
  セッションとして誤カウントされることもない。この修正はCLI・Web双方に効き、書籍モードが
  自分の章数だけでグローバル上限を回してしまう既知の悩み（[[book-mode-session-cache-global-cap]]）
  の解消にもなる。`run_pipeline()`にはこれとは別に`cleanup_sessions: bool = True`も追加し、
  Web起点の論文モード呼び出しには`cleanup_sessions=False`を渡して`_cleanup_task_status()`
  （完了/失敗と判明しているものだけ削除）に一本化した。
- **既知の限界（今回は対応せず）**: 同一書籍の同時重複アップロード時のキャッシュファイル
  競合書き込み（`BookManager.session_dir`が内容フィンガープリント基準で`task_id`に基づかない
  ため）。CLIのキーローテーションはプロセス内で永続・不可逆（1ファイル目で一時的な429に
  遭遇しただけで残り全ファイルが有料キーに固定される）。ローテーションは429/503のみをトリガー
  にし、無効・失効キーは対象外。
- **変更箇所**: `core/config.py`、`core/llm_client.py`（`KeyRotator`、スレッドローカル化、
  `apply_tier_settings`）、`core/pipeline.py`（`cleanup_sessions`/`state_base_dir`）、
  `core/book_manager.py`（章ディレクトリ移設）、
  `core/engine/p4_translate/parallel_translator.py`、`main.py`、`server.py`、`.env.example`。
  設計の詳細は `docs/model_optimization.md` §6 参照。

- [2026-07-21] Phase 2 レジュメ生成の論文モード・サンプリング閾値を書籍モード相当に引き上げ
    - **経緯**: 745,144 文字の文書（`--book` なしの単一文書処理）を Phase 2 に投入したところ、
      `MAX_INPUT_CHARS=500_000`（論文モード）を超えたため冒頭 100,000 字＋末尾 50,000 字に
      サンプリングされ、中間部分がレジュメ生成から欠落した。
    - **判断根拠**: レジュメ生成モデル（`DEFAULT_MODEL_RESUME` 経由、既定は `gemini-3.5-flash` /
      `gemini-3.1-flash-lite`）は両方とも入力上限 1,048,576 tok（`docs/model_optimization.md` §5.1）。
      745,144 文字は概算でも数十万トークン程度でこの上限に対して十分余裕がある。論文モードの
      500,000 字という閾値は 2026-03-12 の変更（`86b29c1`）で `5_000_000` から「仕様書に基づき」
      縮小されたものだが、当時の縮小理由はログに残っておらず、現行のモデル入力上限から見て
      過度に保守的だった。書籍モード（`MAX_BOOK_CHARS=1,500,000`）は同一モデル・同一コンテキスト窓
      で実運用済みのため、論文モードとサンプリング閾値を分ける技術的根拠がない。
    - **実装**: `core/phase2_meta.py` の `MAX_INPUT_CHARS`/`HEAD_CHARS`/`TAIL_CHARS` を書籍モードの
      値（1,500,000 / 1,000,000 / 500,000）に統一し、論文・書籍で閾値を分けていた
      `MAX_BOOK_CHARS`/`BOOK_HEAD_CHARS`/`BOOK_TAIL_CHARS` は削除（`_sample_text` から
      `is_book` によるサイズ分岐を除去、ログラベルの Book/Paper 表示のみ `is_book` を使用）。

- [2026-07-21] 論文（非書籍）モードの入力ルーティングに書籍単位ルーティング規則（①〜④）を移植
    - **経緯**: 同じ `Naven.pdf`（本来は書籍だが `--book` を付けずに論文モードで処理した回）で、
      Docling 不可と判定されたにもかかわらず VLM フォールバックが実行されず、生の物理テキスト
      抽出のまま図版ページ（Figure 4）が寸断された状態で出力される不具合が発覚した（詳細は
      `troubleshooting_log.md` I-38）。原因は `main.py`/`server.py` の論文（非書籍）経路が
      `pdf_mode` 未指定時に無条件で `"hybrid"` を固定しており、`core/book_manager.py`
      （I-16, 2026-07-18）が書籍単位で実装済みの①明示指定②見開き=VLM③Docling可能=hybrid
      ④それ以外=VLM というルーティング規則が論文モードには一度も移植されていなかったこと。
    - **判断根拠**: 書籍モードと論文モードで「同じPDF入力特性に対して異なるモデルルートを
      適用する」技術的合理性はない（Docling不可なPDFはどちらのモードでも同じ理由で不可）。
      書籍モードで既に実運用・テスト済みの判定ロジックをそのまま論文単位に転用するのが最小
      差分かつ最も安全な修正と判断した。
    - **実装**: `core/book_manager.py::_decide_book_pdf_mode` の実体を
      `core/engine/p1_ingest/routing.py::decide_pdf_mode` に切り出し、`book_manager.py` は
      これを re-export するエイリアスに変更（既存テスト・呼び出し名は維持）。`main.py`
      （論文モードループ）・`server.py`（非書籍分岐）はいずれも PDF入力かつ `pdf_mode`
      未指定時に `is_spread_pdf()`/`is_docling_viable()` を実行し、`decide_pdf_mode()` で
      解決した具体的な `pdf_mode` を `run_pipeline()` に渡すよう変更。`core/pipeline.py` の
      I-37 プリフライトチェック（見開き検知・`diagnose_pdf_quality`）は役割が異なる独立した
      安全網として維持（重複はするが害はない）。
    - **既知の限界**: 書籍モード同様、論文モードにも見開きスキャンPDFを単一ページへ分割する
      処理は存在しない（I-37 の既知の残課題を引き継ぐ）。

- [2026-07-22] `DEFAULT_MODEL_RESUME` を `gemini-3.5-flash` から後継の `gemini-3.6-flash` に切替
    - **経緯**: `docs/gemini_models.md` の 2026-07-22 更新で `gemini-3.6-flash`（`gemini-3.5-flash`
      の公式後継）・`gemini-3.5-flash-lite`（`gemini-3.1-flash-lite` の公式後継）が GA として
      確認された。これに合わせて `docs/model_optimization.md` の対象フェーズ（Phase 2 レジュメ生成）
      のモデルルーティングを見直した。
    - **判断根拠**: `gemini-3.6-flash` は `gemini-3.5-flash` と Rate Limit（無料枠・有料枠 Tier 2
      とも）が完全一致し、価格は出力 -17%（入力は同額）と純粋な改善で、据え置くデメリットが
      見当たらないため切替えた。一方 `DEFAULT_MODEL`/`DEFAULT_MODEL_FREE`/`DEFAULT_MODEL_VLM`
      （`gemini-3.1-flash-lite`）は後継 `gemini-3.5-flash-lite` へ切替えなかった：Rate Limit は
      一致するが価格が値上げ（入力+20%・出力+67%）で、コスト最優先というハイブリッド構成
      （2026-07-11 Stage 2）の設計意図と衝突するため。GA 化して間もない現時点で
      `gemini-3.1-flash-lite` の廃止予定もなく、現状維持のリスクは小さいと判断した。
      なお「無料枠なら値上げは無関係では」という指摘があったが、Lite 系の値上げは無料キー
      由来のトラフィックには影響しない一方、`DEFAULT_MODEL`（PAID tier 既定）・
      `DEFAULT_MODEL_VLM`（tier 分岐なしで常時参照）、および有料キーのまま `TierManager` が
      モデルだけダウンシフトした場合の `DEFAULT_MODEL_FREE` には課金が乗りうるため、現状維持の
      判断は変えていない（詳細は `docs/model_optimization.md` §1 の該当ノート）。
    - **実装**: `core/coreprompts.json::DEFAULT_MODEL_RESUME` を `gemini-3.6-flash` に変更。
      `tests/unit/test_coreprompts_stage2.py` のアサーションを追随。`core/book_manager.py`・
      `core/phase2_meta.py` の実効入力上限（I-20、`RESUME_MODEL_SAFE_CHAR_LIMIT=600_000`）に
      関するコメントは `gemini-3.5-flash` 実測値のままであることを明記（`gemini-3.6-flash` での
      再検証は未実施、保守的な値のためガード自体は変更なし）。

- [2026-07-22] 無料枠内での複数Liteモデル併用ローテーション（`ModelRotator`）を実装
    - **経緯**: AI Studio のレート制限ダッシュボードで、同一無料プロジェクト・同一キー内でも
      `gemini-3.1-flash-lite` と `gemini-3.5-flash-lite`（Rate Limit は完全一致）が独立した
      使用量カウンターを持つことを確認した。§6（2026-07-21、別GCPプロジェクトでキーを複数
      発行する運用）とは別軸で、同一キーのまま複数Liteモデルを使い分けることでも実質的に
      RPM/RPD を拡張できる。詳細な検討過程・未決論点の決定・Opus によるコードベース照合込み
      レビュー結果は `docs/superpowers/specs/2026-07-22-free-tier-multi-model-lite-pool-design.md`
      §6・§7 を参照。
    - **判断根拠**: forward-only（`KeyRotator` と同じ設計思想。429/503 検知時のみ次のモデルへ
      進む）を採用し、ラウンドロビンは不採用。対象は `DEFAULT_MODEL_FREE` のみ。文書処理途中
      でのモデル切替（訳文トーンがわずかに変わりうる）と `DEFAULT_MODEL_VLM` への副作用的な
      波及（現状 `DEFAULT_MODEL_VLM == DEFAULT_MODEL_FREE` のため）はいずれもユーザー確認済み
      で許容: 現状は Lite 単体で RPD 枯渇＝そのまま失敗/停滞であり、切替は「本来失敗していた
      ケースを完走させる」場合にのみ発生するため既存挙動に対する正味の劣化ではなく改善と判断。
    - **実装**: `core/coreprompts.json` に `DEFAULT_MODEL_FREE_POOL`
      (`["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]`) を追加。
      `core/llm_client.py` に `ModelRotator`（`TierManager` と同じくスレッドローカルな
      シングルトン、forward-only）を新設し、`call_gemini`/`call_gemini_async` の両リトライ
      ループで毎試行 `current_model` を `model_rotator.resolve()` に通すよう変更（呼び出し元が
      コンストラクタ時点で一度だけ `model` を固定文字列として解決するパターン
      （`pdf_splitter.py`・`ocr_manager.py`・`state_integrator.py`・`book_manager.py` 等)
      でも毎試行効くようにするための必須対応）。429/503 検知時はモデルローテーション
      （同一キー内で完結、client 再生成不要）を §6 のキーローテーションより優先して試行し、
      プールを使い切って初めて（`has_next()` が `False` になって初めて）キーローテーション・
      従来の待機付きダウンシフトにフォールバックする。キーローテーション発生時は
      `model_rotator.reset()` も呼び、新しいキー（＝別プロジェクトの独立枠）側でもプール
      先頭から使い始める。`reset_pipeline_state()` にも `model_rotator.reset()` を追加。
      レートリミッタ（`apply_tier_settings()`、`(tier, api_key)` キーイング）はクライアント側
      RPM ペーシングに過ぎず変更不要と確認済み。
    - **テスト**: `tests/unit/test_llm_client.py` に `ModelRotator` 単体テスト（`resolve()`・
      forward-only `advance()`・`reset()`）と、429 検知後にプール内の次のモデルへ切り替わって
      即リトライすることを確認する `call_gemini_async` 統合テストを追加。既存 409 件と合わせて
      全 411 件パスを確認。
    - **既知の限界**: 書籍モードは章ごとに `run_pipeline()`（＝`reset_pipeline_state()`）を
      呼ぶため `model_rotator` も章ごとにプール先頭へリセットされる。forward-only の
      `advance()` が各章内で 429 を機に即座に効くため容量拡張効果自体は損なわれないが、
      先頭モデルが枯渇していた章では毎回1回分の 429 往復が無駄になる（拒否されたリクエスト
      自体は RPD を消費しないため実害は小さい）。`ModelRotator` はスレッドローカルなため、
      Web の並行パイプライン（複数スレッドが同一プロジェクトの枠を共有）はローテーション状態
      を共有せず、各スレッドが独立してプール先頭から始める（`TierManager` と同じ既存の限界を
      踏襲）。実運用（`--lite` モードでの実ファイル処理・golden-verification）での検証は
      未実施。

- [2026-07-22] Phase4翻訳の速度改善: 無料枠Liteプールのバッチ単位ラウンドロビン、
  `--concurrent` デフォルトを 4 から 8 に変更
    - **経緯**: 直上の`ModelRotator`（RPD拡張目的、forward-only・429検知時のみ切替）実装後、
      「無料枠Liteプールの2モデルが独立したRPM枠を持つなら、429を待たずに最初からバッチを
      2モデルへ振り分ければPhase4翻訳のスループット自体も上げられるのでは」という着想で
      追加実装した。詳細と実測データは `docs/model_optimization.md` §7「Phase4の速度改善」
      を参照。
    - **判断根拠**: 対象は Phase4 翻訳のみ（Phase1〜3はRPMを食うほどのリクエスト数がない）。
      既存の`ModelRotator`（リアクティブなRPD枯渇対応）とプロアクティブなラウンドロビンの
      2つの仕組みが同じ共有状態を取り合うと、ラウンドロビンで意図的に選んだモデルが
      `ModelRotator.resolve()`によって常に先頭モデルへ引き戻されてしまい機能しないことが
      実装中に判明した（`resolve()`は「プールのメンバーならどれを渡しても現在のローテーション
      先へ差し替える」実装のため）。このため`model_pinned`フラグで両者を明示的に分離し、
      ラウンドロビン対象バッチはRPD枯渇時の自動延命フォールバックの対象外とする（同一モデルへの
      ダウンシフト+待機で再試行し、失敗すれば既存の「翻訳失敗」フォールバックに委ねる）という
      トレードオフを受け入れた。
    - **実装**: `core/llm_client.py`に`get_free_pool_rate_limiters()`（モデルごとに独立した
      `AsyncLimiter`）を追加。`call_gemini`/`call_gemini_async`/`translate_batch`に
      `model_pinned: bool = False`を追加。`core/engine/p4_translate/parallel_translator.py`に
      `_pick_batch_target()`を追加し、FREE tier・モデル未指定の場合のみバッチ単位でプール内
      モデルへラウンドロビン割り当てする（ユーザーがモデル明示指定・PAID tierの場合は
      挙動変更なし）。
    - **`--concurrent`デフォルト変更**: AL論文・`--lite`での実測（単発計測）で
      単一モデルconcurrent=4（92s）→ラウンドロビンconcurrent=4（75s、-18.5%）→
      ラウンドロビンconcurrent=8（64s、対単一モデル比-30.4%）を確認し、`main.py`・
      `core/pipeline.py`・`core/phase4_translate.py`・`ParallelTranslator`の全デフォルトを
      `4`から`8`に変更した。2026-05-11時点の旧ベンチマーク（`docs/model_optimization.md`§2）は
      「4と8の優劣は判断できない」との結論だったが、ラウンドロビン導入によりconcurrentを
      上げる意味合いが変わったための再判断。ただし今回も単発計測であり複数trialでの統計的
      検証はまだ行っていない。
    - **速度が理論値（2倍）に届かなかった理由**: `max_concurrent_sections`（セマフォ）が
      実際のボトルネックだった（concurrent=8でさらに短縮したことが裏付け）、レートリミッタは
      「発行間隔の下限」に過ぎずボトルネックはAPI応答レイテンシ（TTFT 6〜20秒）だった、
      小規模文書（10セクション・19バッチ）ではワークロードの立ち上がり・収束コストの影響が
      相対的に大きい、の3点が主因と分析（詳細は`model_optimization.md`§7）。
    - **テスト**: `tests/unit/test_parallel_translator.py`に`_pick_batch_target()`の単体テスト
      3件・統合テスト1件、`tests/unit/test_llm_client.py`に`model_pinned`が429時のローテーション
      をバイパスすることを確認するテスト1件を追加。`tests/unit/test_concurrent_flag.py`の
      デフォルト値アサーションを4→8に更新。全414件パス。
    - **未検証**: 大規模文書（書籍等、バッチ数が多い場合）での速度改善効果の実測、複数trialでの
      統計的な安定性検証、`docs/model_optimization.md`§2の旧ベンチマークの再実施。

- [2026-07-22] Phase1 VLM OCRにも無料枠Liteプールのページ単位ラウンドロビンを適用
    - **経緯**: 直上のPhase4速度改善を踏まえたユーザーからの指摘（「VLMも同じ改善で速度向上が
      期待できるのでは」）を受けて調査。詳細は`docs/model_optimization.md`§7「VLM OCR（Phase1）
      への同様の適用」を参照。
    - **判断根拠**: 調査の結果、VLM（`core/engine/p1_ingest/ocr_manager.py::OCRManager`）は
      Phase4と構造的に異なり、`VLM_SEMAPHORE_LIMIT=10`という同時実行数の上限のみでクライアント
      側レートリミッタが元々存在しないことが判明した（`apply_tier_settings()`を呼んでいない）。
      実装前にALpdf.pdf（18ページ）でベースライン計測したところ、VLM OCR自体（Semaphore=10で
      18ページを並列発行）はエラー無く完走したが、**直後のPhase2 DNA抽出リクエストが429
      RESOURCE_EXHAUSTED（`gemini-3.1-flash-lite`のFree Tier RPM上限15）で弾かれる**ことを
      実測で確認した（既存の`ModelRotator`が自動フォールバックしパイプライン自体は完走したが、
      VLMの18リクエストだけで1分間のRPM枠をほぼ使い切っていたことの直接証拠になった）。この
      具体的な証拠に基づき実装を判断した。
    - **実装**: `OCRManager`に`_pick_page_target()`を追加（`ParallelTranslator._pick_batch_target()`
      と同じ設計思想）。コンストラクタでの`self.model`即時解決をやめ（`None`のまま保持）、
      ページ処理のたびに動的決定するよう変更。ラウンドロビンはFREE tierかつモデル未指定の場合
      のみ適用（`DEFAULT_MODEL_VLM`はtier追従しないため、PAID tierでは無料枠専用ペースを適用
      すると有料ユーザーが不必要に遅くなることを避けるための条件分岐）。Phase4で新設した
      `get_free_pool_rate_limiters()`をそのまま再利用し、新規のリミッタ実装は不要だった。
    - **速度・信頼性測定**（AL PDF、18ページ、`--lite --pdf-mode full_vlm`、単発計測）:
      Phase1(VLM OCR)所要時間が単一モデル56s→ラウンドロビン46s（-18%）。加えて**パイプライン
      全体を通じて429が単一モデル時の1回からゼロ回に減少**。VLMは`--concurrent`のような同時
      実行数抑制もないまま全ページを一度にgatherする設計のため、Phase4よりレート制限との
      衝突が起きやすい箇所だったことが429有無の違いとしてはっきり表れた。
    - **テスト**: `tests/unit/test_ocr_manager.py`に`_pick_page_target()`の単体テスト3件
      （ラウンドロビン・モデル明示指定時の非適用・PAID tier時の非適用）を追加。既存テストが
      `OCRManager.__new__()`で`__init__`を経由せずインスタンスを作る手法だったため、新たに
      必要になった`self.model`/`self._rr_index`属性が欠落してエラーになった7件のテストヘルパー
      （`_make_ocr_manager()`）も合わせて修正。全417件パス。
    - **未検証**: より大きな書籍・ページ数の多いPDFでの効果測定、`VLM_SEMAPHORE_LIMIT`
      自体を引き上げる余地（ハードコードのクラス定数でCLIから調整不可、今回は未変更）、
      golden-verificationでの出力品質検証（ユーザー判断によりスキップ）。

## 2026-07-26: 無料枠APIキー4本 × Liteモデル2種の2軸ラウンドロビン（§8）

- [2026-07-26] CLI の無料枠キーを2本→最大4本に拡張し、「キー × モデル」の2軸で能動的に
  ラウンドロビン分散するよう変更
    - **経緯・前提**: ユーザーが `GEMINI_API_KEY_FREE_1`〜`_4` の4本を**すべて別GCPプロジェクト**
      で用意済みであることを確認したうえでの依頼。無料枠の RPM/RPD はプロジェクト単位（§6）
      なので、4本＝実質4倍の枠になる。設計の全文は `docs/model_optimization.md` §8。
    - **判断根拠**: これまでキー軸は「429/503 検知時にだけ前進する forward-only」（§6）で、
      通常は1本目しか使われていなかった。一方モデル軸は §7 でバッチ/ページ単位のラウンド
      ロビンに拡張済み。両者を掛け合わせれば `(キー, モデル)` の組み合わせ＝レーンがそれぞれ
      独立した RPM 枠を持つため、4×2 = 8レーン ≒ 120RPM 相当になる。**§7 のバッチ単位
      ラウンドロビンを「キー軸」へそのまま拡張する**という位置づけで、設計思想・実装形とも
      §7 から乖離させないことを最優先した。
    - **実装**:
        - `core/config.py`: `GEMINI_API_KEY_FREE_3/_4` と、設定済みキーだけを集めた
          `GEMINI_API_KEY_FREE_KEYS` を追加。`main.py` の `ordered_keys`/`tiers` を
          4無料キー＋有料キーに拡張し、起動ログの本数表示も `pool_keys()` 基準に変更。
          `.env.example` も追随。未設定キーは既存の `configure()` が除外するため、2本しか
          設定していないユーザーの環境は一切壊れない。
        - `core/llm_client.py`: `KeyRotator.pool_keys()`（tier が `"free"` のキーのみ返す）を
          追加。`call_gemini`/`call_gemini_async`/`translate_batch` に `key_pinned: bool = False`
          を追加し、`True` のとき `key_rotator.current()` による `api_key` の上書きと 429 時の
          キーローテーションをスキップする。**`model_pinned`（§7）の完全な鏡写し**であり、
          理由も同じ（プロアクティブな負荷分散とリアクティブな429フォールバックを混ぜない。
          混ぜると、意図的に free_2 を渡してもラウンドロビンが `current()` = free_1 に
          引き戻されて成立しない）。
        - `ParallelTranslator._pick_batch_target()` / `OCRManager._pick_page_target()` を
          `(api_key, model, rate_limiter, model_pinned, key_pinned)` を返す形に拡張。通し番号 `i`
          に対して `key = keys[i % K]`, `model = models[(i // K) % M]`（連続リクエストが必ず
          別キーへ散り、K*M 回で全レーンを一巡する）。`get_free_pool_rate_limiters(api_key)` は
          既に `(tier, api_key, model)` でキーイングされていたため**そのまま流用でき、新規の
          リミッタ実装は不要だった**。適用条件は §7 と同一（FREE tier かつモデル未指定のみ）で、
          無料キーが1本以下ならキー軸は自然に無効化されモデル軸だけの §7 の挙動に戻る。
    - **許容したトレードオフ**: `key_pinned=True` のリクエストが429に遭遇しても
      `KeyRotator.advance()` による有料キーへの延命は効かない（§7 の `model_pinned` と同じ扱い。
      同一レーンでのダウンシフト+待機で再試行し、最終的に失敗したら既存の失敗ハンドリングに
      委ねる）。**根拠**: 混ぜると (a) どのレーンが本当に枯渇しているのかカウンタが追えなくなり、
      (b) 1バッチの一時的な429でプロセス全体が有料キーに移ってしまう（§6 の「forward-only は
      不可逆」がラウンドロビン下では確率的に必ず起きる）。8レーンを能動的に使い切る設計では、
      単一レーンの429は「枠切れ」より「一時的混雑」である可能性の方が高い。
    - **章並列化のためのフック（今回は誰も呼ばない）**: `KeyRotator.restrict_to(keys, tiers)` /
      `clear_restriction()` / `is_restricted()` を追加。呼び出したスレッドだけ `current()` /
      `pool_keys()` / `advance()` / `has_next()` / `current_tier()` / `index` / `count` が部分集合に
      対して動く。書籍モードを章ごとに別スレッドで `run_pipeline()` する後続タスクで、
      レートリミッタが `threading.local()` ベース（§6）であるために複数スレッドが同一レーンを
      共有すると枠を多重に叩いて429が多発する問題を、「章スレッドごとにキーを1本ずつ排他割り
      当てする」ことで避けるための下ごしらえ。**制限を設定していないスレッドの挙動は
      プロセスグローバル状態そのままで完全に不変**（スレッドローカル側はグローバル状態に一切
      書き込まない実装）。今回はユニットテストのみで担保。
    - **実測**（AL/NST 論文、`--lite`、単発計測。数値表は `model_optimization.md` §8）:
      Phase4（AL）は変更前51s → 変更後45s/44s（concurrent=8）。NST も 64s → 60s。
      Phase1 VLM（AL PDF 18ページ、`full_vlm`）は変更前46s → 変更後41s（`VLM_SEMAPHORE_LIMIT=10`）
      → **31s（同20へ引き上げ、対変更前 -33%）**。全実行を通じて429/503・翻訳失敗はゼロ。
      ログ上でも16バッチが8レーンにちょうど2件ずつ均等配分されていることを確認した。
    - **同時実行数の再チューニング**:
        - **Phase4 `--concurrent` は 8 のまま据え置き**。16/24 に上げても改善せず微増した
          （45s → 50s → 52s）。`max_concurrent_sections` は「バッチ」ではなく「セクション」の
          同時実行数であり、AL 論文はセクションが9個しかないため 8 でほぼ上限に達している。
          §7 で 4→8 が効いたのは 4 < 9 だったためで、16以上が効かないのは整合的。
        - **Phase1 `VLM_SEMAPHORE_LIMIT` は 10 → 20 に引き上げ**（41s→31s、429ゼロ）。1ページ
          1リクエストなので同時実行数がそのまま効き、18ページが2ウェーブ→1ウェーブになった。
          引き上げが安全になったのは、§7＋§8 のラウンドロビンで FREE tier の各リクエストが
          必ずレーンごとの `AsyncLimiter` を通るようになり、発行レートの担保がセマフォから
          リミッタへ移ったため（§7 時点はリミッタが一切なくセマフォが唯一の抑制だったので
          引き上げは危険だった）。併せて `OCRManager.__init__(vlm_concurrency=...)` を追加して
          呼び出し元から上書き可能にした（既定値はクラス定数のままで後方互換）。CLI フラグは
          調整したい場面が具体化していないため今回は配線していない。
    - **テスト**: `tests/unit/test_llm_client.py` に `pool_keys()` 3件・`key_pinned` 2件・
      `restrict_to()` のスレッドローカル性2件を追加。`tests/unit/test_parallel_translator.py` に
      2軸RR・単一キー時の無効化・PAID時の無効化・キー伝播の統合テストを追加。
      `tests/unit/test_ocr_manager.py` に2軸RRと `vlm_concurrency` のテストを追加。
      既存テストは戻り値タプルの要素数変更（3→5）に伴う分解部分のみ更新し、期待値の意味は
      変えていない。全430件パス。
    - **出力品質**: `golden-verification` skill を AL・NST 両論文で実施。変更前（HEAD）と
      変更後で `phase3_structure.json` のセクション一覧が完全一致（AL 9件・NST 16件、
      NST は `[Unlabeled Section]` を含む＝仕様どおり）、最終 `_p2.txt` の本文骨格
      （ノード数・インデント・見出し）も完全一致。差分は日本語訳の語彙のみ（LLM の非決定性）。
      非対称階層・`References` 除外・`【翻訳失敗】`ゼロも確認。
    - **未検証**: 書籍モードの実走行（ユーザー指示によりスキップ）、実際にRPDを枯渇させた
      うえでの `key_pinned=True` の延命不能による実害の有無、複数trialでの統計的検証。
      実測中に単一リクエストが135〜144秒かかるスパイクを観測しており（§2 の「max TTFT 200s超」
      と同じ現象）、単発計測の数値には API 側テールレイテンシ由来の揺れが含まれる。

## 2026-07-26: レーン単位のクールダウン（circuit breaker）でKeyRotatorの不可逆性を解消（§9）

- **背景**: §6の「既知の限界」（CLIのキーローテーションはプロセス内で永続・不可逆、1ファイル目
  の一時的な429で残り全ファイルが有料キーに固定される）を解消するタスク。§8で無料キーが4本に
  拡張されたことで、この不可逆性は「一瞬の429で無料枠3本ぶんを捨てて有料キーへ落ちる」という
  実害になっていた。
- **事前調査**（`~/.claude/jobs/b0473b65/tmp/research_gemma_multikey.md`項目B）で判明した事実:
    - Gemini APIは`Retry-After`ヘッダを返さず、エラーボディの`RetryInfo.retryDelay`のみ
      （不正確という報告があるためクランプ前提で使う）。
    - 429のエラーボディの`quotaMetric`/`quotaId`でRPM/TPM/RPD起因を判別できる。
    - **無料枠LiteプールのTPM（トークン毎分）は2モデルで同一値（250,000）を共有している**
      ため、TPM起因の429ではモデルローテーション（§7）が無効。これは§7の前提「Rate Limit
      完全一致・使用量は独立カウンター」がTPMには当てはまらないという設計の盲点であり、
      `model_optimization.md`§7に注記を追加した。
- **決定・実装**（詳細は`model_optimization.md`§9）:
    - `core/llm_client.py`に`LaneCooldownRegistry`（`lane_cooldown`）を新設。`(api_key, model)`
      レーン単位で429/503後の使用不可時間を記録する。**プロセスグローバル + `threading.Lock`
      で意図的にスレッドローカルにしない**（枯渇の事実はスレッドを跨いで共有されるのが正しく、
      書籍の章並列化フック`KeyRotator.restrict_to()`とも整合するため）。
    - クールダウン秒数はquotaMetricで分岐: RPM/TPM起因は既定60秒（`retryDelay`が取れれば
      1〜120秒にクランプ）、RPD起因は次の太平洋時間深夜までの残り秒数（下限1時間・上限24時間）、
      不明（503含む）は既定30秒。
    - `pick_lane()`を新設し、`ParallelTranslator._pick_batch_target()`/
      `OCRManager._pick_page_target()`の重複していたi%K方式のRR算出ロジックを集約。クールダウン
      中のレーンを避けて生きているレーンを選び、全レーン枯渇時はクールダウンを無視した§8従来
      どおりの割り当てにフォールバックする（例外を投げない）。
    - `KeyRotator.advance()`（forward-only、既存テスト依存）はそのまま維持し、新規に
      `best_available(is_available)`を追加。429起点のフォールバックはこちらを使い、
      「現在のキーが使用可能ならそのまま・そうでなければ並び順で最初に見つかった使用可能な
      キーへ」切り替える。並び順（free優先・paid最後）を維持するため「無料キーが1本でも生きて
      いれば有料キーには落ちない」という既存の優先順位は保たれる。全キー使用不可の場合のみ
      既存のforward-only `advance()`にフォールバックする。
    - `call_gemini`/`call_gemini_async`のリトライループ: quotaMetricがTPM起因と判定された
      429では`ModelRotator.advance()`を試さずキー切替へ直行する。判別できない場合は従来どおり
      モデルローテーションを先に試す（安全側）。
    - `reset_pipeline_state()`は`lane_cooldown`をクリアしない（呼び出し元の判断どおり採用）。
      書籍モードは章ごとにこれを呼ぶため、クリアすると前章で判明した枯渇の事実を次章が知らずに
      同じ429を踏み直すことになり、プロセスグローバル化した設計意図と矛盾するため。
- **`retryDelay`/`quotaMetric`の実取得可否**: google-genai SDK（`google/genai/errors.py`）を
  実際に読み、`APIError.details`にレスポンスJSON全体が保持され例外メッセージにも埋め込まれる
  ことを確認した。構造化アクセス・文字列マッチの両方に対応する実装にしたが、**実際の429
  レスポンスでの動作確認（本物の`retryDelay`/`quotaMetric`値の取得）は行っていない**
  （429が実地では発生しなかったため）。
- **テスト**: `tests/unit/test_llm_client.py`に26件、`test_parallel_translator.py`/
  `test_ocr_manager.py`に各2件追加（クールダウンの発火・失効・スレッド間共有・quotaMetric
  分類・retryDelay抽出とクランプ・全滅時フォールバック・KeyRotatorの可逆化と優先順位・
  TPM起因でのモデルローテーションスキップ）。時刻は`time.time()`のパッチで注入し`sleep`待ち
  のテストは書いていない。`tests/unit/conftest.py`を新設し、プロセスグローバルな
  `lane_cooldown`がテスト間で汚染しないようautouse fixtureでクリアするようにした。
  全456件（既存430件＋新規26件）パス。
- **回帰確認**（`--lite`、AL論文PDF、paperモード、2回計測）: Phase1〜5完走、429/503発生なし、
  `【翻訳失敗】`0件、`phase3_structure.json`セクション数9件（§8と同一）。Phase4所要時間は
  77s・42s（§8実測45s/44sと同水準、1回目はAPI側テールレイテンシスパイクの影響）。
- **未検証・既知の限界**: 実際に無料枠RPM/TPM/RPDを枯渇させての実地検証（429が実地で発生
  しなかったため）。RPDの太平洋時間深夜リセットという前提はGemini APIの一般的な仕様説明からの
  推定で、直接確認したものではない。書籍モードの実走行は未実施（ユーザー指示）。half-open型の
  段階的復帰は採用せず、クールダウンが明けた瞬間に即座にフル復帰する単純な実装。

## 2026-07-26: 書籍モードの章並列化（§10）

- **背景**: §8で用意した`KeyRotator.restrict_to()`/`clear_restriction()`（当時は「誰も呼ばない」
  下ごしらえ）を実際に使い、書籍モードの章ループ（`core/book_manager.py::BookManager.run()`）を
  並列化するタスク。§8実測で「論文1本では`max_concurrent_sections`がセクション数の少なさで
  頭打ちになる（AL論文9セクションで8が上限）」ことが判明しており、8レーンの投資を活かす取り分は
  章を並列に走らせて同時実行数そのものを増やせる書籍モードだと判断した。
- **有効化条件（安全装置）**: 無料キーが2本以上`configure()`されている場合のみ章並列を有効にし、
  それ以外（`server.py`のWeb経路、無料キー1本以下のCLI環境）は常に完全直列。`server.py`は
  `key_rotator.configure()`を一切呼ばないため`is_configured()`が常に`False`になり、Web版の
  挙動には一切影響しない。理由: レートリミッタ・`TierManager`・`ModelRotator`が
  `threading.local()`ベース（§6〜§8）である以上、複数の章スレッドが同じ(キー,モデル)レーンを
  共有すると各スレッドが「自分は15RPM使える」と多重に思い込み429が多発するため。
- **章スレッドごとのキー排他割り当て**: 各章スレッドは処理開始時に
  `KeyRotator.restrict_to([自分に割り当てられたキー], ["free"])`を呼び、終了時に必ず
  `clear_restriction()`する（`try/finally`で例外時も対になることを保証）。キー本数<章数に
  備え、キーを`queue.Queue`にプールし「章タスクが1本取り出し、終わったら返す」方式にした。
  これにより「同時に同じキーを使う章は高々1つ」がラウンドロビン計算ではなくキューの排他取得に
  よって構造的に保証される。並列度はキー本数と処理対象章数（章単位resumeでスキップされた章を
  除く）の小さい方が既定値。`--book-concurrency`で明示指定でき、`1`を渡すと有効化条件を満たして
  いても常に直列になる（回帰時の逃げ道）。
- **統合順序の維持**: `chapter_sessions`を`StateIntegrator.integrate_to_book()`へ渡す順序が本の
  並び順そのものであるため、完了順に`append`するのではなく`[None]*N`をあらかじめ用意し
  `run_one_chapter()`がインデックスで直接書き込む方式にした。
- **ログの可読性**: `core/config.py`に`set_log_prefix()`（`threading.local()`ベース）を新設し、
  `print_log()`が呼び出しスレッドのプレフィックス（例`[ch3] `）を自動付与するようにした。
  既存の`print_log()`呼び出し箇所（`core/`全体に散在）を個別に書き換える必要がない。
  `print_log()`自体は標準`logging`モジュールのHandlerロックによりスレッドセーフであることを
  コードを読んで確認済み（追加のロックは不要と判断）。
- **1章あたりのVLM同時実行数**: `OCRManager(vlm_concurrency=...)`（§8で追加済みのフック）を
  `run_pipeline()`→`run_phase1_unified()`→`_run_phase1_pdf()`→`run_pdf_ingestion_async()`まで
  配線し直した。章並列時は`max(4, VLM_SEMAPHORE_LIMIT(20) // effective_concurrency)`を既定値
  として各章に渡す（複数章が同時にVLMルートに入るとページ画像のメモリ使用量とAPIレート負荷が
  章並列数倍に積み上がるため）。CLIフラグは追加していない（引数自体は露出済み、利用場面が
  具体化していないため見送り）。
- **テスト**: `tests/unit/test_book_manager.py`に`TestChapterParallelization`を8件追加
  （キープール排他利用の確認・統合順序が完了順ではなく本の並び順であることの確認・1章の例外が
  他章を止めないことの確認・無料キー未設定/1本のみ/`book_concurrency=1`での完全直列フォール
  バックの確認・2本以上での並列有効化の確認・`restrict_to`/`clear_restriction`が例外時も対に
  なることの確認）。全466件（既存458件＋新規8件）パス。
- **回帰確認**（`--lite`、AL論文PDF、CLI）: Phase1〜5完走、429/503発生なし、`【翻訳失敗】`0件、
  `phase3_structure.json`セクション数9件（§8/§9と一致）。Phase4所要時間41秒（§9実測42〜77秒の
  レンジ内）。書籍モードの変更が論文経路（章並列化コードを一切通らない）を壊していないことを
  実測で確認した。
- **未検証・既知の限界（初版時点）**: 書籍モードの実地走行は未実施（実走行するかどうかの判断は
  ユーザーに委ねている）。VLM同時実行数の按分式は実測に基づく値ではなく保守的な仮説値。
  `max_concurrent_sections`（Phase4）は章並列化に伴って調整していない。無料キーが長時間
  複数本枯渇した状態でのキューの挙動、書籍モードのピークメモリ使用量はいずれも未実測。
  詳細は`docs/model_optimization.md`§10参照。

## 2026-07-26: 書籍モード章並列化の実走行検証（3章限定）とTPM/モデルローテーション判定の訂正

- **背景**: 上記の章並列化実装後、ユーザー判断により`data/input/Booksample/relations/
  relationspdf.pdf`（Booksample中最小、282頁）を`--max-chapters 3 --lite`で実走行検証した
  （1冊丸ごとではなく3章限定）。直列ベースライン（`--book-concurrency 1`）と並列
  （既定=無料キー4本と3章の小さい方=3並列）を比較。条件切替時は
  `state/book_sessions/relationspdf_.../chapters_state/`のみを削除し、`global_context.json`・
  `chapters/`（章分割済みPDF）は両条件で共有した（章単位resumeのスキップ機構が2回目の実行を
  無意味にしないための対処、かつPhase0・章分割自体は並列化と無関係な前処理のため条件間で
  共通化した方が公平）。
- **結果**: 3章とも両条件で完走、失敗章0件。統合後の出力の章出現順は直列・並列とも本来の
  並び順（Preface→Introductions→1. Experimentations）で完全一致し、章並列化のインデックス
  書き込み方式（完了順ではなく`chapter_sessions[idx]`への直接代入）が意図どおり機能している
  ことを実地で確認した。429件数: 直列1件（TPM起因）・並列0件×2回。503件数: 直列0件・並列
  2件（1回目の計装前実行のみ、いずれも`high demand`のサーバ起因、429ではない）。
  `【翻訳失敗】`は全条件で0件。所要時間は直列820秒に対し並列448秒（1回目）・642秒（2回目、
  API側テールレイテンシスパイクの影響）で、いずれも直列より速いが倍率は試行間でばらついた
  （単発計測2回のみのため統計的結論は出せない）。**章並列化がクォータ起因429を増やした証拠は
  無い**（むしろ直列でも429が1件発生しており、並列2回とも429は0件）。
- **キー割り当ての可視化**: 1回目の並列実行のログには章ごとの使用キーが判別できなかった
  （`ParallelTranslator`の`key#N`ログは、そのスレッドから見える`pool_keys()`が2本以上のときに
  しか出ない実装のため、`restrict_to()`で1本に制限された章スレッドでは発火しない）。運用上の
  可視性のギャップと判断し、`core/book_manager.py::worker()`に「使用キー割り当て: freeN」
  ログを恒久的に追加。2回目の並列実行でこのログにより、ch1=free1・ch2=free2・ch3=free3と
  実際に別々のキーへ排他割り当てされていることを確認した。
- **出力品質**: `golden-verification` skillで、非対称階層（英語ネスト・日本語並列展開）、
  References/Notes/Index系セクションの除外、章タイトル重複・見出しシフトの無いことを確認。
- **副産物として発見・修正したバグ（TPM起因429のモデルローテーション判定）**: 直列
  ベースラインで実際に踏んだ429のレスポンスJSONを精査した結果、§9が採用していた
  「無料枠Liteプールの2モデルはTPM枠を共有している」という前提が誤りだったことが判明した。
  実際の`quotaId`は`GenerateContentInputTokensPerModelPerMinute-FreeTier`（＝PerModel、
  モデル単位で独立集計）であり、`quotaDimensions`にも対象モデルが明示されていた。事前調査
  レポート自体も別箇所では「使用量カウンターは独立」と正しく記述しており、「上限の値が同じ」
  と「集計カウンターを共有」を混同した誤りだったと考えられる。`core/llm_client.py`に
  `_is_model_scoped_quota()`を新設し、quotaIdに`"PerModel"`が確認できる場合はTPM起因の429でも
  モデルローテーションを試すよう修正（確認できない場合は従来どおりキー切替に直行、保守的
  デフォルト維持）。詳細は`docs/management/troubleshooting_log.md` I-41、
  `docs/model_optimization.md` §7・§9参照。
- **テスト**: 書籍章並列化8件に加え、実際の429レスポンスJSONを使ったテスト6件を
  `tests/unit/test_llm_client.py`に追加（既存のTPMテスト1件は新仕様に合わせて書き換え）。
  全472件（既存458件＋書籍章並列8件＋TPM訂正6件）パス。
- **未検証・既知の限界（更新）**: 1冊丸ごとの実走行、キー本数を超える章数でのキュープール
  運用、VLM同時実行数按分式の実地検証（今回3章はいずれもDoclingルートでVLM不使用）、
  書籍モードのピークメモリ実測、TPM訂正後の実装でモデルローテーションが実際に429を回復
  させるケースの実地確認、はいずれも未実施。詳細は`docs/model_optimization.md`§10参照。

## 2026-07-26: Phase 4 のセクション内バッチは直列を維持する（ユーザー決定）

- **背景**: 書籍モードの章並列化検証（`relationspdf.pdf` 3章）のログでバッチ分布を数えると、
  ある章では最大の節が8バッチ、他の11節は1〜2バッチと極端に偏っていた。節（セクション）は
  `asyncio.Semaphore(max_concurrent_sections=8)`で並列だが、節の中のバッチは
  `ParallelTranslator.translate_section_chunks()`が直前までの訳文をスライディングウィンドウ
  として渡す（`previous = prompt_builder_func(all_translated)`）ため直列であり、Phase4の
  所要時間は実質的に「最もバッチ数の多い節」で決まる。巨大な節が1つある文書（学術書・論文で
  典型的）ほど、他の節が終わった後にレーンが遊ぶ時間が長くなる。バッチを並列化すれば速くなる
  余地は実際にあるとユーザーに提示した。
- **決定**: **それでも直列を維持する**。逐次の鎖を切るとバッチ境界で訳語・文体がぶれるため
  （例: "the field-site"が前半「調査地」・後半「フィールドサイト」）。本ツールの用途は
  Workflowyで読む対訳アウトラインであり、訳語の揺れは速度と引き換えにしてよい種類の劣化では
  ない、というのがユーザーの判断。
- **検討した折衷案（今回は不採用）**: (a) バッチ数が閾値を超えた大きな節だけ並列化して影響
  範囲を限定する、(b) 直前バッチの訳文の代わりにPhase 2のレジュメと用語集
  （`global_glossary.csv`）を全バッチに配って逐次依存を切る、(c) 並列訳の後に境界だけ整合
  パスをかける。いずれも実装はしていない。
- **対応**: コード変更なし（意図的に据え置く決定のため）。`CLAUDE.md`「パイプラインの
  ハマりどころ」に、この判断がコードを読んだだけでは分からず善意で並列化されてしまうリスクへの
  注意書きを追加し、`docs/model_optimization.md`§3.1（該当節）を参照させた。

## 2026-07-26: `docs/model_optimization.md` の再構成（時系列ログ→現状ドキュメント）

- **背景**: 10節・1,210行まで日付順の変更ログとして積み上がっており、古い結論が新しい結論に
  覆されたまま残る（例: 2026-05-11の「concurrent 4と8の優劣は判断できない」が§8で
  「8が最適、16以上は効果なし」と決着済みなのに残存）、同じ話題（レート制御）が複数節に散る、
  といった問題があった。「現行の設計はこうで、なぜそうなっているか」を先に読めることを
  優先し、次の構成に組み替えた: (1)現行のモデル構成とルーティング、(2)並列度とレート制御の
  現行設計（レーン・セマフォ・クールダウン・章並列を一続きで説明）、(3)意図的な設計判断
  （トレードオフ）、(4)実測値（現行構成のみ）、(5)無料枠と処理能力の目安、(6)未検証・既知の
  限界（集約）、(7)変更履歴（日付+一行要約のみ）。
- **削除・圧縮したもの**: 上書きされた古い結論（2026-05-11のconcurrent 4/8判断不能、
  2026-07-20以前のバッチ上限10/11,000・5/6,000等）は変更履歴に一行圧縮。2026-07-10時点の
  Phase4プロンプトのトークン収支・拡大シナリオ試算（3.5-flash時代の価格前提）は、その後
  Stage1/2で実装済みとなり歴史的スナップショットとしての価値のみになったため変更履歴に圧縮。
  `gemini_models.md`と重複するモデル一覧・トークン上限・料金・無料枠レート値は同ファイルへの
  参照に置き換えた。
- **新規に書き起こしたもの**: 「Phase 4のセクション内バッチは直列のままにする」トレードオフ
  （上記決定の詳細）、Gemma併用を不採用とした理由の1段落まとめ。
- **確認の過程で見つけた食い違い**: 特になし。現行コード（`core/coreprompts.json`の
  `DEFAULT_MODEL*`、`core/pipeline.py`/`core/phase4_translate.py`/
  `core/engine/p4_translate/parallel_translator.py`の`max_concurrent_sections=8`、
  `core/engine/p1_ingest/ocr_manager.py`の`VLM_SEMAPHORE_LIMIT=20`、`server.py`が
  `key_rotator.configure()`を呼ばないこと等）と本文記載を突き合わせ、いずれも一致を確認した。
- **副次対応**: `CLAUDE.md`「パイプラインのハマりどころ」に、Phase 4のセクション内バッチが
  直列である理由を1〜2行で追記し、`docs/model_optimization.md`§3.1への参照を付けた。既存の
  章並列化・並列数・無料枠ラウンドロビンの記述にある節番号参照も新構成に合わせて更新した
  （§10→§2.5、§2・§7・§8→§2.3・§4、§8→§2.2）。
