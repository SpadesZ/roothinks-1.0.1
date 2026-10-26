# Roothinks source maintenance contract
# 目的：Study R2 驗收標準 B1–B12。教授審閱範圍（用真的教授／團隊邀請流程建立身分）、
#       字面相符與主張支持分開、嚴格引文比對、同段多處不默選、筆記所有權與可追溯的建議。
# 邊界：真 create_app、session 登入、tmp SQLite 與 tmp 資料根；不呼叫任何模型。
# 驗證：python -m pytest test/unit/test_study_r2.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core_pro.study import study_anchors as anchors

PID, OTHER, PAPER = "REVW-p", "OTHR-p", "paper1"
QUOTE = "The improvement was observed only at 300 K under one calibration protocol."
CONTENT = [{"page": 1, "blocks": [
    {"type": "body", "seq_id": "p1_body_1", "content": "We report sample A. " + QUOTE},
    {"type": "body", "seq_id": "p1_body_2", "content": "Twice: the effect was small. Again the effect was small."},
    {"type": "body", "seq_id": "p1_body_3", "content": "The value was 10² m and H₂O was used. This improvement is bounded."},
]}]
META = {"pid": PID, "paper_id": PAPER, "source": "flowa_fusion", "source_sha256": "x" * 64}
PRIVATE = {"free": "PRIVATE_FREE_NOTE", "conv": "PRIVATE_CONVERSATION", "matrix": "PRIVATE_MATRIX", "evidence": "PRIVATE_EVIDENCE"}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    from app import create_app, db
    from app.models import Project, User, WorkspaceMember
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    ids = {}
    with app.app_context():
        db.create_all()
        for pid in (PID, OTHER):
            db.session.add(Project(project_id=pid, name=pid, status="formal"))
        for name in ("owner", "editor", "prof", "teamviewer", "outsider", "otherowner", "legacyprof"):
            user = User(username=name, email=f"{name}@r2.local", system_role="mentor" if "prof" in name else "user")
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
        db.session.add_all([WorkspaceMember(user_id=ids["owner"], pid=PID, role="owner"),
                            WorkspaceMember(user_id=ids["editor"], pid=PID, role="editor"),
                            WorkspaceMember(user_id=ids["otherowner"], pid=OTHER, role="owner"),
                            # 舊關係：沒有教授邀請、直接被加成 viewer 的導師（相容性：維持舊行為）
                            WorkspaceMember(user_id=ids["legacyprof"], pid=PID, role="viewer")])
        db.session.commit()
    for pid in (PID, OTHER):
        base = tmp_path / pid / "literature" / "papers" / PAPER / "05_interprets" / "fusion"
        base.mkdir(parents=True)
        (base / "full_text.json").write_text(json.dumps({"paper_id": PAPER, "content": CONTENT}), encoding="utf-8")
    owner = login(app, "owner")
    invite(app, owner, "prof", "professor")
    invite(app, owner, "teamviewer", "team")
    yield app, tmp_path, ids, owner
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def login(app, name):
    client = app.test_client()
    resp = client.post("/api/auth/login", json={"email": f"{name}@r2.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def invite(app, owner, who, kind):
    resp = owner.post(f"/api/collaboration/project/{PID}/invitations", json={"email": f"{who}@r2.local", "kind": kind, "role": "viewer"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    accepted = login(app, who).post(f"/api/collaboration/invitations/{resp.json['invitation']['id']}/accept",
                                    json={"token": resp.json["token"]})
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    return resp.json["invitation"]["id"]


def selection(**extra):
    return {"quote": QUOTE, "side": "original", "ref": "p1-b1", "source": "flowa_fusion", **extra}


def accept(client, base_rev, text="假說", **note):
    body = {"paper_id": PAPER, "anchors": [selection()], "statements": [
        {"kind": "source_fact", "evidence": [0]},
        {"kind": "ai_interpretation", "text": "只在 300 K。", "evidence": [0]},
        {"kind": "human_hypothesis", "text": text}], **note}
    resp = client.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": base_rev, "accepted": True, "note": body})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.json["note"]["note_id"]


def seed_private(root, owner):
    owner.post("/api/study/notes/save", json={"pid": PID, "notes": PRIVATE["free"], "base_rev": 0})
    conv = owner.post("/api/study/conversations/create", json={"pid": PID, "title": "x"}).json["conv_id"]
    path = root / PID / "study" / "conversations" / f"{conv}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["messages"].append({"role": "user", "content": PRIVATE["conv"]})
    path.write_text(json.dumps(doc), encoding="utf-8")
    (root / PID / "study" / "latest_matrix.json").write_text(json.dumps({"matrix_id": "m1", "rows": [PRIVATE["matrix"]]}), encoding="utf-8")
    private_note = accept(owner, 0, PRIVATE["evidence"])
    shared_note = accept(owner, 1, "SHARED_NOTE")
    assert owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": shared_note, "action": "request"}).status_code == 200
    return conv, private_note, shared_note


def private_urls(conv):
    return [f"/api/study/notes/load/{PID}", f"/api/study/{PID}/conversations", f"/api/study/conversation/{conv}?pid={PID}",
            f"/api/study/{PID}/matrix/latest", f"/api/study/{PID}/matrix/history"]


# ---------------- R2-A 審閱範圍 ----------------

def test_b1_professor_review_scope_cannot_read_private_study_work(env):
    app, root, _, owner = env
    conv, *_ = seed_private(root, owner)
    prof = login(app, "prof")
    for url in private_urls(conv):
        resp = prof.get(url)
        assert resp.status_code == 403 and resp.json.get("review_scope") is True, url
        assert not any(v in resp.get_data(as_text=True) for v in PRIVATE.values()), url


def test_b2_professor_sees_only_review_requested_notes_in_list_and_export(env):
    app, root, _, owner = env
    _, private_note, shared_note = seed_private(root, owner)
    prof = login(app, "prof")
    listed = prof.get(f"/api/study/evidence_notes/{PID}?verify=1")
    assert listed.status_code == 200 and listed.json["review_scope"] is True
    assert [n["note_id"] for n in listed.json["notes"]] == [shared_note]
    exported = prof.get(f"/api/study/evidence_notes/{PID}/export").get_data(as_text=True)
    assert "SHARED_NOTE" in exported and PRIVATE["evidence"] not in exported


def test_b3_professor_keeps_paper_reading_and_mentor_review(env):
    app, root, ids, owner = env
    _, _, shared_note = seed_private(root, owner)
    prof = login(app, "prof")
    assert prof.get(f"/api/study/paper_list/{PID}").status_code == 200
    assert prof.get(f"/api/study/get_fulltext/{PID}/{PAPER}").json["ok"] is True
    assert prof.get(f"/study/project/{PID}").status_code == 200
    reviews = prof.get(f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}")
    assert reviews.status_code == 200 and [n["note_id"] for n in reviews.json["notes"]] == [shared_note]
    assert prof.post(f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}/{shared_note}/comments",
                     json={"body": "請補其他溫度"}).status_code == 201
    for path in ("notes/save", "evidence_notes/comment"):  # viewer 仍不能用 Study 寫
        assert prof.post(f"/api/study/{path}", json={"pid": PID, "note_id": shared_note, "notes": "x", "base_rev": 1,
                                                      "body": "x"}).status_code == 403


def test_b3_review_mode_flag_reaches_the_page(env):
    app, _, _, _ = env
    page = login(app, "prof").get(f"/study/project/{PID}").get_data(as_text=True)
    assert "window.STUDY_REVIEW_SCOPE = true" in page
    assert "window.STUDY_REVIEW_SCOPE = false" in login(app, "teamviewer").get(f"/study/project/{PID}").get_data(as_text=True)


def test_b4_mentor_api_withholds_private_notes_and_conversations(env):
    app, root, ids, owner = env
    seed_private(root, owner)
    detail = login(app, "prof").get(f"/api/mentor/mentees/{ids['owner']}?notes=1")
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert PRIVATE["free"] not in body and PRIVATE["conv"] not in body
    [entry] = [n for n in detail.json["study_notes"] if n["pid"] == PID]
    assert entry["withheld"] == "review_scope" and entry["review_requested_evidence_notes"] == 1


@pytest.mark.parametrize("who", ["teamviewer", "legacyprof", "editor"])
def test_b5_other_members_keep_existing_read_access(env, who):
    app, root, _, owner = env
    conv, private_note, _ = seed_private(root, owner)
    client = login(app, who)
    for url in private_urls(conv):
        assert client.get(url).status_code == 200, url
    listed = client.get(f"/api/study/evidence_notes/{PID}").json
    assert listed["review_scope"] is False and len(listed["notes"]) == 2


def test_b6_revoked_cross_project_and_outsider_are_refused(env):
    app, root, ids, owner = env
    seed_private(root, owner)
    invitations = owner.get(f"/api/collaboration/project/{PID}/invitations").json["invitations"]
    prof_inv = next(i for i in invitations if i["kind"] == "professor")
    assert owner.post(f"/api/collaboration/invitations/{prof_inv['id']}/revoke", json={"reason": "結束指導"}).status_code == 200
    prof = login(app, "prof")
    for url in (f"/api/study/get_fulltext/{PID}/{PAPER}", f"/api/study/evidence_notes/{PID}",
                f"/api/study/evidence_notes/{PID}/export", f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}"):
        assert prof.get(url).status_code == 403, url
    assert login(app, "teamviewer").get(f"/api/study/evidence_notes/{OTHER}").status_code == 403
    outsider = login(app, "outsider")
    assert outsider.get(f"/api/study/evidence_notes/{PID}").status_code == 403
    assert outsider.get(f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}").status_code == 403


def test_b6_owner_promoting_professor_to_editor_restores_full_access(env):
    app, root, ids, owner = env
    seed_private(root, owner)
    from app import db
    from app.models import WorkspaceMember
    with app.app_context():
        row = WorkspaceMember.query.filter_by(user_id=ids["prof"], pid=PID).first()
        row.role = "editor"
        db.session.commit()
    assert login(app, "prof").get(f"/api/study/notes/load/{PID}").status_code == 200


# ---------------- R2-B 證據語意 ----------------

@pytest.mark.parametrize("text,quote", [
    ("The value was 10² m.", "The value was 102 m"),
    ("H₂O was used.", "H2O was used"),
    ("This improvement is bounded.", "prove"),
    ("This improvement is bounded.", "improvement is bound"),
])
def test_b7_strict_matching_does_not_change_scientific_meaning(text, quote):
    assert anchors.locate(text, quote) == []


@pytest.mark.parametrize("text,quote", [
    ("He said “the  value\nwas small”.", '"the value was small"'),
    ("The ﬁrst run was stable.", "first run was stable"),
    ("A well‐known effect.", "well-known effect"),
])
def test_b7_typographic_variants_still_match(text, quote):
    assert len(anchors.locate(text, quote)) == 1


def test_b7_short_model_quotes_cannot_be_attributed():
    sel = anchors.resolve(CONTENT, selection(), META)
    window = anchors.evidence_window(CONTENT, sel)
    out = anchors.verify_structured(json.dumps({"interpretation": "x", "points": [
        {"text": "a", "evidence": [{"ref": "E1", "quote": "300 K"}]}]}), window, sel, META)
    assert out["points"][0]["evidence"] == [] and out["rejected_quotes"][0]["reason"].startswith("太短")


def test_b8_duplicate_quote_in_one_paragraph_is_not_silently_resolved():
    amb = anchors.resolve(CONTENT, {"quote": "the effect was small", "ref": "p1-b2"}, META)
    assert amb["status"] == "ambiguous_in_block" and amb["ref"] == "p1-b2" and amb["occurrences"] == 2
    assert "start" not in amb and "end" not in amb
    picked = anchors.resolve(CONTENT, {"quote": "the effect was small", "ref": "p1-b2", "occurrence": 1, "occurrences_total": 2}, META)
    text = CONTENT[0]["blocks"][1]["content"]
    assert picked["status"] == "exact" and picked["disambiguated_by"] == "occurrence" and picked["start"] == text.rindex("the effect")
    mismatch = anchors.resolve(CONTENT, {"quote": "the effect was small", "ref": "p1-b2", "occurrence": 1, "occurrences_total": 3}, META)
    assert mismatch["status"] == "ambiguous_in_block", "前端與伺服器處數不一致時不能猜"
    ctx = anchors.resolve(CONTENT, {"quote": "the effect was small", "ref": "p1-b2", "prefix": "Again "}, META)
    assert ctx["status"] == "exact" and ctx["disambiguated_by"] == "context"


def test_b8_model_quote_repeated_in_paragraph_keeps_paragraph_only():
    sel = anchors.resolve(CONTENT, {"quote": "Twice:", "ref": "p1-b2"}, META)
    window = anchors.evidence_window(CONTENT, sel)
    out = anchors.verify_structured(json.dumps({"interpretation": "x", "points": [
        {"text": "a", "evidence": [{"ref": "E1", "quote": "the effect was small."}]}]}), window, sel, META)
    [ev] = out["points"][0]["evidence"]
    assert ev["status"] == "ambiguous_in_block" and "start" not in ev


def test_b9_no_verified_vocabulary_and_legacy_notes_are_readable(env):
    app, root, _, owner = env
    assert not hasattr(anchors, "VERIFIED")
    note_id = accept(owner, 0)
    listed = owner.get(f"/api/study/evidence_notes/{PID}").get_data(as_text=True)
    assert "verified" not in listed.lower()
    # R1 舊檔：evidence_status=verified、system_check.verified_quotes
    path = root / PID / "study" / f"{PID}_evidence_notes.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    note = doc["notes"][0]
    note.pop("quote_match"), note.pop("suggestions"), note.pop("revisions")
    note["evidence_status"] = "verified"
    note["origin"]["system_check"] = {"verified_quotes": 1, "rejected_quotes": 0}
    for st in note["statements"]:
        st.pop("claim_support", None)
    path.write_text(json.dumps(doc), encoding="utf-8")
    legacy = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert legacy["note_id"] == note_id and legacy["quote_match"] == "found" and "evidence_status" not in legacy
    assert legacy["origin"]["system_check"] == {"quotes_found": 1, "rejected_quotes": 0}
    assert {s.get("claim_support") for s in legacy["statements"] if s["kind"] != "source_fact"} == {"not_assessed"}


def test_b10_claim_support_is_set_only_by_the_researcher_and_needs_evidence(env):
    app, _, ids, owner = env
    bad = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True, "note": {
        "paper_id": PAPER, "anchors": [selection()], "statements": [{"kind": "human_hypothesis", "text": "x", "claim_support": "supports"}]}})
    assert bad.status_code == 400
    invalid = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True, "note": {
        "paper_id": PAPER, "anchors": [selection()], "statements": [{"kind": "ai_interpretation", "text": "x", "evidence": [0],
                                                                     "claim_support": "verified"}]}})
    assert invalid.status_code == 400
    ok = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True, "note": {
        "paper_id": PAPER, "anchors": [selection()], "statements": [{"kind": "ai_interpretation", "text": "x", "evidence": [0],
                                                                     "claim_support": "partial"}]}})
    st = ok.json["note"]["statements"][0]
    assert st["claim_support"] == "partial" and st["claim_support_by"] == {"user_id": ids["owner"], "username": "owner"}


