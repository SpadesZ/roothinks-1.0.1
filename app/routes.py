#路徑(./app/routes.py) #版本 v0.2 #更版時間 20260726-0410
"""
全局視圖路由控制器 (Global Route Rendering)
職責：接管原先位於 __init__.py 的所有根目錄起始視圖路由，便於未來主線路由的持續擴充。

[v0.2] 新增 /api/heartbeat：前端每 60 秒回報一次，用來累計「停留時間」。
       系統原本完全沒有活動埋點，mentor 儀表板的統計全部由這條路徑餵養。
"""
import logging

from flask import Blueprint, jsonify, render_template, request, session
from werkzeug.exceptions import BadRequest

from app.security import validate_id

logger = logging.getLogger("main_routes")

# 宣告主線藍圖，無 url_prefix，直接作用於根目錄
main_bp = Blueprint('main', __name__)

# flask session 中存放目前 UserSession 主鍵的鍵名。
ACTIVITY_SESSION_KEY = "activity_session_id"


@main_bp.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """
    回報使用者仍在線。

    停留時間以「最後一次 heartbeat 減登入時間」計算，因此關掉瀏覽器後最多
    只會多算一個 heartbeat 間隔，不會把整夜掛機都算進去。
    路徑位於 /api/ 之下，不走 CSRF，以 session cookie 認身分。
    """
    from flask_login import current_user

    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "login_required"}), 401

    from app import db
    from app.models import UserSession

    session_id = session.get(ACTIVITY_SESSION_KEY)
    row = db.session.get(UserSession, session_id) if session_id else None

    # session 紀錄遺失（例如伺服器重啟但 cookie 還在）時補開一筆，
    # 否則這段使用時間會整個消失。
    if row is None or row.user_id != current_user.id or row.ended_at is not None:
        row = UserSession(user_id=current_user.id)
        db.session.add(row)
        db.session.flush()
        session[ACTIVITY_SESSION_KEY] = row.id

    try:
        row.touch()
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("heartbeat 更新失敗 user_id=%s", current_user.id, exc_info=True)
        return jsonify({"success": False}), 500

    return jsonify({"success": True, "duration_sec": row.duration_sec}), 200

@main_bp.route('/')
def index():
    """渲染專案管理首頁 Dashboard"""
    return render_template('dashboard.html')

@main_bp.route('/setup')
@main_bp.route('/lava_setup.html')
def setup_page():
    """渲染 LAVA LLM 連線與任務綁定設定頁面 (支援雙路由)"""
    return render_template('lava_setup.html')

@main_bp.route('/paq/<pid>')
def paq_workspace(pid):
    """渲染 PAQ 核心工作檯 (RESTful 風格)"""
    try:
        pid = validate_id(pid, "project_id")
    except BadRequest:
        pid = ""
    return render_template('paq.html', pid=pid)

@main_bp.route('/paq')
@main_bp.route('/paq.html')
def paq_workspace_query():
    """渲染 PAQ 核心工作檯 (支援裸路徑 /paq 與 Query 參數風格 ?pid=xxx)"""
    raw_pid = request.args.get('pid', '')
    try:
        pid = validate_id(raw_pid, "project_id", required=False)
    except BadRequest:
        pid = ""
    return render_template('paq.html', pid=pid)


@main_bp.route('/submit')
@main_bp.route('/submit/')
def submit_workspace():
    """渲染投稿版型輸出頁。"""
    raw_pid = request.args.get('pid', '')
    try:
        pid = validate_id(raw_pid, "project_id", required=False)
    except BadRequest:
        pid = ""
    return render_template('submit.html', pid=pid)
