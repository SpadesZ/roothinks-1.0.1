# Roothinks source maintenance contract
# 檔案路徑: Dockerfile
# 主要責任: 唯一正式 Python 3.12 候選；固定 base、Debian snapshot、CPU wheels 與公開模型。
# 上下游: tracked source -> runtime image -> 非 root Gunicorn；正式 volumes 不在建置範圍。
# 維護邊界: 不 COPY .env/data/模型快取；不關安全守衛，權重缺件或 hash 不符即停止。
# 驗證: python validation/python312/local_build.py build <staged-dir> --dockerfile Dockerfile --target runtime。
FROM python:3.12-slim-bookworm@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0 AS runtime

RUN printf '%s\n' \
    'Types: deb' \
    'URIs: https://snapshot.debian.org/archive/debian/20261024T000000Z' \
    'Suites: bookworm bookworm-updates' \
    'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    '' \
    'Types: deb' \
    'URIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z' \
    'Suites: bookworm-security' \
    'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    > /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot \
    && apt-get update && apt-get install -y --no-install-recommends \
       build-essential libgl1 libglib2.0-0 tesseract-ocr tesseract-ocr-chi-tra poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-py312.lock /tmp/requirements-py312.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements-py312.lock \
    && pip check

ENV NLLB_MODEL_DIR=/opt/models/nllb EASYOCR_MODULE_PATH=/opt/models/easyocr \
    EASYOCR_DOWNLOAD_ENABLED=0 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
COPY scripts/bake_nllb.py scripts/nllb-source.json /tmp/nllb-build/
RUN python /tmp/nllb-build/bake_nllb.py && chmod -R a+rX /opt/models/nllb
COPY scripts/bake_easyocr.py scripts/easyocr-source.json /tmp/easyocr-build/
RUN python /tmp/easyocr-build/bake_easyocr.py && chmod -R a+rX /opt/models/easyocr

ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=/app
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY main.py fix_db_schema.py alembic.ini ./
RUN useradd -m appuser && mkdir -p /tmp/roothinks-locks /app/data \
    && chown -R appuser:appuser /app /tmp/roothinks-locks
USER appuser
ENV FLASK_APP=main.py PYTHONUNBUFFERED=1 LOCK_ROOT=/tmp/roothinks-locks \
    GUNICORN_WORKERS=1 GUNICORN_WORKER_CLASS=eventlet GUNICORN_THREADS=4 \
    GUNICORN_TIMEOUT=300 GUNICORN_GRACEFUL_TIMEOUT=60 GUNICORN_MAX_REQUESTS=1000 \
    GUNICORN_MAX_REQUESTS_JITTER=100 GUNICORN_KEEPALIVE=5
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail http://127.0.0.1:${PORT:-10000}/ || exit 1
CMD ["sh", "-c", "gunicorn -w ${GUNICORN_WORKERS:-1} -k ${GUNICORN_WORKER_CLASS:-eventlet} --threads ${GUNICORN_THREADS:-4} -b 0.0.0.0:${PORT:-10000} --timeout ${GUNICORN_TIMEOUT:-300} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-60} --max-requests ${GUNICORN_MAX_REQUESTS:-1000} --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-100} --keep-alive ${GUNICORN_KEEPALIVE:-5} --access-logfile - --error-logfile - 'main:create_app()'"]
