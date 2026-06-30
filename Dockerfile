#路徑(./Dockerfile) #版本 v0.2
FROM python:3.10-slim

# 安裝系統依賴
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案
COPY . .

# 降權執行
RUN useradd -m appuser \
    && mkdir -p /tmp/roothinks-locks \
    && chown -R appuser:appuser /app /tmp/roothinks-locks
USER appuser

# 環境變數與 Port
ENV FLASK_APP=main.py
ENV PYTHONUNBUFFERED=1
ENV LOCK_ROOT=/tmp/roothinks-locks
ENV GUNICORN_WORKERS=1
ENV GUNICORN_WORKER_CLASS=eventlet
ENV GUNICORN_THREADS=4
ENV GUNICORN_TIMEOUT=300
ENV GUNICORN_GRACEFUL_TIMEOUT=60
ENV GUNICORN_MAX_REQUESTS=1000
ENV GUNICORN_MAX_REQUESTS_JITTER=100
ENV GUNICORN_KEEPALIVE=5
EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail http://127.0.0.1:${PORT:-10000}/ || exit 1

# 啟動命令 (生產環境使用 gunicorn，支援 app factory 語法)
CMD ["sh", "-c", "gunicorn -w ${GUNICORN_WORKERS:-1} -k ${GUNICORN_WORKER_CLASS:-eventlet} --threads ${GUNICORN_THREADS:-4} -b 0.0.0.0:${PORT:-10000} --timeout ${GUNICORN_TIMEOUT:-300} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-60} --max-requests ${GUNICORN_MAX_REQUESTS:-1000} --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-100} --keep-alive ${GUNICORN_KEEPALIVE:-5} --access-logfile - --error-logfile - 'main:create_app()'"]
