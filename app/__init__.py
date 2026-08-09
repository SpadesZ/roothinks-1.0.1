# Roothinks source maintenance contract
# 上下游: main/create_app 啟動本層，再註冊 Blueprint、DB、Socket、runtime service 供 HTTP/worker 使用。
# 檔案路徑: app/__init__.py
# 產生時間: 2026-07-04 18:55 +08:00
# 版本: v1.4
# 模組定位:
#   Roothinks Flask app factory 與啟動期 DB/runtime 初始化。
# 主要責任: 建立 Flask app、DB、Login、Socket.IO 與各 Blueprint 的唯一組裝入口，按設定初始化 schema/runtime。
#   1. create_app 工廠；初始化 SQLAlchemy、CSRF、SocketIO、LoginManager 等擴充。
#   2. [Batch A] 新增 AUTH_MODE config（none / session）；控制 session 登入守衛。
#   3. [Batch A] _auth_guard 統一處理 CSRF + session 守衛 + Bearer token 守衛。
# 維護提醒:
#   - AUTH_MODE 判定順序：test_config dict > 環境變數 AUTH_MODE > 自動推算
#     （TESTING 或 _is_development() → "none"，否則 "session"）。
#   - AUTH_MODE=none：行為與 Batch A 前完全一致（零影響）。
#   - AUTH_MODE=session：未登入訪問非白名單路由 → API 回 401 JSON，
#     其餘 redirect /auth/login?next=...。
#   - 已有 session user 時跳過 Bearer 守衛（session 即身分）；
#     無 session 時 Bearer 邏輯照舊（機器對機器相容）。
#   - 白名單 endpoint：auth.*、static、favicon.ico、/api/auth/*。
# 驗證方式:
#   python -m pytest test -q
# -----------------------------------------------------------------------------
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from PIL import Image
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from werkzeug.exceptions import HTTPException

from app.security import is_api_request_path, parse_allowed_origins, require_request_auth
from app.status import ProcessStatus
from app.system_runtime import apply_cpu_thread_limit_env, setup_system_logging, start_system_monitor

LOGGER = logging.getLogger("roothinks.app")

# Optional schema fixer import.
try:
    import fix_db_schema
except ImportError:
    fix_db_schema = None

db = SQLAlchemy()
# 全域預設 600/min:一次頁面載入會產生多個資源/API 請求,舊值 60/min 在
# 正常瀏覽下即觸發 429 整站鎖死(UI 走查實測)。login/register 等敏感端點
# 另以 @limiter.limit 個別收緊防爆破。可用 env RATE_LIMIT_DEFAULT 覆寫。
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[os.environ.get("RATE_LIMIT_DEFAULT", "600 per minute")],
)
socketio = SocketIO(ping_interval=25, ping_timeout=120)
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "請先登入。"
login_manager.login_message_category = "warning"


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
        row.process_status = ProcessStatus.FAILED.value
        row.process_log = (row.process_log or "") + "\n[startup-recover] stale running task reset."
        changed += 1

    if changed:
        db.session.commit()
        LOGGER.warning("Reset %s stale running paper jobs on startup.", changed)


