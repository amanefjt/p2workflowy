# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

英語学術論文・専門書籍の PDF/テキストを Gemini AI で解析し、Workflowy 向けの階層 Markdown に変換するツール。CLI (`main.py`) と Web API (`server.py`) の両モードを持つ。PDF（VLM OCR / Docling）とプレーンテキスト（Acrobat 抽出等）の両入力に対応。

全体の設計思想・「なぜこうなっているか」を含む説明は `docs/ARCHITECTURE.md` を参照(本ファイルは Claude Code 向けの運用リファレンス、ARCHITECTURE.md は人間・AI 双方向けの設計解説という役割分担)。

## 環境セットアップ

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # GEMINI_API_KEY または GOOGLE_API_KEY を設定
```

必須環境変数: `GEMINI_API_KEY`（または `GOOGLE_API_KEY`）。`APP_ADMIN_PASSCODE` は Web 版で管理者がサーバー側キーを使う際に必要。

## よく使うコマンド

```bash
# Web サーバー起動
python3 server.py

# CLI: 論文モード（テキスト or PDF）
python3 main.py data/paper.txt
python3 main.py data/paper.pdf

# CLI: 書籍モード
python3 main.py data/book.pdf --book

# CLI: テスト用低コストモード（gemini-3.1-flash-lite）
python3 main.py data/paper.txt --lite

# CLI: 中断再開（フェーズ番号 1-5 を指定）
python3 main.py data/paper.pdf --session <session_id> --resume 4

# テスト実行（全体）
python3 -m pytest tests/unit/ -v

