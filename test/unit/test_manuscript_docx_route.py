# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_manuscript_docx_route.py
# 子系統定位:
#   POST /manuscript/api/export/docx/<pid> 的 HTTP 契約與授權（NOTE-031、NOTE-032）。
# 主要責任:
#   1. 回應真的是 .docx：MIME、Content-Disposition、位元組開頭是 PK。
#   2. 授權：非成員 403；**coauthor（限定編輯）403** —— 2C 全篇跨所有章節，
#      不能讓他用匯出繞過章節層的讀取限制；viewer 可以匯出（唯讀但讀得到全篇）。
#   3. 失敗要說得出原因：缺圖／跨專案回 422 且帶可行動訊息，不是 500。
# 明確不負責:
#   - 不驗 OOXML 結構細節（那在 test_manuscript_docx_export.py）。
#   - 不驗前端有沒有按那顆按鈕（真瀏覽器的事）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的 sqlite 與 data root；不碰真實 data/。
# ACL/安全邊界:
#   這條路徑在 /manuscript/api/ 之下，`is_api_request_path()` 會讓它跳過 CSRF，
#   因此授權完全靠端點自己。**放寬本檔任一斷言等於開一個免 CSRF 的讀取口。**
# 不變量:
#   - coauthor 不得匯出全篇（與 _socket_can_read_paper 逐條一致）。
# 相關 NOTE:
#   NOTE-031、NOTE-032。
# 驗證:
#   python -m pytest test/unit/test_manuscript_docx_route.py -q
# ---------------------------------------------------------------------------
import io
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "DOCXRT-p"
ENDPOINT = f"/manuscript/api/export/docx/{PID}"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

HTML = ('<div class="fusion-block" data-section="introduction">'
        '<h5>Introduction<span class="fusion-src-ver">S.Ver 0.2</span></h5>'
        '<div class="fusion-body"><p>Body text.</p></div></div>')


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'docx.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'docx_manu.db'}"},
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
    """建立使用者並（選擇性地）給專案角色。email 一律小寫 —— 登入端點會正規化。"""
    from app import db
    from app.models import Project, User, WorkspaceMember

    with app.app_context():
        if Project.query.filter_by(project_id=PID).first() is None:
            db.session.add(Project(project_id=PID, name="Docx Route", status="formal"))
        u = User(username=name, email=f"{name}@test.local", system_role="user")
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        if role:
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
        db.session.commit()
        return u.id


def _assert_role(app, uid, expected):
    """確認角色真的寫進去了。

    沒有這一道，`coauthor 被擋` 的測試會與 `非成員被擋` 無法分辨 —— 兩者都是
    403，於是「角色根本沒建立」也會讓測試變綠，什麼都沒證明。
    """
    from app.security import get_workspace_role
    with app.app_context():
        actual = get_workspace_role(uid, PID)
    assert actual == expected, f"角色沒有正確建立：預期 {expected}，實際 {actual}"


