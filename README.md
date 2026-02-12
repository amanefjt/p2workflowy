# p2workflowy

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://p2workflowy.streamlit.app)

PDFから抽出されたテキストを、AIによって構造化・要約・翻訳し、Workflowyへ直接貼り付け可能な形式に変換するツールです。
のレジュメ（翻訳・要約）に変換するツールです。

## 主な機能

1.  **構造復元 (Cognitive Structuring)**: バラバラになったPDFのテキストを意味のあるMarkdown構造に修復します。
2.  **Workflowy要約**: 各セクションの論理展開（Chain of Thought）を階層構造で抽出します。
3.  **アカデミック翻訳**: 専門用語辞書（`glossary.csv`）を適用しながら、正確な学術翻訳を行います。
4.  **ノイズ除去**: 参考文献やページ番号などの不要な情報を自動的に取り除きます。

## セットアップ手順

プログラミング初心者の方でも、以下の手順で実行できます。

### 1. 準備
- [Google AI Studio](https://aistudio.google.com/) でAPIキーを取得してください。
- プロジェクト直下の `.env` ファイルを作成し、以下のように記述してください。
    ```text
    GEMINI_API_KEY=あなたのAPIキー
    ```

### 2. インストールと実行
ターミナルを開き、以下のコマンドをコピー＆ペーストして実行してください。

```bash
cd /Users/（ユーザー名）/Antigravity && python -m p2workflowy.src.main
```

## 使い方

1.  コマンドを実行すると、処理したいファイルのパスを聞かれます。
2.  ファイルをターミナルにドラッグ＆ドロップ（またはパスを入力）して Enter を押してください。
3.  処理が完了すると、**入力ファイルと同じ場所に** `(ファイル名)_output.txt` という名前で結果が保存されます。

## 技術スタック
- Python 3.10+
- Google Gemini API (gemini-3-flash-preview)
- python-docx, python-dotenv, google-genai

## ディレクトリ構造
- `src/`: ソースコード
- `input.txt`: 入力用ファイル
- `output/`: 成果物保存先
- `intermediate/`: 中間ファイル（Markdown形式）
- `glossary.csv`: 専門用語辞書
- `.agent/`: AIエージェント用のルールとスキル定義
