# 檔案路徑: app/auth/routes.py
# 產生時間: 2026-07-26 00:35 +08:00
# 版本: v2.0
# 模組定位:
#   Auth blueprint 路由：HTML 表單（register/login/logout/account）
#   與 JSON API（/api/auth/login, /api/auth/logout, /api/auth/me）。
# 主要責任:
#   1. GET/POST /auth/register — 以 email 註冊；username 由 email 前綴自動產生。
#   2. GET/POST /auth/login    — 以 email 登入，支援 next 參數防 open redirect。
#   3. POST /auth/logout       — 登出（HTML）。
#   4. GET/POST /auth/account  — 改密碼（需驗舊密碼，新密碼 >= 8 字元）。
#   5. POST /api/auth/login    — JSON 登入，供前端 JS 呼叫（auth_api_bp）。
#   6. POST /api/auth/logout   — JSON 登出（auth_api_bp）。
#   7. GET  /api/auth/me       — 回傳當前使用者 dict 或 401（auth_api_bp）。
# 呼叫來源:
#   瀏覽器表單；dashboard.js 的 /api/auth/me；test/unit/test_auth_basic.py、
#   test/unit/test_email_auth.py。
# 輸入輸出契約:
#   - /auth/register POST: email, password, confirm_password
#   - /auth/login    POST: email, password, next
#   - /api/auth/login body: {"email": str, "password": str}
#     （亦接受 "username" 鍵承載 email，供舊呼叫端相容）
# 安全邊界:
#   - 表單路由的 POST 已由 _auth_guard 的 csrf.protect() 保護；
#     API 路由（/api/*）不走 csrf.protect()，由前端帶 session cookie 驗證。
#   - next 參數只允許站內相對路徑（不以 http 起頭，不含 //），防 open redirect。
#   - 登入查詢一律經 User.find_by_email()（內含小寫正規化），避免同 email
#     以不同大小寫繞出兩個帳號。
#   - 帳號不存在與密碼錯誤回傳相同訊息，不透露 email 是否已註冊。
# 維護提醒:
#   - email 格式不符時，註冊與登入都回同一句「請使用mail格式註冊」（產品指定字串，
#     前後端一致；修改前請確認 test_email_auth.py）。
#   - username 不再由使用者輸入，改由 User.derive_username() 產生並自動去重。
#   - 密碼最短 8 字元（註冊與 account 改密碼皆強制）。
#   - auth_bp url_prefix="/auth"；auth_api_bp url_prefix="/api/auth"。
# 驗證方式:
#   python -m pytest test/unit/test_auth_basic.py test/unit/test_email_auth.py -q
# ------------------------------------------------------------------------------
import logging
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db, limiter
from app.models import User

LOGGER = logging.getLogger("auth")

# 實用取向的 email 格式驗證：要求 local@domain.tld，不接受空白與多個 @。
# 不追求 RFC 5322 完整性——過度嚴格的正規表達式會誤擋合法位址。
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$")

# 產品指定的格式錯誤提示字串；註冊與登入共用。
EMAIL_FORMAT_ERROR = "請使用mail格式註冊"

_EMAIL_MAX_LEN = 254
_PASSWORD_MIN_LEN = 8


def is_valid_email(raw: str) -> bool:
    """email 格式是否合法。長度上限依 RFC 5321 取 254。"""
    value = str(raw or "").strip()
    if not value or len(value) > _EMAIL_MAX_LEN:
        return False
    return _EMAIL_RE.fullmatch(value) is not None


def _start_activity_session(user) -> None:
    """
    登入成功後開一筆活動紀錄，供 mentor 統計「上線次數／停留時間」。

    寫入失敗只記 log 不中斷登入 —— 統計是附加價值，不該讓使用者登不進來。
    """
    from flask import session as flask_session
    from app.models import UserSession
    from app.routes import ACTIVITY_SESSION_KEY

    try:
        row = UserSession(user_id=user.id)
        db.session.add(row)
        db.session.commit()
        flask_session[ACTIVITY_SESSION_KEY] = row.id
    except Exception:
        db.session.rollback()
        LOGGER.warning("建立活動 session 失敗 user_id=%s", user.id, exc_info=True)


def _end_activity_session() -> None:
    """登出時結算目前的活動紀錄。"""
    from flask import session as flask_session
    from app.models import UserSession
    from app.routes import ACTIVITY_SESSION_KEY

    session_id = flask_session.pop(ACTIVITY_SESSION_KEY, None)
    if not session_id:
        return
    try:
        row = db.session.get(UserSession, session_id)
        if row is not None:
            row.close()
            db.session.commit()
    except Exception:
        db.session.rollback()
        LOGGER.warning("結算活動 session 失敗 id=%s", session_id, exc_info=True)

