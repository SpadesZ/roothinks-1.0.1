# Roothinks source maintenance contract
# 目的：R4 可重現評估包與資料就緒檢查。
#   - 離線證據包：同一份程式重跑時「穩定雜湊」相同；檔案被改或重跑結果不同時 verify 失敗。
#   - 資料就緒檢查只讀（前後所有檔案的雜湊與 mtime 相同）；破損文件列為 blocker；行為改變列為 warning。
# 驗證：python -m pytest test/unit/test_repro_readiness.py -q
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.repro import data_readiness, offline_evidence_bundle as bundle


def test_offline_bundle_is_reproducible_and_detects_tampering(tmp_path):
    manifest = bundle.build(tmp_path / "b")
    assert set(manifest["outputs"]) == {"study_eval_negative_controls", "study_eval_stub_gates", "translation_guard_benchmark"}
    report = bundle.verify(tmp_path / "b")
    assert report["ok"], report
    controls = json.loads((tmp_path / "b" / "study_eval_negative_controls.json").read_text(encoding="utf-8"))
    assert controls["nature"].startswith("TOOL SELF-CHECK") and all(c["as_expected"] for c in controls["controls"].values())
    path = tmp_path / "b" / "translation_guard_benchmark.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["base_patterns"]["faithful"]["new_loss_flagged_sentences"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    tampered = bundle.verify(tmp_path / "b")
    assert not tampered["ok"] and tampered["outputs"]["translation_guard_benchmark"]["file_matches_manifest"] is False


def _tree_digest(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


def _seed(tmp_path):
    root = tmp_path / "data"
    (root / "P1-p" / "study" / "conversations").mkdir(parents=True)
    (root / "P1-p" / "study" / "P1-p_evidence_notes.json").write_text(json.dumps({"notes": [], "_rev": 2}), encoding="utf-8")
    hist = root / "P1-p" / "study" / "P1-p_evidence_notes.json.history"
    hist.mkdir()
    (hist / "1.json").write_text("{}", encoding="utf-8")
    (root / "P1-p" / "study" / "P1-p_note.json").write_text("{broken", encoding="utf-8")
    (root / "P1-p" / "study" / "conversations" / "c1.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    (root / "P1-p" / "literature").mkdir(parents=True)
    (root / "P1-p" / "literature" / "library.json").write_text(json.dumps(
        {"_rev": 1, "entries": {"e1": {"reading_note": "private"}, "e2": {"reading_note": ""}}}), encoding="utf-8")
    (root / "_logs" / "system").mkdir(parents=True)
    (root / "_logs" / "system" / "runtime.log").write_text(
        "old line without tag\n2026-10-27 10:00:00,000 [INFO] 1/M x [pid=p1]: tagged\n", encoding="utf-8")
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table users(id integer primary key, system_role text);
        create table workspace_members(id integer primary key, user_id int, pid text, role text);
        create table mentor_links(id integer primary key, mentor_id int, mentee_id int);
        create table project_invitations(id text, pid text, accepted_by int, kind text, status text);
        insert into users values (1,'user'),(2,'mentor'),(3,'mentor');
        insert into workspace_members values (1,1,'P1-p','owner'),(2,2,'P1-p','viewer'),(3,3,'P1-p','viewer');
        insert into mentor_links values (1,2,1),(2,3,1);
        insert into project_invitations values ('i1','P1-p',3,'professor','accepted');
    """)
    con.commit()
    con.close()
    return root, db


def test_data_readiness_is_read_only_and_reports(tmp_path):
    root, db = _seed(tmp_path)
    before = _tree_digest(tmp_path)
    report = data_readiness.run(root, db)
    assert _tree_digest(tmp_path) == before, "就緒檢查不得改動任何檔案"
    assert any("study_free_notes" in b for b in report["blockers"])
    assert report["documents"]["evidence_notes"] == {"files": 1, "problems": []}
    assert report["database"]["admin_accounts"] == 0
    assert report["database"]["legacy_mentor_viewers"] == [{"pid": "P1-p", "user_id": 2}]
    assert report["database"]["reading_notes_hidden_from_professors"] == 1
    assert report["conversations"] == {"total": 1, "without_creator": 1}
    assert report["runtime_log"]["tagged"] == 1 and report["runtime_log"]["untagged"] == 1
    assert len(report["warnings"]) == 5
    assert data_readiness.main(["--data-root", str(root), "--db", str(db), "--out", str(tmp_path / "r.json")]) == 2


def test_history_newer_than_revision_is_a_blocker(tmp_path):
    root, db = _seed(tmp_path)
    (root / "P1-p" / "study" / "P1-p_note.json").write_text(json.dumps({"_rev": 1}), encoding="utf-8")
    hist = root / "P1-p" / "study" / "P1-p_evidence_notes.json.history"
    (hist / "5.json").write_text("{}", encoding="utf-8")
    report = data_readiness.run(root, db)
    assert report["blockers"] == ["evidence_notes: P1-p/study/P1-p_evidence_notes.json：歷史快照 5 不小於目前 _rev 2"]
