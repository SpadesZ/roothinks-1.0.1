# 檔案路徑: roothinks/test/unit/test_workspace_roles.py
# 產生時間: 2026-07-19 00:00 +08:00
# 版本: v1.0
# 模組定位:
#   Batch B 工作區角色授權單元測試。
# 主要責任:
#   1. 角色矩陣：viewer/editor/owner 對各端點的存取控制。
#   2. 隔離：不同 user 的 list 只回傳自己有 membership 的專案。
#   3. 成員管理 API：POST/DELETE/PATCH 的 owner-only 保護與最後 owner 防護。
#   4. 相容：AUTH_MODE=none 時原有流程不破。
#   5. dev 模式：無 session user 時 create project 也能成功（不建 membership）。
# 維護提醒:
#   - 所有測試使用 AUTH_MODE=session、WTF_CSRF_ENABLED=False。
#   - 三位 user：alice(owner 建 P1)、bob(陸續加入)、carol(成員操作測試)。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test/unit/test_workspace_roles.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_MEMBERS_PAYLOAD = [{"name": "主持人", "role": "主持人"}]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def session_app(monkeypatch, tmp_path):
    """建立 AUTH_MODE=session 的測試 App；每個測試函數獨立 DB。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)

    main_db = tmp_path / "ws_test.db"
    manu_db = tmp_path / "ws_manu.db"

    from app import create_app, db as _db

    app = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        "SECRET_KEY": "test-secret-for-ws-roles",
    })

    with app.app_context():
        _db.drop_all()
        _db.create_all()

    yield app

    with app.app_context():
        _db.session.remove()
        try:
            _db.engine.dispose()
        except Exception:
            pass


@pytest.fixture()
def client(session_app):
    return session_app.test_client()


def _register_and_login(client, username: str, password: str = "password123"):
    """在 test client 中建立 user 並登入；回傳 user dict。"""
    from app import db
    from app.models import User

    with client.application.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.get_data(as_text=True)}"
    return resp.get_json()["user"]


def _logout(client):
    client.post("/api/auth/logout")


def _create_project(client, name: str = "Test Project") -> str:
    """以目前 session 建立專案，回傳 pid。"""
    resp = client.post("/api/project/create", json={
        "name": name,
        "members": _MEMBERS_PAYLOAD,
    })
    assert resp.status_code == 201, f"create_project failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["pid"]


# ---------------------------------------------------------------------------
# 1. 角色矩陣測試
# ---------------------------------------------------------------------------

class TestRoleMatrix:
    def test_no_membership_get_returns_403(self, client):
        """bob 未加入 → GET /api/project/<pid>/members 403。"""
        _register_and_login(client, "alice_rm1")
        pid = _create_project(client, "Alice P1")
        _logout(client)

        _register_and_login(client, "bob_rm1")
        resp = client.get(f"/api/project/{pid}/members")
        assert resp.status_code == 403

    def test_viewer_can_get_members(self, client):
        """bob 加為 viewer → GET /api/project/<pid>/members 200。"""
        _register_and_login(client, "alice_rm2")
        pid = _create_project(client, "Alice P2")

        # 先建立 bob 帳號，再切回 alice 加入
        _register_and_login(client, "bob_rm2")
        _logout(client)
        _register_and_login(client, "alice_rm2")
        resp = client.post(f"/api/project/{pid}/members", json={"username": "bob_rm2", "role": "viewer"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        _logout(client)

        _register_and_login(client, "bob_rm2")
        resp = client.get(f"/api/project/{pid}/members")
        assert resp.status_code == 200

    def test_viewer_cannot_post_update(self, client):
        """bob=viewer → POST /api/project/update/<pid> 403。"""
        _register_and_login(client, "alice_rm3")
        pid = _create_project(client, "Alice P3")
        _register_and_login(client, "bob_rm3")
        _logout(client)
        _register_and_login(client, "alice_rm3")
        client.post(f"/api/project/{pid}/members", json={"username": "bob_rm3", "role": "viewer"})
        _logout(client)

        _register_and_login(client, "bob_rm3")
        resp = client.post(f"/api/project/update/{pid}", json={"name": "New Name"})
        assert resp.status_code == 403

    def test_editor_can_post_update(self, client):
        """bob=editor → POST /api/project/update/<pid> 200。"""
        _register_and_login(client, "alice_rm4")
        pid = _create_project(client, "Alice P4")
        _register_and_login(client, "bob_rm4")
        _logout(client)
        _register_and_login(client, "alice_rm4")
        client.post(f"/api/project/{pid}/members", json={"username": "bob_rm4", "role": "editor"})
        _logout(client)

        _register_and_login(client, "bob_rm4")
        resp = client.post(f"/api/project/update/{pid}", json={"name": "Updated"})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_editor_cannot_delete(self, client):
        """editor → DELETE 403。"""
        _register_and_login(client, "alice_rm5")
        pid = _create_project(client, "Alice P5")
        _register_and_login(client, "bob_rm5")
        _logout(client)
        _register_and_login(client, "alice_rm5")
        client.post(f"/api/project/{pid}/members", json={"username": "bob_rm5", "role": "editor"})
        _logout(client)

        _register_and_login(client, "bob_rm5")
        resp = client.delete(f"/api/project/delete/{pid}")
        assert resp.status_code == 403

    def test_owner_can_delete(self, client):
        """alice=owner → DELETE 200。"""
        _register_and_login(client, "alice_rm6")
        pid = _create_project(client, "Alice P6")
        resp = client.delete(f"/api/project/delete/{pid}")
        assert resp.status_code == 200, resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 2. 隔離測試
# ---------------------------------------------------------------------------

class TestProjectIsolation:
    def test_alice_only_sees_p1(self, client):
        """alice 建 P1，bob 建 P2；alice /list 只見 P1。"""
        alice_info = _register_and_login(client, "alice_iso")
        pid_alice = _create_project(client, "Alice Iso P1")
        _logout(client)

        _register_and_login(client, "bob_iso")
        _create_project(client, "Bob Iso P2")
        _logout(client)

        _register_and_login(client, "alice_iso")
        resp = client.get("/api/project/list")
        assert resp.status_code == 200
        projects = resp.get_json()["projects"]
        pids = [p["project_id"] for p in projects]
        assert pid_alice in pids
        assert all(p["project_id"] == pid_alice for p in projects), \
            f"alice 看到了非自己的專案: {pids}"

    def test_bob_only_sees_p2(self, client):
        """bob /list 只見 P2（同 fixture 延伸邏輯，此處獨立建立）。"""
        _register_and_login(client, "alice_iso2")
        _create_project(client, "Alice Iso2 P1")
        _logout(client)

        _register_and_login(client, "bob_iso2")
        pid_bob = _create_project(client, "Bob Iso2 P2")

        resp = client.get("/api/project/list")
        assert resp.status_code == 200
        projects = resp.get_json()["projects"]
        pids = [p["project_id"] for p in projects]
        assert pid_bob in pids
        assert all(p["project_id"] == pid_bob for p in projects), \
            f"bob 看到了非自己的專案: {pids}"


# ---------------------------------------------------------------------------
# 3. 成員管理 API 測試
# ---------------------------------------------------------------------------

class TestMemberManagement:
    def test_non_owner_post_members_403(self, client):
        """bob=viewer → POST /api/project/<pid>/members 403。"""
        _register_and_login(client, "alice_mm1")
        pid = _create_project(client, "MM P1")
        _register_and_login(client, "bob_mm1")
        _logout(client)
        _register_and_login(client, "alice_mm1")
        client.post(f"/api/project/{pid}/members", json={"username": "bob_mm1", "role": "viewer"})
        _logout(client)

        _register_and_login(client, "bob_mm1")
        resp = client.post(f"/api/project/{pid}/members", json={"username": "bob_mm1", "role": "editor"})
        # viewer 做 POST → enforce_project_ownership 先過 editor，但 require_workspace_role(owner) 403
        assert resp.status_code == 403

    def test_owner_can_add_member(self, client):
        """owner 加 bob=editor → 200。"""
        _register_and_login(client, "alice_mm2")
        pid = _create_project(client, "MM P2")
        _register_and_login(client, "bob_mm2")
        _logout(client)

        _register_and_login(client, "alice_mm2")
        resp = client.post(f"/api/project/{pid}/members", json={"username": "bob_mm2", "role": "editor"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["member"]["role"] == "editor"

    def test_last_owner_cannot_remove_self(self, client):
        """最後一位 owner 自我移除 → 400 last_owner。"""
        _register_and_login(client, "alice_mm3")
        pid = _create_project(client, "MM P3")
        resp = client.delete(f"/api/project/{pid}/members/alice_mm3")
        assert resp.status_code == 400
        assert resp.get_json().get("error") == "last_owner"

    def test_owner_can_exit_when_another_owner_exists(self, client):
        """alice 加 carol=owner 後，alice 自行退出 → 200。"""
        _register_and_login(client, "alice_mm4")
        pid = _create_project(client, "MM P4")
        _register_and_login(client, "carol_mm4")
        _logout(client)

        _register_and_login(client, "alice_mm4")
        resp = client.post(f"/api/project/{pid}/members", json={"username": "carol_mm4", "role": "owner"})
        assert resp.status_code == 200

        resp = client.delete(f"/api/project/{pid}/members/alice_mm4")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_get_members_returns_list(self, client):
        """GET /api/project/<pid>/members 回傳成員清單。"""
        _register_and_login(client, "alice_mm5")
        pid = _create_project(client, "MM P5")
        resp = client.get(f"/api/project/{pid}/members")
        assert resp.status_code == 200
        members = resp.get_json()["members"]
        assert any(m["username"] == "alice_mm5" and m["role"] == "owner" for m in members)


# ---------------------------------------------------------------------------
# 4. 相容性：AUTH_MODE 預設（TESTING）不破舊流程
# ---------------------------------------------------------------------------

class TestAuthModeNoneCompat:
    @pytest.fixture()
    def none_client(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.delenv("APP_ENV", raising=False)
        main_db = tmp_path / "compat_test.db"
        manu_db = tmp_path / "compat_manu.db"

        from app import create_app, db as _db

        app = create_app({
            "TESTING": True,
            # AUTH_MODE 不設定 → TESTING=True 自動 "none"
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
            "SECRET_KEY": "test-compat",
        })
        with app.app_context():
            _db.drop_all()
            _db.create_all()
        yield app.test_client()

    def test_list_without_login_200(self, none_client):
        """AUTH_MODE=none 時，未登入也可以呼叫 /api/project/list 並得到 200。"""
        resp = none_client.get("/api/project/list")
        assert resp.status_code == 200

    def test_create_without_login_201(self, none_client):
        """AUTH_MODE=none 時，create project 不需要 session user。"""
        resp = none_client.post("/api/project/create", json={
            "name": "Compat Test Project",
            "members": _MEMBERS_PAYLOAD,
        })
        assert resp.status_code == 201, resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 5. dev 模式下 create_project 不建 membership 也成功
# ---------------------------------------------------------------------------

class TestDevModeNoMembership:
    def test_create_project_no_session_user(self, monkeypatch, tmp_path):
        """
        dev 模式 + 無 session user → create_project 成功，
        workspace_members 表不產生任何記錄（跳過 membership 寫入）。
        """
        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.delenv("APP_ENV", raising=False)
        main_db = tmp_path / "dev_test.db"
        manu_db = tmp_path / "dev_manu.db"

        from app import create_app, db as _db
        from app.models import WorkspaceMember

        app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
            "SECRET_KEY": "test-dev",
        })
        with app.app_context():
            _db.drop_all()
            _db.create_all()

        client = app.test_client()
        resp = client.post("/api/project/create", json={
            "name": "Dev Project No Session",
            "members": _MEMBERS_PAYLOAD,
        })
        assert resp.status_code == 201, resp.get_data(as_text=True)

        pid = resp.get_json()["pid"]
        with app.app_context():
            count = WorkspaceMember.query.filter_by(pid=pid).count()
            # dev 模式無 session user，不寫 membership
            assert count == 0
