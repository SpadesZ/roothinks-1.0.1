# Roothinks source maintenance contract
# 目的：R3 跨模組權限修補的對抗性回歸測試。
#   - 全站執行日誌只給系統管理員；Flow B 診斷只回傳本專案（字界比對）日誌行、不給伺服器路徑。
#   - 教授審閱身分不能讀 Literature／PAQ 的 AI 對話；團隊 viewer、舊導師、owner 照常。
#   - 舊導師相容選項：預設 keep 不撤銷；明確設 review_scope 才收窄；團隊邀請的 viewer 永不收窄；報告只讀。
#   - 審閱版本不含舊草稿與成員建議；過時建議必須明確確認才可接受。
#   - 整個權限矩陣（自動列舉所有 GET 路由）的結論被鎖定。
# 驗證：python -m pytest test/unit/test_acl_cross_module.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID, OTHER, PAPER = "XMOD-p", "XMOD12-p", "paper1"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("REVIEW_SCOPE_LEGACY_MENTORS", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    log = tmp_path / "runtime.log"
    monkeypatch.setenv("LECTURE_SYSTEM_LOG_FILE", str(log))
    from app import create_app, db
    from app.models import MentorLink, Paper, Project, User, WorkspaceMember
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    from app.core_pro.literature import literature_routes
    monkeypatch.setattr(literature_routes, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(literature_routes, "literature_chat_store_service", None)
    ids = {}
    with app.app_context():
        db.create_all()
        for pid in (PID, OTHER):
            db.session.add(Project(project_id=pid, name=pid, status="formal"))
        for name, role in (("owner", "user"), ("editor", "user"), ("prof", "mentor"), ("legacy", "mentor"),
                           ("teamviewer", "user"), ("mentorteam", "mentor"), ("outsider", "user"), ("admin", "admin")):
            user = User(username=name, email=f"{name}@x.local", system_role=role)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
        db.session.add_all([WorkspaceMember(user_id=ids["owner"], pid=PID, role="owner"),
                            WorkspaceMember(user_id=ids["editor"], pid=PID, role="editor"),
                            WorkspaceMember(user_id=ids["legacy"], pid=PID, role="viewer"),
                            WorkspaceMember(user_id=ids["outsider"], pid=OTHER, role="owner"),
                            MentorLink(mentor_id=ids["legacy"], mentee_id=ids["owner"]),
                            MentorLink(mentor_id=ids["mentorteam"], mentee_id=ids["owner"]),
                            Paper(paper_id=PAPER, pid=PID, title="p", interpretation_status=Paper.STATUS_GOLD_READY)])
        db.session.commit()
    owner = login(app, "owner")
    for who, kind in (("prof", "professor"), ("teamviewer", "team"), ("mentorteam", "team")):
        r = owner.post(f"/api/collaboration/project/{PID}/invitations", json={"email": f"{who}@x.local", "kind": kind, "role": "viewer"})
        assert login(app, who).post(f"/api/collaboration/invitations/{r.json['invitation']['id']}/accept",
                                    json={"token": r.json["token"]}).status_code == 200
    for sub, prefix in (("literature", "lit-chat_"), ("paq", "paq-chat_")):
        chat = tmp_path / PID / sub / "chat"
        chat.mkdir(parents=True)
        (chat / f"{prefix}260101-000000.json").write_text(json.dumps([{"role": "user", "content": f"PRIVATE_{sub.upper()}_CHAT"}]),
                                                          encoding="utf-8")
    log.write_text(f"INFO [Flow B] run_translation pid={PID} OWN_LINE\n"
                   f"INFO [Flow B] run_translation pid={OTHER} OTHER_PROJECT_LINE\n"
                   f"INFO [Flow B] run_translation pid=ELSEWHERE-p ELSEWHERE_LINE\n", encoding="utf-8")
    yield app, tmp_path, ids, owner
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def login(app, name):
    client = app.test_client()
    resp = client.post("/api/auth/login", json={"email": f"{name}@x.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def test_runtime_log_tail_is_admin_only(env):
    app, _, _, owner = env
    for name in ("owner", "teamviewer", "outsider"):
        resp = login(app, name).get("/api/literature/debug/runtime_log_tail")
        assert resp.status_code == 403 and "OTHER_PROJECT_LINE" not in resp.get_data(as_text=True), name
    admin = login(app, "admin").get("/api/literature/debug/runtime_log_tail")
    assert admin.status_code == 200 and "OTHER_PROJECT_LINE" in admin.get_data(as_text=True)


def test_flowb_probe_returns_only_this_projects_log_lines(env):
    app, _, _, _ = env
    resp = login(app, "teamviewer").get(f"/api/literature/debug/flowb_probe?pid={PID}&paper_id={PAPER}")
    assert resp.status_code == 200
    debug = resp.json["debug"]
    hits = " ".join(debug["runtime_log"]["tail_hits"])
    assert "OWN_LINE" in hits
    assert "OTHER_PROJECT_LINE" not in hits, "XMOD12-p 不得因前綴相同而命中 XMOD-p"
    assert "ELSEWHERE_LINE" not in hits and "path" not in debug["runtime_log"]
    assert login(app, "outsider").get(f"/api/literature/debug/flowb_probe?pid={PID}&paper_id={PAPER}").status_code == 403


@pytest.mark.parametrize("url,secret", [
    (f"/api/literature/chat/history?pid={PID}", "PRIVATE_LITERATURE_CHAT"),
    (f"/api/paq/chat_history/{PID}", "PRIVATE_PAQ_CHAT"),
])
def test_review_scope_hides_ai_conversations_in_other_modules(env, url, secret):
    app, _, _, _ = env
    prof = login(app, "prof").get(url)
    assert prof.status_code == 403 and prof.json["review_scope"] is True and secret not in prof.get_data(as_text=True)
    for name in ("owner", "teamviewer", "legacy", "mentorteam"):
        resp = login(app, name).get(url)
        assert resp.status_code == 200 and secret in resp.get_data(as_text=True), name
    assert login(app, "outsider").get(url).status_code == 403


def test_legacy_mentor_policy_is_explicit_and_never_touches_team_invitations(env, monkeypatch):
    app, tmp_path, ids, owner = env
    url = f"/api/paq/chat_history/{PID}"
    assert login(app, "legacy").get(url).status_code == 200, "預設 keep：不默默撤銷歷史授權"
    from scripts import review_scope_legacy_report as rep
    report = rep.report(app)
    assert report["current_policy"] == "keep"
    assert [(r["user_id"], r["pid"]) for r in report["affected_if_review_scope"]] == [(ids["legacy"], PID)]
    app.config["REVIEW_SCOPE_LEGACY_MENTORS"] = "review_scope"
    assert login(app, "legacy").get(url).status_code == 403
    assert login(app, "legacy").get(f"/api/study/notes/load/{PID}").status_code == 403
    assert login(app, "mentorteam").get(url).status_code == 200, "以團隊邀請加入的 viewer 即使有 MentorLink 也不收窄"
    assert login(app, "teamviewer").get(url).status_code == 200
    app.config["REVIEW_SCOPE_LEGACY_MENTORS"] = "something-else"
    assert login(app, "legacy").get(url).status_code == 200, "不合法設定視為 keep"
    with app.app_context():
        from app.models import WorkspaceMember
        assert WorkspaceMember.query.filter_by(user_id=ids["legacy"], pid=PID).first().role == "viewer", "報告與設定都不改資料"


def _accept_note(client, root):
    fusion = root / PID / "literature" / "papers" / PAPER / "05_interprets" / "fusion"
    fusion.mkdir(parents=True, exist_ok=True)
    quote = "The improvement was observed only at 300 K under one calibration protocol."
    (fusion / "full_text.json").write_text(json.dumps({"paper_id": PAPER, "content": [{"page": 1, "blocks": [
        {"type": "body", "content": quote}]}]}), encoding="utf-8")
    note = {"paper_id": PAPER, "anchors": [{"quote": quote, "side": "original", "ref": "p1-b1", "source": "flowa_fusion"}],
            "statements": [{"kind": "source_fact", "evidence": [0]}, {"kind": "human_hypothesis", "text": "DRAFT_ONE"}]}
    resp = client.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True, "note": note})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.json["note"]["note_id"]


def test_review_copy_excludes_old_drafts_and_unresolved_suggestions(env):
    app, root, ids, owner = env
    nid = _accept_note(owner, root)
    login(app, "editor").post("/api/study/evidence_notes/suggest", json={"pid": PID, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "EDITOR_SUGGESTION_TEXT"}]})
    assert owner.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 2, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "CURRENT_VERSION"}]}).status_code == 200
    assert owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "request"}).status_code == 200
    for url in (f"/api/study/evidence_notes/{PID}", f"/api/study/evidence_notes/{PID}/export",
                f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}"):
        body = login(app, "prof").get(url).get_data(as_text=True)
        assert "CURRENT_VERSION" in body, url
        assert "DRAFT_ONE" not in body and "EDITOR_SUGGESTION_TEXT" not in body, url
    own = owner.get(f"/api/study/evidence_notes/{PID}").get_data(as_text=True)
    assert "DRAFT_ONE" in own and "EDITOR_SUGGESTION_TEXT" in own, "作者自己仍看得到完整歷史"


