# Roothinks source maintenance contract
# 上下游: main/create_app 啟動本層，再註冊 Blueprint、DB、Socket、runtime service 供 HTTP/worker 使用。
# 檔案路徑: app/mentor/__init__.py
# 產生時間: 2026-07-26 04:00 +08:00
# 版本: v1.0
# 模組定位:
#   Mentor blueprint 套件初始化。匯出 mentor_bp 與 mentor_api_bp 供 create_app 註冊。
# 主要責任: 建立 Mentor/Reviewer Blueprint 並註冊關係管理與 2C workbench routes。
#   1. 建立 mentor_bp（HTML 頁面，url_prefix='/mentor'）。
#   2. 延遲匯入 routes，讓 routes.py 建立 mentor_api_bp 並掛載路由。
# 維護提醒:
#   - 與 auth 套件同規：HTML 與 JSON API 分開 blueprint，避免路徑前綴打架。
#   - mentor_api_bp 的 url_prefix 為 /api/mentor，屬 is_api_request_path，
#     因此不走 csrf.protect()，以 session cookie 驗身分。
# 驗證方式:
#   python -m pytest test/unit/test_mentor_scope.py -q
# ------------------------------------------------------------------------------
from flask import Blueprint

mentor_bp = Blueprint("mentor", __name__, url_prefix="/mentor")

from app.mentor import routes  # noqa: E402, F401  # pylint: disable=wrong-import-position

from app.mentor.routes import mentor_api_bp  # noqa: E402, F401
