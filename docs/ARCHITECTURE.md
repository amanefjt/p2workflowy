# p2workflowy アーキテクチャ概要

このドキュメントは、p2workflowy を初めて触る人（人間・AI 問わず）が、コード全体を読まずに設計の全体像とその理由を掴めるようにするためのものです。README がユーザー向けの使い方ガイド、CLAUDE.md が Claude Code 向けの運用リファレンスであるのに対し、本書は「なぜこう設計されているか」を含めた説明（explanation）に徹します。実装の正本は常に `core/` 配下のコードと `.cursor/rules/` であり、本書はそこへ入るための地図です。

読了目安: 10〜15分。

---

## 1. 鳥瞰図 — このツールは何をするものか

p2workflowy は、英語の学術論文・専門書籍（PDF またはテキスト）を Gemini AI で解析し、**Workflowy で深く読むための日英対訳アウトライン**に変換するツールです。

背景にあるのは、文化人類学者である開発者自身の読書・執筆ワークフローです。Workflowy でアウトラインを書きながら思考を構造化する運用をしており、外国語の学術文献を読む際も「原文と訳文が対応づいた階層構造」として取り込めれば、要約を読むのではなく原文の含意を保持したまま深く読み込めます。単なる機械翻訳ツールではなく、**テクストの論理構造（節・段落・論証の階層）を保持したまま対訳化する**ことが中心的な価値です。

出力の階層構造には非対称なルールがあります。

- 英語ブロックは親子ネスト（原文の論理階層をそのまま反映）
- 日本語ブロックは並列展開（Workflowy 上で訳文だけを素早く読み流せるように）

この非対称性は事故ではなく意図的な設計判断です（詳細は §4）。

入力は 2 系統に対応します。

- **PDF**: スキャン画像でもデジタル PDF でも可（VLM OCR または Docling で処理）
- **プレーンテキスト**: Acrobat 等でコピーした本文（見出しのないベタテキストでも LLM が構造を推定）

処理モードは 2 つです。

- **論文モード**（デフォルト）: Abstract → Introduction → Methods → ... という論文の定型構造を認識
- **書籍モード**（`--book`）: 目次（TOC）を解析し、章・節単位に分割して処理

---

## 2. コードマップ

### 2.1 エントリーポイントと全体の流れ

```
main.py (CLI)  ┐
server.py (Web API) ┴──> core/pipeline.py :: run_pipeline()
                              │
                              ├─ Phase 1: core/phase1_preprocessor.py
                              ├─ Phase 2: core/phase2_meta.py
                              ├─ Phase 3: core/phase3_structure.py
                              ├─ Phase 4: core/phase4_translate.py
                              └─ Phase 5: core/phase5_export.py
```

`main.py` / `server.py` はエントリーポイント専任、`run_pipeline` はフェーズのオーケストレーション専任です。個別のアルゴリズムはここには置かず、各フェーズのファサード（`core/phaseN_*.py`）経由で `core/engine/` 配下のエンジン層に閉じ込めます。この責務境界は `.cursor/rules/10-pipeline-and-hierarchy.mdc` で明文化されたルールで、リファクタリング時も崩さないことが求められています。

書籍モードは `core/book_manager.py::BookManager` が入口で、章ごとに（通常モードと同じ）`run_pipeline()` を呼び出し、処理後の統合を `core/engine/p3_structure/state_integrator.py::StateIntegrator` が担当します。

### 2.2 5 フェーズパイプライン

| フェーズ | ファサード | 役割 | 状態ファイル |
|---|---|---|---|
| Phase 1 | `phase1_preprocessor.py` | PDF OCR またはテキスト解析 → `RawChunk` 列に正規化 | `phase1_preprocessor.json` |
| Phase 2 | `phase2_meta.py` | DNA 抽出（要約・キーワード・論文の輪郭） | `phase2_meta.json` |
| Phase 3 | `phase3_structure.py` | 論理構造ツリー（`TreeNode`）の構築 | `phase3_structure.json`, `phase3_sections.json` |
| Phase 4 | `phase4_translate.py` | スライディングウィンドウ翻訳 | `phase4_translate.json` |
| Phase 5 | `phase5_export.py` | Markdown / Workflowy テキストへの出力 | `_p2.md`, `_p2.txt` |

