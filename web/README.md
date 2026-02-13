# p2workflowy Web

PDFなどのテキストをWorkflowy形式に変換するためのWebツールです。
Google Gemini APIを使用し、構造化・翻訳・整形を行います。

## 特徴
- **テキスト構造化**: 崩れたテキスト（PDFからのコピーなど）を正しいMarkdown構造に復元。
- **Workflowy形式**: インデント付きのテキストとして出力。
- **翻訳機能**: 辞書を使用した高精度な翻訳（オプション）。
- **セキュア**: APIキーはブラウザにのみ保存されます。

## 使い方
1. Google Gemini APIキーを入力（初回のみ）。
2. 必要に応じて辞書ファイルをアップロード。
3. テキストファイル (.txt) をドラッグ＆ドロップ。
4. 処理結果をコピーしてWorkflowyに貼り付け。

## 開発・デプロイ
```bash
npm install
npm run dev
# ビルド
npm run build
```