# テスト実行（単一ファイル）
python3 -m pytest tests/unit/test_json_pipeline.py -v
```

## アーキテクチャ

### パイプライン（5 フェーズ）

`main.py` / `server.py` → `core/pipeline.py::run_pipeline()` → 各フェーズ

| フェーズ | モジュール | 役割 | 状態ファイル |
|---|---|---|---|
| Phase 1 | `core/phase1_preprocessor.py` | PDF OCR またはテキスト解析 | `phase1_preprocessor.json` |
| Phase 2 | `core/phase2_meta.py` | DNA 抽出・要約・キーワード | `phase2_meta.json` |
| Phase 3 | `core/phase3_structure.py` | 論理構造ツリー構築 | `phase3_structure.json`, `phase3_sections.json` |
| Phase 4 | `core/phase4_translate.py` | スライディングウィンドウ翻訳 | `phase4_translate.json` |
| Phase 5 | `core/phase5_export.py` | Markdown / Workflowy 出力 | `_p2.md`, `_p2.txt` |

中間状態は `state/<session_id>/` に JSON として保存される。`--resume <N>` で任意フェーズから再開可能。

### エンジン層（`core/engine/`）

各フェーズの内部ロジックは `core/engine/` 配下のサブパッケージに閉じ込められている。フェーズのファサード（`core/phaseN_*.py`）はオーケストレーションのみ担当し、アルゴリズムはエンジン層に置く。

サブパッケージは `p1_ingest/`（PDF/テキスト取り込み）, `p2_meta/`（DNA 抽出ロジック）, `p3_structure/`（構造ツリー・章境界構築）, `p4_translate/`（並列翻訳）, `p5_export/`（Markdown/Workflowy出力）の5つ。個別モジュールの最新一覧・役割は **`docs/ARCHITECTURE.md` §2.3** を参照(このファイルには詳細を重複して書かない — 移設のたびに陳腐化するため)。

### Phase 1 の入力ルーティング

`run_phase1_unified()` が `.pdf` か否かで自動判定する。

- **PDF ルート** (`_run_phase1_pdf`):
  - まず `is_docling_viable()` で Docling 適用可否を判定し、デジタル PDF なら Docling（`docling_ingester.py`）を優先使用。
  - Docling が不適なスキャン PDF 等は `run_pdf_ingestion()` → VLM または物理抽出にフォールバック。
- **テキストルート** (`_run_phase1_text`): 段落分割 → `TextStructureExtractor` (LLM) で見出し抽出 → role 付き RawChunk

**テキスト分割の自動判定**: `\n\n` 分割後チャンク数が `\n` 分割後の 1/10 未満なら、Acrobat 形式（1行=1段落）と判定して `\n` 分割を使う。

### Phase 2 の DNA と intro_pre_heading

Phase 2 が抽出する DNA には `intro_pre_heading`（最初の節タイトル前の見出しなし序論のチャンク ID 範囲）が含まれる。Phase 3 はこれを使って見出しなし Introduction を独立した `[Unlabeled Section]` として正しく分離する。

### Phase 3 の [Unlabeled Section] の扱い

学術論文では Abstract 後に見出しのない Introduction 本文が続くパターンが一般的（NST 論文等）。このケースは `[Unlabeled Section]` として正しい動作であり、バグではない。問題となるのは本来ある節タイトルが検出されずに前のセクションに吸収される場合のみ。

### 書籍モード

`core/book_manager.py` が入口。章ごとに `run_pipeline()` を呼び出し、章処理後の統合は `core/engine/p3_structure/state_integrator.py` で行う。`BookManager` は `run_pipeline()` の戻り値（出力ファイルパス）を `chapter_sessions[].output_paths` に記録して `StateIntegrator` に渡す（パス推定なし）。

章の境界抽出は `core/engine/p3_structure/chapter_parser.py::ChapterParser` が担当し、`List[ChapterBoundary]` を返す。

### LLM クライアント（`core/llm_client.py`）

- **TierManager（シングルトン）**: 429/503 エラーで自動的に FREE ティア（Lite モデル）へダウンシフトする自己修復機構。
- **モデル情報の参照順序**: 
  - **Gemini API 共通知識**（モデル一覧・thinking_level・無料枠・廃止情報）は `docs/gemini_models.md` を参照。これは `~/Code/shared/gemini_models.md` から同期された共通ドキュメント（直接編集禁止）。
  - **p2workflowy 固有の運用**（フェーズ別ルーティング・ベンチマーク）は `docs/model_optimization.md` を参照。
  - **runtime の設定値** `core/coreprompts.json` の `DEFAULT_MODEL` / `DEFAULT_MODEL_FREE` / `DEFAULT_MODEL_VLM` は `model_optimization.md` に合わせて更新する。実装とドキュメントが不一致の場合はドキュメント側に揃える。
- **Phase 4 並列数**: デフォルト `max_concurrent_sections=4`。直列化（=1）は実測で約 50% 遅く（338s vs 227s）避けるべき。`--concurrent 8` はさらに速いが高分散。詳細は `docs/model_optimization.md` Section 3 参照。

### プロンプト管理（`core/coreprompts.json`）

全プロンプトを一元管理。`@lru_cache` でキャッシュされるため**変更後はプロセス再起動が必要**。`TEXT_STRUCTURE_EXTRACTION_PROMPT`（テキスト入力の見出し抽出用）を含む。

### データモデル（`core/models.py`）

- `RawChunk`: Phase 1 出力。`role`（h1/h2/p）・物理情報（font_size/is_bold/bbox）付き。
- `TreeNode`: Phase 3-5 で使用する構造化ツリーノード。
- `ChapterBoundary`: Book モード Phase 3 の章境界。`title`, `role`, `start_page`, `paragraphs` を持つ。

### テスト資産（`data/input/`）

| ディレクトリ | 内容 |
|---|---|
| `paperplain/AL/` | Arbitrary Locations 論文（テキスト）+ 理想出力 |
| `paperplain/NST/` | NST 論文（テキスト）+ 理想出力 |
| `paperpdf/AL/` | 同論文 PDF + 理想出力 |
| `paperpdf/NST/` | 同論文 PDF + 理想出力（最高品質参照用） |
| `Booksample/` | 書籍 PDF 3冊（理想出力なし） |

## 設計原則

- **判断優先順位**: `VLM の論理役割判断 > 物理証拠（フォント・座標）> 幾何的ヒント`。レイアウトが複雑な PDF では Route C（全ページ VLM）を優先し、中途半端な混在モードは避ける。OCR 補正は「VLM が特定した位置の Native テキストで肉付けする」方針を守る。
- **責務境界**: `main.py` はエントリーポイント専任。`run_pipeline` はオーケストレーション専任。個別アルゴリズムは各フェーズモジュールに閉じ込める。
- **出力形式**: `_p2.md` / `_p2.txt` が標準。Workflowy では英語ブロックを親子ネスト、日本語ブロックを並列展開する非対称階層を維持する。
- **エクスポート不変条件**: `References` 系セクションは出力から除外し、`Appendix` は保持する。注釈ノードは言語ブロック末尾へ再配置する。
- **設計の正本**: 実装変更の判断根拠は常に `core/` 配下の現行コードを優先する。本ファイルや `docs/ARCHITECTURE.md` はあくまで補助情報であり、コードと矛盾する場合はコードを信じてドキュメント側を直す。
- **機密ファイルの扱い**: `.env` など機密情報を含みうるファイルは不用意に読み書きしない。

## 変更管理

仕様変更や判断根拠は `docs/management/requirements_log.md` に追記する。不具合の原因・再現手順・対策は `docs/management/troubleshooting_log.md` に追記する（`core/` 変更を含むコミットでこの追記が漏れていないかは `.claude/hooks/check_management_logs.sh` が `git commit` 時に注意喚起する）。モデルティアの切替ロジックを変更した場合は、対象フェーズと理由もここに残す。

完了宣言前の構造検証チェックリストは `golden-verification` skill、構造・翻訳まわりのデバッグ手順は `p2workflowy-debug` skill を参照（`.claude/skills/` 配下、Claude Code が状況に応じて自動候補に挙げる）。

## デプロイ

Hugging Face Spaces（Docker）向け: ポート `7860` で `uvicorn` を起動。`Dockerfile` 参照。