各フェーズの中間状態は `state/<session_id>/` 配下に JSON として永続化されます。これにより `--resume <N>` で任意のフェーズから再開できます（例: Phase 4 の翻訳だけをやり直す、Phase 1 の重い OCR をスキップする、など）。長時間・高コストな LLM 呼び出しを含むパイプラインでは、この再開可能性が実運用上の生命線になっています。

### 2.3 エンジン層 (`core/engine/`)

フェーズファサードは薄いオーケストレーション層で、実際のアルゴリズムはここに住んでいます。

| サブパッケージ | 主なモジュール | 役割 |
|---|---|---|
| `p1_ingest/` | `docling_ingester.py`, `physical_ingester.py`, `ocr_manager.py`, `text_structure_extractor.py`, `spread_splitter.py`, `formatter.py` | PDF/テキストの取り込みと正規化 |
| `p3_structure/` | `tree_builder.py`, `heading_matcher.py`, `chapter_parser.py`, `chapter_extractor.py`, `toc_extractor.py`, `state_integrator.py` | 論理構造ツリー・章境界の構築 |
| `p4_translate/` | `parallel_translator.py`, `prompt_builder.py`, `tree_reconstructor.py` | 並列翻訳とツリーへの再統合 |
| `p5_export/` | `workflowy_engine.py`, `markdown_engine.py`, `text_book_integrator.py`, `formatter_utils.py` | 最終出力形式への変換 |
| （直下） | `meta_analyzer.py` | Phase 2 の DNA 抽出ロジック |

### 2.4 データモデル (`core/models.py`)

パイプライン全体を貫く 3 つのデータクラスがあります。

- **`RawChunk`**: Phase 1 の出力単位。テキストに加え `role`（`h1`/`p`/`metadata`/`note` など）と物理情報（`font_size` / `is_bold` / `bbox`）を持つ。
- **`TreeNode`**: Phase 3〜5 で使う構造化ツリーノード。`RawChunk` と同様に物理情報を保持しつつ、`children` で階層を表現し、`translation` フィールドに Phase 4 の訳文が入る。
- **`ChapterBoundary`**: 書籍モード Phase 3 の出力。章の境界（`title` / `role` / `start_page` / `paragraphs`）を表す、PDF 物理解析の詳細を隠蔽したクリーンなインターフェース。

いずれも `to_dict()` / `from_dict()` を持ち、`state/` への JSON 永続化と `--resume` 時の読み込みに使われます。`from_dict()` が未知のキーを無視する実装になっているのは、フィールド追加後に古いセッション JSON を読んでも壊れないようにするためです。

### 2.5 LLM クライアント (`core/llm_client.py`)

全ての Gemini API 呼び出しがここを通ります。特徴的なのは `TierManager`（シングルトン）で、429/503 エラーを検知すると自動的に有料ティアから無料ティア（Lite モデル）へダウンシフトする自己修復機構を持っています。モデル名やプロンプトのハードコードを避けるため、プロンプトは `core/coreprompts.json` に一元管理され、`@lru_cache` でキャッシュされます（変更後はプロセス再起動が必要な点に注意）。

---

## 3. 横断的関心事

個別のフェーズに属さない、システム全体を貫く仕組みです。

