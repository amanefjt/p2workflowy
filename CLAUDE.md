# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

英語学術論文・専門書籍の PDF/テキストを Gemini AI で解析し、Workflowy 向けの階層 Markdown に変換するツール。CLI (`main.py`) と Web API (`server.py`) の両モードを持つ。PDF（VLM OCR）とプレーンテキスト（Acrobat 抽出等）の両入力に対応。

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

# テスト実行
python3 -m pytest tests/unit/ -v
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

### Phase 1 の入力ルーティング

`run_phase1_unified()` が `.pdf` か否かで自動判定する。

- **PDF ルート** (`_run_phase1_pdf`): `run_pdf_ingestion_v3()` → VLM または物理抽出 → role 付き RawChunk
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
- **モデル選択**: `core/coreprompts.json` の `DEFAULT_MODEL` / `DEFAULT_MODEL_FREE` / `DEFAULT_MODEL_VLM` が真実ソース。
- **Phase 4 並列数**: `max_concurrent_sections=4` が最適値。**絶対に 1（直列化）にしてはならない**（API 側の 240 秒 TTFT キューイングを並列で相殺する設計）。

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

- **判断優先順位**: `VLM の論理役割判断 > 物理証拠（フォント・座標）> 幾何的ヒント`
- **責務境界**: `main.py` はエントリーポイント専任。`run_pipeline` はオーケストレーション専任。個別アルゴリズムは各フェーズモジュールに閉じ込める。
- **出力形式**: `_p2.md` / `_p2.txt` が標準。Workflowy では英語ブロックを親子ネスト、日本語ブロックを並列展開する非対称階層を維持する。
- **ルール正本**: `.cursor/rules/` を唯一の正本として更新する（`.agent/` は凍結互換レイヤー）。

## デプロイ

Hugging Face Spaces（Docker）向け: ポート `7860` で `uvicorn` を起動。`Dockerfile` 参照。
