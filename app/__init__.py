#路徑(./app/__init__.py) #版本 v1.2 #更版時間 20260429-0138
import json
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from PIL import Image
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from werkzeug.exceptions import HTTPException

from app.security import is_api_request_path, parse_allowed_origins, require_request_auth
from app.system_runtime import apply_cpu_thread_limit_env, setup_system_logging, start_system_monitor

LOGGER = logging.getLogger("roothinks.app")

# Optional schema fixer import.
try:
    import fix_db_schema
except ImportError:
    fix_db_schema = None

db = SQLAlchemy()
limiter = Limiter(key_func=get_remote_address, default_limits=["60 per minute"])
socketio = SocketIO(ping_interval=25, ping_timeout=120)
csrf = CSRFProtect()


def _to_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _is_development() -> bool:
    env = (os.environ.get("FLASK_ENV") or os.environ.get("APP_ENV") or "").strip().lower()
    if env in {"dev", "development", "local"}:
        return True
    return _to_bool(os.environ.get("FLASK_DEBUG"), False)


def _parse_tokens(raw: str) -> list[str]:
    out = []
    for seg in str(raw or "").split(","):
        token = seg.strip()
        if token:
            out.append(token)
    return out


def _parse_json_env(key: str, fallback):
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        LOGGER.warning("Invalid JSON in env %s", key)
        return fallback


