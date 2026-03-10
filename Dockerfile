FROM python:3.11-slim

# 作業ディレクトリの設定
WORKDIR /app

# ビルドに必要な最小限のパッケージをインストール（もし必要なら）
# RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev && rm -rf /var/lib/apt/lists/*

# 依存関係のコピーとインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# プロジェクト全ファイルのコピー
COPY . .

# 実行環境の設定
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# サーバーの起動
CMD ["python", "server.py"]
