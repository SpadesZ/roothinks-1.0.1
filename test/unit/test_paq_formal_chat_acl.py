# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_paq_formal_chat_acl.py
# 子系統定位:
#   formal 專案 PAQ 2A 對話的授權契約（NOTE-027）。
# 主要責任:
#   1. `status='formal'` **不得**成為對話的門檻：owner（PI）與 editor（Co-PI）
#      在已轉正的專案上仍可送出並讀回對話。
#   2. viewer 仍然只讀不寫（POST 403 / GET 200）。
#   3. `status='readonly'`（轉正後的舊 provisional 快照）維持整體擋下 ——
#      本輪不放寬它，這條是對照組。
#   4. /api/paq/status 回傳的 access.can_edit 必須與**實際授權結果**一致；
#      不一致代表前端拿到的是第二份角色表。
# 明確不負責:
#   - 不驗前端有沒有把 #chat-input 解鎖（DOM 行為只有瀏覽器算數，
#     見 docs/HANDOFF.md 的實機驗收紀錄）。這裡只驗伺服器契約。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的 sqlite 與 data root，**不碰真實 data/**。
# ACL/安全邊界:
#   本檔守的就是授權邊界本身。放寬任何一條斷言，等於讓唯讀成員寫入專案內容。
# 不變量:
#   - 「formal 可以」與「viewer 不可以」必須同時存在。只驗前者的話，
#     把整道守衛拿掉也會全綠。
# 相關 NOTE:
#   NOTE-027（formal 只鎖編輯面；can_edit 由 enforcement 函式回答）。
# 驗證:
#   python -m pytest test/unit/test_paq_formal_chat_acl.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "PAQFRM-p"


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        # session 模式是前提：其他模式下角色守衛直接放行，ACL 斷言會假綠。
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'frm.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'frm_manu.db'}"},
        "SERVER_NAME": None,
    })
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _seed(app, status="formal"):
    from app import db
    from app.models import Project, User, WorkspaceMember

    with app.app_context():
        proj = Project.query.filter_by(project_id=PID).first()
        if proj is None:
            proj = Project(project_id=PID, name="Formal PAQ", status=status)
            db.session.add(proj)
        else:
            proj.status = status
        for name, role in [("frmpi", "owner"), ("frmcopi", "editor"),
                           ("frmviewer", "viewer")]:
            if User.query.filter_by(username=name).first() is None:
                u = User(username=name, email=f"{name}@test.local", system_role="user")
                u.set_password("password123")
                db.session.add(u)
                db.session.flush()
                db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
        db.session.commit()


def _login(app, name):
    client = app.test_client()
    resp = client.post("/api/auth/login",
                       json={"email": f"{name}@test.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _send(client, message):
    return client.post("/api/paq/run_task", json={
        "pid": PID,
        "task_id": "task_2a_chat",
        "input_data": {"user_input": message, "chat_history": []},
    })


@pytest.fixture()
def fake_chat(monkeypatch):
    from app.llm_service.matching_tasks import task2A_paqchat

    monkeypatch.setattr(
        task2A_paqchat, "execute_paq_chat",
        lambda *a, **k: (True, {"reply": "echo"}),
    )


class TestFormalDoesNotBlockChat:
    @pytest.mark.parametrize("username", ["frmpi", "frmcopi"])
    def test_editor_and_owner_can_chat_on_formal_project(self, app, fake_chat, username):
        """
        轉正是專案的正常生命週期，不是降級。
        修好之前這條在**伺服器**是通的，壞的是前端 —— 保留這個測試是為了
        釘住「後端從來沒有以 formal 為由擋下對話」，避免有人日後把它加進來。
        """
        _seed(app, status="formal")
        client = _login(app, username)

        resp = _send(client, f"{username} 在 formal 專案發話")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["data"]["reply"] == "echo"

    def test_viewer_is_still_read_only(self, app, fake_chat):
        """對照組。少了它，把 editor 門檻整個拿掉也會讓上面那條通過。"""
        _seed(app, status="formal")
        client = _login(app, "frmviewer")

        assert _send(client, "viewer 想插話").status_code == 403
        assert client.get(f"/api/paq/chat_history/{PID}").status_code == 200

    def test_readonly_snapshot_is_still_blocked(self, app, fake_chat):
        """
        第二個對照組：readonly（轉正後的舊 provisional 快照）不在本輪放寬範圍。
        少了它，「formal 可以」很容易被實作成「所有狀態都可以」。
        """
        _seed(app, status="readonly")
        client = _login(app, "frmpi")

        resp = _send(client, "owner 想改已鎖定的快照")

        assert resp.status_code == 403, resp.get_data(as_text=True)


class TestAccessDescriptionMatchesEnforcement:
    """
    NOTE(NOTE-027): can_edit 是**呈現**用的，但它一旦與實際授權不一致，
    使用者就會看到可以打字卻送不出去（或反之）。所以要比對的不是它的值，
    而是它與同一支 API 實際放行結果的**一致性**。
    """

    @pytest.mark.parametrize("username,expected_role", [
        ("frmpi", "owner"), ("frmcopi", "editor"), ("frmviewer", "viewer"),
    ])
    def test_can_edit_agrees_with_the_actual_post_result(
        self, app, fake_chat, username, expected_role
    ):
        _seed(app, status="formal")
        client = _login(app, username)

        status = client.get(f"/api/paq/status/{PID}")
        assert status.status_code == 200
        access = status.get_json()["access"]
        assert access["role"] == expected_role

        actually_allowed = _send(client, "一致性探針").status_code == 200

        assert access["can_edit"] is actually_allowed, (
            f"{username}: access.can_edit={access['can_edit']} 但實際 POST "
            f"{'成功' if actually_allowed else '被擋'} —— 前端拿到的是錯的角色資訊"
        )