def _env_int(key: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        LOGGER.warning("Invalid int env %s=%r, fallback=%s", key, raw, default)
        return default
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _default_lock_root() -> str:
    if os.name == "nt":
        return os.path.join(tempfile.gettempdir(), "roothinks-locks")
    return "/tmp/roothinks-locks"


def _ensure_lock_root(lock_root: str) -> str:
    target = os.path.abspath(str(lock_root or "").strip() or _default_lock_root())
    os.makedirs(target, exist_ok=True)
    probe = os.path.join(target, ".probe_write")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("ok")
    try:
        os.remove(probe)
    except FileNotFoundError:
        # Multi-worker boot can race on shared probe file; file is still writable so treat as success.
        pass
    return target


def _build_engine_options(db_uri: str) -> dict:
    uri = str(db_uri or "")
    is_sqlite = uri.startswith("sqlite:///")

    # SQLite should avoid connection reuse to survive transient bind-mount glitches.
    pool_size_default = 5
    max_overflow_default = 10
    pool_timeout_default = 30
    pool_recycle_default = 1800
    connect_timeout_default = 30

    if is_sqlite:
        return {
            "poolclass": NullPool,
            "connect_args": {
                "check_same_thread": False,
                "timeout": _env_int("DB_CONNECT_TIMEOUT_SEC", connect_timeout_default, minimum=5),
            },
        }

    return {
        "pool_pre_ping": True,
        "pool_size": _env_int("DB_POOL_SIZE", pool_size_default, minimum=1),
        "max_overflow": _env_int("DB_MAX_OVERFLOW", max_overflow_default, minimum=0),
        "pool_timeout": _env_int("DB_POOL_TIMEOUT", pool_timeout_default, minimum=5),
        "pool_recycle": _env_int("DB_POOL_RECYCLE", pool_recycle_default, minimum=30),
    }


def _run_schema_fix(max_retries: int = 3):
    if not fix_db_schema or not hasattr(fix_db_schema, "fix_schema"):
        raise RuntimeError("fix_db_schema.fix_schema is required but unavailable")
    for attempt in range(1, max_retries + 1):
        try:
            fix_db_schema.fix_schema()
            return
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "unable to open database file" not in msg or attempt >= max_retries:
                raise
            wait_sec = min(2.0, 0.5 * attempt)
            LOGGER.warning(
                "Schema fix sqlite open failed (attempt %s/%s): %s; retrying in %.1fs",
                attempt,
                max_retries,
                exc,
                wait_sec,
            )
            time.sleep(wait_sec)


def _sqlite_uri_from_path(path: str) -> str:
    abs_path = os.path.abspath(path)
    if os.name == "nt":
        abs_path = abs_path.replace("\\", "/")
    return f"sqlite:///{abs_path}"


def _normalize_sqlite_uri(sqlalchemy_uri: str) -> str:
    uri = str(sqlalchemy_uri or "").strip()
    if not uri.startswith("sqlite:///"):
        return uri
    raw_path = uri.replace("sqlite:///", "", 1)
    if not raw_path:
        return uri
    return _sqlite_uri_from_path(raw_path)


def _sqlite_path(sqlalchemy_uri: str) -> str:
    if not str(sqlalchemy_uri).startswith("sqlite:///"):
        return ""
    path = sqlalchemy_uri.replace("sqlite:///", "", 1)
    return os.path.abspath(path)


def _enable_sqlite_wal(sqlalchemy_uri: str):
    db_path = _sqlite_path(sqlalchemy_uri)
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        return
    with sqlite3.connect(db_path, timeout=30) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        conn.commit()


def _reset_stale_papers():
    from app.models import Paper

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    # [Fix Bug5] 同時重置 analyzing / t5_running / t5_queued
    # t5_running/t5_queued 在 thread 意外死亡後會永久卡住，必須一起納入 stale 偵測
    stale_statuses = {Paper.STATUS_ANALYZING, Paper.STATUS_T5_RUNNING, Paper.STATUS_T5_QUEUED}
    stale = Paper.query.filter(Paper.interpretation_status.in_(list(stale_statuses))).all()
    changed = 0
    for row in stale:
        updated = row.updated_at
        if updated is None:
            is_stale = True
        else:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            is_stale = updated < cutoff
        if not is_stale:
            continue
        row.interpretation_status = Paper.STATUS_FAILED
        row.process_status = "error"
        row.process_log = (row.process_log or "") + "\n[startup-recover] stale running task reset."
        changed += 1

    if changed:
        db.session.commit()
        LOGGER.warning("Reset %s stale running paper jobs on startup.", changed)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    is_dev = _is_development()
    root_dir = os.path.abspath(os.path.join(app.root_path, ".."))

    secret_key = (os.environ.get("SECRET_KEY") or "").strip()
    if not secret_key and not is_dev:
        raise RuntimeError("SECRET_KEY must be set in non-development environments")
    if not secret_key:
        secret_key = "dev-only-change-me"
    fernet_key = (os.environ.get("FERNET_KEY") or "").strip()
    if not fernet_key and not is_dev:
        raise RuntimeError("FERNET_KEY must be set in non-development environments")
    if fernet_key:
        try:
            Fernet(fernet_key.encode("utf-8"))
        except Exception as exc:
            raise RuntimeError("FERNET_KEY must be a valid 32-byte url-safe base64 key") from exc

    default_db_uri = _sqlite_uri_from_path(os.path.join(app.root_path, "../data/roothinks.db"))
    db_uri = _normalize_sqlite_uri(os.environ.get("DATABASE_URL", default_db_uri))
    manuscript_db_uri = _sqlite_uri_from_path(os.path.join(app.root_path, "../data/manu_core.db"))
    max_mb = int(os.environ.get("MAX_CONTENT_MB", "50"))
    api_auth_enabled = _to_bool(os.environ.get("API_AUTH_ENABLED"), not is_dev)
    tokens = _parse_tokens(os.environ.get("API_BEARER_TOKENS") or os.environ.get("API_BEARER_TOKEN"))
    if api_auth_enabled and not tokens:
        raise RuntimeError("API auth enabled but API_BEARER_TOKENS is empty")

    cors_origins = parse_allowed_origins(os.environ.get("CORS_ALLOWED_ORIGINS"))
    if not cors_origins and is_dev:
        cors_origins = ["http://127.0.0.1:10000", "http://localhost:10000"]
    if not cors_origins and not is_dev:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must be set in non-development environments")

    socketio_async_mode = os.environ.get("SOCKETIO_ASYNC_MODE", "eventlet").strip().lower() or "eventlet"
    socketio_message_queue = (os.environ.get("SOCKETIO_MESSAGE_QUEUE") or "").strip() or None
    ratelimit_storage = (os.environ.get("RATELIMIT_STORAGE_URI") or "").strip() or "memory://"
    lock_root = (os.environ.get("LOCK_ROOT") or "").strip() or _default_lock_root()
    engine_options = _build_engine_options(db_uri)

    app.config.from_mapping(
        SECRET_KEY=secret_key,
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_BINDS={"manuscript": manuscript_db_uri},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=engine_options,
        MAX_CONTENT_LENGTH=max_mb * 1024 * 1024,
        SESSION_COOKIE_SECURE=not is_dev,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        API_AUTH_ENABLED=api_auth_enabled,
        API_BEARER_TOKENS=tokens,
        TOKEN_ACL_MAP=_parse_json_env("TOKEN_ACL_MAP_JSON", {}),
        RATELIMIT_STORAGE_URI=ratelimit_storage,
        SOCKETIO_ASYNC_MODE=socketio_async_mode,
        SOCKETIO_MESSAGE_QUEUE=socketio_message_queue,
        CORS_ALLOWED_ORIGINS=cors_origins,
        LOCK_ROOT=lock_root,
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_CHECK_DEFAULT=False,
    )

    if test_config:
        app.config.from_mapping(test_config)

    # Runtime logging + CPU limits (available to Flow A / translation workers).
    log_info = setup_system_logging(root_dir)
    cpu_limit_info = apply_cpu_thread_limit_env()
    monitor_info = start_system_monitor(root_dir)
    LOGGER.info(
        "[Runtime] system_log=%s cpu_limit=%s monitor=%s",
        log_info.get("log_file"),
        cpu_limit_info.get("cpu_limit"),
        monitor_info,
    )

    os.makedirs(app.instance_path, exist_ok=True)
    app.config["LOCK_ROOT"] = _ensure_lock_root(app.config.get("LOCK_ROOT"))
    try:
        Image.MAX_IMAGE_PIXELS = int(os.environ.get("PIL_MAX_IMAGE_PIXELS", "89478485"))
    except Exception:
        Image.MAX_IMAGE_PIXELS = 89478485

    # Block startup when schema fix fails.
    with app.app_context():
        _run_schema_fix()

    db.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins=app.config["CORS_ALLOWED_ORIGINS"],
        async_mode=app.config["SOCKETIO_ASYNC_MODE"],
        message_queue=app.config["SOCKETIO_MESSAGE_QUEUE"],
    )

    @app.before_request
    def _auth_guard():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not is_api_request_path(request.path):
            csrf.protect()
        result = require_request_auth()
        if result is not None:
            return result
        return None

    @app.after_request
    def _apply_security_headers(resp):
        csp = (
            os.environ.get("CONTENT_SECURITY_POLICY")
            or "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.plot.ly https://apis.google.com https://accounts.google.com 'unsafe-inline'; "
            "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' https://cdn.jsdelivr.net data:; "
            "connect-src 'self' https: wss:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.errorhandler(HTTPException)
    def _handle_http_error(err: HTTPException):
        if request.path.startswith("/api/") or request.path.startswith("/manuscript/api/"):
            return jsonify({"success": False, "message": err.description}), err.code
        return err

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(err: CSRFError):
        if request.path.startswith("/api/") or request.path.startswith("/manuscript/api/"):
            return jsonify({"success": False, "message": "CSRF token missing or invalid"}), 403
        return "CSRF token missing or invalid", 403

    @app.errorhandler(Exception)
    def _handle_unexpected_error(err: Exception):
        app.logger.exception("Unhandled exception", exc_info=err)
        if request.path.startswith("/api/") or request.path.startswith("/manuscript/api/"):
            return jsonify({"success": False, "message": "Internal server error"}), 500
        return "Internal server error", 500

    from app.project_portfolio import project_routes
    from app.core_pro.paq import paq_routes
    from app.core_pro.literature import literature_routes
    from app.core_pro.study import study_routes
    from app.core_pro.manuscript import manuscript_routes
    from app.llm_service import llm_routes
    from app.routes import main_bp

    app.register_blueprint(project_routes.bp)
    app.register_blueprint(paq_routes.bp)
    app.register_blueprint(literature_routes.literature_bp)
    app.register_blueprint(study_routes.study_bp)
    app.register_blueprint(manuscript_routes.bp)
    app.register_blueprint(llm_routes.bp)
    app.register_blueprint(main_bp)

    from app import models
    from app.llm_service.llm_model import LLMModel
    from app.core_pro.manuscript import model_manu
    from app.core_pro.manuscript import model_section

    with app.app_context():
        try:
            db.create_all()
            _enable_sqlite_wal(app.config["SQLALCHEMY_DATABASE_URI"])
            _enable_sqlite_wal(app.config["SQLALCHEMY_BINDS"]["manuscript"])
            LLMModel.init_db()

            # Re-apply persisted runtime settings from ConfigKV.
            try:
                saved_cpu = str(models._get_kv("LECTURE_CPU_CORES", "") or "").strip().lower()
                if saved_cpu in {"auto", "default", "system", ""}:
                    os.environ.pop("LECTURE_CPU_CORES", None)
                else:
                    if int(saved_cpu) > 0:
                        os.environ["LECTURE_CPU_CORES"] = str(int(saved_cpu))
            except Exception:
                os.environ.pop("LECTURE_CPU_CORES", None)

            try:
                saved_workers = str(models._get_kv("LECTURE_PAGE_WORKERS", "") or "").strip()
                if saved_workers and int(saved_workers) > 0:
                    os.environ["LECTURE_PAGE_WORKERS"] = str(int(saved_workers))
                    os.environ["LITERATURE_MAX_WORKERS"] = str(int(saved_workers))
            except Exception:
                os.environ.pop("LECTURE_PAGE_WORKERS", None)

            reapplied = apply_cpu_thread_limit_env()
            LOGGER.info("[Runtime] reapplied CPU limit from DB setting: %s", reapplied.get("cpu_limit"))

            _reset_stale_papers()
            LOGGER.info("Database initialization completed.")
        except sqlite3.OperationalError as db_err:
            if "locked" in str(db_err).lower():
                LOGGER.warning("Database locked by another worker during startup; skip duplicate init.")
            else:
                raise
        except OperationalError as op_err:
            LOGGER.warning("OperationalError during startup initialization: %s", op_err)

    return app
