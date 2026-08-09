# Roothinks source maintenance contract
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 檔案路徑: test/unit/test_auth_basic.py
# 產生時間: 2026-07-19 00:00 +08:00
# 版本: v1.0
# 模組定位:
#   Batch A 帳號登入系統的基本單元測試。
# 主要責任: 重現並驗收 auth basic 的成功、失敗與回歸邊界。
#   1. 驗證 User model 註冊/登入/密碼流程。
#   2. 驗證 /api/auth/* JSON API 行為。
#   3. 驗證 AUTH_MODE=session 的路由保護效果。
#   4. 驗證預設（TESTING=True 無 AUTH_MODE）下相容性不受影響。
# 維護提醒:
#   - fixture 使用 TESTING=True + AUTH_MODE='session' + WTF_CSRF_ENABLED=False。
#   - AUTH_MODE 判定優先順序：test_config dict > env AUTH_MODE > 自動推算。
#   - 所有 POST 到 /api/auth/* 不需 CSRF（is_api_request_path 為 True）。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test/unit/test_auth_basic.py -v
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

# 確保 project root 在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_session(monkeypatch, tmp_path):
    """AUTH_MODE=session app fixture；CSRF 關閉方便測試。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)

    # 使用 in-memory SQLite 隔離測試
    db_uri = "sqlite:///:memory:"

    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "AUTH_MODE": "session",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": db_uri,
            "SQLALCHEMY_BINDS": {"manuscript": db_uri},
            "SERVER_NAME": None,
        }
    )
    # WTF_CSRF_CHECK_DEFAULT 也關掉以防萬一
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with app.app_context():
        from app import db
        db.create_all()
        yield app


@pytest.fixture
def client_session(app_session):
    return app_session.test_client()


@pytest.fixture
def app_none(monkeypatch, tmp_path):
    """預設模式 app（TESTING=True，不設 AUTH_MODE → 自動 none）。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)

    db_uri = "sqlite:///:memory:"

    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": db_uri,
            "SQLALCHEMY_BINDS": {"manuscript": db_uri},
        }
    )
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with app.app_context():
        from app import db
        db.create_all()
        yield app


@pytest.fixture
def client_none(app_none):
    return app_none.test_client()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _register(client, email="testuser@example.com", password="password123"):
    return client.post(
        "/auth/register",
        data={"email": email, "password": password, "confirm_password": password},
        follow_redirects=True,
    )


def _login_api(client, email="testuser@example.com", password="password123"):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        content_type="application/json",
    )


def _logout_api(client):
    return client.post("/api/auth/logout", content_type="application/json")


def _me(client):
    return client.get("/api/auth/me")


# ---------------------------------------------------------------------------
# Test: User model — 註冊 / 重複帳號
# ---------------------------------------------------------------------------


