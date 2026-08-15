# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_navigation_guard.py
# 子系統定位:
#   離開手稿工作檯時的草稿保底端點（NOTE-029）的伺服器契約。
# 主要責任:
#   1. POST /manuscript/api/draft/flush 會真的寫出 _draft 檔，且**不產生版本**。
#   2. 授權與 socket 版 cmd_autosave_block 逐條一致：非成員 403、
#      coauthor 在未指派章節 403、**coauthor 在自己被指派的章節必須成功**。
#   3. 接受 sendBeacon 送出的 Blob（Content-Type 可能不是 application/json）。
# 明確不負責:
#   - 不驗瀏覽器真的在 beforeunload 呼叫了 sendBeacon。那只有真瀏覽器算數，
#     證據記在 docs/HANDOFF.md（打字後 6ms 內導頁，草稿仍落地）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的 sqlite 與 data root，不碰真實 data/。
# ACL/安全邊界:
#   這條端點**刻意不受 CSRF 保護**（sendBeacon 無法帶自訂標頭），
#   因此授權完全靠端點自己。放寬本檔任一斷言等於開一個免 CSRF 的寫入口。
#   特別注意：不得改用 enforce_project_ownership —— 它依 method 推 min_role，
#   POST 要 editor，會把 coauthor 擋在自己的章節外（見 test_coauthor_*）。
# 不變量:
#   - 「coauthor 可寫被指派章節」與「coauthor 不可寫其他章節」必須同時存在；
#     少了前者會退化成 enforce_project_ownership，少了後者等於沒有章節權限。
# 相關 NOTE:
#   NOTE-029。
# 驗證:
#   python -m pytest test/unit/test_navigation_guard.py -q
# ---------------------------------------------------------------------------
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "NAVGRD-p"
ENDPOINT = "/manuscript/api/draft/flush"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'nav.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'nav_manu.db'}"},
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
            db.session.add(Project(project_id=PID, name="Nav Guard", status="formal"))
        u = User(username=name, email=f"{name}@test.local", system_role="user")
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        if role:
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
        db.session.commit()
        return u.id


