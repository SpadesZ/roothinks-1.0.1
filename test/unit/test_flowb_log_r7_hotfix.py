# Roothinks source maintenance contract
# 目的：安全熱修 H10（第六輪）— Flow B 診斷快照的混合專案日誌。
#   main 的全站日誌沒有結構化出處欄位，只能比對文字；文字提到本專案 pid 不能證明這行屬於本專案：
#   別的專案的路徑、佇列狀態行、traceback、甚至別的專案把論文取名成本專案 pid，都會讓別的專案的行被當成本專案的行。
#   修補後：一般成員（session 成員、專案 token）從 flowb_probe／recover_flowb 拿不到任何全站日誌原文，只拿到行數與說明；
#   系統管理員（以及兩種認證都關的開發模式）行為不變。快照的 events_tail／process_log／日誌行不含伺服器絕對路徑。
# 驗證：python -m pytest test/unit/test_flowb_log_r7_hotfix.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID, PAPER, OTHER = "LOGA-p", "paper1", "OTHER-p"
ADMIN_TOKEN, PROJECT_TOKEN = "tok-admin-r7", "tok-project-r7"
SECRETS = ("OTHER_PATH_SECRET", "B_QUEUE_SECRET", "B_TRACE_SECRET", "B_PAPERNAME_SECRET")


def _write_log(path, data_root):
    lines = [
        f"INFO [Flow B] run_translation pid={PID} paper={PAPER} OWN_LINE",
        # 別的專案的輸出路徑剛好有一段叫 LOGA（或任何等於本專案 pid 的字）。
        f"INFO [HybridTranslator] Batch-translated 3 blocks to {data_root}/{OTHER}/literature/LOGA/06_translates/x.json "
        "flow b OTHER_PATH_SECRET",
        # 佇列狀態行同時列出兩個專案（混合行）。
        f"INFO [FlowB] queue: running {OTHER}/p9 translating_local title=B_QUEUE_SECRET; waiting {PID}/{PAPER}",
        # 別的專案的 traceback 續行，例外訊息提到本專案。
        f"ValueError: block copied from {PID} {PAPER} B_TRACE_SECRET",
        # 別的專案把論文 id 取成本專案的 pid。
        f"INFO [DB] Updated Paper status: {PID} -> processing_B ({OTHER}) B_PAPERNAME_SECRET",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_app(monkeypatch, tmp_path, **config):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    data_root = tmp_path / "srv-data"
    data_root.mkdir()
    log = tmp_path / "runtime.log"
    monkeypatch.setenv("LECTURE_SYSTEM_LOG_FILE", str(log))
    from app import create_app, db
    from app.models import Paper, Project, User, WorkspaceMember
    base = {"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}}
    base.update(config)
    app = create_app(base)
    _write_log(log, data_root)  # create_app 會寫 SYSTEM_PROFILE；之後再覆寫成測試內容
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    from app.core_pro.literature import literature_routes
    monkeypatch.setattr(literature_routes, "DATA_ROOT", str(data_root))
    ids = {}
    with app.app_context():
        db.create_all()
        for pid in (PID, OTHER):
            db.session.add(Project(project_id=pid, name=pid, status="formal"))
        for name, role in (("owner", "user"), ("viewer", "user"), ("mentor", "mentor"), ("admin", "admin")):
            user = User(username=name, email=f"{name}@x.local", system_role=role)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
        db.session.add_all([
            WorkspaceMember(user_id=ids["owner"], pid=PID, role="owner"),
            WorkspaceMember(user_id=ids["viewer"], pid=PID, role="viewer"),
            WorkspaceMember(user_id=ids["mentor"], pid=PID, role="viewer"),
            WorkspaceMember(user_id=ids["admin"], pid=PID, role="viewer"),
            Paper(paper_id=PAPER, pid=PID, title="p", interpretation_status="processing_B",
                  process_log=f"Flow B translation error: [Errno 2] {data_root}/{PID}/x.json [run_id=r1]"),
        ])
        db.session.commit()
        literature_routes._append_flow_event(
            PID, PAPER, flow="flow_b", event="completed", message="ok",
            extra={"output": f"{data_root}/{PID}/literature/{PAPER}/06_translates/fusion/full_text_trans.json",
                   "payload": {"input_path": "/opt/models/nllb/cache/model.bin"}})
    app.test_data_root = str(data_root)
    return app


def _dispose(app):
    from app import db
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


@pytest.fixture()
def session_app(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path, AUTH_MODE="session")
    yield app
    _dispose(app)


@pytest.fixture()
def token_app(monkeypatch, tmp_path):
    acl = {ADMIN_TOKEN: {"allowed_pids": [PID], "system_role": "admin"}, PROJECT_TOKEN: {"allowed_pids": [PID]}}
    app = _make_app(monkeypatch, tmp_path, AUTH_MODE="none", API_AUTH_ENABLED=True,
                    API_BEARER_TOKENS=[ADMIN_TOKEN, PROJECT_TOKEN], TOKEN_ACL_MAP=acl)
    yield app
    _dispose(app)


@pytest.fixture()
def dev_app(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path, AUTH_MODE="none", API_AUTH_ENABLED=False)
    yield app
    _dispose(app)


def _login(app, name):
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": f"{name}@x.local", "password": "password123"}).status_code == 200
    return client


def _probe(client, headers=None):
    return client.get(f"/api/literature/debug/flowb_probe?pid={PID}&paper_id={PAPER}", headers=headers or {})


def _assert_member_view(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_data(as_text=True)
    for secret in SECRETS + ("OWN_LINE",):
        assert secret not in body, secret
    runtime = resp.json["debug"]["runtime_log"]
    assert runtime["tail_hits"] == [] and runtime["withheld_lines"] >= 1
    assert "runtime_log_tail" in runtime["note"]


@pytest.mark.parametrize("who", ["owner", "viewer", "mentor"])
def test_members_get_no_global_log_text_from_flowb_probe(session_app, who):
    _assert_member_view(_probe(_login(session_app, who)))


def test_project_token_gets_no_global_log_text(token_app):
    _assert_member_view(_probe(token_app.test_client(), {"Authorization": f"Bearer {PROJECT_TOKEN}"}))


def test_recover_flowb_snapshot_gives_members_no_global_log_text(session_app):
    resp = _login(session_app, "owner").post("/api/literature/debug/recover_flowb",
                                             json={"pid": PID, "paper_id": PAPER, "force": True})
    _assert_member_view(resp)


def test_system_admin_session_still_sees_pid_filtered_lines(session_app):
    runtime = _probe(_login(session_app, "admin")).json["debug"]["runtime_log"]
    assert any("OWN_LINE" in r for r in runtime["tail_hits"])


def test_system_admin_token_still_sees_pid_filtered_lines(token_app):
    runtime = _probe(token_app.test_client(), {"Authorization": f"Bearer {ADMIN_TOKEN}"}).json["debug"]["runtime_log"]
    assert any("OWN_LINE" in r for r in runtime["tail_hits"])


def test_dev_mode_without_auth_is_unchanged(dev_app):
    runtime = _probe(dev_app.test_client()).json["debug"]["runtime_log"]
    assert any("OWN_LINE" in r for r in runtime["tail_hits"])


def test_snapshot_returns_no_absolute_server_paths(session_app):
    resp = _probe(_login(session_app, "viewer"))
    debug = resp.json["debug"]
    text = json.dumps(debug, ensure_ascii=False)
    assert debug["events_tail"] and debug["db"]["process_log"]
    assert session_app.test_data_root not in text and "/opt/models" not in text, text
    assert "full_text_trans.json" in text  # 只留檔名，除錯仍可用
