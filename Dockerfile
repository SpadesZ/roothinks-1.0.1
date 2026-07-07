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

# NLLB / easyocr 共用的 PyTorch:強制 CPU-only wheel,先裝好讓後續 easyocr、
# transformers 直接複用,避免預設 index 拉進數 GB 的 CUDA 版本。
# 不釘版本:本環境 index 提供 torch 2.12.x。torch.load 的 CVE 守衛(要求 >=2.6,
# 卻誤判 2.12 為 <2.6)改由 bake_nllb.py 轉 safetensors 繞過,故此處取最新 CPU wheel。
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch torchvision

RUN pip install --no-cache-dir -r requirements.txt

# Bake NLLB-200-distilled-600M 進映像,產出為本地 safetensors 模型目錄 /opt/models/nllb。
# 路徑刻意放在 prod compose 的 ./data:/app/data bind-mount 之外,執行時不會被 host 覆蓋。
# bake_nllb.py 把 HF 的 pytorch_model.bin 轉成 safetensors,繞過 transformers 的
# torch.load CVE 守衛(見腳本註解)。此步驟需要網路下載 (~1.2GB),必須在設 HF_HUB_OFFLINE 之前。
ENV NLLB_MODEL_DIR=/opt/models/nllb
COPY scripts/bake_nllb.py /tmp/bake_nllb.py
RUN python /tmp/bake_nllb.py && chmod -R a+rX /opt/models

# 執行期強制離線,NLLB 不再嘗試連網 (必須在 bake 之後才設)。
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

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
