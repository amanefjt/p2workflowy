# p2workflowy v2.1 System Manual

## 1. System Overview
**p2workflowy** は、学術論文を Workflowy 形式の階層構造アウトラインに変換するための専門的なパイプラインツールです。
Gemini 3 Flash の強力なロングコンテキストと処理速度を活かし、長文の論文でも精度を落とさず処理します。

- **推奨モデル**: `gemini-3-flash-preview`

## 2. コア・アーキテクチャ

### 2.1 階層的分割戦略 (Hierarchical Chunking)
翻訳品質と文脈維持を両立するため、`src/skills.py` では以下の優先順位でテキストを分割します。
1. **H2 見出し**: 章や主要セクション
2. **H3 見出し**: 節
3. **H4 見出し**: 項
4. **段落 (\n\n)**: 見出しがない場合、または見出し内が長すぎる場合

これにより、文の途中でテキストが切断されることを防ぎ、AIが前後の脈絡を正確に把握した状態で翻訳を行うことができます。

### 2.2 処理パイプライン (Sequential Flow)
1. **Phase 1: レジュメ生成 (Summarize)**:
   - 全文から論理構造を抽出し、日本語で詳細な要約を作成します。
2. **Phase 2: 構造化 (Structure)**:
   - レジュメの見出しをガイドにして、OCRのノイズを除去しながら英語の Markdown に整形します。
3. **Phase 3: 並列翻訳 (Translate)**:
   - 分割されたセクションを並列に翻訳します。同時実行数は `3` に制限されています。
4. **Phase 4: 統合と変換 (Assembly)**:
   - レジュメと翻訳本文を Workflowy 形式（2スペースインデント）に変換し、一つのファイルにまとめます。

## 3. プロンプト管理 (`shared/prompts.json`)
- **STRUCTURING_WITH_HINT_PROMPT**: 構造化用。レジュメのアウトラインに沿った整形を指示。
- **SUMMARY_PROMPT**: 日本語レジュメ用。詳細な論理展開（CoT）を要求。
- **TRANSLATION_PROMPT**: 翻訳用。用語集（Glossary）の適用と文体の維持。

## 4. ファイル構成
- `src/main.py`: パイプラインのエントリポイント。
- `src/skills.py`: AI処理のコアロジック。
- `src/utils.py`: ファイル操作、Workflowy変換、テキスト整形。
- `src/llm_processor.py`: Gemini API との通信（リトライ・進捗通知）。
- `shared/prompts.json`: AIへの全指示（プロンプト）。

## 5. 開発・運用
- **中間ファイル**: `intermediate/` フォルダに一時的な生成物が保存されます。
- **成果物**: `_output.txt` (Workflowy形式) および `_structured_eng.md` (英語形式) が入力ファイルと同じディレクトリに生成されます。
