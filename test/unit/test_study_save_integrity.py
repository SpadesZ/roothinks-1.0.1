# Roothinks source maintenance contract
# 目的：以合成檔案驗證 Study 衝突、磁碟故障、對話保存與真實失敗狀態。
# 邊界：Flask blueprint/函式層；角色端到端另見 test_llm_admin_acl 及既有 ACL 測試。
# 驗證：python -m pytest test/unit/test_study_save_integrity.py -q
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def study_app(monkeypatch, tmp_path):
    from app.core_pro.study import study_routes as routes
    app = Flask(__name__)
    app.config.update(TESTING=True, LOCK_ROOT=str(tmp_path / "locks"),
                      SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "app.db"}')
    (tmp_path / "app").mkdir()
    app.root_path = str(tmp_path / "app")
    monkeypatch.setattr(routes, "_normalize_study_pid", lambda pid: str(pid or ""))
    monkeypatch.setattr(routes, "enforce_project_ownership", lambda pid: None)
    monkeypatch.setattr(routes, "_get_study_note_path", lambda pid: str(tmp_path / "notes.json"))
    app.register_blueprint(routes.study_bp)
    return app, routes, tmp_path


def test_stale_note_is_rejected_without_changing_newer_content(study_app):
    app, _, root = study_app
    client = app.test_client()
    first = client.post("/api/study/notes/save", json={"pid": "P-p", "notes": "Alice", "base_rev": 0})
    assert first.status_code == 200
    stale = client.post("/api/study/notes/save", json={"pid": "P-p", "notes": "Bob old tab", "base_rev": 0})
    assert stale.status_code == 409
    assert stale.json["conflict"] is True
    assert json.loads((root / "notes.json").read_text(encoding="utf-8"))["notes"] == "Alice"
    assert client.get("/api/study/notes/load/P-p").json["rev"] == 1


def test_missing_note_revision_requires_reload(study_app):
    app, _, root = study_app
    response = app.test_client().post("/api/study/notes/save", json={"pid": "P-p", "notes": "unversioned"})
    assert response.status_code == 428
    assert not (root / "notes.json").exists()


@pytest.mark.parametrize("revision", [-1, True, "1.5", {}, []])
def test_invalid_note_revision_does_not_write(study_app, revision):
    app, _, root = study_app
    response = app.test_client().post("/api/study/notes/save", json={"pid": "P-p", "notes": "invalid", "base_rev": revision})
    assert response.status_code == 400
    assert not (root / "notes.json").exists()


def test_corrupt_note_is_not_replaced_or_reported_as_empty(study_app):
    app, _, root = study_app
    original = b'{"notes":"original truncated'
    (root / "notes.json").write_bytes(original)
    response = app.test_client().post("/api/study/notes/save", json={"pid": "P-p", "notes": "overwrite", "base_rev": 0})
    assert response.status_code == 500
    assert (root / "notes.json").read_bytes() == original
    assert app.test_client().get("/api/study/notes/load/P-p").status_code == 500


def test_simultaneous_note_writers_have_one_winner(study_app):
    app, _, root = study_app
    def save(text):
        return app.test_client().post("/api/study/notes/save", json={"pid": "P-p", "notes": text, "base_rev": 0}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(save, ["one", "two"]))
    assert sorted(statuses) == [200, 409]
    assert json.loads((root / "notes.json").read_text(encoding="utf-8"))["_rev"] == 1


def test_chat_save_failure_returns_recoverable_result(study_app, monkeypatch):
    app, routes, root = study_app
    conv_dir = root / "conversations"
    conv_dir.mkdir()
    original = {"conv_id": "c1", "pid": "P-p", "title": "Original", "messages": []}
    (conv_dir / "c1.json").write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(routes, "_get_conversations_dir", lambda pid: str(conv_dir))
    monkeypatch.setattr(routes.tutor_engine, "process_query", lambda *a: "Generated answer")
    def fail_replace(*args):
        raise OSError("synthetic disk full")
    monkeypatch.setattr("app.security.os.replace", fail_replace)
    response = app.test_client().post("/api/study/chat", json={"pid": "P-p", "conv_id": "c1", "question": "Keep me"})
    assert response.json["ok"] is False
    assert response.json["saved"] is False
    assert response.json["answer"] == "Generated answer"
    assert json.loads((conv_dir / "c1.json").read_text(encoding="utf-8")) == original


@pytest.mark.parametrize("mode,status", [("all_failed", 502), ("disk_failed", 503), ("partial", 207)])
def test_matrix_outcome_and_storage_are_separate(study_app, monkeypatch, mode, status):
    app, routes, root = study_app
    for paper in ("a", "b"):
        (root / paper).mkdir()
    monkeypatch.setattr(routes, "resolve_literature_paper_dir", lambda data_root, pid, paper_id, **k: str(root / paper_id))
    monkeypatch.setattr(routes, "_get_matrix_history_dir", lambda pid: str(root))
    snapshot = root / "latest_snapshot.json"
    snapshot.write_text('{"previous":"keep"}', encoding="utf-8")
    monkeypatch.setattr(routes, "_get_latest_matrix_snapshot_path", lambda pid: str(snapshot))
    comparisons = [{"analysis": "One result"}]
    if mode == "all_failed":
        comparisons = [{"error": "offline"}]
    if mode == "partial":
        comparisons.append({"error": "offline"})
    monkeypatch.setattr(routes.matrix_engine, "generate_matrix", lambda *a: {"comparisons": comparisons})
    if mode == "disk_failed":
        def fail(*a):
            raise OSError("synthetic disk full")
        monkeypatch.setattr("app.security.os.replace", fail)
    response = app.test_client().post("/api/study/compare", json={"pid": "P-p", "paper_ids": ["a", "b"]})
    assert response.status_code == status
    assert response.json["ok"] is False
    assert response.json["data"]["comparisons"]
    assert response.json["saved"] is (mode == "partial")
    if mode != "partial":
        assert snapshot.read_text(encoding="utf-8") == '{"previous":"keep"}'


def test_note_history_survives_explicit_conflict_resolution(study_app):
    app, _, root = study_app
    client = app.test_client()
    assert client.post('/api/study/notes/save', json={'pid':'P-p','notes':'A original','base_rev':0}).status_code == 200
    assert client.post('/api/study/notes/save', json={'pid':'P-p','notes':'B resolved','base_rev':1}).status_code == 200
    old = client.get('/api/study/notes/load/P-p?rev=1')
    assert old.status_code == 200
    assert old.json['notes'] == 'A original'
    assert old.json['rev'] == 1
    assert client.get('/api/study/notes/load/P-p').json['notes'] == 'B resolved'
    assert client.get('/api/study/notes/load/P-p?rev=999').status_code == 404
    assert client.get('/api/study/notes/load/P-p?rev=../notes').status_code == 400


def test_history_write_failure_keeps_canonical_note(study_app, monkeypatch):
    app, _, root = study_app
    client = app.test_client()
    assert client.post('/api/study/notes/save', json={'pid':'P-p','notes':'keep','base_rev':0}).status_code == 200
    import app.security as security
    real = security._write_json_atomic
    def fail_history(path, obj):
        if '.history' in str(path):
            raise OSError('synthetic full history disk')
        return real(path, obj)
    monkeypatch.setattr(security, '_write_json_atomic', fail_history)
    assert client.post('/api/study/notes/save', json={'pid':'P-p','notes':'replacement','base_rev':1}).status_code == 500
    assert client.get('/api/study/notes/load/P-p').json['notes'] == 'keep'
