# ベースイメージ (Python 3.11)
FROM python:3.11-slim

# システムライブラリのインストール
# libxcb1: opencv-python-headless 4.13.0.90 の退行 (libxcb.so.1 への意図しない依存追加) により
# 必要。Docling が内部で使う OpenCV の import に失敗し、常に VLM フォールバックへ回っていた。
# https://github.com/opencv/opencv-python/issues/1183
RUN apt-get update && apt-get install -y \
    build-essential \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリ
WORKDIR /app

# 先にディレクトリを作成し、所有権をユーザー1000に設定する (root権限で実行)
RUN useradd -m -u 1000 user && \
    mkdir -p /app/data/uploads /app/state && \
    chown -R user:user /app

# 以降のコマンドは一般ユーザーで実行
USER user
ENV PATH="/home/user/.local/bin:${PATH}"

# 依存関係のコピーとインストール
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# アプリケーションコードのコピー
COPY --chown=user . .

# Hugging Face はポート 7860 を使用する
ENV PORT=7860
EXPOSE 7860

# アプリケーションの起動
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