def _login(app, name):
    c = app.test_client()
    r = c.post("/api/auth/login",
               json={"email": f"{name}@test.local", "password": "password123"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return c


def _export(client, html=HTML, title="My Paper"):
    return client.post(ENDPOINT, json={"title": title, "html": html})


class TestResponseIsARealDocx:
    def test_owner_gets_a_docx_package(self, app):
        _mk_user(app, "dxowner", "owner")
        resp = _export(_login(app, "dxowner"))

        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.mimetype == DOCX_MIME
        blob = resp.get_data()
        assert blob[:2] == b"PK", "回傳的不是 zip，仍是舊的假 Word 匯出"
        names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
        assert "word/document.xml" in names
        assert "[Content_Types].xml" in names

    def test_content_disposition_offers_a_docx_filename(self, app):
        _mk_user(app, "dxowner2", "owner")
        resp = _export(_login(app, "dxowner2"))
        disposition = resp.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert ".docx" in disposition
        assert ".doc\"" not in disposition, "還在送舊版 .doc"

    def test_cjk_title_uses_rfc5987_filename(self, app):
        _mk_user(app, "dxowner3", "owner")
        resp = _export(_login(app, "dxowner3"), title="中文標題")
        disposition = resp.headers.get("Content-Disposition", "")
        assert "filename*=UTF-8''" in disposition, (
            "中文標題只給 ASCII filename 會變亂碼")

    def test_chrome_is_not_in_the_downloaded_document(self, app):
        _mk_user(app, "dxowner4", "owner")
        blob = _export(_login(app, "dxowner4")).get_data()
        doc = zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")
        assert "S.Ver" not in doc
        assert "fusion-src-ver" not in doc


class TestAuthorisation:
    def test_anonymous_is_rejected(self, app):
        _mk_user(app, "dxowner5", "owner")
        resp = _export(app.test_client())
        assert resp.status_code in (401, 403), resp.status_code

    def test_non_member_is_rejected(self, app):
        _mk_user(app, "dxowner6", "owner")
        _mk_user(app, "dxstranger", None)
        resp = _export(_login(app, "dxstranger"))
        assert resp.status_code == 403

    def test_viewer_can_export(self, app):
        """匯出是讀取動作。viewer 讀得到全篇，就該匯得出來。"""
        _mk_user(app, "dxowner7", "owner")
        uid = _mk_user(app, "dxviewer", "viewer")
        _assert_role(app, uid, "viewer")
        resp = _export(_login(app, "dxviewer"))
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_coauthor_cannot_export_whole_paper(self, app):
        """限定編輯讀不到整篇，就不得用匯出繞過章節層的讀取限制。

        這一條與 `_socket_can_read_paper` 是同一個判斷。少了它，
        章節權限會被一顆匯出按鈕整個繞開。
        """
        _mk_user(app, "dxowner8", "owner")
        uid = _mk_user(app, "dxcoauthor", "coauthor")
        _assert_role(app, uid, "coauthor")
        resp = _export(_login(app, "dxcoauthor"))
        assert resp.status_code == 403, (
            f"coauthor 匯出了全篇（HTTP {resp.status_code}）")


class TestFailuresAreExplicit:
    def test_empty_html_is_rejected(self, app):
        _mk_user(app, "dxowner9", "owner")
        resp = _export(_login(app, "dxowner9"), html="   ")
        assert resp.status_code == 400
        assert "沒有可匯出的內容" in resp.get_json()["message"]

    def test_external_image_is_rejected_with_reason(self, app):
        _mk_user(app, "dxowner10", "owner")
        resp = _export(_login(app, "dxowner10"),
                       html='<p><img src="https://evil.example.com/x.png"></p>')
        assert resp.status_code == 422, resp.get_data(as_text=True)
        assert "外部圖片來源" in resp.get_json()["message"]

    def test_cross_pid_image_is_rejected_with_reason(self, app):
        _mk_user(app, "dxowner11", "owner")
        resp = _export(_login(app, "dxowner11"),
                       html='<p><img src="/manuscript/image/OTHER1-p/x.png"></p>')
        assert resp.status_code == 422
        assert "不屬於本專案" in resp.get_json()["message"]

    def test_unregistered_image_is_rejected_not_silently_dropped(self, app):
        _mk_user(app, "dxowner12", "owner")
        resp = _export(_login(app, "dxowner12"),
                       html=f'<p><img src="/manuscript/image/{PID}/nope.png"></p>')
        assert resp.status_code == 422, "缺圖必須明確失敗，不得交出缺圖的 docx"

    def test_oversized_payload_is_rejected(self, app, monkeypatch):
        _mk_user(app, "dxowner13", "owner")
        monkeypatch.setenv("MANUSCRIPT_DOCX_MAX_HTML_CHARS", "500")
        resp = _export(_login(app, "dxowner13"), html="<p>" + "x" * 2000 + "</p>")
        assert resp.status_code == 413

    def test_invalid_pid_is_404(self, app):
        _mk_user(app, "dxowner14", "owner")
        client = _login(app, "dxowner14")
        resp = client.post("/manuscript/api/export/docx/NOPE99-p",
                           json={"title": "T", "html": HTML})
        assert resp.status_code in (403, 404), resp.status_code
