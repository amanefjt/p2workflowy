---
name: p2workflowy パイプライン
description: 学術論文・書籍を Workflowy 形式に変換する高度なパイプライン（v1.6）
---

# p2workflowy パイプライン

## 概要
`p2workflowy` は、PDF 等から抽出されたテキストを、AI（Gemini）を使って構造化・要約・翻訳し、Workflowy へ直接貼り付け可能な形式に変換するツールです。

## インターフェース
- **CLI版**: `python3 -m src.main` で実行。
- **Web版**: React + Vite で構築された静的 Web アプリ（`web/` ディレクトリ）。`shared/prompts.json` を共有。

## 処理モード

### 1. 論文モード (Paper Mode)
数十ページ程度の文書向け。全体要約 → 並列翻訳。

### 2. 書籍モード (Book Mode)
100ページ超の書籍向け。以下の5段階フェーズで実行。

- **Phase 1: TOC Extraction**: 冒頭部分から目次(TOC)と各章の開始アンカーを抽出。
- **Phase 1.5: Overall Summary**: 本全体の要約を生成。
- **Phase 2: Deterministic Splitting**: Python コードにより、アンカーテキストを用いて正確に章ごとに分割。
- **Phase 3: Chapter Processing (Parallel)**: 各章ごとに「構造化」→「章要約生成」→「翻訳」を並列実行。
- **Phase 4: Assembly**: 全ての成果物を統合し、Workflowy 形式で出力。

## v1.6 の改善点
- **タイトルベースの命名**: 出力ファイル名を入力ファイル名ではなく、抽出された「文書タイトル」に基づき生成 (`Utils.sanitize_filename`)。
- **出力整理**: 要約ファイルなどの中間生成物は `intermediate/` ディレクトリに集約。
- **並列制御**: API レートリミット回避のため、`asyncio.Semaphore(5)` により同時実行数を制限。

## コアロジック
- **Python版**: `src/main.py`, `src/skills.py`, `src/utils.py`
- **Web版**: `web/src/lib/gemini.ts`, `web/src/lib/formatter.ts`

## 推奨設定
- **Model**: `gemini-3-flash-preview`（高速・長文対応）
- **Glossary**: `glossary.csv` による用語統一。
- **文体**: 常体（だ・である調）。
