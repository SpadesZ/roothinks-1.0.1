# 檔案路徑: test/unit/test_comment_versioning.py
# 產生時間: 2026-08-10 14:10 +08:00
# 版本: v1.0
# 模組定位:
#   章節／全文留言的「版本綁定」測試。留言必須永遠指回它當初評論的那一版。
# 主要責任:
#   1. 2B 章節留言綁 pid + section_key + s_ver；換版本就換一組留言，不得漂移。
#   2. 2C 全文留言綁 pid + g_ver，scope=paper，不得混進章節留言清單。
#   3. 本次改動前留下的舊留言（s_ver/g_ver 皆 NULL）標為 legacy_unversioned，
#      預設不出現在任何特定版本旁，只有 include_legacy=1 才回傳。
#   4. ACL：section-scoped 角色（限定編輯）不得讀寫 2C 全文留言 —— 全文是所有
#      章節組裝的成品，讓他讀整篇等於繞過章節層讀取限制。
#   5. 留言建立後版本定位不可變：改內容不會改動 s_ver。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   AUTH_MODE=session，CSRF 關閉；每個測試獨立的 tmp_path 資料庫。
#   受測端點：GET/POST/PATCH /manuscript/api/chapter/<pid>/comments
# 安全邊界:
#   - 重點在越權與越版本兩條路徑：限定編輯讀全文留言、以及留言跨版本外洩。
# 維護提醒:
#   - 版本欄位語意見 app/models.py ChapterComment 的 NOTE(NOTE-008)，
#     篩選語意見 chapter_routes.py 的 NOTE(NOTE-010)；改動任一處本檔會紅。
#   - 舊留言一律不回填版本（NOTE-009）：回填等於偽造「這句話在說現在這版」。
# 驗證方式:
#   python -m pytest test/unit/test_comment_versioning.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

FORMAL_PID = "CMTVER-p"
COMMENTS_URL = f"/manuscript/api/chapter/{FORMAL_PID}/comments"


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
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'cv.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'cv_manu.db'}"},
            "SERVER_NAME": None,
        }
    )
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=FORMAL_PID, name="Comment Ver Test", status="formal"))
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


def _post(client, payload):
    return client.post(COMMENTS_URL, json=payload)


def _bodies(resp):
    data = resp.get_json()
    assert data["success"] is True, data
    return [c["body"] for c in data["comments"]]


# ---------------------------------------------------------------------------
# 2B 章節留言：版本隔離
# ---------------------------------------------------------------------------