def test_b10_translation_derived_source_fact_is_labelled_as_paragraph():
    content = [{"page": 1, "blocks": [{"type": "body", "content": QUOTE, "content_zh": "改善只在 300 K 觀察到。"}]}]
    sel = anchors.resolve(content, {"quote": "改善只在 300 K", "side": "translation", "ref": "p1-b1"}, META)
    derived = anchors.original_for(content, sel, META)
    assert derived["match_kind"] == "paragraph_from_translation"
    from app.core_pro.study import study_evidence_notes as notes
    [st] = notes.statements([{"kind": "source_fact", "evidence": [0]}], [derived])
    assert st["quote_scope"] == "paragraph_from_translation"


# ---------------- R2-C 所有權 ----------------

def test_b11_non_author_cannot_overwrite_but_can_suggest(env):
    app, root, ids, owner = env
    nid = accept(owner, 0, "作者的假說")
    editor = login(app, "editor")
    overwrite = editor.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 1, "note_id": nid,
                                                                      "statements": [{"kind": "human_hypothesis", "text": "編輯覆寫"}]})
    assert overwrite.status_code == 403
    suggestion = editor.post("/api/study/evidence_notes/suggest", json={"pid": PID, "note_id": nid, "reason": "範圍太寬",
                                                                       "statements": [{"kind": "source_fact", "evidence": [0]},
                                                                                      {"kind": "human_hypothesis", "text": "只限樣本 A"}]})
    assert suggestion.status_code == 200
    sid = suggestion.json["suggestion"]["suggestion_id"]
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert [s["text"] for s in note["statements"]][-1] == "作者的假說", "建議不會改動作者的內容"
    assert editor.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 2, "note_id": nid,
                                                                     "suggestion_id": sid, "decision": "accept"}).status_code == 403
    assert owner.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 1, "note_id": nid,
                                                                    "suggestion_id": sid, "decision": "accept"}).status_code == 409
    done = owner.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 2, "note_id": nid,
                                                                   "suggestion_id": sid, "decision": "accept"})
    assert done.status_code == 200
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert [s["text"] for s in note["statements"]][-1] == "只限樣本 A"
    assert note["statements"][-1]["added_by"]["username"] == "editor"
    [rev] = note["revisions"]
    assert rev["action"] == "accepted_suggestion" and rev["suggested_by"]["username"] == "editor"
    assert rev["previous_statements"][-1]["text"] == "作者的假說", "被取代的內容保留在修訂紀錄"
    assert note["suggestions"][0]["status"] == "accepted" and note["suggestions"][0]["resolved_by"]["username"] == "owner"
    again = owner.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 3, "note_id": nid,
                                                                    "suggestion_id": sid, "decision": "decline"})
    assert again.status_code == 400


