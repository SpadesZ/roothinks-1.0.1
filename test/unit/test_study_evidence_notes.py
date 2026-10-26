# Roothinks source maintenance contract
# 目的：Study 選段 Tutor 與證據筆記的端到端 API 行為（真 create_app、session 登入、tmp SQLite 與 tmp 資料根）。
# 重點：AI 草稿不自動存檔；原文事實不可偽造；舊版本不覆蓋；權限沿用既有 Study ACL 與教授審閱門檻。
# 驗證：python -m pytest test/unit/test_study_evidence_notes.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID = "EVID-p"
PAPER = "paper1"
QUOTE = "The improvement was observed only at 300 K under one calibration protocol."
FAKE = "replicated in three independent laboratories"
CONTENT = [
    {"page": 1, "blocks": [
        {"type": "title", "content": "Calibrated Thermal Response", "seq_id": "p1_title_1"},
        {"type": "body", "seq_id": "p1_body_2", "content": "We report a bounded measurement for sample A. " + QUOTE,
         "content_zh": "改善只在 300 K、單一校正流程下觀察到。"},
        {"type": "body", "seq_id": "p1_body_3", "content": "No claim is made about samples B or C."},
    ]},
]


class StubBus:
    def __init__(self):
        self.prompts = []

    def dispatch_task(self, task, prompt, **kwargs):
        self.prompts.append(prompt)
        if "ROOTHINKS_ANCHORED_JSON" not in prompt:
            return True, "純文字回答"
        return True, json.dumps({"interpretation": "只支持 300 K。", "points": [
            {"text": "範圍限於 300 K", "evidence": [{"ref": "E1", "quote": QUOTE}]},
            {"text": "已重現", "evidence": [{"ref": "E1", "quote": FAKE}]}],
            "uncertainty": {"level": "medium", "reasons": ["單一溫度"]}, "open_questions": ["其他溫度？"]})


