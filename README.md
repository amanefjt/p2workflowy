# 📚 p2workflowy V3 (Stable Release)

[![Gemini](https://img.shields.io/badge/Model-Gemini%202.0%20/%201.5%20Pro-blue.svg)](https://deepmind.google/technologies/gemini/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://www.python.org/)

**p2workflowy** は、学術論文や専門書籍の PDF/テキストを Gemini AI を活用して解析、Workflowy などのアウトライナーで扱いやすい高品質な Markdown 形式に変換・翻訳する強力なツールです。

---

## ✨ V3 (Stable) 新機能と改善

### 1. 認知透過性翻訳 (Cognitive Clarity Translation)
- **サイトトランスレーション・リズム**: 英語固有の構文に囚われず、日本語として「前から後ろへ」流れるように訳す最新プロンプトを採用（Gemini 2.0 / 1.5 Pro 最適化）。
- **論理的リズムの最適化**: 複雑な論理関係を意味のまとまりごとに分割し、論理展開が明快な prose（散文）を提供します。

### 2. 論文モード (Paper Mode) の洗練
- **H3/H2 非対称階層**: 英語パートを H3、日本語パートを H2 で出力。Workflowy 上で英語セクションを日本語見出しの直下に、論理的な親子関係として配置可能です。
- **[Unlabeled Section] の解消**: Abstract 以前のメタデータやタイトルを自動識別。構造の不純物を排除し、即座に執筆・思考に活用できるクリーンな出力を実現。

### 3. 書籍モード (Book Mode) & ハイブリッド Ingestion
- **Book Mode**: 目次 (TOC) を LLM で抽出し、PyMuPDF と組み合わせて完全な章・節構造（Tree）を再構築。
- **VLM-Assist OCR**: テキスト抽出が困難なスキャン書籍も、Gemini VLM を用いて高精度に OCR 処理します。

---

## 🚀 セットアップ

### 必要条件
- Python 3.10 以上 / Gemini API キー

### 導入手順
```bash
git clone https://github.com/your-repo/p2workflowy.git
cd p2workflowy
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env # GEMINI_API_KEY / PASSCODE を設定
```

---

## 🛠 使い方

### CLI版
```bash
# 論文モード (デフォルト)
python3 main.py data/paper.pdf

# 書籍モード (章・節構造の構築)
python3 main.py data/book.pdf --book
```

### Web版 (FastAPI)
直感的なブラウザインターフェースからも利用可能です。
```bash
python3 server.py
# ブラウザで http://localhost:8000 にアクセス
```

---

## 📂 ディレクトリ構成
- `core/`: 翻訳・構造化エンジンのコアロジック。
- `web/`: WebUI フロントエンド（静的ファイル）。
- `archive/`: 過去の安定版スナップショット（v3.x 系統）。
- `docs/`: 仕様書・デザインドキュメント。

---

## 📄 ライセンス
MIT License. 詳細は `LICENSE` ファイルを確認してください。