def _login(app, name):
    c = app.test_client()
    r = c.post("/api/auth/login",
               json={"email": f"{name}@test.local", "password": "password123"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return c


def _beacon(client, section="introduction", content="<p>草稿哨兵 KEEP-1</p>"):
    """模擬 sendBeacon：Blob 送出，Content-Type 未必是 application/json。"""
    return client.post(
        ENDPOINT,
        data=json.dumps({"pid": PID, "title": "T", "section": section,
                         "content": content}),
        content_type="text/plain;charset=UTF-8",
    )


class TestDraftIsPreserved:
    def test_beacon_writes_a_draft(self, app, tmp_path):
        _mk_user(app, "navowner", "owner")
        client = _login(app, "navowner")

        resp = _beacon(client)

        assert resp.status_code == 200, resp.get_data(as_text=True)
        # data root = 設定的 DB 所在目錄（manuscript_io._get_data_root），
        # 這裡就是 tmp_path 本身，**不是** tmp_path/data。
        drafts = list((tmp_path / PID / "manuscript" / "block"
                       / "introduction").glob("_draft*.json"))
        assert drafts, "beacon 回 200 卻沒有草稿落地"
        assert "KEEP-1" in drafts[0].read_text(encoding="utf-8")

    def test_beacon_does_not_create_a_version(self, app, tmp_path):
        """
        與 cmd_autosave_block 同語意：草稿就地覆寫，不跳版。
        少了這條，之後有人把它接到 save_block_version 上也不會有人發現。
        """
        _mk_user(app, "navowner2", "owner")
        client = _login(app, "navowner2")

        _beacon(client)

        section_dir = tmp_path / PID / "manuscript" / "block" / "introduction"
        versions = [p.name for p in section_dir.glob("V*.json")]
        assert versions == [], f"草稿保底不得產生版本，實際產生了 {versions}"


class TestAuthorisationMatchesTheSocketPath:
    def test_non_member_is_rejected(self, app):
        _mk_user(app, "navowner3", "owner")
        _mk_user(app, "navstranger", None)   # 沒有 membership
        client = _login(app, "navstranger")

        assert _beacon(client).status_code == 403

    def test_coauthor_can_write_their_assigned_section(self, app, tmp_path):
        """
        **這條是本檔最重要的一條。**
        若有人把授權改成 enforce_project_ownership（POST → 需要 editor），
        coauthor 會在自己被指派的章節上存不了草稿，而症狀是「偶爾掉字」
        —— 幾乎不可能被回報成權限問題。
        """
        from app import db
        from app.models import ChapterAssignment

        uid = _mk_user(app, "navcoauthor", "coauthor")
        with app.app_context():
            db.session.add(ChapterAssignment(
                pid=PID, section_key="introduction", user_id=uid))
            db.session.commit()

        client = _login(app, "navcoauthor")
        resp = _beacon(client, section="introduction")

        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_coauthor_cannot_write_an_unassigned_section(self, app):
        """對照組。少了它，上一條可以靠「完全不做章節判定」通過。"""
        from app import db
        from app.models import ChapterAssignment

        uid = _mk_user(app, "navcoauthor2", "coauthor")
        with app.app_context():
            db.session.add(ChapterAssignment(
                pid=PID, section_key="introduction", user_id=uid))
            db.session.commit()

        client = _login(app, "navcoauthor2")
        resp = _beacon(client, section="discussion")

        assert resp.status_code == 403, resp.get_data(as_text=True)


class TestBeaconEndpointCannotEscapeItsSandbox:
    """
    這條端點**刻意免 CSRF**（sendBeacon 帶不了標頭），所以它的輸入面比一般
    寫入端點更值得驗。`section` 完全來自請求體。

    既有的 `_section_dir_name` + `safe_join_under` 應該擋得住路徑穿越 ——
    但「既有程式碼會處理」正是最該實測而不是假設的那種說法。
    """

    @pytest.mark.parametrize("evil", [
        "../../../../etc/passwd",
        "..\\..\\..\\windows\\system32",
        "../../OTHERPRJ-p/manuscript/block/abstract",
        "a/../../b",
    ])
    def test_section_cannot_escape_the_project_dir(self, app, tmp_path, evil):
        _mk_user(app, f"nav{abs(hash(evil)) % 9999}", "owner")
        client = _login(app, f"nav{abs(hash(evil)) % 9999}")

        resp = _beacon(client, section=evil, content="<p>ESCAPE-ME</p>")

        # 允許成功（被清洗成安全名字）或被拒；不允許的是寫到專案目錄之外。
        assert resp.status_code in (200, 400, 403), resp.get_data(as_text=True)
        escaped = [p for p in tmp_path.rglob("*.json")
                   if "ESCAPE-ME" in p.read_text(encoding="utf-8", errors="ignore")
                   and PID not in str(p)]
        assert not escaped, f"草稿寫到專案目錄之外: {escaped}"

    def test_cannot_write_into_another_project(self, app, tmp_path):
        """
        帶別人的 pid 就該被成員檢查擋下 —— 這是 CSRF 免驗之後唯一的門。
        """
        from app import db
        from app.models import Project

        _mk_user(app, "navowner9", "owner")
        with app.app_context():
            db.session.add(Project(project_id="OTHERPR-p", name="別人的", status="formal"))
            db.session.commit()

        client = _login(app, "navowner9")
        resp = client.post(
            ENDPOINT,
            data=json.dumps({"pid": "OTHERPR-p", "title": "T",
                             "section": "abstract", "content": "<p>CROSS-PID</p>"}),
            content_type="text/plain;charset=UTF-8",
        )

        assert resp.status_code == 403, resp.get_data(as_text=True)
        leaked = [p for p in tmp_path.rglob("*.json")
                  if "CROSS-PID" in p.read_text(encoding="utf-8", errors="ignore")]
        assert not leaked, f"寫進了不該碰的專案: {leaked}"


class TestUnloadGuardIsWiredInTheClient:
    """
    契約層測不到 beforeunload，但「這段程式碼有沒有被接上」還是要守 ——
    HANDOFF 記載過多次「寫了但沒有呼叫端」的形狀（IndexService、PaqCore）。
    真正的行為證據來自瀏覽器實測。
    """

    def test_guard_is_installed_and_uses_beacon(self):
        ws = (PROJECT_ROOT / "app/static/js/manuscript_ws.js").read_text(encoding="utf-8")
        assert "this.setupUnloadGuard();" in ws, "守衛沒有被 init 呼叫"
        assert "navigator.sendBeacon('/manuscript/api/draft/flush'" in ws
        assert "beforeunload" in ws

    def test_guard_does_not_use_socket_emit_for_the_final_flush(self):
        """
        unload 期間 socket 正在拆除，emit 不保證送達。
        這條防止有人「簡化」成再 emit 一次。
        """
        ws = (PROJECT_ROOT / "app/static/js/manuscript_ws.js").read_text(encoding="utf-8")
        start = ws.index("flushDraftBeacon()")
        body = ws[start:start + 1600]
        assert "socket.emit" not in body, (
            "最後一次保底不得走 socket —— unload 期間送不出去"
        )
