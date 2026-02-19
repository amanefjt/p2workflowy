---
name: p2workflowy パイプライン
description: 学術論文を Workflowy 形式に変換する高度なパイプライン（v2.1）
---

# p2workflowy パイプライン (v2.1)

## 概要
`p2workflowy` は、PDF 等から抽出されたテキストを、AI（gemini-3-flash-preview）を使って「レジュメ生成」「構造化」「翻訳」を行い、Workflowy へ直接貼り付け可能な形式に変換するツールです。

## バージョン履歴
- **v2.1 (2026-02-16)**:
    - **Cleanup & Simplify**: 未使用の書籍モードロジックや実験的なサニタイズ処理を削除し、コードの可読性を向上。
    - **Heading-Aware Chunking**: Markdownの見出し階層（H2, H3, H4）を考慮した意味的な分割ロジックを導入。
- **v2.0 (2026-02-14)**:
    - **Chunked Processing**: 全文一括処理から、並列チャンク処理への移行。

## 処理フロー (Summary-First Sequential Pipeline)

### 1. Phase 1: Semantic Mapping (レジュメ生成)
- 原文全体から、リサーチ・クエスチョン、核心的主張、各セクションの論理展開を抽出。
- 日本語で詳細なレジュメを作成。

### 2. Phase 2: Anchored Structuring (構造化)
- 生成されたレジュメをヒント（構造定義）として、汚い OCR テキストをきれいな Markdown に整形。
- チャンク並列処理により、長文でも脱落なく最後まで構造化。

### 3. Phase 3: Contextual Translation (並列翻訳)
- 構造化された Markdown を、見出し階層を考慮して適切に分割。
- `asyncio.Semaphore` による流量制御を行いながら、Gemini で高精度翻訳。

### 4. Phase 4: Assembly (結合)
- レジュメと翻訳本文を統合し、Workflowy 形式（インデント付きリスト）に変換。

## コアロジック (`src/skills.py`)
- `_split_markdown_hierarchically`: 見出しレベル（H2 > H3 > H4 > 段落）に基づき、文脈を壊さずにテキストを分割する。

## 推奨設定
- **Model**: `gemini-3-flash-preview` (実効出力トークン上限: 8,192)
- **Chunk Size**: `6,000` 文字 (v2.1 改訂)
    - ※ドキュメント上は 65,536 トークンなどの記載がある場合でも、書籍モードのような長文構造化タスクでは 8,192 トークン付近で生成が停止する事例が確認されています。安全のため 6,000 文字単位で分割処理を行います。
## AI学習データに関する重要事項
- **AIの知識更新**: AIエージェント（私）の内部学習データに含まれる「Gemini 1.5が最新」という情報は古く、最新ではありません。ユーザーとの対話および最新ドキュメントにより、**Gemini 3 Flash (Preview)** が現時点での最新モデルであり、最大65,536トークンの出力能力を持つことが確認されました。本プロジェクトの設計（`max_output_tokens=30000`設定等）は、このユーザー提供の最新情報に基づいています。
