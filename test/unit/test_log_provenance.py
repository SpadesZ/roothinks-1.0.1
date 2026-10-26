# Roothinks source maintenance contract
# 目的：日誌的結構化專案出處（app/log_provenance.py）。
#   - 授權成功的請求標成該專案；被拒絕的請求（外人帶別人的 pid）標成 [pid=-]，不能寫進別人的日誌視圖。
#   - 背景工作用 llm_usage 的 contextvar；子程序用 ROOTHINKS_LOG_PID；都沒有就是 [pid=-]。
#   - 過濾只看出處欄位：不看訊息文字、未標記的舊行一律排除、相近的 pid（ABC 與 ABC12、ABC_x）不互通、ABC 與 ABC-p 視為同一個。
# 驗證：python -m pytest test/unit/test_log_provenance.py -q
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import log_provenance as lp

STAMP = "2026-10-27 10:00:00,000 [INFO] 1/MainThread x"


def _format(record_msg, **extra):
    record = logging.LogRecord("t", logging.INFO, __file__, 1, record_msg, None, None)
    for k, v in extra.items():
        setattr(record, k, v)
    lp.ProjectContextFilter().filter(record)
    return logging.Formatter(lp.TAG_FORMAT).format(record)


def test_background_context_and_env_fallback(monkeypatch):
    from app.llm_service import llm_usage
    monkeypatch.delenv("ROOTHINKS_LOG_PID", raising=False)
    assert "[pid=-]: hello" in _format("hello")
    with llm_usage.usage_context("ABC-p", "paper"):
        assert "[pid=abc]: inside" in _format("inside")
    monkeypatch.setenv("ROOTHINKS_LOG_PID", "SUB1-p")
    assert "[pid=sub1]: child" in _format("child")


def test_filter_uses_structured_tag_only():
    rows = [f"{STAMP} [pid=abc]: run_translation no pid in text",
            f"{STAMP} [pid=abc12]: run_translation pid=ABC12-p",
            f"{STAMP} [pid=abc_x]: run_translation",
            f"{STAMP} [pid=other]: run_translation pid=ABC-p spoof",
            f"{STAMP} [pid=-]: run_translation pid=ABC-p global",
            "2026-07-01 legacy run_translation pid=ABC-p"]
    kept, untagged = lp.lines_for_project(rows, "ABC-p")
    assert kept == rows[:1] and untagged == 1
    assert lp.lines_for_project(rows, "ABC")[0] == rows[:1]
    assert lp.lines_for_project(rows, "")[0] == []


@pytest.fixture()
def app_session(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    from app import create_app, db
    from app.models import Project, User, WorkspaceMember
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    from app.core_pro.literature import literature_routes
    monkeypatch.setattr(literature_routes, "DATA_ROOT", str(tmp_path))
    with app.app_context():
        db.create_all()
        for pid in ("PROVA-p", "PROVB-p"):
            db.session.add(Project(project_id=pid, name=pid, status="formal"))
        for name in ("a", "b"):
            u = User(username=name, email=f"{name}@x.local", system_role="user")
            u.set_password("password123")
            db.session.add(u)
            db.session.flush()
            db.session.add(WorkspaceMember(user_id=u.id, pid=f"PROV{name.upper()}-p", role="owner"))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def test_request_tag_is_set_only_after_authorization(app_session):
    captured = []

    class Grab(logging.Handler):
        def emit(self, record):
            captured.append((record.getMessage(), getattr(record, "rt_pid", None)))

    handler = Grab()
    handler.addFilter(lp.ProjectContextFilter())
    target = logging.getLogger("prov-test")
    target.addHandler(handler)
    target.setLevel(logging.INFO)

    @app_session.after_request
    def _log(resp):
        target.info("after %s", resp.status_code)
        return resp

    try:
        a = app_session.test_client()
        a.post("/api/auth/login", json={"email": "a@x.local", "password": "password123"})
        assert a.get("/api/literature/library?pid=PROVA-p").status_code == 200
        assert a.get("/api/literature/library?pid=PROVB-p").status_code == 403
    finally:
        target.removeHandler(handler)
    tags = [t for msg, t in captured if msg.startswith("after")]
    assert tags[-2:] == ["prova", "-"], captured
