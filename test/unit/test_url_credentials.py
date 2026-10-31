# Roothinks source maintenance contract
# 目的：hotfix H7——Manuscript 不再把認證 token 放進網址，且舊資料裡的 token 不會再被帶出。
#   1. HTML 的 src／href 去除 access_token／token／api_key；正文文字與其他參數不變。
#   2. 稿件存檔不落盤 token；讀取舊版本時去除 token，但不改寫舊檔（不動既有資料）。
#   3. 圖片路由：session 成員不需要 token（main 的全域模組守衛本來就擋非成員；這裡再明確檢查一次）。
#   4. 只有 token 的用戶端：標頭 token 換到限定本專案圖片路徑的 HttpOnly 票證 cookie；票證不含 token、
#      換專案無效、竄改無效、token 從設定移除後立即無效。網址 token 預設仍相容，可設 API_QUERY_TOKEN_ALLOWED=0 關閉。
#   5. gunicorn 存取日誌遮罩 token 值。
#   6. 前端程式不再組出 access_token= 網址。
# 驗證：python -m pytest test/unit/test_url_credentials.py -q
import json
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PID, OTHER = "IMGA-p", "IMGB-p"
GOOD, PROJECT_ONLY = "tok-good-imga", "tok-imgb-only"


def test_strip_html_url_credentials_only_touches_url_attributes():
    from app.security import strip_html_url_credentials, strip_url_credentials
    html = ('<p>寫作中提到 token=abc 不應被改</p><img src="/manuscript/image/P-p/a.png?access_token=SECRET1">'
            "<img src='/m/b.png?w=2&amp;token=SECRET2&amp;h=3'><a href=\"/x?api_key=SECRET3#frag\">l</a>")
    out = strip_html_url_credentials(html)
    assert "SECRET" not in out and "token=abc" in out
    assert 'src="/manuscript/image/P-p/a.png"' in out and "src='/m/b.png?w=2&amp;h=3'" in out and 'href="/x#frag"' in out
    assert strip_url_credentials("/a?token=1&b=2") == "/a?b=2" and strip_url_credentials("/a?b=2&token=1") == "/a?b=2"