def test_b11_declined_suggestion_leaves_note_unchanged_and_author_edits_keep_history(env):
    app, _, _, owner = env
    nid = accept(owner, 0, "原假說")
    editor = login(app, "editor")
    sid = editor.post("/api/study/evidence_notes/suggest", json={"pid": PID, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "改掉"}]}).json["suggestion"]["suggestion_id"]
    assert owner.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 2, "note_id": nid, "suggestion_id": sid,
                                                                    "decision": "decline", "reason": "證據不足"}).status_code == 200
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert note["statements"][-1]["text"] == "原假說" and note["suggestions"][0]["resolution_reason"] == "證據不足"
    assert owner.post("/api/study/evidence_notes/suggest", json={"pid": PID, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "x"}]}).status_code == 400, "作者請直接修改"
    assert owner.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 3, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "作者改寫"}]}).status_code == 200
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert note["revisions"][-1]["action"] == "edited" and note["revisions"][-1]["previous_statements"][-1]["text"] == "原假說"


def test_b12_only_the_author_requests_review(env):
    app, _, _, owner = env
    nid = accept(owner, 0)
    editor = login(app, "editor")
    assert editor.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "request"}).status_code == 403
    assert editor.post("/api/study/evidence_notes/comment", json={"pid": PID, "note_id": nid, "body": "留言仍可"}).status_code == 200
    assert owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "request"}).status_code == 200
