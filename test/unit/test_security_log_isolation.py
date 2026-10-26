# Roothinks source maintenance contract
# 目的：安全熱修（跨專案日誌外洩）的對抗性回歸測試。
#   1. /api/literature/debug/runtime_log_tail 沒有 pid，專案 ACL 不會執行；修補前任何登入者（含外人）可讀全站日誌。
#      修補後只給系統管理員：session（system_role=admin）、Bearer（TOKEN_ACL_MAP 明確 system_role=admin）。
#   2. flowb_probe／recover_flowb 的快照用通用字過濾全站日誌，會把其他專案的行給本專案成員。
#      修補後只留下提到本專案 pid（含 -p 別名、嚴格字界）的行，且不回傳伺服器日誌路徑。
#   3. 開發模式（兩種認證都關）行為不變，便於本機除錯與回滾相容。
# 驗證：python -m pytest test/unit/test_security_log_isolation.py -q
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID, PAPER = "LOGA-p", "paper1"
# 與 PID 相近、但屬於其他專案的 id：修補前的字界若只看英數字，LOGA 會命中這些行。
NEIGHBOURS = ("LOGA12-p", "LOGA-x", "LOGA_x", "XLOGA-p")
ADMIN_TOKEN, PROJECT_TOKEN, WILDCARD_TOKEN = "tok-admin", "tok-project", "tok-wildcard"


def _write_log(path):
    lines = [f"INFO [Flow B] run_translation pid={PID} paper={PAPER} OWN_LINE",
             f"INFO [Flow B] run_translation pid=LOGA paper={PAPER} OWN_BASE_ALIAS_LINE"]
    lines += [f"INFO [Flow B] run_translation pid={n} paper=secret OTHER_PROJECT_LINE_{i}" for i, n in enumerate(NEIGHBOURS)]
    lines.append("INFO [Flow B] run_translation pid=ELSEWHERE-p ELSEWHERE_LINE")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_app(monkeypatch, tmp_path, **config):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    log = tmp_path / "runtime.log"
    monkeypatch.setenv("LECTURE_SYSTEM_LOG_FILE", str(log))
    _write_log(log)
    from app import create_app, db
    from app.models import Paper, Project, User, WorkspaceMember
    base = {"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}}
    base.update(config)
    app = create_app(base)
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    from app.core_pro.literature import literature_routes
    monkeypatch.setattr(literature_routes, "DATA_ROOT", str(tmp_path))
    ids = {}
    with app.app_context():
        db.create_all()
        for pid in (PID,) + NEIGHBOURS:
            db.session.add(Project(project_id=pid, name=pid, status="formal"))
        for name, role in (("owner", "user"), ("viewer", "user"), ("outsider", "user"), ("mentor", "mentor"), ("admin", "admin")):
            user = User(username=name, email=f"{name}@x.local", system_role=role)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
        db.session.add_all([WorkspaceMember(user_id=ids["owner"], pid=PID, role="owner"),
                            WorkspaceMember(user_id=ids["viewer"], pid=PID, role="viewer"),
                            WorkspaceMember(user_id=ids["mentor"], pid=PID, role="viewer"),
                            WorkspaceMember(user_id=ids["outsider"], pid="LOGA12-p", role="owner"),
                            Paper(paper_id=PAPER, pid=PID, title="p", interpretation_status=Paper.STATUS_GOLD_READY)])
        db.session.commit()
    return app


def _login(app, name):
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": f"{name}@x.local", "password": "password123"}).status_code == 200
    return client


@pytest.fixture()
def session_app(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path, AUTH_MODE="session")
    yield app
    _dispose(app)


@pytest.fixture()
def token_app(monkeypatch, tmp_path):
    acl = {ADMIN_TOKEN: {"allowed_pids": [PID], "system_role": "admin"},
           PROJECT_TOKEN: {"allowed_pids": [PID]},
           WILDCARD_TOKEN: {"allowed_pids": ["*"]}}
    app = _make_app(monkeypatch, tmp_path, AUTH_MODE="none", API_AUTH_ENABLED=True,
                    API_BEARER_TOKENS=[ADMIN_TOKEN, PROJECT_TOKEN, WILDCARD_TOKEN], TOKEN_ACL_MAP=acl)
    yield app
    _dispose(app)


@pytest.fixture()
def dev_app(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path, AUTH_MODE="none", API_AUTH_ENABLED=False)
    yield app
    _dispose(app)


def _dispose(app):
    from app import db
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def _tail(client, headers=None):
    return client.get("/api/literature/debug/runtime_log_tail?lines=50", headers=headers or {})


def _probe(client, headers=None):
    return client.get(f"/api/literature/debug/flowb_probe?pid={PID}&paper_id={PAPER}", headers=headers or {})


@pytest.mark.parametrize("who", ["owner", "viewer", "mentor", "outsider"])
def test_runtime_log_tail_refuses_every_non_admin_session_user(session_app, who):
    resp = _tail(_login(session_app, who))
    assert resp.status_code == 403
    assert "OTHER_PROJECT_LINE" not in resp.get_data(as_text=True)


def test_runtime_log_tail_requires_login_and_works_for_system_admin(session_app):
    assert _tail(session_app.test_client()).status_code == 401
    resp = _tail(_login(session_app, "admin"))
    assert resp.status_code == 200 and any("OWN_LINE" in r for r in resp.json["lines"])


def test_runtime_log_tail_token_mode_needs_explicit_admin_grant(token_app):
    client = token_app.test_client()
    assert _tail(client).status_code == 401
    assert _tail(client, {"Authorization": "Bearer wrong"}).status_code == 401
    for token in (PROJECT_TOKEN, WILDCARD_TOKEN):  # 專案 wildcard 不等於全站管理權
        assert _tail(client, {"Authorization": f"Bearer {token}"}).status_code == 403
    assert _tail(client, {"Authorization": f"Bearer {ADMIN_TOKEN}"}).status_code == 200


def test_dev_mode_without_auth_is_unchanged(dev_app):
    assert _tail(dev_app.test_client()).status_code == 200


@pytest.mark.parametrize("who", ["owner", "viewer", "mentor"])
def test_flowb_probe_returns_only_this_projects_lines_and_no_server_path(session_app, who):
    resp = _probe(_login(session_app, who))
    assert resp.status_code == 200
    runtime = resp.json["debug"]["runtime_log"]
    text = "\n".join(runtime["tail_hits"])
    assert "OWN_LINE" in text and "OWN_BASE_ALIAS_LINE" in text
    assert "OTHER_PROJECT_LINE" not in text and "ELSEWHERE_LINE" not in text
    assert "path" not in runtime and "runtime.log" not in resp.get_data(as_text=True)


def test_flowb_probe_outsider_and_anonymous_are_refused(session_app):
    assert _probe(_login(session_app, "outsider")).status_code == 403
    assert _probe(session_app.test_client()).status_code == 401


def test_flowb_probe_token_scoped_to_project_sees_only_own_lines(token_app):
    resp = _probe(token_app.test_client(), {"Authorization": f"Bearer {PROJECT_TOKEN}"})
    assert resp.status_code == 200
    text = "\n".join(resp.json["debug"]["runtime_log"]["tail_hits"])
    assert "OWN_LINE" in text and "OTHER_PROJECT_LINE" not in text


def test_recover_flowb_snapshot_is_filtered_too(session_app):
    resp = _login(session_app, "owner").post("/api/literature/debug/recover_flowb",
                                             json={"pid": PID, "paper_id": PAPER, "force": False})
    assert "OTHER_PROJECT_LINE" not in resp.get_data(as_text=True)
