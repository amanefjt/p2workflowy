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
