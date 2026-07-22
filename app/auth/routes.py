# 檔案路徑: app/auth/routes.py
# 產生時間: 2026-07-19 00:00 +08:00
# 版本: v1.0
# 模組定位:
#   Auth blueprint 路由：HTML 表單（register/login/logout/account）
#   與 JSON API（/api/auth/login, /api/auth/logout, /api/auth/me）。
# 主要責任:
#   1. GET/POST /auth/register — 新帳號註冊（username 唯一驗證）。
#   2. GET/POST /auth/login    — 登入，支援 next 參數防 open redirect。
#   3. POST /auth/logout       — 登出（HTML）。
#   4. GET/POST /auth/account  — 改密碼（需驗舊密碼，新密碼 >= 8 字元）。
#   5. POST /api/auth/login    — JSON 登入，供前端 JS 呼叫（auth_api_bp）。
#   6. POST /api/auth/logout   — JSON 登出（auth_api_bp）。
#   7. GET  /api/auth/me       — 回傳當前使用者 dict 或 401（auth_api_bp）。
# 維護提醒:
#   - 表單路由的 POST 已由 _auth_guard 的 csrf.protect() 保護；
#     API 路由（/api/*）不走 csrf.protect()，由前端帶 session cookie 驗證。
#   - next 參數只允許站內相對路徑（不以 http 起頭，不含 //），防 open redirect。
#   - username 長度限制 3-32 字元（硬驗證，正規表達式 [A-Za-z0-9_\-]{3,32}）。
#   - 密碼最短 8 字元（account 改密碼時強制）。
#   - auth_bp url_prefix="/auth"；auth_api_bp url_prefix="/api/auth"。
# 驗證方式:
#   python -m pytest test/unit/test_auth_basic.py -q
# ------------------------------------------------------------------------------
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db, limiter
from app.models import User

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")

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
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not _USERNAME_RE.fullmatch(username):
            error = "帳號需為 3–32 個英數字或 _- 字元。"
        elif password != confirm:
            error = "兩次密碼不一致。"
        elif len(password) < 8:
            error = "密碼至少需 8 個字元。"
        elif User.query.filter_by(username=username).first():
            error = f"帳號「{username}」已被使用。"
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
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
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = _safe_next(request.form.get("next") or request.args.get("next"))

        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            return redirect(next_url or url_for("main.index"))
        else:
            error = "帳號或密碼錯誤。"

    next_val = request.args.get("next", "")
    return render_template("auth/login.html", error=error, next=next_val)


@auth_bp.route("/logout", methods=["POST"])
def logout():
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
        elif len(new_pw) < 8:
            error = "新密碼至少需 8 個字元。"
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
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")

    user = User.query.filter_by(username=username).first()
    if user and user.is_active and user.check_password(password):
        login_user(user)
        return jsonify({"success": True, "user": user.to_dict()})
    return jsonify({"success": False, "error": "invalid_credentials"}), 401


@auth_api_bp.route("/logout", methods=["POST"])
def api_logout():
    logout_user()
    return jsonify({"success": True})


@auth_api_bp.route("/me", methods=["GET"])
def api_me():
    if current_user.is_authenticated:
        return jsonify({"success": True, "user": current_user.to_dict()})
    return jsonify({"success": False, "error": "login_required"}), 401
