# p2workflowy 使用・デプロイガイド

`p2workflowy` は、Webブラウザ版（React）と Python CLI版の2種類を提供しています。

## 1. Web版 (推奨)
ブラウザだけで完結し、環境構築が不要です。

### 🚀 公開・デプロイ方法
Web版は静的サイトとして Cloudflare Pages や Vercel にデプロイ可能です。
1. `web/` ディレクトリに移動します。
2. 依存関係のインストール: `npm install`
3. ビルド: `npm run build`
4. `web/dist` フォルダの内容をホスティングサービスにアップロードします。

### 🔐 セキュリティ
- ユーザーが入力した API キーはブラウザのローカルストレージにのみ保存され、サーバーへ送信されることはありません。

---

## 2. Python CLI版
大量のファイルを一括処理したり、ローカルスクリプトとして組み込む場合に適しています。

### 💻 セットアップ
1. 依存関係のインストール: `pip install -r requirements.txt`
2. `.env` ファイルに `GEMINI_API_KEY` を設定、またはコマンド実行時に提供します。

### 🚀 実行方法
```bash
python -m src.main --input path/to/your_paper.txt
```

---

## 💡 共通の注意事項
- **Gemini API キー**: どちらのバージョンも、利用にはご自身の [Google AI Studio](https://aistudio.google.com/) から発行された API キーが必要です。
- **コスト**: Gemini 1.5 Flash の無料枠の範囲内で十分に動作しますが、最新の利用規約を確認してください。
