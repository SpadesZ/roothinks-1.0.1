# 檔案路徑: test/unit/test_mentor_scope.py
# 產生時間: 2026-07-26 04:40 +08:00
# 版本: v1.1；更新時間: 2026-08-10 +08:00
# 模組定位:
#   Mentor 功能的權限邊界與活動統計測試。
# 主要責任:
#   1. *** 越權防線 ***：mentor 讀不到「非自己名下」的 mentee 資料。
#   2. 非 mentor 身分存取 /api/mentor/mentees* 一律 403。
#   3. mentor 同時保有一般 user 身分（不因 mentor 而失去原有專案權限）。
#   4. 以 email 加入 mentee 的流程與錯誤處理。
#   5. UserSession：登入建立、heartbeat 累計、登出結算、逾時自動結算。
#   6. 派工與評論的權限：mentee 只能改狀態，不能竄改工作內容。
#   7. MentorLink 不等於 2C ACL；雙方共同 workspace、section scope 與 admin bypass。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   AUTH_MODE=session，CSRF 關閉；每個測試獨立的臨時資料庫。
# 安全邊界:
#   本檔的核心價值在越權測試。mentor API 是全系統唯一繞過 WorkspaceMember
#   讀取他人資料的路徑，commit 36fc14b 才修過同類的跨租戶越權讀取，
#   任何對 _require_mentee 的改動都必須讓本檔維持全綠。
# 維護提醒:
#   - 「不是我的 mentee」與「查無此人」都回 403，避免用列舉試出他人歸屬；
#     因此測試斷言的是 403 而非 404。
# 驗證方式:
#   python -m pytest test/unit/test_mentor_scope.py -q
# ------------------------------------------------------------------------------
import sys
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


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
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'mentor.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'mentor_manu.db'}"},
            "SERVER_NAME": None,
        }
    )
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


@pytest.fixture()
def make_user(app):
    def _make(name, system_role="user"):
        from app import db
        from app.models import User

        with app.app_context():
            user = User(
                username=name, email=f"{name}@test.local", system_role=system_role
            )
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()
            return user.id

    return _make


