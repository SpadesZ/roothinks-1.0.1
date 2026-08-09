# Roothinks source maintenance contract
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 檔案路徑: roothinks/test/unit/test_literature_bootstrap_isolation.py
# 產生時間: 2026-07-22 +08:00
# 版本: v1.0
# 模組定位:
#   Literature bootstrap 跨租戶隔離迴歸測試（Security Fix 20260722）。
# 主要責任: 重現並驗收 literature bootstrap isolation 的成功、失敗與回歸邊界。
#   1. [核心] 全新帳號（零 WorkspaceMember membership）呼叫
#      GET /api/literature/bootstrap（不帶 pid）必須拿到空結果，
#      不得回傳他人正式專案清單或第一個專案的 manual_context。
#   2. [正向] 有 membership 的 owner 仍能透過 bootstrap 看到自己的正式專案。
#   3. [相容] AUTH_MODE=none（單機）維持全回，不受過濾影響。
# 背景:
#   修法前 get_literature_bootstrap 用 raw Project.query.filter_by(status='formal')
#   撈全部正式專案；blueprint 的 pid ACL 在「pid 為空」時放行，導致零權限帳號開
#   /literature（URL 無 pid）時被回傳他人專案與 context = 跨租戶越權讀取。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test/unit/test_literature_bootstrap_isolation.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures（比照 test_workspace_roles.py）
# ---------------------------------------------------------------------------

@pytest.fixture()
def session_app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)

    main_db = tmp_path / "boot_iso.db"
    manu_db = tmp_path / "boot_iso_manu.db"

    from app import create_app, db as _db

    app = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        "SECRET_KEY": "test-secret-boot-iso",
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


def _make_user(app, username: str, password: str = "password123") -> int:
    from app import db
    from app.models import User
    with app.app_context():
        user = User(username=username, email=_test_email(username))
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(client, username: str, password: str = "password123"):
    resp = client.post(
        "/api/auth/login", json={"email": _test_email(username), "password": password}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _logout(client):
    client.post("/api/auth/logout")


def _make_formal_project(app, pid: str, owner_user_id: int, title: str, background: str):
    """直接寫 DB 建立一個 formal 專案 + owner membership（pid 完全對齊）。"""
    from app import db
    from app.models import Project, WorkspaceMember, ROLE_OWNER
    with app.app_context():
        proj = Project(
            project_id=pid,
            name=title,
            research_title=title,
            status="formal",
            context_background=background,
        )
        db.session.add(proj)
        db.session.add(WorkspaceMember(user_id=owner_user_id, pid=pid, role=ROLE_OWNER))
        db.session.commit()


# ---------------------------------------------------------------------------
# 1. 核心：全新帳號不得越權讀到他人正式專案 / context
# ---------------------------------------------------------------------------

def test_new_account_bootstrap_is_empty(session_app, client):
    """
    alice 有一個 formal 專案（含機密 context）；bob 為全新帳號、零 membership。
    bob 呼叫 /api/literature/bootstrap（不帶 pid）→ 必須完全為空，不得洩漏 alice 的資料。
    """
    alice_id = _make_user(session_app, "alice_boot")
    _make_user(session_app, "bob_boot")
    _make_formal_project(
        session_app,
        pid="ALICEP-p",
        owner_user_id=alice_id,
        title="Alice 機密研究",
        background="人類大腦處理語言的 Transformer fMRI 機密研究內容",
    )

    _login(client, "bob_boot")
    resp = client.get("/api/literature/bootstrap")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()

    assert data["status"] == "success"
    assert data["active_project"] is None, f"洩漏了 active_project: {data['active_project']}"
    assert data["formal_projects"] == [], f"洩漏了他人專案清單: {data['formal_projects']}"
    assert data["manual_context"] == "", f"洩漏了他人 context: {data['manual_context']!r}"


def test_new_account_bootstrap_does_not_leak_context_string(session_app, client):
    """更嚴格：回應中任何欄位都不得出現 alice 的機密字串。"""
    alice_id = _make_user(session_app, "alice_leak")
    _make_user(session_app, "bob_leak")
    secret = "SECRET_MARKER_大腦_fMRI_9f83"
    _make_formal_project(
        session_app,
        pid="LEAKP-p",
        owner_user_id=alice_id,
        title=secret,
        background=secret,
    )

    _login(client, "bob_leak")
    resp = client.get("/api/literature/bootstrap")
    assert resp.status_code == 200
    assert secret not in resp.get_data(as_text=True), "bootstrap 回應洩漏了他人機密字串"


# ---------------------------------------------------------------------------
# 2. 正向：owner 仍能看到自己的正式專案
# ---------------------------------------------------------------------------

def test_owner_bootstrap_sees_own_project(session_app, client):
    alice_id = _make_user(session_app, "alice_own")
    _make_formal_project(
        session_app,
        pid="OWNP-p",
        owner_user_id=alice_id,
        title="Alice 自己的專案",
        background="alice 的研究背景",
    )

    _login(client, "alice_own")
    resp = client.get("/api/literature/bootstrap")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()

    assert data["active_project"] == "OWNP-p"
    pids = [p["pid"] for p in data["formal_projects"]]
    assert "OWNP-p" in pids


def test_owner_does_not_see_others_project(session_app, client):
    """alice 有 P1、bob 有 P2；alice 的 bootstrap 只見 P1，不見 P2。"""
    alice_id = _make_user(session_app, "alice_x")
    bob_id = _make_user(session_app, "bob_x")
    _make_formal_project(session_app, "AXP-p", alice_id, "A", "a-bg")
    _make_formal_project(session_app, "BXP-p", bob_id, "B", "b-bg")

    _login(client, "alice_x")
    resp = client.get("/api/literature/bootstrap")
    assert resp.status_code == 200
    pids = [p["pid"] for p in resp.get_json()["formal_projects"]]
    assert "AXP-p" in pids
    assert "BXP-p" not in pids, f"alice 看到了 bob 的專案: {pids}"


# ---------------------------------------------------------------------------
# 3. 相容性：AUTH_MODE=none 維持全回（不因過濾而破壞單機模式）
# ---------------------------------------------------------------------------

def test_none_mode_returns_all_formal(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    main_db = tmp_path / "none_boot.db"
    manu_db = tmp_path / "none_boot_manu.db"

    from app import create_app, db as _db
    from app.models import Project

    app = create_app({
        "TESTING": True,
        # AUTH_MODE 不設 → TESTING 下為 none
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        "SECRET_KEY": "test-none-boot",
    })
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        _db.session.add(Project(
            project_id="SOLOP-p", name="solo", research_title="solo",
            status="formal", context_background="single-user bg",
        ))
        _db.session.commit()

    client = app.test_client()
    resp = client.get("/api/literature/bootstrap")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    # none 模式不做 membership 過濾 → 看得到該正式專案
    pids = [p["pid"] for p in data["formal_projects"]]
    assert "SOLOP-p" in pids
