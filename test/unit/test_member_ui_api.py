# 檔案路徑: test/unit/test_member_ui_api.py
# 產生時間: 2026-07-19 00:00 +08:00
# 版本: v1.0
# 模組定位:
#   成員管理 UI 所依賴的 API 合約驗證（AUTH_MODE=session）。
#   確認前端 openMembersModal / membersAddNew / membersChangeRole /
#   membersRemove 等函式的底層 HTTP 合約穩定。
# 主要責任:
#   1. GET /api/project/<pid>/members 回傳含 username/role 欄位。
#   2. POST 不存在 username → 404，body 含可辨識錯誤欄位。
#   3. PATCH 改角色後 GET 即反映新角色（讀後寫一致性）。
#   4. DELETE 最後 owner → 400 with error="last_owner"（UI 錯誤訊息依賴）。
#   5. GET /api/auth/me → 已登入 200 含 username；未登入 401（
#      UI 以此判斷單機模式）。
#   (本檔不重複 test_workspace_roles.py 已覆蓋的角色矩陣案例。)
# 呼叫來源:
#   pytest test/unit/test_member_ui_api.py
# 輸入輸出契約:
#   所有 API 回傳 JSON；success=bool；錯誤有 message 或 error 欄位。
# 安全邊界:
#   AUTH_MODE=session；WTF_CSRF_ENABLED=False；每測試獨立 DB。
# 維護提醒:
#   - 若後端欄位命名更動（username→name 等），UI 亦須同步更新。
#   - 本次新增成員管理 modal，此測試為其 API 合約守門人。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test/unit/test_member_ui_api.py -q
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
    """AUTH_MODE=session 獨立測試 App。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)

    main_db = tmp_path / "mua_test.db"
    manu_db = tmp_path / "mua_manu.db"

    from app import create_app, db as _db

    app = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        "SECRET_KEY": "test-secret-mua",
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


def _test_email(username: str) -> str:
    """由測試用 username 推出登入 email。email 已是唯一登入識別。"""
    return f"{username.lower()}@test.local"


def _register_and_login(client, username: str, password: str = "password123"):
    from app import db
    from app.models import User

    with client.application.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username, email=_test_email(username))
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

    resp = client.post(
        "/api/auth/login", json={"email": _test_email(username), "password": password}
    )
    assert resp.status_code == 200, f"Login failed for {username}: {resp.get_data(as_text=True)}"
    return resp.get_json()["user"]


def _logout(client):
    client.post("/api/auth/logout")


def _create_project(client, name: str = "Test Project") -> str:
    resp = client.post("/api/project/create", json={
        "name": name,
        "members": _MEMBERS_PAYLOAD,
    })
    assert resp.status_code == 201, f"create_project failed: {resp.get_data(as_text=True)}"
    return resp.get_json()["pid"]


# ---------------------------------------------------------------------------
# 1. GET members 回傳欄位合約
# ---------------------------------------------------------------------------

class TestGetMembersContract:
    def test_get_members_returns_username_and_role_fields(self, client):
        """
        UI openMembersModal 依賴 members[].username 與 members[].role。
        GET /api/project/<pid>/members 必須回傳含這兩個欄位的物件陣列。
        """
        _register_and_login(client, "mua_alice1")
        pid = _create_project(client, "MUA P1")

        resp = client.get(f"/api/project/{pid}/members")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "members" in data
        members = data["members"]
        assert isinstance(members, list)
        assert len(members) >= 1
        for m in members:
            assert "username" in m, f"member missing 'username': {m}"
            assert "role" in m, f"member missing 'role': {m}"

    def test_get_members_owner_role_value(self, client):
        """
        建立者角色值為 'owner'（英文小寫字串）。
        UI ROLE_LABELS 映射依賴此值。
        """
        _register_and_login(client, "mua_alice2")
        pid = _create_project(client, "MUA P2")

        resp = client.get(f"/api/project/{pid}/members")
        data = resp.get_json()
        creator = next((m for m in data["members"] if m["username"] == "mua_alice2"), None)
        assert creator is not None, "建立者應在成員列表中"
        assert creator["role"] == "owner", f"預期 owner，實際 {creator['role']}"


# ---------------------------------------------------------------------------
# 2. POST 不存在 username → 404 + 可辨識錯誤
# ---------------------------------------------------------------------------

class TestPostMemberNotFound:
    def test_post_nonexistent_username_returns_404(self, client):
        """
        UI membersAddNew 在 404 時顯示「找不到此帳號」。
        後端必須回傳 404，且 body 含 success=false 與 message 欄位。
        """
        _register_and_login(client, "mua_alice3")
        pid = _create_project(client, "MUA P3")

        resp = client.post(f"/api/project/{pid}/members", json={
            "username": "definitely_not_registered_xyzxyz",
            "role": "viewer"
        })
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["success"] is False
        # body 需有 message 欄位供前端提取錯誤說明
        assert "message" in data, f"404 body 缺少 message 欄位: {data}"
        assert data["message"]  # 非空

    def test_post_nonexistent_username_message_mentions_user(self, client):
        """
        404 message 需與 username 相關（前端不需解析，但方便 debug）。
        """
        _register_and_login(client, "mua_alice4")
        pid = _create_project(client, "MUA P4")

        target = "ghost_user_404_mua"
        resp = client.post(f"/api/project/{pid}/members", json={
            "username": target,
            "role": "editor"
        })
        assert resp.status_code == 404
        msg = resp.get_json().get("message", "")
        assert target in msg or "not found" in msg.lower() or "找不到" in msg


# ---------------------------------------------------------------------------
# 3. PATCH 改角色後 GET 反映新角色（讀後寫一致性）
# ---------------------------------------------------------------------------

class TestPatchRoleConsistency:
    def test_patch_role_reflected_in_get(self, client):
        """
        UI membersChangeRole 呼叫 PATCH 後再呼叫 _membersRefresh(GET)；
        GET 必須回傳更新後的角色。
        """
        _register_and_login(client, "mua_alice5")
        pid = _create_project(client, "MUA P5")
        _register_and_login(client, "mua_bob5")
        _logout(client)

        # alice 加 bob 為 viewer
        _register_and_login(client, "mua_alice5")
        add_resp = client.post(f"/api/project/{pid}/members", json={
            "username": "mua_bob5", "role": "viewer"
        })
        assert add_resp.status_code == 200

        # PATCH: viewer → editor
        patch_resp = client.patch(f"/api/project/{pid}/members/mua_bob5", json={"role": "editor"})
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["success"] is True

        # GET 驗證一致性
        get_resp = client.get(f"/api/project/{pid}/members")
        assert get_resp.status_code == 200
        members = get_resp.get_json()["members"]
        bob = next((m for m in members if m["username"] == "mua_bob5"), None)
        assert bob is not None, "bob 應在成員列表"
        assert bob["role"] == "editor", f"預期 editor，實際 {bob['role']}"


# ---------------------------------------------------------------------------
# 4. DELETE 最後 owner → 400 + error="last_owner"（UI 錯誤訊息依賴）
# ---------------------------------------------------------------------------

class TestDeleteLastOwnerProtection:
    def test_delete_last_owner_returns_400_last_owner(self, client):
        """
        UI membersRemove / membersSelfExit 在 400 last_owner 時
        顯示「專案至少需要一位 Owner」。
        後端 body 必須含 error="last_owner"。
        """
        _register_and_login(client, "mua_alice6")
        pid = _create_project(client, "MUA P6")

        resp = client.delete(f"/api/project/{pid}/members/mua_alice6")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("error") == "last_owner", f"預期 error='last_owner'，實際: {data}"
        assert data.get("success") is False


# ---------------------------------------------------------------------------
# 5. GET /api/auth/me 合約（UI openMembersModal 單機模式判斷依賴）
# ---------------------------------------------------------------------------

class TestAuthMeContract:
    def test_me_returns_401_when_not_logged_in(self, client):
        """
        UI 在 me=401 時顯示「單機模式無成員管理」提示。
        未登入狀態必須回傳 401。
        """
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_returns_username_when_logged_in(self, client):
        """
        UI 以 me.user.username 識別「目前登入者」。
        登入後 /api/auth/me 必須回傳 success=true 與 user.username。
        """
        _register_and_login(client, "mua_alice7")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "user" in data
        assert data["user"]["username"] == "mua_alice7"
