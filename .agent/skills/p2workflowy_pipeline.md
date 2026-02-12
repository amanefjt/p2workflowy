```
---
name: p2workflowy パイプライン
description: 学術論文を Workflowy 形式に変換する三段階パイプライン
---

# p2workflowy パイプライン

## 概要
`p2workflowy` は、PDF から抽出された乱雑なテキストを、AI（Gemini）を使って美しく構造化・翻訳し、Workflowy へ直接貼り付け可能な形式に変換するツールです。

## インターフェース
- **CLI版**: `python3 -m src.main` で実行する Python スクリプト
- **Web版**: React + Vite で構築された静的 Web アプリ（`web/` ディレクトリ）
  - ブラウザ完結型（サーバー不要）
  - BYOK (Bring Your Own Key) モデル
  - Cloudflare Pages 等で公開可能

## 三段階パイプライン

### Phase 1: Semantic Mapping (要約生成)
- **目的**: 原文から直接「論理構造の地図（要約）」を作成する
- **出力**: 論文の主要な章立て、議論の展開を示す Markdown 形式の要約
- **プロンプト**: `SUMMARIZATION_PROMPT` (constants.ts/py)

### Phase 2: Anchored Structuring (構造化)
- **目的**: Phase 1 の要約を「ヒント」として LLM に渡し、原文の構造化を行う
- **重要**: 原文の見た目（フォントサイズ等）より、要約の論理構造を優先して見出しを付与
- **プロンプト**: `STRUCTURING_WITH_HINT_PROMPT` (constants.ts/py)

### Phase 3: Contextual Translation (文脈を考慮した翻訳)
- **目的**: 構造化された Markdown を翻訳する
- **特徴**: Phase 1 の要約をコンテキストとして全スレッド（チャンク）に共有
- **並列処理**: セクションごとに並列翻訳を実行（高速化）
- **プロンプト**: `TRANSLATION_PROMPT` (constants.ts/py)

## コアロジック
- **Python版**: `src/skills.py`, `src/utils.py`, `src/llm_processor.py`
- **TypeScript版**: `web/src/processor.ts`, `web/src/utils.ts`
- 両者は同じロジックを実装しており、動作は完全に一致する

## 設定可能なパラメータ
- **API Key**: Gemini API キー（必須）
- **Model**: `gemini-3-flash-preview` (推奨) または `gemini-2.5-flash`
- **Glossary**: 用語辞書（CSV形式、オプション）
.csv`) を適用しながら翻訳を実行する。
   - `<result>` タグによる出力隔離を行い、クリーンなテキストを結合する。

## トラブルシューティング

- **404 Error (Model Not Found)**:
  - `src/constants.py` の `DEFAULT_MODEL` が有効なモデル名（例: `gemini-2.0-flash`）になっているか確認する。
- **指示文の混入**:
  - `src/skills.py` の `<result>` タグ抽出ロジックが正しく機能しているか確認する。
