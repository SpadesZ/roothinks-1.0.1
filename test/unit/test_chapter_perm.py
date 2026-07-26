# 檔案路徑: test/unit/test_chapter_perm.py
# 產生時間: 2026-07-26 03:30 +08:00
# 版本: v1.0
# 模組定位:
#   coauthor 角色、章節指派與章節留言的權限測試。
# 主要責任:
#   1. ROLE_ORDER 階層正確（viewer < coauthor < editor < owner）。
#   2. can_write_section：coauthor 只能寫被指派章節；editor 以上寫全部。
#   3. 章節指派 API 的權限與驗證（含「未加入專案者不可被指派」）。
#   4. 章節留言 API：viewer 也能留言；改／刪限作者本人或 editor 以上。
#   5. my-permissions 回傳的逐章可寫狀態。
#   6. 非成員一律 403，不透露專案是否存在。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   AUTH_MODE=session，CSRF 關閉；每個測試獨立的 in-memory 資料庫。
# 安全邊界:
#   - 本檔重點在越權路徑：coauthor 寫未指派章節、非成員存取、他人留言竄改。
# 維護提醒:
#   - 前端的唯讀鎖定只是體驗優化，真正把關在 can_write_section；
#     若有人改動 security.can_write_section 的語意，本檔會紅。
# 驗證方式:
#   python -m pytest test/unit/test_chapter_perm.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "CHPTST"
FORMAL_PID = "CHPTST-p"


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db

    application = create_app(
        {
            "TESTING": True,
            "AUTH_MODE": "session",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'ch.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'ch_manu.db'}"},
            "SERVER_NAME": None,
        }
    )
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=FORMAL_PID, name="Chapter Test", status="formal"))
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


@pytest.fixture()
def make_user(app):
    def _make(name, role=None):
        from app import db
        from app.models import User, WorkspaceMember

        with app.app_context():
            user = User(username=name, email=f"{name}@test.local")
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()
            uid = user.id
            if role:
                db.session.add(WorkspaceMember(user_id=uid, pid=FORMAL_PID, role=role))
                db.session.commit()
        return uid

    return _make


