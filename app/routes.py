#路徑(./app/routes.py) #版本 v0.1 #更版時間 20260312-0010
"""
全局視圖路由控制器 (Global Route Rendering)
職責：接管原先位於 __init__.py 的所有根目錄起始視圖路由，便於未來主線路由的持續擴充。
"""
from flask import Blueprint, render_template, request
from werkzeug.exceptions import BadRequest
from app.security import validate_id

# 宣告主線藍圖，無 url_prefix，直接作用於根目錄
main_bp = Blueprint('main', __name__)

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
