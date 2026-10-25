# 本機遷移候選；原 production Dockerfile／Compose 保持不變。linux/amd64。
FROM python:3.12-slim-bookworm@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0 AS runtime

# 固定 Debian snapshot，同時維持 HTTPS 與 apt 套件簽章；禁止使用不受信來源。
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
       build-essential libgl1 libglib2.0-0 tesseract-ocr poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY validation/python312/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
    && pip check

ENV NLLB_MODEL_DIR=/opt/models/nllb OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
COPY validation/python312/bake_model.py validation/python312/nllb-source.json /tmp/model-build/
RUN python /tmp/model-build/bake_model.py && chmod -R a+rX /opt/models
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHON_DOTENV_DISABLED=1

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

FROM runtime AS verification
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY validation/python312/requirements-test.lock /tmp/requirements-test.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements-test.lock && pip check
COPY --chown=appuser:appuser . /app
USER appuser
CMD ["python", "-m", "pytest", "test/unit", "tests", "test/integration_e2e", "-q", "-p", "no:cacheprovider"]
