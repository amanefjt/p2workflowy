# ベースイメージ (Python 3.11)
FROM python:3.11-slim

# システムライブラリのインストール (PyMuPDFや開発に必要なもの)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリ
WORKDIR /app

# 権限設定のためにユーザー1000を作成 (Hugging Faceの標準)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:${PATH}"

# 依存関係のコピーとインストール
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# アプリケーションコードのコピー
COPY --chown=user . .

# 書き込みが必要なディレクトリを確実に作成
RUN mkdir -p data/uploads state

# Hugging Face はポート 7860 を使用する
ENV PORT=7860
EXPOSE 7860

# アプリケーションの起動
# server.py の FastAPI インスタンス "app" を起動
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