class TestSectionCommentVersionIsolation:
    def test_comment_records_the_s_ver_it_was_written_against(self, app, make_user):
        make_user("owner_a", role="owner")
        client = app.test_client()
        _login(client, "owner_a")

        resp = _post(client, {"section_key": "introduction", "s_ver": "0.5", "body": "against 0.5"})
        assert resp.status_code == 201
        comment = resp.get_json()["comment"]
        assert comment["s_ver"] == "0.5"
        assert comment["scope"] == "section"
        assert comment["g_ver"] is None
        assert comment["legacy_unversioned"] is False

    def test_switching_version_does_not_drift_comments(self, app, make_user):
        """存成新版後，針對舊版寫的意見不得出現在新版旁邊。"""
        make_user("owner_b", role="owner")
        client = app.test_client()
        _login(client, "owner_b")

        _post(client, {"section_key": "introduction", "s_ver": "0.4", "body": "on v0.4"})
        _post(client, {"section_key": "introduction", "s_ver": "0.5", "body": "on v0.5"})

        assert _bodies(client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.4")) == ["on v0.4"]
        assert _bodies(client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.5")) == ["on v0.5"]
        # 沒有人對 0.6 留過言 —— 存了新版之後這裡必須是空的。
        assert _bodies(client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.6")) == []

    def test_comments_do_not_leak_across_sections(self, app, make_user):
        make_user("owner_c", role="owner")
        client = app.test_client()
        _login(client, "owner_c")

        _post(client, {"section_key": "introduction", "s_ver": "0.1", "body": "intro note"})
        _post(client, {"section_key": "method", "s_ver": "0.1", "body": "method note"})

        assert _bodies(client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.1")) == ["intro note"]
        assert _bodies(client.get(f"{COMMENTS_URL}?section=method&s_ver=0.1")) == ["method note"]

    def test_editing_body_does_not_move_the_version_anchor(self, app, make_user):
        """版本定位在建立當下寫死；改內容不得把留言搬到別版。"""
        make_user("owner_d", role="owner")
        client = app.test_client()
        _login(client, "owner_d")

        created = _post(client, {"section_key": "results", "s_ver": "0.2", "body": "first"}).get_json()["comment"]
        patched = client.patch(f"{COMMENTS_URL}/{created['id']}", json={"body": "edited"})
        assert patched.status_code == 200, patched.get_data(as_text=True)
        assert patched.get_json()["comment"]["s_ver"] == "0.2"


# ---------------------------------------------------------------------------
# 2C 全文留言
# ---------------------------------------------------------------------------


class TestPaperCommentScope:
    def test_paper_comment_binds_g_ver_and_stays_out_of_section_list(self, app, make_user):
        make_user("owner_e", role="owner")
        client = app.test_client()
        _login(client, "owner_e")

        resp = _post(client, {"scope": "paper", "g_ver": "V3", "body": "whole-paper note"})
        assert resp.status_code == 201
        comment = resp.get_json()["comment"]
        assert comment["scope"] == "paper"
        assert comment["g_ver"] == "V3"
        assert comment["s_ver"] is None

        # 章節清單不得混入全文留言。
        _post(client, {"section_key": "introduction", "s_ver": "0.1", "body": "section note"})
        assert _bodies(client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.1")) == ["section note"]
        assert _bodies(client.get(f"{COMMENTS_URL}?scope=paper&g_ver=V3")) == ["whole-paper note"]

    def test_paper_comment_does_not_drift_to_other_g_ver(self, app, make_user):
        make_user("owner_f", role="owner")
        client = app.test_client()
        _login(client, "owner_f")

        _post(client, {"scope": "paper", "g_ver": "V3", "body": "on V3"})
        assert _bodies(client.get(f"{COMMENTS_URL}?scope=paper&g_ver=V2")) == []
        assert _bodies(client.get(f"{COMMENTS_URL}?scope=paper&g_ver=V3")) == ["on V3"]


# ---------------------------------------------------------------------------
# 舊留言（沒有版本定位）
# ---------------------------------------------------------------------------


class TestLegacyUnversionedComments:
    def _seed_legacy(self, app):
        """直接寫入一筆沒有版本欄位的留言，模擬本次改動之前的既有資料。"""
        from app import db
        from app.models import ChapterComment

        with app.app_context():
            db.session.add(ChapterComment(
                pid=FORMAL_PID, section_key="introduction", scope="section",
                s_ver=None, g_ver=None, body="legacy note",
            ))
            db.session.commit()

    def test_legacy_comment_is_flagged_and_excluded_by_strict_filter(self, app, make_user):
        make_user("owner_g", role="owner")
        self._seed_legacy(app)
        client = app.test_client()
        _login(client, "owner_g")

        # 嚴格篩選：舊留言不得混進任何特定版本。
        assert _bodies(client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.5")) == []

        # 明確要求時才回傳，且標記為未標版本。
        resp = client.get(f"{COMMENTS_URL}?section=introduction&s_ver=0.5&include_legacy=1")
        data = resp.get_json()
        assert [c["body"] for c in data["comments"]] == ["legacy note"]
        assert data["comments"][0]["legacy_unversioned"] is True


# ---------------------------------------------------------------------------
# ACL：正向與負向
# ---------------------------------------------------------------------------


class TestPaperCommentAcl:
    def test_section_scoped_coauthor_cannot_read_or_write_paper_comments(self, app, make_user):
        """
        限定編輯（coauthor 且被指派了特定章節）不得碰 2C 全文留言。
        全文是所有章節組裝的成品，放行等於讓他繞過章節層的讀取限制。
        """
        from app import db
        from app.models import ChapterAssignment

        uid = make_user("scoped_coauthor", role="coauthor")
        with app.app_context():
            db.session.add(ChapterAssignment(pid=FORMAL_PID, section_key="introduction", user_id=uid))
            db.session.commit()

        client = app.test_client()
        _login(client, "scoped_coauthor")

        assert client.get(f"{COMMENTS_URL}?scope=paper&g_ver=V3").status_code == 403
        assert _post(client, {"scope": "paper", "g_ver": "V3", "body": "nope"}).status_code == 403

    def test_owner_can_read_and_write_paper_comments(self, app, make_user):
        make_user("owner_h", role="owner")
        client = app.test_client()
        _login(client, "owner_h")

        assert _post(client, {"scope": "paper", "g_ver": "V3", "body": "ok"}).status_code == 201
        assert client.get(f"{COMMENTS_URL}?scope=paper&g_ver=V3").status_code == 200

    def test_viewer_can_read_paper_comments(self, app, make_user):
        """viewer 不是 section-scoped，看得到整篇，因此也看得到全文留言。"""
        make_user("viewer_a", role="viewer")
        client = app.test_client()
        _login(client, "viewer_a")

        assert client.get(f"{COMMENTS_URL}?scope=paper&g_ver=V3").status_code == 200