def _write_paper(root, content=CONTENT):
    base = root / PID / "literature" / "papers" / PAPER / "05_interprets"
    (base / "fusion").mkdir(parents=True, exist_ok=True)
    (base / "fusion" / "full_text.json").write_text(json.dumps({"paper_id": PAPER, "content": content}), encoding="utf-8")
    (base / "full_text_trans.json").write_text(json.dumps({"paper_id": PAPER, "content": content}), encoding="utf-8")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    from app import create_app, db
    from app.models import MentorLink, Project, User, WorkspaceMember
    from app.core_pro.study import study_routes
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    bus = StubBus()
    monkeypatch.setattr(study_routes.tutor_engine, "bus", bus)
    monkeypatch.setattr(study_routes, "get_context_chain_service", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    ids = {}
    with app.app_context():
        db.create_all()
        db.session.add(Project(project_id=PID, name="Evidence", status="formal"))
        for name, role, system_role in [("owner", "owner", "user"), ("editor", "editor", "user"), ("viewer", "viewer", "user"),
                                        ("prof", "viewer", "mentor"), ("otherprof", None, "mentor"), ("outsider", None, "user")]:
            user = User(username=name, email=f"{name}@test.local", system_role=system_role)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
            if role:
                db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        db.session.add(MentorLink(mentor_id=ids["prof"], mentee_id=ids["owner"]))
        db.session.add(MentorLink(mentor_id=ids["otherprof"], mentee_id=ids["owner"]))
        db.session.commit()
    _write_paper(tmp_path)
    yield app, tmp_path, ids, bus
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def client_for(app, name):
    client = app.test_client()
    resp = client.post("/api/auth/login", json={"email": f"{name}@test.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def selection(**extra):
    return {"quote": QUOTE, "side": "original", "ref": "p1-b2", "source": "flowb_trans", **extra}


def notes_file(root):
    return root / PID / "study" / f"{PID}_evidence_notes.json"


def accept(client, base_rev=0, **note):
    body = {"paper_id": PAPER, "anchors": [selection()], "origin": {"type": "tutor", "mode": "explain"},
            "statements": [{"kind": "source_fact", "evidence": [0]},
                           {"kind": "ai_interpretation", "text": "只支持 300 K。", "evidence": [0]},
                           {"kind": "human_hypothesis", "text": "退火時間可能是混淆因素"}]}
    body.update(note)
    return client.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": base_rev, "accepted": True, "note": body})


def test_explain_mode_verifies_selection_and_rejects_fabricated_quotes(env):
    app, root, _, bus = env
    client = client_for(app, "owner")
    conv = client.post("/api/study/conversations/create", json={"pid": PID, "title": "新的對話"}).json
    conv_id = conv["conv_id"]
    resp = client.post("/api/study/chat", json={"pid": PID, "question": "這段的範圍？", "mode": "explain",
                                               "paper_id": PAPER, "selection": selection(), "conv_id": conv_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.json
    structured = data["structured"]
    assert structured["selection"]["status"] == "exact" and structured["selection"]["ref"] == "p1-b2"
    assert [p["support"] for p in structured["points"]] == ["source_quoted", "ai_inference"]
    assert structured["rejected_quotes"][0]["quote"] == FAKE
    assert FAKE not in json.dumps([p["evidence"] for p in structured["points"]])
    assert "E1 (page 1, contains the selection)" in bus.prompts[-1] and QUOTE in bus.prompts[-1]
    saved = json.loads((root / PID / "study" / "conversations" / f"{conv_id}.json").read_text(encoding="utf-8"))
    assert saved["messages"][-1]["structured"]["system_check"]["rejected_quotes"] == 1
    assert not notes_file(root).exists(), "AI 回答不得自動成為筆記"


def test_unlocatable_selection_is_refused_before_calling_the_model(env):
    app, _, _, bus = env
    client = client_for(app, "owner")
    resp = client.post("/api/study/chat", json={"pid": PID, "question": "?", "mode": "critique", "paper_id": PAPER,
                                               "selection": selection(quote="text that is not in the paper")})
    assert resp.status_code == 409 and resp.json["anchor"]["status"] == "not_found"
    assert bus.prompts == []
    assert client.post("/api/study/chat", json={"pid": PID, "question": "?", "mode": "summarise", "paper_id": PAPER,
                                                "selection": selection()}).status_code == 400


def test_plain_tutor_requests_are_unchanged(env):
    app, _, _, bus = env
    resp = client_for(app, "owner").post("/api/study/chat", json={"pid": PID, "question": "hi", "context": {"pid": PAPER}})
    assert resp.status_code == 200 and resp.json == {"ok": True, "saved": False, "answer": "純文字回答"}
    assert "ROOTHINKS_ANCHORED_JSON" not in bus.prompts[-1]


def test_note_is_written_only_after_explicit_acceptance(env):
    app, root, ids, _ = env
    client = client_for(app, "owner")
    body = {"pid": PID, "base_rev": 0, "note": {"paper_id": PAPER, "anchors": [selection()],
                                                "statements": [{"kind": "ai_interpretation", "text": "x"}]}}
    assert client.post("/api/study/evidence_notes/accept", json=body).status_code == 400
    assert client.post("/api/study/evidence_notes/accept", json={**body, "accepted": "yes"}).status_code == 400
    assert client.post("/api/study/evidence_notes/accept", json={**body, "accepted": True, "base_rev": None}).status_code == 428
    assert not notes_file(root).exists()
    resp = accept(client)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    note = resp.json["note"]
    assert [s["kind"] for s in note["statements"]] == ["source_fact", "ai_interpretation", "human_hypothesis"]
    assert note["statements"][0]["text"] == QUOTE and note["anchors"][0]["status"] == "exact"
    assert note["accepted_by"] == {"user_id": ids["owner"], "username": "owner"} and note["evidence_status"] == "verified"
    listed = client.get(f"/api/study/evidence_notes/{PID}").json
    assert listed["rev"] == 1 and listed["notes"][0]["note_id"] == note["note_id"]


@pytest.mark.parametrize("statements,anchors", [
    ([{"kind": "source_fact", "text": "The improvement was large at all temperatures.", "evidence": [0]}], None),
    ([{"kind": "source_fact", "evidence": [0]}], [selection(quote="not in the paper at all")]),
    ([{"kind": "source_fact", "evidence": [0]}], [selection(quote="改善只在 300 K", side="translation")]),
    ([{"kind": "source_fact", "evidence": []}], None),
    ([{"kind": "conclusion", "text": "AI says so"}], None),
    ([{"kind": "ai_interpretation", "text": "x", "evidence": [5]}], None),
])
def test_ai_text_cannot_be_promoted_to_source_fact(env, statements, anchors):
    app, root, _, _ = env
    extra = {"statements": statements}
    if anchors is not None:
        extra["anchors"] = anchors
    resp = accept(client_for(app, "owner"), **extra)
    assert resp.status_code == 400
    assert not notes_file(root).exists()


def test_stale_tab_cannot_overwrite_and_history_is_kept(env):
    app, root, _, _ = env
    client = client_for(app, "owner")
    nid = accept(client).json["note"]["note_id"]
    update = {"pid": PID, "note_id": nid, "statements": [{"kind": "human_hypothesis", "text": "改寫後"}]}
    assert client.post("/api/study/evidence_notes/update", json={**update, "base_rev": 1}).status_code == 200
    stale = client.post("/api/study/evidence_notes/update", json={**update, "base_rev": 1,
                                                                   "statements": [{"kind": "human_hypothesis", "text": "舊分頁"}]})
    assert stale.status_code == 409 and stale.json["conflict"] is True
    doc = json.loads(notes_file(root).read_text(encoding="utf-8"))
    assert doc["notes"][0]["statements"][0]["text"] == "改寫後" and doc["_rev"] == 2
    assert (root / PID / "study" / f"{PID}_evidence_notes.json.history" / "1.json").exists()
    assert client.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True,
                                                                 "note": {"statements": [{"kind": "human_hypothesis", "text": "x"}]}}).status_code == 409


def test_rerun_source_shows_version_change_or_missing_text(env):
    app, root, _, _ = env
    client = client_for(app, "owner")
    accept(client)
    changed = json.loads(json.dumps(CONTENT))
    changed[0]["blocks"].insert(0, {"type": "header", "content": "Journal header"})
    _write_paper(root, changed)
    anchor = client.get(f"/api/study/evidence_notes/{PID}?verify=1").json["notes"][0]["anchors"][0]
    assert anchor["status"] == "exact", "保存時的狀態不被改寫"
    assert anchor["current"] == {"status": "relocated", "version_changed": True, "ref": "p1-b3", "page": 1}
    changed[0]["blocks"][2]["content"] = "We report a bounded measurement for sample A."
    _write_paper(root, changed)
    anchor = client.get(f"/api/study/evidence_notes/{PID}?verify=1").json["notes"][0]["anchors"][0]
    assert anchor["current"]["status"] == "not_found"


def test_notes_save_and_export_without_professor_or_verified_evidence(env):
    app, root, _, _ = env
    client = client_for(app, "owner")
    (root / PID / "literature").rename(root / PID / "literature-gone")
    resp = accept(client, statements=[{"kind": "human_hypothesis", "text": "我的推論，尚無證據"}])
    assert resp.status_code == 200 and resp.json["note"]["evidence_status"] == "unverified"
    assert resp.json["note"]["anchors"][0]["status"] == "unavailable"
    manual = client.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 1, "accepted": True,
                                                                   "note": {"statements": [{"kind": "human_hypothesis", "text": "純手寫"}]}})
    assert manual.status_code == 200 and manual.json["note"]["anchors"] == []
    export = client.get(f"/api/study/evidence_notes/{PID}/export")
    assert export.status_code == 200 and export.mimetype == "text/markdown"
    text = export.get_data(as_text=True)
    assert "**我的假說**：我的推論，尚無證據" in text and "無已驗證證據" in text and "純手寫" in text


def test_team_comments_and_review_requests_use_existing_study_acl(env):
    app, root, _, _ = env
    owner = client_for(app, "owner")
    nid = accept(owner).json["note"]["note_id"]
    editor = client_for(app, "editor")
    assert editor.post("/api/study/evidence_notes/comment", json={"pid": PID, "note_id": nid, "body": "樣本數？"}).status_code == 200
    viewer = client_for(app, "viewer")
    assert viewer.get(f"/api/study/evidence_notes/{PID}").status_code == 200
    for url, body in [("comment", {"body": "x"}), ("review", {"action": "request"}),
                      ("update", {"base_rev": 1, "statements": [{"kind": "human_hypothesis", "text": "x"}]})]:
        assert viewer.post(f"/api/study/evidence_notes/{url}", json={"pid": PID, "note_id": nid, **body}).status_code == 403
    outsider = client_for(app, "outsider")
    assert outsider.get(f"/api/study/evidence_notes/{PID}").status_code == 403
    assert outsider.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True,
                                                                   "note": {"statements": []}}).status_code == 403
    assert owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "withdraw"}).status_code == 400
    assert owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "request",
                                                                "message": "請看範圍"}).status_code == 200
    assert owner.post("/api/study/evidence_notes/comment", json={"pid": PID, "note_id": "en_000000000000", "body": "x"}).status_code == 404
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert note["review"]["state"] == "requested" and [e["action"] for e in note["review_events"]] == ["request"]
    assert [c["author_role"] for c in note["comments"]] == ["member"]
    assert [s["kind"] for s in note["statements"]] == ["source_fact", "ai_interpretation", "human_hypothesis"]


