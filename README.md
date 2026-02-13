## p2workflowy (Python & Web)

A tool to convert academic papers/images into [WorkFlowy](https://workflowy.com/) compatible text format.

### Versions
- **Desktop (Python)**: CLI tool for PDF processing. See `src/`.
- **Web (React/Vite)**: Browser-based tool for Image processing. See `web/`.

## Web Version Quick Start
```bash
cd web
npm install
npm run dev
```

**PDFから抽出したテキストを、AIで構造化・要約・翻訳し、Workflowyへ直接貼り付け可能な形式に変換するツール**

学術論文やレポートのPDFを、意味のある階層構造を持つMarkdown形式に変換し、専門用語辞書を活用した正確な翻訳を行います。

---

## 🎯 主な機能

### 1. **構造復元 (Cognitive Structuring)**
バラバラになったPDFのテキストを、意味のあるMarkdown構造に修復します。
- タイトル、著者、アブストラクト、章、セクションを自動認識
- 段落の論理的な繋がりを復元

### 2. **Workflowy要約**
各セクションの論理展開（Chain of Thought）を階層構造で抽出します。
- 論文の骨格を把握しやすい形式で出力
- Workflowyへ直接コピー＆ペースト可能

### 3. **アカデミック翻訳**
専門用語辞書（`glossary.csv`）を適用しながら、正確な学術翻訳を行います。
- 分野固有の用語を一貫して翻訳
- 文脈を考慮した自然な日本語表現

### 4. **ノイズ除去**
参考文献やページ番号などの不要な情報を自動的に取り除きます。

---

## 📦 セットアップ手順

### 前提条件
- Python 3.10以上がインストールされていること
- Google Gemini APIキーを取得していること（[Google AI Studio](https://aistudio.google.com/)で無料取得可能）

### 1. リポジトリのクローン

```bash
git clone https://github.com/yourusername/p2workflowy.git
cd p2workflowy
```

### 2. 仮想環境の作成と有効化

```bash
python -m venv .venv
source .venv/bin/activate  # Windowsの場合: .venv\Scripts\activate
```

### 3. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 4. 環境変数の設定

プロジェクトのルートディレクトリに `.env` ファイルを作成し、以下の内容を記述してください:

```bash
# .env ファイルの内容
GEMINI_API_KEY=あなたのAPIキーをここに貼り付け
```

**📝 `.env` ファイルの作成方法:**
1. `.env.example` をコピーして `.env` にリネーム
2. `your_api_key_here` の部分を、[Google AI Studio](https://aistudio.google.com/)で取得したAPIキーに置き換える

> ⚠️ **重要**: `.env` ファイルは `.gitignore` に含まれているため、GitHubにアップロードされません。APIキーは絶対に公開しないでください。

---

## 🚀 使い方

### 基本的な実行方法

```bash
python -m src.main
```

実行すると、以下のように処理したいファイルのパスを聞かれます:

```
処理したいファイルのパスを入力してください: 
```

ファイルをターミナルにドラッグ＆ドロップするか、パスを直接入力してEnterを押してください。

### 出力ファイル

処理が完了すると、**入力ファイルと同じディレクトリ**に以下のファイルが生成されます:

- `(元のファイル名)_output.txt` - 最終的な翻訳済みMarkdown
- `intermediate/` ディレクトリ内に中間ファイル（英語版Markdown、要約など）

### 実行例

```bash
# 例: デスクトップにあるPDFを処理する場合
python -m src.main
# プロンプトが表示されたら:
/Users/yourname/Desktop/research_paper.pdf
```

---

## 📁 ディレクトリ構造

```
p2workflowy/
├── src/                    # ソースコード
│   ├── main.py            # メインエントリーポイント
│   ├── llm_processor.py   # Gemini API連携
│   ├── skills.py          # 各処理ステップの実装
│   ├── utils.py           # ユーティリティ関数
│   └── constants.py       # プロンプト定義
├── glossary.csv           # 専門用語辞書（カスタマイズ可能）
├── .env                   # APIキー設定（要作成）
├── .env.example           # .envのサンプル
├── requirements.txt       # 依存パッケージ
└── README.md              # このファイル
```

---

## 📝 設定ファイルのフォーマット

### `glossary.csv` - 専門用語辞書

専門用語の翻訳を統一するための辞書ファイルです。CSVフォーマットで記述します。

**フォーマット:**
```csv
原語,訳語,備考
ethnography,エスノグラフィー,民族誌学
abduction,アブダクション,仮説形成的推論
semantic break,意味の切断,
field note,フィールドノート,
```

**カスタマイズ方法:**
1. `glossary.csv` をテキストエディタで開く
2. 自分の研究分野の専門用語を追加
3. 保存して実行

> 💡 **ヒント**: 備考欄は空欄でもOKです。翻訳時の参考情報として使えます。

### `.env` - 環境変数設定

APIキーを安全に管理するためのファイルです。

**フォーマット:**
```bash
# Google Gemini API Key
# Get your API key from: https://aistudio.google.com/
GEMINI_API_KEY=your_api_key_here
```

**設定手順:**
1. [Google AI Studio](https://aistudio.google.com/) にアクセス
2. 「Get API Key」をクリックしてAPIキーを生成
3. `.env.example` をコピーして `.env` にリネーム
4. `your_api_key_here` を実際のAPIキーに置き換える

---

## 🛠️ 技術スタック

- **Python 3.10+**
- **Google Gemini API** (`gemini-3-flash-preview`)
- **主要ライブラリ:**
  - `python-docx` - Word文書の読み込み
  - `google-genai` - Gemini API連携
  - `python-dotenv` - 環境変数管理

---

## 🤝 コントリビューション

バグ報告や機能提案は、GitHubのIssuesでお願いします。プルリクエストも歓迎します！

---

## 📄 ライセンス

MIT License

---

## 💡 トラブルシューティング

### Q: `ModuleNotFoundError` が出る
**A:** 仮想環境が有効化されているか確認し、`pip install -r requirements.txt` を再実行してください。

### Q: APIキーエラーが出る
**A:** `.env` ファイルが正しく作成されているか、APIキーが正しく設定されているか確認してください。

### Q: 翻訳の精度を上げたい
**A:** `glossary.csv` に専門用語を追加することで、翻訳の一貫性と精度が向上します。

### Q: 処理が途中で止まる
**A:** Gemini APIの利用制限に達している可能性があります。しばらく待ってから再実行してください。

---

**開発者**: [あなたの名前]  
**リポジトリ**: https://github.com/yourusername/p2workflowy
