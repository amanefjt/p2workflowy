---
name: p2workflowy Pipeline Execution
description: PDFテキストからWorkflowy形式の翻訳・要約を作成するパイプラインの実行手順
---

# p2workflowy Pipeline Execution

学術論文のテキストデータ（`input.txt`）を入力とし、構造化・要約・翻訳を経て `Final_Output.txt` を生成する一連のプロセス。

## 実行手順

1. **入力ファイルの配置**:
   - 処理対象のテキストを `input.txt` に保存する。

2. **パイプラインの実行**:
   - 以下のコマンドを実行し、パイプラインを起動する。
   ```bash
   python -m src.main
   ```

3. **出力の確認**:
   - 処理完了後、`output/Final_Output.txt` が生成される。
   - 内容に余分なノイズ（指示文など）が含まれていないか確認する。

## 処理フェーズ

1. **Cognitive Structuring**:
   - テキストをMarkdown構造に復元し、不要なセクションを除去する。
   - 中間ファイル: `intermediate/structured.md`

2. **Summarization**:
   - 構造化されたテキストから、Workflowy形式のアウトライン要約を作成する。

3. **Academic Translation**:
   - セクションごとに分割し、辞書 (`glossary.csv`) を適用しながら翻訳を実行する。
   - `<result>` タグによる出力隔離を行い、クリーンなテキストを結合する。

## トラブルシューティング

- **404 Error (Model Not Found)**:
  - `src/constants.py` の `DEFAULT_MODEL` が有効なモデル名（例: `gemini-2.0-flash`）になっているか確認する。
- **指示文の混入**:
  - `src/skills.py` の `<result>` タグ抽出ロジックが正しく機能しているか確認する。
