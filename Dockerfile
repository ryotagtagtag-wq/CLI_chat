FROM python:3.10-slim

# 作業ディレクトリの作成
WORKDIR /app

# 必要なファイルをコピー
COPY requirements.txt .

# ライブラリのインストール
RUN pip install --no-cache-dir -r requirements.txt

# 残りのファイルをコピー
COPY . .

# プログラムを動かす標準ポートを明示
EXPOSE 2222

# サーバー起動コマンド
CMD ["python", "ssh_chat_server.py"]