def _app(monkeypatch, tmp_path, **config):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    monkeypatch.chdir(tmp_path)  # 圖片路由讀 cwd/data；測試全在 tmp
    from app import create_app, db
    from app.models import Project, User, WorkspaceMember
    base = {"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False, "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}}
    base.update(config)
    app = create_app(base)
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    with app.app_context():
        db.create_all()
        for pid in (PID, OTHER):
            db.session.add(Project(project_id=pid, name=pid, status="formal"))
        for name in ("member", "outsider"):
            u = User(username=name, email=f"{name}@i.local", system_role="user")
            u.set_password("password123")
            db.session.add(u)
            db.session.flush()
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID if name == "member" else OTHER, role="viewer"))
        db.session.commit()
    for pid in (PID, OTHER):
        d = tmp_path / "data" / pid / "manuscript" / "image"
        d.mkdir(parents=True)
        (d / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return app


def _dispose(app):
    from app import db
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def _login(app, name, headers=None):
    c = app.test_client()
    resp = c.post("/api/auth/login", json={"email": f"{name}@i.local", "password": "password123"}, headers=headers or {})
    assert resp.status_code == 200, resp.status_code
    return c


def test_session_mode_image_requires_membership_not_token(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, AUTH_MODE="session")
    try:
        assert _login(app, "member").get(f"/manuscript/image/{PID}/fig.png").status_code == 200
        assert _login(app, "outsider").get(f"/manuscript/image/{PID}/fig.png").status_code == 403
        assert app.test_client().get(f"/manuscript/image/{PID}/fig.png").status_code in (302, 401)
    finally:
        _dispose(app)


def test_session_plus_api_mode_members_need_no_url_token(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, AUTH_MODE="session", API_AUTH_ENABLED=True, API_BEARER_TOKENS=[GOOD],
               TOKEN_ACL_MAP={GOOD: {"allowed_pids": [PID]}})
    try:
        # 這個模式下 /api/* 要 token（全域守衛），所以登入時帶標頭；之後讀圖只靠 session cookie，網址不帶 token。
        auth = {"Authorization": f"Bearer {GOOD}"}
        assert _login(app, "member", auth).get(f"/manuscript/image/{PID}/fig.png").status_code == 200
        assert _login(app, "outsider", auth).get(f"/manuscript/image/{PID}/fig.png").status_code == 403
    finally:
        _dispose(app)


@pytest.fixture()
def token_app(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, AUTH_MODE="none", API_AUTH_ENABLED=True, API_BEARER_TOKENS=[GOOD, PROJECT_ONLY],
               TOKEN_ACL_MAP={GOOD: {"allowed_pids": [PID]}, PROJECT_ONLY: {"allowed_pids": [OTHER]}})
    yield app
    _dispose(app)


def test_token_only_client_uses_header_then_image_ticket_cookie(token_app):
    c = token_app.test_client()
    img = f"/manuscript/image/{PID}/fig.png"
    assert c.get(img).status_code == 401
    assert c.get(img, headers={"Authorization": f"Bearer {GOOD}"}).status_code == 200
    resp = c.post("/manuscript/api/image_session", json={"pid": PID}, headers={"Authorization": f"Bearer {GOOD}"})
    assert resp.status_code == 200 and resp.json["ticket"] is True
    cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and f"Path=/manuscript/image/{PID}/" in cookie and "SameSite=Strict" in cookie
    assert GOOD not in cookie, "票證不得含 token 原文"
    assert c.get(img).status_code == 200, "只靠票證 cookie 就能讀本專案圖片"
    # 別的專案：cookie 路徑不符，也驗不過
    assert c.get(f"/manuscript/image/{OTHER}/fig.png").status_code == 401
    # 不能替沒權限的專案換票證
    assert c.post("/manuscript/api/image_session", json={"pid": PID},
                  headers={"Authorization": f"Bearer {PROJECT_ONLY}"}).status_code == 403


def test_ticket_tamper_expiry_and_revocation(token_app):
    from app.security import image_ticket_cookie_name, issue_image_ticket, verify_image_ticket
    with token_app.test_request_context():
        ticket = issue_image_ticket(GOOD, PID, now=1000)
        assert verify_image_ticket(ticket, PID, now=1001) == GOOD
        assert verify_image_ticket(ticket, OTHER, now=1001) == ""
        assert verify_image_ticket(ticket.replace(PID, OTHER, 1), OTHER, now=1001) == ""
        assert verify_image_ticket(ticket, PID, now=1000 + 13 * 3600) == ""
        token_app.config["API_BEARER_TOKENS"] = [PROJECT_ONLY]
        assert verify_image_ticket(ticket, PID, now=1001) == "", "token 移除後票證立即失效"
        assert image_ticket_cookie_name(PID) != image_ticket_cookie_name(OTHER)


def test_query_token_is_compatible_by_default_and_can_be_disabled(token_app):
    c = token_app.test_client()
    img = f"/manuscript/image/{PID}/fig.png"
    assert c.get(f"{img}?access_token={GOOD}").status_code == 200
    token_app.config["API_QUERY_TOKEN_ALLOWED"] = False
    assert c.get(f"{img}?access_token={GOOD}").status_code == 401
    assert c.get(img, headers={"Authorization": f"Bearer {GOOD}"}).status_code == 200


def test_manuscript_save_strips_and_legacy_read_does_not_rewrite(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, AUTH_MODE="session")
    try:
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        html = f'<p>t</p><img src="/manuscript/image/{PID}/fig.png?access_token=SAVEDSECRET">'
        with app.app_context():
            res = ManuscriptIO.save_block_version(PID, "introduction", "T", html, updated_by="member")
            stored = list((tmp_path / PID).rglob(res["filename"]))  # ManuscriptIO 的資料根 = DB 所在目錄
            assert stored and "SAVEDSECRET" not in stored[0].read_text(encoding="utf-8")
            # 舊版本檔（升級前寫入）裡已經有 token：讀取時去除，但檔案本身不改
            stored[0].write_text(json.dumps({"title": "T", "section": "introduction", "content": html}), encoding="utf-8")
            loaded = ManuscriptIO.load_block(PID, "introduction", res["filename"])
            assert "SAVEDSECRET" not in loaded["content"] and f"/manuscript/image/{PID}/fig.png" in loaded["content"]
            assert "SAVEDSECRET" in stored[0].read_text(encoding="utf-8")
            paper = ManuscriptIO.save_paper_version(PID, "P", html, updated_by="member")
            assert "SAVEDSECRET" not in json.dumps(ManuscriptIO.load_paper_version(PID, paper["g_ver"]))
    finally:
        _dispose(app)


def test_gunicorn_access_log_redacts_tokens(monkeypatch):
    from gunicorn.glogging import Logger
    from app import gunicorn_logging as gl
    monkeypatch.setattr(Logger, "atoms", lambda self, *a: {
        "r": "GET /manuscript/image/P/f.png?w=1&access_token=LEAK1 HTTP/1.1", "q": "token=LEAK2&w=1",
        "f": "https://h/x?api_key=LEAK3", "{authorization}i": "Bearer LEAK4", "s": "200"})
    atoms = gl.RedactingLogger.atoms(object.__new__(gl.RedactingLogger), None, None, {}, None)
    assert "LEAK" not in json.dumps(atoms) and atoms["s"] == "200" and "w=1" in atoms["r"]
    assert "--logger-class app.gunicorn_logging.RedactingLogger" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_frontend_no_longer_builds_token_urls():
    offenders = []
    for path in (ROOT / "app" / "static" / "js").glob("*.js"):
        text = path.read_text(encoding="utf-8")
        # 舊程式是 `${u}${sep}access_token=${encodeURIComponent(token)}`：任何把 token 接在 access_token=／token= 後面的寫法都算。
        if re.search(r"(access_token|[?&$}]token)=\$\{", text):
            offenders.append(path.name)
    assert offenders == []