def _login(client, name):
    resp = client.post(
        "/api/auth/login",
        json={"email": f"{name}@test.local", "password": "password123"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _link(app, mentor_id, mentee_id):
    from app import db
    from app.models import MentorLink

    with app.app_context():
        db.session.add(MentorLink(mentor_id=mentor_id, mentee_id=mentee_id))
        db.session.commit()


def _grant_shared_project(app, mentor_id, mentee_id, pid="SHARED-p"):
    from app import db
    from app.models import Project, WorkspaceMember

    with app.app_context():
        if Project.query.filter_by(project_id=pid).first() is None:
            db.session.add(Project(project_id=pid, name="Shared Review", status="formal"))
        db.session.add_all([
            WorkspaceMember(user_id=mentor_id, pid=pid, role="viewer"),
            WorkspaceMember(user_id=mentee_id, pid=pid, role="editor"),
        ])
        db.session.commit()


# ---------------------------------------------------------------------------
# 越權防線
# ---------------------------------------------------------------------------


class TestMentorScopeIsolation:
    def test_mentor_cannot_read_other_mentors_mentee(self, app, make_user):
        """核心越權測試：A 的 mentee 不能被 B 讀到。"""
        mentor_a = make_user("mentora", "mentor")
        mentor_b = make_user("mentorb", "mentor")
        mentee = make_user("student1")
        _link(app, mentor_a, mentee)

        client = app.test_client()
        _login(client, "mentorb")

        assert client.get(f"/api/mentor/mentees/{mentee}").status_code == 403
        assert client.get(f"/api/mentor/mentees/{mentee}/tasks").status_code == 403
        assert client.get(f"/api/mentor/mentees/{mentee}/comments").status_code == 403

    def test_mentor_cannot_write_to_other_mentors_mentee(self, app, make_user):
        mentor_a = make_user("ma2", "mentor")
        make_user("mb2", "mentor")
        mentee = make_user("student2")
        _link(app, mentor_a, mentee)

        client = app.test_client()
        _login(client, "mb2")

        assert client.post(
            f"/api/mentor/mentees/{mentee}/tasks", json={"title": "sneaky"}
        ).status_code == 403
        assert client.post(
            f"/api/mentor/mentees/{mentee}/comments", json={"body": "sneaky"}
        ).status_code == 403
        assert client.delete(f"/api/mentor/mentees/{mentee}").status_code == 403

    def test_non_mentor_gets_403_everywhere(self, app, make_user):
        """一般使用者不得存取任何 mentor 端點。"""
        make_user("plainuser")
        mentee = make_user("student3")

        client = app.test_client()
        _login(client, "plainuser")

        assert client.get("/api/mentor/mentees").status_code == 403
        assert client.get(f"/api/mentor/mentees/{mentee}").status_code == 403
        assert client.post(
            "/api/mentor/mentees", json={"email": "student3@test.local"}
        ).status_code == 403

    def test_unknown_mentee_id_returns_403_not_404(self, app, make_user):
        """不透露 id 是否存在，避免用列舉試出他人歸屬。"""
        make_user("ma3", "mentor")
        client = app.test_client()
        _login(client, "ma3")
        assert client.get("/api/mentor/mentees/999999").status_code == 403

    def test_mentee_list_only_contains_own(self, app, make_user):
        mentor_a = make_user("ma4", "mentor")
        mentor_b = make_user("mb4", "mentor")
        s1 = make_user("s41")
        s2 = make_user("s42")
        _link(app, mentor_a, s1)
        _link(app, mentor_b, s2)

        client = app.test_client()
        _login(client, "ma4")
        data = client.get("/api/mentor/mentees").get_json()
        ids = [m["mentee_id"] for m in data["mentees"]]
        assert ids == [s1]

    def test_unauthenticated_gets_401(self, app):
        client = app.test_client()
        assert client.get("/api/mentor/mentees").status_code == 401


# ---------------------------------------------------------------------------
# mentor 兼具 user 身分
# ---------------------------------------------------------------------------


class TestMentorAlsoUser:
    def test_mentor_flag(self, app, make_user):
        from app.models import User

        make_user("m5", "mentor")
        make_user("u5", "user")
        with app.app_context():
            assert User.find_by_email("m5@test.local").is_mentor is True
            assert User.find_by_email("u5@test.local").is_mentor is False

    def test_mentor_keeps_normal_project_access(self, app, make_user):
        """mentor 身分是附加的，不影響其原有的專案層權限。"""
        from app import db
        from app.models import WorkspaceMember
        from app.security import get_workspace_role

        uid = make_user("m6", "mentor")
        with app.app_context():
            db.session.add(WorkspaceMember(user_id=uid, pid="PJ6-p", role="editor"))
            db.session.commit()
            assert get_workspace_role(uid, "PJ6-p") == "editor"

    def test_mentor_can_see_own_assigned_tasks(self, app, make_user):
        """my-tasks 是給任何登入者看自己收到的工作，不需 mentor 身分。"""
        mentor_id = make_user("m7", "mentor")
        mentee_id = make_user("s7")
        _link(app, mentor_id, mentee_id)

        mc = app.test_client()
        _login(mc, "m7")
        mc.post(f"/api/mentor/mentees/{mentee_id}/tasks", json={"title": "讀完第三章"})

        sc = app.test_client()
        _login(sc, "s7")
        data = sc.get("/api/mentor/my-tasks").get_json()
        assert [t["title"] for t in data["tasks"]] == ["讀完第三章"]


# ---------------------------------------------------------------------------
# 加入 mentee
# ---------------------------------------------------------------------------


class TestAddMentee:
    def test_add_by_email(self, app, make_user):
        mentor_id = make_user("m8", "mentor")
        mentee_id = make_user("s8")
        _grant_shared_project(app, mentor_id, mentee_id, "ADD8-p")
        client = app.test_client()
        _login(client, "m8")

        resp = client.post("/api/mentor/mentees", json={"email": "s8@test.local"})
        assert resp.status_code == 201
        assert resp.get_json()["mentee"]["mentee_email"] == "s8@test.local"

    def test_add_requires_shared_full_2c_project(self, app, make_user):
        make_user("m8denied", "mentor")
        make_user("s8denied")
        client = app.test_client()
        _login(client, "m8denied")

        resp = client.post(
            "/api/mentor/mentees", json={"email": "s8denied@test.local"}
        )
        assert resp.status_code == 403
        assert client.get("/api/mentor/mentees").get_json()["mentees"] == []

    def test_admin_can_add_without_shared_project(self, app, make_user):
        make_user("admin8", "admin")
        make_user("sadmin8")
        client = app.test_client()
        _login(client, "admin8")

        resp = client.post(
            "/api/mentor/mentees", json={"email": "sadmin8@test.local"}
        )
        assert resp.status_code == 201

    def test_add_unknown_email_404(self, app, make_user):
        make_user("m9", "mentor")
        client = app.test_client()
        _login(client, "m9")
        assert client.post(
            "/api/mentor/mentees", json={"email": "ghost@test.local"}
        ).status_code == 404

    def test_cannot_add_self(self, app, make_user):
        make_user("m10", "mentor")
        client = app.test_client()
        _login(client, "m10")
        resp = client.post("/api/mentor/mentees", json={"email": "m10@test.local"})
        assert resp.status_code == 400

    def test_duplicate_add_is_idempotent(self, app, make_user):
        mentor_id = make_user("m11", "mentor")
        mentee_id = make_user("s11")
        _grant_shared_project(app, mentor_id, mentee_id, "ADD11-p")
        client = app.test_client()
        _login(client, "m11")

        first = client.post("/api/mentor/mentees", json={"email": "s11@test.local"})
        second = client.post("/api/mentor/mentees", json={"email": "s11@test.local"})
        assert first.status_code == 201
        assert second.status_code == 200
        assert len(client.get("/api/mentor/mentees").get_json()["mentees"]) == 1


# ---------------------------------------------------------------------------
# 活動統計
# ---------------------------------------------------------------------------


class TestActivityTracking:
    def test_login_creates_session(self, app, make_user):
        uid = make_user("act1")
        client = app.test_client()
        _login(client, "act1")

        from app.models import UserSession
        with app.app_context():
            assert UserSession.query.filter_by(user_id=uid).count() == 1

    def test_heartbeat_accumulates_duration(self, app, make_user):
        uid = make_user("act2")
        client = app.test_client()
        _login(client, "act2")

        from app import db
        from app.models import UserSession

        # 把登入時間往回撥 120 秒，模擬使用者已停留兩分鐘。
        with app.app_context():
            row = UserSession.query.filter_by(user_id=uid).first()
            row.login_at = datetime.now(timezone.utc) - timedelta(seconds=120)
            db.session.commit()

        resp = client.post("/api/heartbeat")
        assert resp.status_code == 200
        assert resp.get_json()["duration_sec"] >= 119

    def test_heartbeat_requires_login(self, app):
        client = app.test_client()
        assert client.post("/api/heartbeat").status_code == 401

    def test_logout_closes_session(self, app, make_user):
        uid = make_user("act3")
        client = app.test_client()
        _login(client, "act3")
        client.post("/api/auth/logout")

        from app.models import UserSession
        with app.app_context():
            row = UserSession.query.filter_by(user_id=uid).first()
            assert row.ended_at is not None

    def test_stale_session_is_closed_on_stats_read(self, app, make_user):
        """沒有 heartbeat 超過閒置上限的 session，讀統計時應被結算。"""
        mentor_id = make_user("m12", "mentor")
        mentee_id = make_user("s12")
        _link(app, mentor_id, mentee_id)

        from app import db
        from app.models import UserSession

        with app.app_context():
            stale = UserSession(user_id=mentee_id)
            stale.login_at = datetime.now(timezone.utc) - timedelta(hours=3)
            stale.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=2)
            db.session.add(stale)
            db.session.commit()

        client = app.test_client()
        _login(client, "m12")
        data = client.get(f"/api/mentor/mentees/{mentee_id}").get_json()

        assert data["stats"]["online_now"] is False
        # 停留時間以最後一次心跳為準（約 1 小時），不把閒置的 2 小時算進去。
        assert 3500 <= data["stats"]["total_seconds"] <= 3700

    def test_stats_counts_logins(self, app, make_user):
        mentor_id = make_user("m13", "mentor")
        mentee_id = make_user("s13")
        _link(app, mentor_id, mentee_id)

        for _ in range(3):
            c = app.test_client()
            _login(c, "s13")
            c.post("/api/auth/logout")

        client = app.test_client()
        _login(client, "m13")
        data = client.get(f"/api/mentor/mentees/{mentee_id}").get_json()
        assert data["stats"]["login_count"] == 3


# ---------------------------------------------------------------------------
# 派工與評論
# ---------------------------------------------------------------------------


class TestTasksAndComments:
    def _setup(self, app, make_user, suffix):
        mentor_id = make_user(f"tm{suffix}", "mentor")
        mentee_id = make_user(f"ts{suffix}")
        _link(app, mentor_id, mentee_id)
        mc = app.test_client()
        _login(mc, f"tm{suffix}")
        return mc, mentee_id

    def test_create_and_list_task(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "1")
        resp = mc.post(
            f"/api/mentor/mentees/{mentee_id}/tasks",
            json={"title": "完成 Method 初稿", "due_date": "2026-08-01"},
        )
        assert resp.status_code == 201
        tasks = mc.get(f"/api/mentor/mentees/{mentee_id}/tasks").get_json()["tasks"]
        assert tasks[0]["title"] == "完成 Method 初稿"
        assert tasks[0]["status"] == "open"

    def test_empty_title_rejected(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "2")
        assert mc.post(
            f"/api/mentor/mentees/{mentee_id}/tasks", json={"title": "  "}
        ).status_code == 400

    def test_invalid_status_rejected(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "3")
        tid = mc.post(
            f"/api/mentor/mentees/{mentee_id}/tasks", json={"title": "x"}
        ).get_json()["task"]["id"]
        assert mc.patch(
            f"/api/mentor/tasks/{tid}", json={"status": "bogus"}
        ).status_code == 400

    def test_mentee_can_update_status_only(self, app, make_user):
        """被指派者可回報進度，但不得竄改工作內容。"""
        mc, mentee_id = self._setup(app, make_user, "4")
        tid = mc.post(
            f"/api/mentor/mentees/{mentee_id}/tasks", json={"title": "原始標題"}
        ).get_json()["task"]["id"]

        sc = app.test_client()
        _login(sc, "ts4")

        ok = sc.patch(f"/api/mentor/tasks/{tid}", json={"status": "done"})
        assert ok.status_code == 200
        assert ok.get_json()["task"]["status"] == "done"

        attempt = sc.patch(f"/api/mentor/tasks/{tid}", json={"title": "被改掉的標題"})
        assert attempt.status_code == 200
        assert attempt.get_json()["task"]["title"] == "原始標題"

    def test_unrelated_user_cannot_touch_task(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "5")
        tid = mc.post(
            f"/api/mentor/mentees/{mentee_id}/tasks", json={"title": "x"}
        ).get_json()["task"]["id"]

        make_user("outsider5")
        oc = app.test_client()
        _login(oc, "outsider5")
        assert oc.patch(f"/api/mentor/tasks/{tid}", json={"status": "done"}).status_code == 403
        assert oc.delete(f"/api/mentor/tasks/{tid}").status_code == 403

    def test_delete_task(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "6")
        tid = mc.post(
            f"/api/mentor/mentees/{mentee_id}/tasks", json={"title": "x"}
        ).get_json()["task"]["id"]
        assert mc.delete(f"/api/mentor/tasks/{tid}").status_code == 200
        assert mc.get(f"/api/mentor/mentees/{mentee_id}/tasks").get_json()["tasks"] == []

    def test_create_comment(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "7")
        resp = mc.post(
            f"/api/mentor/mentees/{mentee_id}/comments",
            json={"body": "這一版的 Method 寫得比上次清楚"},
        )
        assert resp.status_code == 201
        comments = mc.get(f"/api/mentor/mentees/{mentee_id}/comments").get_json()["comments"]
        assert len(comments) == 1

    def test_empty_comment_rejected(self, app, make_user):
        mc, mentee_id = self._setup(app, make_user, "8")
        assert mc.post(
            f"/api/mentor/mentees/{mentee_id}/comments", json={"body": "  "}
        ).status_code == 400


# ---------------------------------------------------------------------------
# mentee 詳情內容
# ---------------------------------------------------------------------------


class TestMenteeDetail:
    def test_detail_includes_projects_and_stats(self, app, make_user):
        from app import db
        from app.models import Project, WorkspaceMember

        mentor_id = make_user("m20", "mentor")
        mentee_id = make_user("s20")
        _link(app, mentor_id, mentee_id)

        with app.app_context():
            db.session.add(Project(project_id="DETAIL-p", name="題目", status="formal"))
            db.session.add(
                WorkspaceMember(user_id=mentee_id, pid="DETAIL-p", role="editor")
            )
            db.session.commit()

        client = app.test_client()
        _login(client, "m20")
        data = client.get(f"/api/mentor/mentees/{mentee_id}").get_json()

        assert data["mentee"]["email"] == "s20@test.local"
        assert data["projects"][0]["pid"] == "DETAIL-p"
        assert data["projects"][0]["role"] == "editor"
        assert data["projects"][0]["name"] == "題目"
        assert "stats" in data and "study_notes" in data

    def test_detail_does_not_leak_password_hash(self, app, make_user):
        mentor_id = make_user("m21", "mentor")
        mentee_id = make_user("s21")
        _link(app, mentor_id, mentee_id)

        client = app.test_client()
        _login(client, "m21")
        raw = client.get(f"/api/mentor/mentees/{mentee_id}").get_data(as_text=True)
        assert "password_hash" not in raw
        assert "scrypt" not in raw


# ---------------------------------------------------------------------------
# 2C review workbench
# ---------------------------------------------------------------------------


class TestMentorReviewWorkbench:
    def _setup(self, app, make_user, suffix="review", role="editor"):
        mentor_id = make_user(f"m{suffix}", "mentor")
        mentee_id = make_user(f"s{suffix}")
        _link(app, mentor_id, mentee_id)

        from app import db
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        from app.models import Project, WorkspaceMember

        pid = f"REV{suffix.upper()}-p"
        with app.app_context():
            db.session.add(Project(project_id=pid, name="Review Paper", status="formal"))
            db.session.add_all([
                WorkspaceMember(user_id=mentor_id, pid=pid, role="viewer"),
                WorkspaceMember(user_id=mentee_id, pid=pid, role=role),
            ])
            db.session.commit()
            if role != "coauthor":
                ManuscriptIO.save_paper_version(pid, "First", "<p>old</p>", updated_by="author")
                ManuscriptIO.save_paper_version(pid, "Latest", "<p>latest 2C</p>", updated_by="author")

        client = app.test_client()
        _login(client, f"m{suffix}")
        return client, mentor_id, mentee_id, pid

    def test_latest_2c_and_feedback_items(self, app, make_user):
        client, _mentor_id, mentee_id, pid = self._setup(app, make_user, "r1")
        endpoint = f"/api/mentor/mentees/{mentee_id}/reviews/{pid}"

        review = client.get(endpoint)
        assert review.status_code == 200
        assert review.get_json()["manuscript"]["version"] == "V2"
        assert "latest 2C" in review.get_json()["manuscript"]["content"]

        assert client.post(
            endpoint + "/items",
            json={"kind": "comment", "body": "Method 需要補充樣本來源", "paper_version": "V2"},
        ).status_code == 201
        assert client.post(
            endpoint + "/items",
            json={"kind": "suggestion", "body": "建議移動圖二", "paper_version": "V2"},
        ).status_code == 201
        assert client.post(
            endpoint + "/items",
            json={"kind": "resource_url", "title": "Reporting guide", "url": "https://example.org/guide"},
        ).status_code == 201

        data = client.get(endpoint).get_json()
        assert [item["kind"] for item in data["items"]] == [
            "comment", "suggestion", "resource_url",
        ]

    def test_url_scheme_and_project_scope_are_enforced(self, app, make_user):
        client, _mentor_id, mentee_id, pid = self._setup(app, make_user, "r2")
        endpoint = f"/api/mentor/mentees/{mentee_id}/reviews/{pid}"
        assert client.post(
            endpoint + "/items",
            json={"kind": "resource_url", "url": "javascript:alert(1)"},
        ).status_code == 400

        make_user("outsidementor", "mentor")
        outsider = app.test_client()
        _login(outsider, "outsidementor")
        assert outsider.get(endpoint).status_code == 403
        assert outsider.post(
            endpoint + "/items", json={"kind": "comment", "body": "sneaky"}
        ).status_code == 403

    def test_section_scoped_mentee_does_not_expose_full_2c(self, app, make_user):
        client, _mentor_id, mentee_id, pid = self._setup(
            app, make_user, "r3", role="coauthor"
        )
        assert client.get(f"/api/mentor/mentees/{mentee_id}/reviews/{pid}").status_code == 403

    def test_mentor_link_alone_does_not_grant_2c(self, app, make_user):
        client, mentor_id, mentee_id, pid = self._setup(app, make_user, "rdeny")
        from app import db
        from app.models import WorkspaceMember

        with app.app_context():
            WorkspaceMember.query.filter_by(user_id=mentor_id, pid=pid).delete()
            db.session.commit()

        endpoint = f"/api/mentor/mentees/{mentee_id}/reviews/{pid}"
        assert client.get(endpoint).status_code == 403
        assert client.post(
            endpoint + "/items", json={"kind": "comment", "body": "sneaky"}
        ).status_code == 403

    def test_pdf_magic_size_and_download_authorization(self, app, make_user, monkeypatch):
        client, mentor_id, mentee_id, pid = self._setup(app, make_user, "r4")
        endpoint = f"/api/mentor/mentees/{mentee_id}/reviews/{pid}/items"

        fake = client.post(
            endpoint,
            data={"kind": "resource_pdf", "file": (BytesIO(b"not a pdf"), "fake.pdf")},
            content_type="multipart/form-data",
        )
        assert fake.status_code == 400

        valid = client.post(
            endpoint,
            data={
                "kind": "resource_pdf",
                "title": "Reviewer attachment",
                "paper_version": "V2",
                "file": (BytesIO(b"%PDF-1.4\n%%EOF\n"), "review.pdf"),
            },
            content_type="multipart/form-data",
        )
        assert valid.status_code == 201
        item = valid.get_json()["item"]
        assert "file_path" not in item

        download = client.get(item["download_url"])
        assert download.status_code == 200
        assert download.data.startswith(b"%PDF-")
        assert download.headers["X-Content-Type-Options"] == "nosniff"

        other_id = make_user("mreviewr4b", "mentor")
        _link(app, other_id, mentee_id)
        other = app.test_client()
        _login(other, "mreviewr4b")
        assert other.get(item["download_url"]).status_code == 403

        from app.mentor import routes as mentor_routes
        monkeypatch.setattr(mentor_routes, "_MAX_RESOURCE_PDF_BYTES", 8)
        oversized = client.post(
            endpoint,
            data={"kind": "resource_pdf", "file": (BytesIO(b"%PDF-1234"), "large.pdf")},
            content_type="multipart/form-data",
        )
        assert oversized.status_code == 413