def test_stale_suggestion_needs_explicit_confirmation(env):
    app, root, _, owner = env
    nid = _accept_note(owner, root)
    sid = login(app, "editor").post("/api/study/evidence_notes/suggest", json={"pid": PID, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "OLD_BASED_SUGGESTION"}]}).json["suggestion"]["suggestion_id"]
    assert owner.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 2, "note_id": nid, "statements": [
        {"kind": "human_hypothesis", "text": "AUTHOR_NEWER_EDIT"}]}).status_code == 200
    stale = owner.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 3, "note_id": nid,
                                                                    "suggestion_id": sid, "decision": "accept"})
    assert stale.status_code == 409 and stale.json["stale_suggestion"] is True
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert note["statements"][-1]["text"] == "AUTHOR_NEWER_EDIT" and note["suggestions"][0]["status"] == "open"
    ok = owner.post("/api/study/evidence_notes/suggestion", json={"pid": PID, "base_rev": 3, "note_id": nid,
                                                                 "suggestion_id": sid, "decision": "accept", "allow_stale": True})
    assert ok.status_code == 200
    note = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]
    assert note["statements"][-1]["text"] == "OLD_BASED_SUGGESTION"
    assert note["revisions"][-1]["previous_statements"][-1]["text"] == "AUTHOR_NEWER_EDIT", "被蓋掉的較新內容保留在修訂紀錄"


def test_route_matrix_conclusions_are_locked():
    """自動列舉全部 GET 路由、六種角色實測；唯一剩下的教授可讀項目是文獻庫閱讀筆記（Owner 決定事項）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "test" / "integration_e2e"))
    import acl_route_matrix
    result = acl_route_matrix.run()
    assert result["routes"] >= 80
    assert [(f["role"], f["leaks"]) for f in result["findings"]] == [("prof", ["literature_reading_note"])]
    assert not [r for r in result["rows"] for v in r["roles"].values() if v["status"] == "exception"]