def _configure_logging():
    """讓 logger.info() 真的輸出到 stdout。

    這個 repo 從來沒有設定過 root logger。Python 的預設是 level=WARNING 且只有
    lastResort handler，於是**所有 logger.info() 都被無聲丟棄**，只有 warning
    以上出得來。

    後果比「少了一些日誌」嚴重得多：診斷 drafter 無聲卡死時，前一輪是以
    「容器日誌裡 `manu_chat` 出現 0 次」推論 handler 從未被呼叫 —— 但那 0 次
    只是因為 `[manu_chat] job_queued` 那幾行全是 logger.info。**觀察是對的，
    訊號本身是壞的**，於是推論必然錯。看起來像證據的空值比完全沒有日誌更危險。

    等級由 LOG_LEVEL 控制（預設 INFO）；gunicorn 會把 stdout 收進容器日誌。
    """
    level_name = str(os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    # 只掛一次。create_app 在測試裡會被呼叫很多次，重複 addHandler 會讓
    # 每一行日誌印很多份。
    if not any(getattr(h, "_roothinks_stdout", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
        )
        handler._roothinks_stdout = True
        root.addHandler(handler)


def create_app(test_config=None):
    _configure_logging()
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

    # [Batch A] AUTH_MODE: env 變數預設；test_config 可覆寫。
    # TESTING 或 dev 環境下預設 "none"（不破壞既有測試相容性）。
    _testing_flag = (test_config or {}).get("TESTING", False)
    _auth_mode_default = "none" if (_testing_flag or is_dev) else "session"
    auth_mode = str(os.environ.get("AUTH_MODE") or _auth_mode_default).strip().lower()

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
        # session cookie 是否僅限 HTTPS。生產預設 True,但 HTTP 部署(如 GCP VM
        # 直連 IP 的 demo)必須設 SESSION_COOKIE_SECURE=0,否則 cookie 送不出去,
        # 會導致「CSRF token missing」且完全無法登入。可用 env 覆寫。
        SESSION_COOKIE_SECURE=_to_bool(os.environ.get("SESSION_COOKIE_SECURE"), not is_dev),
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
        AUTH_MODE=auth_mode,
    )

    if test_config:
        app.config.from_mapping(test_config)
    # Re-resolve AUTH_MODE after test_config override so dict override wins.
    # If test_config explicitly sets AUTH_MODE, it's now in app.config.
    # If test_config sets TESTING=True but not AUTH_MODE, keep computed value.
    if test_config and "AUTH_MODE" not in test_config and test_config.get("TESTING"):
        app.config["AUTH_MODE"] = "none"
    if app.config.get("TESTING") and not os.environ.get("SOCKETIO_ASYNC_MODE"):
        app.config["SOCKETIO_ASYNC_MODE"] = "threading"

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
    login_manager.init_app(app)

    @login_manager.user_loader
    def _load_user(user_id):
        from app.models import User
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    # socketio.init_app 刻意延後到路由模組匯入之後才呼叫，見下方 [socketio 註冊順序]。

    @app.context_processor
    def _inject_module_access():
        """給模板一個 can_show_module()，讓導覽列只列出進得去的模組。

        注意：這只是**不顯示**，不是防護。真正的把關在 _auth_guard 的
        模組守衛——藏起連結擋不住直接打網址的人。兩層都要有：
        沒有前者使用者會一直點到 403，沒有後者則根本沒擋住。
        """
        def can_show_module(module: str) -> bool:
            try:
                if not current_user.is_authenticated:
                    return True
                from app.security import can_access_module, request_pid

                return can_access_module(current_user.id, request_pid(), module)
            except Exception:
                return True   # 導覽列不該因為判斷失敗就整條消失

        return {"can_show_module": can_show_module}

    @app.before_request
    def _auth_guard():
        # ── 1. CSRF guard（非 API 的寫入請求）─────────────────────────────
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not is_api_request_path(request.path)
            and app.config.get("WTF_CSRF_ENABLED", True)
        ):
            csrf.protect()

        # ── 2. [Batch A] Session 登入守衛 ────────────────────────────────
        _auth_mode = app.config.get("AUTH_MODE", "none")
        if _auth_mode == "session":
            _path = request.path
            _endpoint = request.endpoint or ""

            # 白名單：auth blueprint、api/auth、static、favicon
            _is_whitelisted = (
                _endpoint.startswith("auth.")
                or _path.startswith("/api/auth/")
                or _endpoint == "static"
                or _path == "/favicon.ico"
            )

            if not _is_whitelisted and not current_user.is_authenticated:
                if is_api_request_path(_path):
                    return jsonify({"success": False, "error": "login_required"}), 401
                return redirect(url_for("auth.login", next=_path))

            # ── 2b. 模組層存取控制 ──────────────────────────────────────
            # 放在全域守衛而不是逐條路由加裝飾器：模組頁面與 API 加起來
            # 有數十個進入點，散著加一定會漏掉一兩個，而漏掉的那個就是洞。
            # 這裡是唯一咽喉，新增路由不必記得補。
            #
            # 擋的是「限定編輯(coauthor)進到 Manuscript 以外的模組」——
            # enforce_project_ownership 對 GET 用 min_role=viewer，
            # 而 coauthor 在 ROLE_ORDER 上高於 viewer 會直接通過，
            # 但它的讀取範圍其實比 viewer 還窄（見 models.ROLE_ORDER 註解）。
            if current_user.is_authenticated and not _is_whitelisted:
                try:
                    from app.security import (
                        can_access_module, module_of_path, request_pid,
                    )

                    _module = module_of_path(_path)
                    if _module and not can_access_module(
                        current_user.id, request_pid(), _module
                    ):
                        app.logger.warning(
                            "[perm] 擋下模組存取 user_id=%s module=%s path=%s",
                            current_user.id, _module, _path,
                        )
                        if is_api_request_path(_path):
                            return jsonify({
                                "success": False, "error": "forbidden",
                                "message": "你的角色沒有此模組的存取權",
                            }), 403
                        return render_template("errors/403_module.html",
                                               module=_module), 403
                except Exception:
                    # 守衛本身壞掉不可以變成「全部放行」——那會把洞開得更大。
                    app.logger.exception("[perm] 模組守衛失敗，保守擋下 path=%s", _path)
                    return jsonify({"success": False, "error": "forbidden"}), 403

            # 已有 session user → 跳過 Bearer 守衛（session 即身分）
            if current_user.is_authenticated:
                return None

        # ── 3. Bearer token 守衛（機器對機器相容）────────────────────────
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
            "frame-src 'self' https://content.googleapis.com https://accounts.google.com; "
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
        # 表單頁 CSRF 失敗(多半是頁面停太久 token 過期):不吐純文字,
        # 導回登入頁並帶友善提示,讓使用者直接重登。
        if request.path.startswith("/auth/"):
            flash("表單已過期,請重新登入。", "warning")
            return redirect(url_for("auth.login")), 303
        flash("操作已過期,請重新整理頁面後再試。", "warning")
        return redirect(request.referrer or url_for("main.index")), 303

    @app.errorhandler(Exception)
    def _handle_unexpected_error(err: Exception):
        app.logger.exception("Unhandled exception", exc_info=err)
        if request.path.startswith("/api/") or request.path.startswith("/manuscript/api/"):
            return jsonify({"success": False, "message": "Internal server error"}), 500
        return "Internal server error", 500

    from app.project_portfolio import project_routes
    from app.project_portfolio import member_routes  # noqa: F401  # [Batch B] 成員管理路由
    from app.core_pro.paq import paq_routes
    from app.core_pro.literature import literature_routes
    from app.core_pro.study import study_routes
    from app.core_pro.manuscript import manuscript_routes
    from app.core_pro.manuscript import chapter_routes  # noqa: F401  # [collab] 章節指派與留言
    from app.llm_service import llm_routes
    from app.routes import main_bp
    from app.auth import auth_bp, auth_api_bp
    from app.mentor import mentor_bp, mentor_api_bp

    # ── [socketio 註冊順序] 必須在上面的路由模組匯入之後才 init_app ──────────
    # flask_socketio 的 @socketio.on 裝飾器行為是：
    #   if self.server:  直接註冊到當前 server
    #   else:            存進 self.handlers，留待 init_app 時註冊
    # 而 init_app 每次都會重建 self.server，並只從 self.handlers 還原 handler。
    #
    # 若在匯入 manuscript_routes 之前就 init_app，第一次建立 app 時 server 已存在，
    # 所有 @socketio.on 只會掛在「那一個」server 上、完全不進 self.handlers；
    # 之後任何再次 create_app（測試、部分 WSGI 部署方式）重建 server 後，
    # /manu_ws 命名空間會沒有任何 handler，連線一律被拒，手稿即時協作整組失效。
    socketio.init_app(
        app,
        cors_allowed_origins=app.config["CORS_ALLOWED_ORIGINS"],
        async_mode=app.config["SOCKETIO_ASYNC_MODE"],
        message_queue=app.config["SOCKETIO_MESSAGE_QUEUE"],
    )

    app.register_blueprint(project_routes.bp)
    app.register_blueprint(paq_routes.bp)
    app.register_blueprint(literature_routes.literature_bp)
    app.register_blueprint(study_routes.study_bp)
    app.register_blueprint(manuscript_routes.bp)
    app.register_blueprint(llm_routes.bp)
    app.register_blueprint(main_bp)

    # [Batch A] auth blueprints
    # auth_bp  — HTML 表單路由（url_prefix="/auth"）
    # auth_api_bp — JSON API 路由（url_prefix="/api/auth"）
    app.register_blueprint(auth_bp)
    app.register_blueprint(auth_api_bp)

    # [mentor] mentor_bp  — 儀表板頁面（url_prefix="/mentor"）
    #          mentor_api_bp — JSON API（url_prefix="/api/mentor"）
    app.register_blueprint(mentor_bp)
    app.register_blueprint(mentor_api_bp)

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

            # PI／Co-PI 是內容決策者，既有專案也要立即補到 editor；同步函式
            # 只升不降且可重入，owner 仍保留成員管理權。
            try:
                from app.project_portfolio.project_service import ProjectService
                lead_granted, lead_skipped = ProjectService.ensure_academic_lead_access()
                LOGGER.info(
                    "[members-access] academic leads ensured: granted=%s skipped=%s",
                    lead_granted, len(lead_skipped),
                )
            except Exception:
                db.session.rollback()
                LOGGER.warning("[members-access] academic lead startup sync failed", exc_info=True)

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
