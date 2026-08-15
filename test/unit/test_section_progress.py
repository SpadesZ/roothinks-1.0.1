# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_section_progress.py
# 子系統定位:
#   章節完成比例（NOTE-036）的儲存契約、授權與 Dashboard 批次讀取。
# 主要責任:
#   1. **進度不得被章節設定的全量覆蓋清掉** —— save_sections 會
#      `ManuSectionConfig.query.filter_by(pid=pid).delete()`，這是本題唯一的陷阱。
#   2. 授權：viewer 寫入 403；owner/editor 可寫全部；coauthor 只可寫被指派章節。
#   3. 讀取範圍與 _filter_visible_sections 一致 —— coauthor 不得看到未指派章節。
#   4. 值一律 clamp 成 0~100 整數；看不懂的輸入回 400，**不得當成 0**。
#   5. 「沒填」回 null，不是 0。
#   6. 整體比例現階段一律 null（overall_status='not_calculated'）。
# 明確不負責:
#   - 不驗欄位在瀏覽器裡長什麼樣、也不驗切章有沒有刷新顯示（真瀏覽器的事，
#     這台機器沒有 Node，證據記在 docs/HANDOFF.md）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的 sqlite 與 data root；不碰真實 data/。
# ACL/安全邊界:
#   POST 在 /manuscript/api/ 之下會跳過 CSRF，授權完全靠端點自己。
#   **放寬 test_viewer_cannot_write 等於讓唯讀角色改別人的進度回報數字。**
# 不變量:
#   - 未知 section 一律 404，不得在表裡長出孤兒紀錄。
# 相關 NOTE:
#   NOTE-036。
# 驗證:
#   python -m pytest test/unit/test_section_progress.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "PROGRS-p"
GET = f"/manuscript/api/progress/{PID}"
POST = f"/manuscript/api/progress/{PID}"
SUMMARY = "/manuscript/api/progress_summary"


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))
    monkeypatch.chdir(tmp_path)

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'prog.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'prog_manu.db'}"},
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


def _mk_user(app, name, role):
    from app import db
    from app.models import Project, User, WorkspaceMember

    with app.app_context():
        if Project.query.filter_by(project_id=PID).first() is None:
            db.session.add(Project(project_id=PID, name="Progress", status="formal"))
        u = User(username=name, email=f"{name}@test.local", system_role="user")
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        if role:
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
        db.session.commit()
        return u.id


def _assert_role(app, uid, expected):
    """確認角色真的建立了。

    少了這道，「coauthor 被擋」與「使用者根本不是成員」都是 403，無法分辨——
    角色沒建成功也會讓測試變綠，什麼都沒證明。
    """
    from app.security import get_workspace_role
    with app.app_context():
        actual = get_workspace_role(uid, PID)
    assert actual == expected, f"角色沒建立：預期 {expected}，實際 {actual}"


