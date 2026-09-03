# Roothinks source maintenance contract
# 檔案路徑: Dockerfile
# 模組定位: 容器建置與編排層；固定 app/test/Redis 的映像、掛載、健康檢查與啟動順序。
# 主要責任: 宣告服務、映像、環境鍵、volume 與啟動/驗證命令，保持各環境可重現。
# 上下游: 開發/測試/GCP 操作者 -> Docker build/Compose -> app 與 Redis；正式 data/.env 由 host volume 提供。
# 維護邊界: 不得把 .env/data/secrets 打進 image；正式 cutover 只重建 app、保留 Redis 身分、健康檢查與 rollback image。
# 驗證: docker build --check -f Dockerfile .
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
#
# 必須釘版本:PyTorch 在 2.6.0 之後不再為 Python 3.10 出 wheel,CPU index 上
# 最新的 cp310 檔就是 torch 2.6.0+cpu / torchvision 0.21.0+cpu。不釘版本會讓
# pip 去要最新版(2.12.x)、找不到 cp310 wheel 後退回原始碼編譯而失敗。
# 2.6.0 同時滿足 transformers 的 torch>=2.6 CVE 守衛,且不會踩到該守衛把
# "2.12" 字串誤判成小於 "2.6" 的比較問題。
#
# --extra-index-url 是必要的:--index-url 會完全取代 PyPI,一旦 pip 需要抓
# 建置依賴(例如 typing_extensions 的 flit_core)就會 "No matching distribution"。
# 併用 PyPI 不會誤抓 CUDA 版,因為 +cpu 這個 local version 只存在於 PyTorch index。
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple \
    torch==2.6.0+cpu torchvision==0.21.0+cpu

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
