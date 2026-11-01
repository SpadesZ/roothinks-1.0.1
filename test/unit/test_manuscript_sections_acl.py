# Roothinks source maintenance contract
# 目的：hotfix H9——章節結構（POST /manuscript/api/sections/<pid>）只有 editor 以上可以改。
#       原本只檢查專案成員：viewer、限定編輯（coauthor）都能刪除或改名整份稿件的章節（含別人被指派的章節），
#       章節從介面、全篇總裝與 AI 脈絡消失。讀取章節清單（GET）的行為不變。
# 驗證：python -m pytest test/unit/test_manuscript_sections_acl.py -q
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID = "SECACL-p"
FIXED = [{"id": k, "label": k.title()} for k in ("title", "author", "abstract", "keyword")]


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    monkeypatch.chdir(tmp_path)
    from app import create_app, db
    from app.models import ChapterAssignment, Project, User, WorkspaceMember
    application = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False,
                              "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 's.db'}",
                              "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'sm.db'}"}})
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False
    with application.app_context():
        db.create_all()
        db.session.add(Project(project_id=PID, name="Sections", status="formal"))
        for name, role in (("ow", "owner"), ("ed", "editor"), ("vw", "viewer"), ("co", "coauthor")):
            u = User(username=name, email=f"{name}@t.local")
            u.set_password("password123")
            db.session.add(u)
            db.session.flush()
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
            if name == "co":
                db.session.add(ChapterAssignment(pid=PID, section_key="introduction", user_id=u.id))
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _client(app, who):
    c = app.test_client()
    assert c.post("/api/auth/login", json={"email": f"{who}@t.local", "password": "password123"}).status_code == 200
    return c


def _keys(client):
    resp = client.get(f"/manuscript/api/sections/{PID}")
    assert resp.status_code == 200
    return [s["id"] for s in resp.json["sections"]]


@pytest.mark.parametrize("who", ["vw", "co"])
def test_members_below_editor_cannot_rewrite_chapter_structure(app, who):
    owner = _client(app, "ow")
    before = _keys(owner)
    assert "introduction" in before and "results" in before
    attacker = _client(app, who)
    # 讀取章節清單不受影響（coauthor 本來就只列出被指派的章節）
    assert _keys(attacker) == (["introduction"] if who == "co" else before)
    resp = attacker.post(f"/manuscript/api/sections/{PID}", json={"sections": FIXED + [{"id": "hijack", "label": "x"}]})
    assert resp.status_code == 403
    assert _keys(owner) == before, "章節結構不得被改"


def test_editor_and_owner_can_still_edit_chapter_structure(app):
    for who in ("ed", "ow"):
        client = _client(app, who)
        keys = _keys(client)
        sections = [{"id": k, "label": k.title()} for k in keys] + [{"id": f"extra_{who}", "label": f"Extra {who}"}]
        resp = client.post(f"/manuscript/api/sections/{PID}", json={"sections": sections})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert f"extra_{who}" in _keys(client)