def _login(app, name):
    c = app.test_client()
    r = c.post("/api/auth/login",
               json={"email": f"{name}@test.local", "password": "password123"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return c


def _sections(client):
    return {s["id"]: s for s in client.get(GET).get_json()["sections"]}


# ---------------------------------------------------------------------------
# 讀寫基本契約
# ---------------------------------------------------------------------------

class TestReadWrite:
    def test_unset_section_reports_null_not_zero(self, app):
        """「還沒填」與「填了 0%」在進度回報上是完全不同的事。"""
        _mk_user(app, "prgowner", "owner")
        client = _login(app, "prgowner")
        body = client.get(GET).get_json()
        assert body["ok"] is True
        assert body["sections"], "應該要有預設章節"
        assert all(s["progress"] is None for s in body["sections"]), (
            "未填章節必須回 None")

    def test_owner_can_set_and_read_back(self, app):
        _mk_user(app, "prgowner2", "owner")
        client = _login(app, "prgowner2")

        resp = client.post(POST, json={"section": "introduction", "progress": 40})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["progress"] == 40

        assert _sections(client)["introduction"]["progress"] == 40

    def test_zero_is_stored_as_zero_not_as_unset(self, app):
        _mk_user(app, "prgowner3", "owner")
        client = _login(app, "prgowner3")
        client.post(POST, json={"section": "method", "progress": 0})
        assert _sections(client)["method"]["progress"] == 0

    def test_update_overwrites_in_place(self, app):
        _mk_user(app, "prgowner4", "owner")
        client = _login(app, "prgowner4")
        client.post(POST, json={"section": "results", "progress": 10})
        client.post(POST, json={"section": "results", "progress": 75})
        assert _sections(client)["results"]["progress"] == 75

        from app.core_pro.manuscript.model_section import ManuSectionProgress
        with app.app_context():
            rows = ManuSectionProgress.query.filter_by(
                pid=PID, section_key="results").all()
            assert len(rows) == 1, f"就地覆寫，不得堆積：{len(rows)} 列"

    @pytest.mark.parametrize("sent,expected", [
        (150, 100), (-20, 0), ("55", 55), ("80%", 80), (33.4, 33), (33.6, 34),
    ])
    def test_values_are_clamped_server_side(self, app, sent, expected):
        """前端的 min/max 只是體驗優化；這裡才是把關。"""
        _mk_user(app, f"prgc{abs(hash(str(sent))) % 9999}", "owner")
        client = _login(app, f"prgc{abs(hash(str(sent))) % 9999}")
        resp = client.post(POST, json={"section": "discussion", "progress": sent})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["progress"] == expected

    @pytest.mark.parametrize("bad", ["", "abc", None, "  ", "N/A", True, [], {}])
    def test_unparseable_input_is_rejected_not_coerced_to_zero(self, app, bad):
        """看不懂的輸入寫成 0 會讓使用者以為自己填的被記錄了。"""
        _mk_user(app, f"prgb{abs(hash(str(bad))) % 9999}", "owner")
        client = _login(app, f"prgb{abs(hash(str(bad))) % 9999}")
        resp = client.post(POST, json={"section": "conclusion", "progress": bad})
        assert resp.status_code == 400, f"{bad!r} 應被拒絕，實際 {resp.status_code}"
        assert _sections(client)["conclusion"]["progress"] is None

    def test_unknown_section_is_404(self, app):
        _mk_user(app, "prgowner5", "owner")
        client = _login(app, "prgowner5")
        resp = client.post(POST, json={"section": "not_a_section", "progress": 50})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 本題唯一真正的陷阱
# ---------------------------------------------------------------------------

class TestProgressSurvivesSectionConfigRewrite:
    def test_saving_section_config_does_not_wipe_progress(self, app):
        """`save_sections` 會 `delete()` 整張 ManuSectionConfig 再重建。

        完成度若是那張表上的欄位，使用者每按一次「章節管理 → 儲存」
        （改名或調順序）就會把所有進度靜默歸零，畫面上還不會有任何錯誤。
        這一條就是 NOTE-036 分表的理由；它紅了代表分表被合回去了。
        """
        _mk_user(app, "prgowner6", "owner")
        client = _login(app, "prgowner6")
        client.post(POST, json={"section": "introduction", "progress": 60})
        client.post(POST, json={"section": "method", "progress": 25})

        current = client.get(f"/manuscript/api/sections/{PID}").get_json()["sections"]
        # 模擬使用者改了一個章節名稱後按儲存（全量覆蓋路徑）。
        payload = [{"id": s["id"],
                    "label": ("Methods (renamed)" if s["id"] == "method" else s["label"]),
                    "is_fixed": s["is_fixed"]} for s in current]
        resp = client.post(f"/manuscript/api/sections/{PID}", json={"sections": payload})
        assert resp.status_code == 200, resp.get_data(as_text=True)

        after = _sections(client)
        assert after["introduction"]["progress"] == 60, "章節設定重寫把進度清掉了"
        assert after["method"]["progress"] == 25, "章節設定重寫把進度清掉了"
        assert after["method"]["label"] == "Methods (renamed)"


# ---------------------------------------------------------------------------
# 授權
# ---------------------------------------------------------------------------

class TestAuthorisation:
    def test_anonymous_cannot_read_or_write(self, app):
        _mk_user(app, "prgowner7", "owner")
        anon = app.test_client()
        assert anon.get(GET).status_code in (401, 403)
        assert anon.post(POST, json={"section": "introduction",
                                     "progress": 10}).status_code in (401, 403)

    def test_non_member_is_rejected(self, app):
        _mk_user(app, "prgowner8", "owner")
        _mk_user(app, "prgstranger", None)
        client = _login(app, "prgstranger")
        assert client.get(GET).status_code == 403
        assert client.post(POST, json={"section": "introduction",
                                       "progress": 10}).status_code == 403

    def test_viewer_can_read_but_cannot_write(self, app):
        _mk_user(app, "prgowner9", "owner")
        uid = _mk_user(app, "prgviewer", "viewer")
        _assert_role(app, uid, "viewer")
        client = _login(app, "prgviewer")

        assert client.get(GET).status_code == 200, "檢視者應該看得到進度"
        resp = client.post(POST, json={"section": "introduction", "progress": 90})
        assert resp.status_code == 403, f"viewer 寫入成功了（{resp.status_code}）"
        assert _sections(client)["introduction"]["progress"] is None

    def test_editor_can_write(self, app):
        _mk_user(app, "prgowner10", "owner")
        uid = _mk_user(app, "prgeditor", "editor")
        _assert_role(app, uid, "editor")
        client = _login(app, "prgeditor")
        assert client.post(POST, json={"section": "introduction",
                                       "progress": 35}).status_code == 200

    def test_coauthor_can_write_assigned_section_only(self, app):
        """NOTE-036：被指派的人才知道那一章寫到哪裡。

        兩半都要在：只有「可寫被指派章節」會退化成 editor 門檻的相反面，
        只有「不可寫其他章節」則等於沒有章節權限。
        """
        from app import db
        from app.models import ChapterAssignment

        _mk_user(app, "prgowner11", "owner")
        uid = _mk_user(app, "prgcoauthor", "coauthor")
        _assert_role(app, uid, "coauthor")

        with app.app_context():
            db.session.add(ChapterAssignment(pid=PID, section_key="introduction",
                                             user_id=uid))
            db.session.commit()

        client = _login(app, "prgcoauthor")
        ok = client.post(POST, json={"section": "introduction", "progress": 45})
        assert ok.status_code == 200, (
            f"coauthor 在自己被指派的章節被擋了（{ok.status_code}）")

        denied = client.post(POST, json={"section": "results", "progress": 45})
        assert denied.status_code in (403, 404), (
            f"coauthor 寫了沒被指派的章節（{denied.status_code}）")


# ---------------------------------------------------------------------------
# Dashboard 批次端點
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_returns_progress_for_member_projects(self, app):
        _mk_user(app, "prgowner12", "owner")
        client = _login(app, "prgowner12")
        client.post(POST, json={"section": "introduction", "progress": 70})

        body = client.get(SUMMARY).get_json()
        assert body["ok"] is True
        assert PID in body["projects"]
        sections = {s["id"]: s["progress"] for s in body["projects"][PID]["sections"]}
        assert sections["introduction"] == 70
        assert sections["method"] is None

    def test_summary_excludes_projects_the_user_is_not_in(self, app):
        _mk_user(app, "prgowner13", "owner")
        _mk_user(app, "prgoutsider", None)
        client = _login(app, "prgoutsider")
        body = client.get(SUMMARY).get_json()
        assert PID not in (body.get("projects") or {}), "非成員拿到了別人的進度"

    def test_anonymous_summary_is_rejected(self, app):
        _mk_user(app, "prgowner14", "owner")
        assert app.test_client().get(SUMMARY).status_code in (401, 403)


# ---------------------------------------------------------------------------
# 整體比例：保留欄位，現階段不計算
# ---------------------------------------------------------------------------

class TestOverallIsDeliberatelyNotCalculated:
    def test_overall_is_null_even_when_every_section_is_filled(self, app):
        """欄位在，但不得自己編一個數字出來（NOTE-036 決策二）。

        各章不等重，有些章節根本不會寫；在權重規則定案前給一個看起來合理
        但其實錯的數字，比明白說「還沒算」更糟 —— 使用者會拿它去回報進度。
        """
        _mk_user(app, "prgowner15", "owner")
        client = _login(app, "prgowner15")
        for sec in _sections(client):
            client.post(POST, json={"section": sec, "progress": 100})

        body = client.get(GET).get_json()
        assert "overall" in body, "欄位必須保留"
        assert body["overall"] is None, f"整體比例被計算了：{body['overall']}"
        assert body["overall_status"] == "not_calculated"


class TestClampHelper:
    @pytest.mark.parametrize("raw,expected", [
        (0, 0), (100, 100), (101, 100), (-1, 0), ("42", 42), ("42%", 42),
        (" 42 ", 42), (12.5, 12), (13.5, 14), (None, None), ("", None),
        ("abc", None), (True, None), (False, None), (float("nan"), None),
        (float("inf"), None),
    ])
    def test_clamp(self, raw, expected):
        from app.core_pro.manuscript.model_section import ManuSectionProgress
        assert ManuSectionProgress.clamp(raw) == expected