# Blueprint imported from __init__ (html routes)
from app.auth import auth_bp

# Separate blueprint for JSON API — no CSRF, url_prefix=/api/auth
auth_api_bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")


def _safe_next(next_url: str | None) -> str | None:
    """只允許站內相對路徑，防止 open redirect。"""
    if not next_url:
        return None
    # 拒絕絕對 URL（含 // 開頭或 http/https scheme）
    if next_url.startswith("//") or re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:", next_url):
        return None
    return next_url


# ---------------------------------------------------------------------------
# HTML routes  (auth_bp, url_prefix="/auth")
# ---------------------------------------------------------------------------


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    error = None
    if request.method == "POST":
        email_raw = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not is_valid_email(email_raw):
            error = EMAIL_FORMAT_ERROR
        elif password != confirm:
            error = "兩次密碼不一致。"
        elif len(password) < _PASSWORD_MIN_LEN:
            error = f"密碼至少需 {_PASSWORD_MIN_LEN} 個字元。"
        elif User.find_by_email(email_raw) is not None:
            error = f"Email「{User.normalize_email(email_raw)}」已被使用。"
        else:
            email = User.normalize_email(email_raw)
            # username 不再由使用者輸入；以 email 前綴產生顯示名並自動去重。
            user = User(email=email, username=User.derive_username(email))
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            _start_activity_session(user)
            flash("註冊成功，歡迎！", "success")
            return redirect(url_for("main.index"))

    return render_template("auth/register.html", error=error)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        next_url = _safe_next(request.args.get("next"))
        return redirect(next_url or url_for("main.index"))

    error = None
    if request.method == "POST":
        email_raw = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        next_url = _safe_next(request.form.get("next") or request.args.get("next"))

        if not is_valid_email(email_raw):
            # 格式就錯的話直接指出格式問題，不必浪費一次查詢。
            error = EMAIL_FORMAT_ERROR
        else:
            user = User.find_by_email(email_raw)
            if user and user.is_active and user.check_password(password):
                login_user(user)
                _start_activity_session(user)
                return redirect(next_url or url_for("main.index"))
            # 帳號不存在與密碼錯誤共用同一訊息，不透露該 email 是否已註冊。
            error = "Email 或密碼錯誤。"

    next_val = request.args.get("next", "")
    return render_template("auth/login.html", error=error, next=next_val)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # 先結算活動紀錄再登出，否則拿不到 session 中的 activity id。
    _end_activity_session()
    logout_user()
    flash("已登出。", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    error = None
    success = None
    if request.method == "POST":
        old_pw = request.form.get("old_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not current_user.check_password(old_pw):
            error = "舊密碼錯誤。"
        elif len(new_pw) < _PASSWORD_MIN_LEN:
            error = f"新密碼至少需 {_PASSWORD_MIN_LEN} 個字元。"
        elif new_pw != confirm:
            error = "兩次新密碼不一致。"
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            success = "密碼已成功更新。"

    return render_template("auth/account.html", error=error, success=success)


# ---------------------------------------------------------------------------
# JSON API routes  (auth_api_bp, url_prefix="/api/auth")
# ---------------------------------------------------------------------------


@auth_api_bp.route("/login", methods=["POST"])
@limiter.limit("15 per minute")
def api_login():
    data = request.get_json(silent=True) or {}
    # 主鍵為 "email"；仍接受舊呼叫端的 "username" 鍵承載 email 值。
    email_raw = str(data.get("email") or data.get("username") or "").strip()
    password = str(data.get("password") or "")

    if not is_valid_email(email_raw):
        return jsonify({
            "success": False,
            "error": "invalid_email_format",
            "message": EMAIL_FORMAT_ERROR,
        }), 400

    user = User.find_by_email(email_raw)
    if user and user.is_active and user.check_password(password):
        login_user(user)
        _start_activity_session(user)
        return jsonify({"success": True, "user": user.to_dict()})
    return jsonify({"success": False, "error": "invalid_credentials"}), 401


@auth_api_bp.route("/logout", methods=["POST"])
def api_logout():
    _end_activity_session()
    logout_user()
    return jsonify({"success": True})


@auth_api_bp.route("/me", methods=["GET"])
def api_me():
    if current_user.is_authenticated:
        return jsonify({"success": True, "user": current_user.to_dict()})
    return jsonify({"success": False, "error": "login_required"}), 401
