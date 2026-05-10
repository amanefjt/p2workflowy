---
title: p2workflowy
emoji: 📚
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# 📚 p2workflowy

**学術論文・専門書籍を、Workflowy で深く読むための日本語アウトラインに変換するツール**

Gemini AI が論文・書籍の構造を解析し、英語原文と日本語訳を対応させた階層 Markdown を生成します。PDF アップロードまたはテキスト貼り付けに対応。

---

## 🌐 Web 版で今すぐ使う

**[https://p2workflowy.pages.dev](https://p2workflowy.pages.dev)**

ブラウザだけで利用できます。インストール不要です。

### 使い方（Web 版）

1. **[Google AI Studio](https://aistudio.google.com/app/apikey)** で Gemini API キーを取得する
   - 無料枠で利用できますが、Googleの仕様上、支払い手段の登録が必要です
   - 入力したキーはあなたのブラウザにのみ保存され、サーバーには送信されません
2. Web UI でAPIキーと専門分野を入力する
3. 論文 PDF をアップロード、またはテキストを貼り付ける
4. 変換完了後、`.md`（英語+日本語）と `.txt`（Workflowy 用）をダウンロードする

---

## 📄 出力形式について

生成されるファイルは **Workflowy** のアウトライン構造に最適化されています。

```
論文タイトル
  ├─ 1. Introduction          ← 英語セクション見出し（H3）
  │    └─ 1. はじめに          ← 日本語見出し（H2）
  │         ├─ [英語原文段落]
  │         └─ 【日本語訳段落】
  └─ 2. Methods
       └─ ...
```

- `.md` ファイル：Obsidian・Notion などでの閲覧に適した Markdown
- `.txt` ファイル：Workflowy へのインポートに適したインデント形式

---

## ⚙️ 主な機能

### 論文モード（デフォルト）
学術論文の構造（Abstract → Introduction → Methods → ...）を自動認識し、各セクションを翻訳します。PDF または Acrobat でコピーしたテキストの貼り付けに対応。

### 書籍モード
目次（TOC）を AI で解析し、章・節ごとに分割して処理します。スキャン書籍（画像 PDF）も VLM（視覚言語モデル）による OCR で対応。

### 用語集（グロッサリー）
専門用語の翻訳ルールを CSV で事前登録すると、訳語が統一されます（任意）。

---

## 🖥 ローカル / CLI 版セットアップ

```bash
git clone https://github.com/amanefjt/p2workflowy.git
cd p2workflowy
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # GEMINI_API_KEY を設定
```

### CLI での実行

```bash
# 論文モード（PDF）
python3 main.py data/paper.pdf

# 論文モード（テキスト）
python3 main.py data/paper.txt

# 書籍モード
python3 main.py data/book.pdf --book

# Web サーバー起動（http://localhost:8000）
python3 server.py
```

---

## 📂 ディレクトリ構成

```
p2workflowy/
├── core/          # パイプライン・翻訳エンジン（5フェーズ）
├── web/           # Web UI フロントエンド
├── docs/          # 仕様書・設計ドキュメント
├── tests/         # ユニットテスト
└── data/          # サンプル入力・テスト資産
```

---

## 📄 ライセンス

MIT License
