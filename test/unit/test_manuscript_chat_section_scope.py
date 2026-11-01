# Roothinks source maintenance contract
# 目的：hotfix H8——寫作 AI 對話（cmd_load_chat）的章節層讀取。限定編輯（coauthor）只讀得到被指派章節的對話；
#       原本只檢查專案成員，指定別章就能讀到別章的對話。editor／viewer（全章可讀）行為不變。
# 驗證：python -m pytest test/unit/test_manuscript_chat_section_scope.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID = "CHATSC-p"


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
                              "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.db'}",
                              "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'cm.db'}"}})
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False
    with application.app_context():
        db.create_all()
        db.session.add(Project(project_id=PID, name="Chat", status="formal"))
        for name, role in (("co", "coauthor"), ("ed", "editor"), ("vw", "viewer")):
            u = User(username=name, email=f"{name}@t.local")
            u.set_password("password123")
            db.session.add(u)
            db.session.flush()
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
            if name == "co":
                db.session.add(ChapterAssignment(pid=PID, section_key="introduction", user_id=u.id))
        db.session.commit()
    # main 的資料根是 cwd/data；整合分支是 DB 所在目錄：兩處都放
    for base_dir in (tmp_path / "data", tmp_path):
        chat = base_dir / PID / "manuscript" / "chat"
        chat.mkdir(parents=True, exist_ok=True)
        for section in ("introduction", "results"):
            (chat / f"chat_{section}.json").write_text(json.dumps([{"role": "user", "content": f"CHAT_{section.upper()}"}]),
                                                       encoding="utf-8")
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _history(app, who, section):
    from app import socketio
    http = app.test_client()
    assert http.post("/api/auth/login", json={"email": f"{who}@t.local", "password": "password123"}).status_code == 200
    sio = socketio.test_client(app, namespace="/manu_ws", flask_test_client=http, auth={"pid": PID})
    try:
        sio.get_received("/manu_ws")
        sio.emit("cmd_load_chat", {"pid": PID, "section": section}, namespace="/manu_ws")
        got = [e for e in sio.get_received("/manu_ws") if e["name"] == "chat_history"]
        return json.dumps(got[-1]["args"], ensure_ascii=False)
    finally:
        sio.disconnect(namespace="/manu_ws")


def test_coauthor_reads_only_assigned_section_chat(app):
    assert "CHAT_INTRODUCTION" in _history(app, "co", "introduction")
    leaked = _history(app, "co", "results")
    assert "CHAT_RESULTS" not in leaked and '"forbidden": true' in leaked


@pytest.mark.parametrize("who", ["ed", "vw"])
def test_full_readers_unchanged(app, who):
    assert "CHAT_RESULTS" in _history(app, who, "results")
