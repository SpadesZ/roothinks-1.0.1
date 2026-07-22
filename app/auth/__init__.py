# 檔案路徑: app/auth/__init__.py
# 產生時間: 2026-07-19 00:00 +08:00
# 版本: v1.0
# 模組定位:
#   Auth blueprint 套件初始化。匯出 auth_bp 與 auth_api_bp 供 create_app 註冊。
# 主要責任:
#   1. 建立 Flask Blueprint auth_bp，url_prefix='/auth'（HTML 表單路由）。
#   2. 延遲匯入 routes，讓 routes.py 建立 auth_api_bp 並掛載所有路由函式。
#   3. 匯出 auth_api_bp，由 create_app 另行 register_blueprint。
# 維護提醒:
#   - auth_bp 與 auth_api_bp 分開 prefix，避免 /auth/api/auth/... 路徑衝突。
#   - AUTH_MODE 控制是否啟用 session 登入守衛（見 app/__init__.py _auth_guard）。
# 驗證方式:
#   python -m pytest test/unit/test_auth_basic.py -q
# ------------------------------------------------------------------------------
from flask import Blueprint

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

from app.auth import routes  # noqa: E402, F401  # pylint: disable=wrong-import-position

# auth_api_bp is created in routes.py; re-export it here for create_app
from app.auth.routes import auth_api_bp  # noqa: E402, F401
