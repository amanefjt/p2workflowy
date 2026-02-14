---
name: p2workflowy パイプライン
description: 学術論文・書籍を Workflowy 形式に変換する高度なパイプライン（v2.0）
---

# p2workflowy パイプライン (v2.0)

## 概要
`p2workflowy` は、PDF 等から抽出されたテキストを、AI（gemini-3-flash-preview）を使って構造化・要約・翻訳し、Workflowy へ直接貼り付け可能な形式に変換するツールです。
v2.0では、**堅牢なチャンク処理**と**厳格なプロンプト制御**により、長文処理の安定性と精度が飛躍的に向上しました。

## バージョン履歴
- **v2.0 (2026-02-14)**:
    - **Chunked Structuring**: Paper/Book両モードで、段落を考慮したスマートなチャンク分割 (`_split_text_by_length`) と並列構造化を導入。
    - **Strict Prompts**: Meta-commentary（AIのひとりごと）を徹底排除する厳格なプロンプト (`STRUCTURING_WITH_HINT_PROMPT`, `BOOK_STRUCTURING_PROMPT`) を採用。
    - **Sanitization**: 出力後の重複ヘッダーやハルシネーション（Introductionの勝手な挿入）を自動除去する `Utils` メソッドを強化。

## 処理モード

### 1. 論文モード (Paper Mode)
数十ページ程度の文書向け。
1. **Summarize**: 文書全体の要約を作成。
2. **Structure (Chunked)**: 全文を20,000文字単位（段落考慮）で分割し、並列にMarkdown構造化。これにより後半の脱落（Attention Drift）を防ぐ。
3. **Translate**: 構造化されたMarkdownをセクションごとに翻訳。

### 2. 書籍モード (Book Mode)
100ページ超の書籍向け。
1. **TOC Analysis**: 目次と章の境界（アンカー）を特定。
2. **Deterministic Splitting**: アンカーテキストを用いて物理的に章分割。
3. **Per-Chapter Processing**:
    - **Summarize**: 章ごとの要約。
    - **Structure (Chunked)**: 章の内容を分割並列処理で構造化（Paper Modeと同じロジック）。
    - **Translate**: 構造化データを基に翻訳。
4. **Assembly**: 全章を統合。

## コアロジック (`src/skills.py`)
- `_structure_text_chunked`: 共通化されたチャンク並列構造化ロジック。
- `_split_text_by_length`: 改行・段落を考慮したスマート分割ロジック。

## 推奨設定
- **Model**: `gemini-3-flash-preview`（高速・長文対応）
- **Prompt Rules**: "Original English ONLY", "No Meta-commentary"