class TestUserModel:
    def test_register_success(self, client_session):
        """正常註冊後跳轉到首頁（已自動登入）。"""
        resp = _register(client_session)
        assert resp.status_code == 200

    def test_duplicate_email_fails(self, client_session, app_session):
        """重複 email 返回錯誤訊息，不建立第二個帳號。"""
        _register(client_session, email="alice@example.com", password="password123")
        # 先登出
        _logout_api(client_session)

        resp = client_session.post(
            "/auth/register",
            data={
                "email": "alice@example.com",
                "password": "password456",
                "confirm_password": "password456",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "已被使用" in body

        # DB 中只有一個 alice@example.com
        with app_session.app_context():
            from app.models import User
            count = User.query.filter_by(email="alice@example.com").count()
            assert count == 1


# ---------------------------------------------------------------------------
# Test: /api/auth/* JSON API
# ---------------------------------------------------------------------------


class TestAuthAPI:
    def test_login_success_then_me_200(self, client_session):
        """登入成功後 /api/auth/me 回 200 + user dict。"""
        _register(client_session)
        _logout_api(client_session)

        resp = _login_api(client_session)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["user"]["username"] == "testuser"
        assert "password_hash" not in data["user"]

        me_resp = _me(client_session)
        assert me_resp.status_code == 200
        assert me_resp.get_json()["user"]["username"] == "testuser"

    def test_wrong_password_401(self, client_session):
        """錯誤密碼應回 401。"""
        _register(client_session)
        _logout_api(client_session)

        resp = _login_api(client_session, password="wrongpass")
        assert resp.status_code == 401

    def test_logout_then_me_401(self, client_session):
        """登出後 /api/auth/me 應回 401。"""
        _register(client_session)
        _logout_api(client_session)

        me_resp = _me(client_session)
        assert me_resp.status_code == 401

    def test_me_unauthenticated_401(self, client_session):
        """未登入時 /api/auth/me 回 401。"""
        resp = _me(client_session)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test: 改密碼
# ---------------------------------------------------------------------------


class TestChangePassword:
    def _setup_user(self, client):
        _register(client, email="bob@example.com", password="oldpass1")
        _logout_api(client)

    def test_change_password_wrong_old_fails(self, client_session):
        """舊密碼錯 → 頁面顯示錯誤訊息。"""
        self._setup_user(client_session)
        _login_api(client_session, email="bob@example.com", password="oldpass1")

        resp = client_session.post(
            "/auth/account",
            data={
                "old_password": "wrongold",
                "new_password": "newpass99",
                "confirm_password": "newpass99",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "舊密碼錯誤" in resp.data.decode("utf-8")

    def test_change_password_success(self, client_session):
        """成功改密碼：舊密碼登不進，新密碼可以。"""
        self._setup_user(client_session)
        _login_api(client_session, email="bob@example.com", password="oldpass1")

        resp = client_session.post(
            "/auth/account",
            data={
                "old_password": "oldpass1",
                "new_password": "newpass99",
                "confirm_password": "newpass99",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "密碼已成功更新" in resp.data.decode("utf-8")

        # 登出後用舊密碼登不進
        _logout_api(client_session)
        resp_old = _login_api(client_session, email="bob@example.com", password="oldpass1")
        assert resp_old.status_code == 401

        # 新密碼可以
        resp_new = _login_api(client_session, email="bob@example.com", password="newpass99")
        assert resp_new.status_code == 200


# ---------------------------------------------------------------------------
# Test: AUTH_MODE=session 路由保護
# ---------------------------------------------------------------------------


class TestSessionModeGuard:
    def test_api_project_list_unauthenticated_401(self, client_session):
        """AUTH_MODE=session 未登入訪問 /api/project/list → 401。"""
        resp = client_session.get("/api/project/list")
        assert resp.status_code == 401
        data = resp.get_json()
        assert data is not None
        assert data.get("error") == "login_required"

    def test_root_unauthenticated_redirects_to_login(self, client_session):
        """AUTH_MODE=session 未登入訪問 / → 302 到 /auth/login。"""
        resp = client_session.get("/", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/auth/login" in location

    def test_auth_login_page_accessible_unauthenticated(self, client_session):
        """/auth/login 自身不被守衛擋住（白名單）。"""
        resp = client_session.get("/auth/login")
        assert resp.status_code == 200

    def test_api_auth_login_accessible_unauthenticated(self, client_session):
        """/api/auth/login 不被 session 守衛擋住。"""
        # email 格式必須合法，否則會先被格式驗證擋成 400，測不到守衛行為。
        resp = _login_api(client_session, email="nobody@example.com", password="x")
        # 401 是帳號錯誤，不是 session 守衛
        assert resp.status_code == 401
        data = resp.get_json()
        assert data.get("error") == "invalid_credentials"


# ---------------------------------------------------------------------------
# Test: AUTH_MODE=none 相容性（預設 TESTING 不設 AUTH_MODE）
# ---------------------------------------------------------------------------


class TestNoneModeCompat:
    def test_api_project_list_no_auth_required(self, client_none):
        """AUTH_MODE=none（預設）時 /api/project/list 不需登入。"""
        resp = client_none.get("/api/project/list")
        # 可能 200 或 404，但絕對不是 401（無守衛）
        assert resp.status_code != 401

    def test_auth_mode_is_none(self, app_none):
        """確認 TESTING=True 不設 AUTH_MODE 時 config 為 none。"""
        assert app_none.config["AUTH_MODE"] == "none"