def _login(client, name):
    resp = client.post(
        "/api/auth/login",
        json={"email": f"{name}@test.local", "password": "password123"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _assign(app, user_id, section):
    from app import db
    from app.models import ChapterAssignment

    with app.app_context():
        db.session.add(
            ChapterAssignment(pid=FORMAL_PID, section_key=section, user_id=user_id)
        )
        db.session.commit()


# ---------------------------------------------------------------------------
# 角色階層
# ---------------------------------------------------------------------------


class TestRoleOrder:
    def test_coauthor_sits_between_viewer_and_editor(self):
        from app.models import ROLE_ORDER, ROLE_VIEWER, ROLE_COAUTHOR, ROLE_EDITOR, ROLE_OWNER

        assert (
            ROLE_ORDER[ROLE_VIEWER]
            < ROLE_ORDER[ROLE_COAUTHOR]
            < ROLE_ORDER[ROLE_EDITOR]
            < ROLE_ORDER[ROLE_OWNER]
        )

    def test_member_api_accepts_coauthor_role(self):
        from app.project_portfolio.member_routes import _VALID_ROLES
        assert "coauthor" in _VALID_ROLES


# ---------------------------------------------------------------------------
# can_write_section 核心判定
# ---------------------------------------------------------------------------


class TestCanWriteSection:
    def test_viewer_cannot_write_any_section(self, app, make_user):
        uid = make_user("v1", "viewer")
        from app.security import can_write_section

        with app.app_context():
            assert can_write_section(uid, FORMAL_PID, "introduction") is False

    def test_editor_writes_all_sections_without_assignment(self, app, make_user):
        uid = make_user("e1", "editor")
        from app.security import can_write_section

        with app.app_context():
            assert can_write_section(uid, FORMAL_PID, "introduction") is True
            assert can_write_section(uid, FORMAL_PID, "method") is True

    def test_owner_writes_all_sections(self, app, make_user):
        uid = make_user("o1", "owner")
        from app.security import can_write_section

        with app.app_context():
            assert can_write_section(uid, FORMAL_PID, "discussion") is True

    def test_coauthor_without_assignment_cannot_write(self, app, make_user):
        uid = make_user("c1", "coauthor")
        from app.security import can_write_section

        with app.app_context():
            assert can_write_section(uid, FORMAL_PID, "introduction") is False

    def test_coauthor_writes_only_assigned_section(self, app, make_user):
        uid = make_user("c2", "coauthor")
        _assign(app, uid, "introduction")
        from app.security import can_write_section

        with app.app_context():
            assert can_write_section(uid, FORMAL_PID, "introduction") is True
            assert can_write_section(uid, FORMAL_PID, "method") is False

    def test_non_member_cannot_write(self, app, make_user):
        uid = make_user("stranger")  # 無 membership
        from app.security import can_write_section

        with app.app_context():
            assert can_write_section(uid, FORMAL_PID, "introduction") is False

    def test_assignment_in_other_project_does_not_leak(self, app, make_user):
        """在別的專案被指派同名章節，不得因此取得本專案的寫入權。"""
        uid = make_user("c3", "coauthor")
        from app import db
        from app.models import ChapterAssignment
        from app.security import can_write_section

        with app.app_context():
            db.session.add(
                ChapterAssignment(pid="OTHER-p", section_key="introduction", user_id=uid)
            )
            db.session.commit()
            assert can_write_section(uid, FORMAL_PID, "introduction") is False

    def test_comment_threshold_is_viewer(self, app, make_user):
        from app.security import can_comment

        vid = make_user("v2", "viewer")
        sid = make_user("s2")
        with app.app_context():
            assert can_comment(vid, FORMAL_PID) is True
            assert can_comment(sid, FORMAL_PID) is False

    def test_assign_threshold_is_editor(self, app, make_user):
        from app.security import can_assign_sections

        cid = make_user("c4", "coauthor")
        eid = make_user("e4", "editor")
        oid = make_user("o4", "owner")
        with app.app_context():
            assert can_assign_sections(cid, FORMAL_PID) is False
            assert can_assign_sections(eid, FORMAL_PID) is True
            assert can_assign_sections(oid, FORMAL_PID) is True


# ---------------------------------------------------------------------------
# 章節指派 API
# ---------------------------------------------------------------------------


class TestAssignmentAPI:
    def test_editor_can_assign(self, app, make_user):
        make_user("boss", "editor")
        make_user("worker", "coauthor")
        client = app.test_client()
        _login(client, "boss")

        resp = client.post(
            f"/manuscript/api/chapter/{FORMAL_PID}/assignments",
            json={"section_key": "introduction", "email": "worker@test.local"},
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json()["assignment"]["section_key"] == "introduction"

    def test_coauthor_cannot_assign(self, app, make_user):
        make_user("c5", "coauthor")
        make_user("c6", "coauthor")
        client = app.test_client()
        _login(client, "c5")

        resp = client.post(
            f"/manuscript/api/chapter/{FORMAL_PID}/assignments",
            json={"section_key": "introduction", "email": "c6@test.local"},
        )
        assert resp.status_code == 403

    def test_cannot_assign_non_member(self, app, make_user):
        """指派給非專案成員應被擋——他根本讀不到這個專案。"""
        make_user("boss2", "editor")
        make_user("outsider")
        client = app.test_client()
        _login(client, "boss2")

        resp = client.post(
            f"/manuscript/api/chapter/{FORMAL_PID}/assignments",
            json={"section_key": "introduction", "email": "outsider@test.local"},
        )
        assert resp.status_code == 400
        assert "尚未加入" in resp.get_json()["message"]

    def test_assign_unknown_user_404(self, app, make_user):
        make_user("boss3", "editor")
        client = app.test_client()
        _login(client, "boss3")

        resp = client.post(
            f"/manuscript/api/chapter/{FORMAL_PID}/assignments",
            json={"section_key": "introduction", "email": "ghost@test.local"},
        )
        assert resp.status_code == 404

    def test_duplicate_assignment_is_idempotent(self, app, make_user):
        make_user("boss4", "editor")
        make_user("w4", "coauthor")
        client = app.test_client()
        _login(client, "boss4")

        payload = {"section_key": "method", "email": "w4@test.local"}
        first = client.post(f"/manuscript/api/chapter/{FORMAL_PID}/assignments", json=payload)
        second = client.post(f"/manuscript/api/chapter/{FORMAL_PID}/assignments", json=payload)
        assert first.status_code == 201
        assert second.status_code == 200

        listing = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/assignments")
        assert len(listing.get_json()["assignments"]) == 1

    def test_delete_assignment(self, app, make_user):
        make_user("boss5", "editor")
        wid = make_user("w5", "coauthor")
        _assign(app, wid, "results")
        client = app.test_client()
        _login(client, "boss5")

        listing = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/assignments")
        aid = listing.get_json()["assignments"][0]["id"]

        resp = client.delete(f"/manuscript/api/chapter/{FORMAL_PID}/assignments/{aid}")
        assert resp.status_code == 200
        assert client.get(
            f"/manuscript/api/chapter/{FORMAL_PID}/assignments"
        ).get_json()["assignments"] == []

    def test_non_member_cannot_list_assignments(self, app, make_user):
        make_user("nm", None)
        client = app.test_client()
        _login(client, "nm")

        resp = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/assignments")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 章節留言 API
# ---------------------------------------------------------------------------


class TestCommentAPI:
    def _post_comment(self, client, section="introduction", body="looks good"):
        return client.post(
            f"/manuscript/api/chapter/{FORMAL_PID}/comments",
            json={"section_key": section, "body": body},
        )

    def test_viewer_can_comment(self, app, make_user):
        make_user("cv", "viewer")
        client = app.test_client()
        _login(client, "cv")

        assert self._post_comment(client).status_code == 201

    def test_coauthor_can_comment_on_unassigned_section(self, app, make_user):
        """需求重點：未被指派的章節仍可留言。"""
        uid = make_user("cc", "coauthor")
        _assign(app, uid, "introduction")
        client = app.test_client()
        _login(client, "cc")

        resp = self._post_comment(client, section="method", body="建議補上統計方法")
        assert resp.status_code == 201
        assert resp.get_json()["comment"]["section_key"] == "method"

    def test_non_member_cannot_comment(self, app, make_user):
        make_user("nm2")
        client = app.test_client()
        _login(client, "nm2")
        assert self._post_comment(client).status_code == 403

    def test_empty_body_rejected(self, app, make_user):
        make_user("cv2", "viewer")
        client = app.test_client()
        _login(client, "cv2")
        assert self._post_comment(client, body="   ").status_code == 400

    def test_overlong_body_rejected(self, app, make_user):
        make_user("cv3", "viewer")
        client = app.test_client()
        _login(client, "cv3")
        assert self._post_comment(client, body="x" * 4001).status_code == 400

    def test_list_comments_filtered_by_section(self, app, make_user):
        make_user("cv4", "viewer")
        client = app.test_client()
        _login(client, "cv4")
        self._post_comment(client, section="introduction", body="a")
        self._post_comment(client, section="method", body="b")

        resp = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/comments?section=method")
        comments = resp.get_json()["comments"]
        assert len(comments) == 1
        assert comments[0]["body"] == "b"

    def test_author_can_edit_own_comment(self, app, make_user):
        make_user("ca", "viewer")
        client = app.test_client()
        _login(client, "ca")
        cid = self._post_comment(client).get_json()["comment"]["id"]

        resp = client.patch(
            f"/manuscript/api/chapter/{FORMAL_PID}/comments/{cid}",
            json={"body": "updated"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["comment"]["body"] == "updated"

    def test_editor_cannot_rewrite_others_comment(self, app, make_user):
        """editor 可以刪、可以標記已解決，但不得竄改他人發言內容。"""
        make_user("author1", "viewer")
        make_user("ed1", "editor")

        author_client = app.test_client()
        _login(author_client, "author1")
        cid = self._post_comment(author_client).get_json()["comment"]["id"]

        ed_client = app.test_client()
        _login(ed_client, "ed1")
        resp = ed_client.patch(
            f"/manuscript/api/chapter/{FORMAL_PID}/comments/{cid}",
            json={"body": "tampered"},
        )
        assert resp.status_code == 403

    def test_editor_can_resolve_others_comment(self, app, make_user):
        make_user("author2", "viewer")
        make_user("ed2", "editor")

        author_client = app.test_client()
        _login(author_client, "author2")
        cid = self._post_comment(author_client).get_json()["comment"]["id"]

        ed_client = app.test_client()
        _login(ed_client, "ed2")
        resp = ed_client.patch(
            f"/manuscript/api/chapter/{FORMAL_PID}/comments/{cid}",
            json={"resolved": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["comment"]["resolved"] is True

    def test_viewer_cannot_delete_others_comment(self, app, make_user):
        make_user("author3", "viewer")
        make_user("other3", "viewer")

        a = app.test_client()
        _login(a, "author3")
        cid = self._post_comment(a).get_json()["comment"]["id"]

        b = app.test_client()
        _login(b, "other3")
        assert b.delete(
            f"/manuscript/api/chapter/{FORMAL_PID}/comments/{cid}"
        ).status_code == 403

    def test_editor_can_delete_others_comment(self, app, make_user):
        make_user("author4", "viewer")
        make_user("ed4", "editor")

        a = app.test_client()
        _login(a, "author4")
        cid = self._post_comment(a).get_json()["comment"]["id"]

        b = app.test_client()
        _login(b, "ed4")
        assert b.delete(
            f"/manuscript/api/chapter/{FORMAL_PID}/comments/{cid}"
        ).status_code == 200


# ---------------------------------------------------------------------------
# my-permissions
# ---------------------------------------------------------------------------


class TestMyPermissions:
    def test_coauthor_sees_only_assigned_section_writable(self, app, make_user):
        uid = make_user("cp", "coauthor")
        _assign(app, uid, "introduction")
        client = app.test_client()
        _login(client, "cp")

        data = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/my-permissions").get_json()
        assert data["role"] == "coauthor"
        assert data["can_assign"] is False
        assert data["can_comment"] is True
        assert data["sections"]["introduction"] is True
        assert data["sections"]["method"] is False

    def test_editor_sees_all_sections_writable(self, app, make_user):
        make_user("ep", "editor")
        client = app.test_client()
        _login(client, "ep")

        data = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/my-permissions").get_json()
        assert data["can_assign"] is True
        assert all(data["sections"].values())

    def test_viewer_sees_nothing_writable(self, app, make_user):
        make_user("vp", "viewer")
        client = app.test_client()
        _login(client, "vp")

        data = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/my-permissions").get_json()
        assert not any(data["sections"].values())
        assert data["can_comment"] is True

    def test_non_member_403(self, app, make_user):
        make_user("np")
        client = app.test_client()
        _login(client, "np")

        resp = client.get(f"/manuscript/api/chapter/{FORMAL_PID}/my-permissions")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Socket 層強制力 —— 真正的把關點
# ---------------------------------------------------------------------------


class TestSocketChapterGate:
    """
    前端的唯讀鎖定只是體驗優化；即使有人直接送 socket 事件，
    伺服器仍必須擋下未授權章節的寫入。這裡就是驗這件事。
    """

    def _sio(self, app, name, tmp_path, monkeypatch):
        from app import socketio

        client = app.test_client()
        _login(client, name)
        sio = socketio.test_client(app, flask_test_client=client, namespace="/manu_ws")
        assert sio.is_connected("/manu_ws"), "socket 連線被拒"
        sio.get_received("/manu_ws")
        return sio

    def _save(self, sio, section, content="hacked"):
        sio.emit(
            "cmd_save_block",
            {
                "pid": FORMAL_PID,
                "title": "T",
                "section": section,
                "content": content,
            },
            namespace="/manu_ws",
        )
        return sio.get_received("/manu_ws")

    @pytest.fixture(autouse=True)
    def _data_root(self, tmp_path, monkeypatch):
        root = tmp_path / "sockdata"
        root.mkdir()
        from app.core_pro.manuscript import manuscript_io as mio
        from app.core_pro.manuscript import manuscript_routes as mroutes
        monkeypatch.setattr(mio, "_get_data_root", lambda: str(root))
        monkeypatch.setattr(mroutes, "_get_data_root", lambda: str(root))
        return root

    def test_coauthor_can_save_assigned_section(self, app, make_user, tmp_path, monkeypatch):
        uid = make_user("sc1", "coauthor")
        _assign(app, uid, "introduction")
        sio = self._sio(app, "sc1", tmp_path, monkeypatch)

        events = self._save(sio, "introduction", "legit work")
        names = [e["name"] for e in events]
        assert "save_ack" in names, names

    def test_coauthor_blocked_on_unassigned_section(self, app, make_user, tmp_path, monkeypatch):
        uid = make_user("sc2", "coauthor")
        _assign(app, uid, "introduction")
        sio = self._sio(app, "sc2", tmp_path, monkeypatch)

        events = self._save(sio, "method")
        names = [e["name"] for e in events]
        assert "save_ack" not in names, "未指派章節竟然存檔成功"
        assert "sys_msg" in names
        assert "權限不足" in events[0]["args"][0]["msg"]

    def test_viewer_blocked_on_every_section(self, app, make_user, tmp_path, monkeypatch):
        make_user("sv1", "viewer")
        sio = self._sio(app, "sv1", tmp_path, monkeypatch)

        for section in ("introduction", "method", "results"):
            names = [e["name"] for e in self._save(sio, section)]
            assert "save_ack" not in names, f"viewer 竟然寫得進 {section}"

    def test_editor_can_save_any_section(self, app, make_user, tmp_path, monkeypatch):
        make_user("se1", "editor")
        sio = self._sio(app, "se1", tmp_path, monkeypatch)

        for section in ("introduction", "method"):
            names = [e["name"] for e in self._save(sio, section, "editor work")]
            assert "save_ack" in names

    def test_coauthor_blocked_from_paper_assembly(self, app, make_user, tmp_path, monkeypatch):
        """coauthor 被指派了某章，仍不得總裝 2C 全篇。"""
        uid = make_user("sc3", "coauthor")
        _assign(app, uid, "introduction")
        sio = self._sio(app, "sc3", tmp_path, monkeypatch)

        sio.emit(
            "cmd_save_paper",
            {"pid": FORMAL_PID, "title": "T", "content": "<p>whole paper</p>"},
            namespace="/manu_ws",
        )
        events = sio.get_received("/manu_ws")
        names = [e["name"] for e in events]
        assert "save_ack" not in names
        assert "sys_msg" in names

    def test_coauthor_autosave_blocked_on_unassigned_section(
        self, app, make_user, tmp_path, monkeypatch
    ):
        """自動存檔也要過同一道 gate，否則草稿會成為繞過管道。"""
        uid = make_user("sc4", "coauthor")
        _assign(app, uid, "introduction")
        sio = self._sio(app, "sc4", tmp_path, monkeypatch)

        sio.emit(
            "cmd_autosave_block",
            {"pid": FORMAL_PID, "title": "T", "section": "method", "content": "sneaky"},
            namespace="/manu_ws",
        )
        events = sio.get_received("/manu_ws")
        assert not any(e["name"] == "autosave_ack" for e in events)

        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        with app.app_context():
            assert ManuscriptIO.load_draft(FORMAL_PID, "method") is None