def test_professor_sees_and_comments_only_on_requested_notes(env):
    app, root, ids, _ = env
    owner = client_for(app, "owner")
    first = accept(owner).json["note"]["note_id"]
    second = accept(owner, base_rev=1).json["note"]["note_id"]
    owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": second, "action": "request"})
    prof = client_for(app, "prof")
    url = f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}"
    listed = prof.get(url)
    assert listed.status_code == 200 and [n["note_id"] for n in listed.json["notes"]] == [second]
    assert prof.post(f"{url}/{second}/comments", json={"body": "請補其他溫度"}).status_code == 201
    assert prof.post(f"{url}/{first}/comments", json={"body": "偷看"}).status_code == 403
    assert prof.post(f"{url}/en_000000000000/comments", json={"body": "x"}).status_code == 403
    assert prof.post(f"{url}/{second}/comments", json={"body": " "}).status_code == 400
    other = client_for(app, "otherprof")  # 有 MentorLink 但不是專案成員
    assert other.get(url).status_code == 403
    assert other.post(f"{url}/{second}/comments", json={"body": "x"}).status_code == 403
    doc = json.loads(notes_file(root).read_text(encoding="utf-8"))
    by_id = {n["note_id"]: n for n in doc["notes"]}
    assert [c["author_role"] for c in by_id[second]["comments"]] == ["mentor"] and by_id[first]["comments"] == []
    assert by_id[second]["statements"] == by_id[first]["statements"], "教授留言不改陳述"