- **状態管理と再開**: `state/<session_id>/` への JSON 永続化と `--resume` は、全フェーズ共通の仕組みです。新しいフェーズやデータモデルを追加する際も、この永続化・復元の互換性を壊さないことが `.cursor/rules/` のルールとして明文化されています。
- **物理データ主権**: OCR・構造抽出では「VLM（視覚言語モデルによる論理的判断） > 物理証拠（フォントサイズ・太字・座標） > 幾何的ヒント」という優先順位が徹底されています（`.cursor/rules/20-vlm-determinism.mdc`）。物理情報は VLM の判断を裏付ける証拠として使うのであって、物理情報だけで構造を決定しないという設計思想です。
- **入力ルーティングの自動判定**: PDF はまず `is_docling_viable()`（`p1_ingest/docling_ingester.py`）でデジタル PDF かどうかを判定し、可能なら Docling を優先します。不向きなスキャン PDF は VLM または物理抽出（`pdf_ingester.py::run_pdf_ingestion_v3`）にフォールバックします。テキスト入力は段落分割の後、`TextStructureExtractor`（LLM）が見出しを推定します。
- **書籍モードの統合**: 章ごとに独立した `run_pipeline()` 呼び出しの結果を `StateIntegrator` が後段で統合します。`chN_` プレフィックス付与・見出し昇格・重複タイトル除去は、章間の一貫性を保つための必須ステップです。

---

## 4. 主要な設計判断とその理由

コードだけを読んでも意図が伝わりにくい判断を挙げます。

- **英日の非対称階層**: 英語は原文の論理構造を保つためネスト、日本語は Workflowy 上で読み流せるよう並列展開。これは Workflowy というツールの UX（親子を畳んで俯瞰する／並列展開で流し読みする）を前提にした選択で、単なる見た目の違いではありません。
- **References 除外・Appendix 保持**: 出力の実用性を優先し、参考文献リストは除外、付録は保持するという明示的な不変条件があります（`.cursor/rules/30-export-standards.mdc`）。
- **VLM 主権**: OCR やレイアウト解析で物理証拠（フォント・座標）とテキストの論理的判断が食い違う場合、VLM の判断を優先します。学術 PDF はレイアウトが崩れがちで、物理情報だけに頼ると誤判定しやすいという実運用上の教訓に基づいています。
- **[Unlabeled Section] は仕様**: 論文の Abstract 直後に見出しのない Introduction 本文が続くパターン（NST 論文などで顕著）は `[Unlabeled Section]` として独立させます。これはバグではなく、見出しなし本文の意味的まとまりを壊さないための正しい挙動です。問題になるのは、本来ある見出しが検出漏れで前セクションに吸収されるケースのみです。
- **テキスト分割の自動判定**: `\n\n` 分割後のチャンク数が `\n` 分割後の 1/10 未満なら「Acrobat 形式（1行=1段落）」と判定し `\n` 分割に切り替えます。コピー元アプリによって改行の意味が変わることへの実務的な対処です。
- **Phase 4 の並列数**: デフォルト `max_concurrent_sections=4`。直列化（=1）は実測で約 50% 遅く、`--concurrent 8` はさらに速いが分散も大きい、というベンチマーク結果に基づくデフォルト値です（詳細: `docs/model_optimization.md`）。

---

## 5. テストとデータ資産

`tests/unit/` にユニットテストがあり、`data/input/` には理想出力付きのテスト資産（論文の平文/PDF 版、書籍サンプル）が揃っています。特に `paperpdf/NST/` は最高品質参照用として扱われています。新しい抽出・翻訳ロジックを検証する際は、まずこれらの既存資産で回帰がないかを確認するのが定石です。

---

## 6. もっと詳しく知るには

- 個々のフェーズ・モジュールの詳細な責務: 各 `core/phaseN_*.py` のモジュール docstring
- 設計原則の正本（実装変更時に従うべきルール）: `.cursor/rules/*.mdc`
- モデル選定・並列数などの運用パラメータの根拠: `docs/model_optimization.md`
- 仕様変更・不具合の履歴: `docs/management/requirements_log.md`, `docs/management/troubleshooting_log.md`
- Claude Code 向けの操作リファレンス（コマンド・環境変数など）: `CLAUDE.md`
