# Roothinks source maintenance contract
# 目的：Manuscript 段落級出處（R4）的 API 行為，用真 create_app、session 登入、tmp 資料根。
#   - 已保存的段落可以連到 Study 證據筆記（保存陳述、引文、研究者判斷與修訂次數的快照）或標成自由寫作。
#   - 之後段落被改、筆記被改、原文被改，都只顯示「需要重新核對」，不擋寫作、保存或匯出。
#   - 只追加：撤回是追加一筆撤回紀錄；舊分頁（舊 base_rev）不能覆蓋。
#   - 權限：viewer 不能連結；外人讀不到；教授審閱身分看不到未請求審閱的筆記內容，也拿不到筆記選單。
#   - Study 端看得到「這則筆記被用在稿件哪一段」。
# 驗證：python -m pytest test/unit/test_paragraph_provenance.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID, PAPER, SECTION = "PROV-p", "paper1", "introduction"
QUOTE = "The improvement was observed only at 300 K under one calibration protocol."
PARA = "我們只在 300 K 觀察到改善，這個結論不能外推到其他溫度。"
OTHER_PARA = "第二段是研究者自己的推論。"


def _write_paper(root, text=QUOTE):
    base = root / PID / "literature" / "papers" / PAPER / "05_interprets"
    (base / "fusion").mkdir(parents=True, exist_ok=True)
    content = [{"page": 1, "blocks": [{"type": "title", "content": "T", "seq_id": "p1_title_1"},
                                      {"type": "body", "seq_id": "p1_body_2", "content": "Bounded result. " + text}]}]
    (base / "fusion" / "full_text.json").write_text(json.dumps({"paper_id": PAPER, "content": content}), encoding="utf-8")


def _save_section(html):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    return ManuscriptIO.save_block_version(PID, SECTION, "Intro", html, updated_by="owner")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("REVIEW_SCOPE_LEGACY_MENTORS", raising=False)
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    from app import create_app, db
    from app.models import Project, User, WorkspaceMember
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    with app.app_context():
        db.create_all()
        db.session.add(Project(project_id=PID, name="Prov", status="formal"))
        for name, role, system_role in [("owner", "owner", "user"), ("editor", "editor", "user"), ("viewer", "viewer", "user"),
                                        ("prof", None, "mentor"), ("outsider", None, "user")]:
            user = User(username=name, email=f"{name}@p.local", system_role=system_role)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            if role:
                db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        db.session.commit()
        _save_section(f"<p>{PARA}</p><p>{OTHER_PARA}</p>")
    _write_paper(tmp_path)
    owner = login(app, "owner")
    r = owner.post(f"/api/collaboration/project/{PID}/invitations", json={"email": "prof@p.local", "kind": "professor", "role": "viewer"})
    assert login(app, "prof").post(f"/api/collaboration/invitations/{r.json['invitation']['id']}/accept",
                                   json={"token": r.json["token"]}).status_code == 200
    note = {"paper_id": PAPER, "anchors": [{"quote": QUOTE, "side": "original", "ref": "p1-b2", "source": "flowa_fusion"}],
            "origin": {"type": "tutor", "mode": "explain", "uncertainty": {"level": "medium", "reasons": ["單一溫度"]}},
            "statements": [{"kind": "source_fact", "evidence": [0]},
                           {"kind": "ai_interpretation", "text": "只支持 300 K。", "evidence": [0]},
                           {"kind": "human_hypothesis", "text": "退火時間可能是混淆因素"}]}
    resp = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 0, "accepted": True, "note": note})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    yield app, tmp_path, owner, resp.json["note"]["note_id"]
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def login(app, name):
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": f"{name}@p.local", "password": "password123"}).status_code == 200
    return client


def link(client, base_rev, **body):
    return client.post("/manuscript/api/provenance/link", json={"pid": PID, "section": SECTION, "base_rev": base_rev, **body})


def listed(client):
    return client.get(f"/manuscript/api/provenance?pid={PID}&section={SECTION}").json


def test_link_note_and_free_writing_then_see_backlink(env):
    app, _, owner, nid = env
    resp = link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    snap = resp.json["link"]["note"]
    assert snap["revision_count"] == 0 and [s["kind"] for s in snap["statements"]] == ["source_fact", "ai_interpretation", "human_hypothesis"]
    assert snap["anchors"][0]["quote"] == QUOTE and snap["ai_uncertainty"]["level"] == "medium"
    free = link(owner, 1, kind="free_writing", paragraph_text=OTHER_PARA, reason="我的推論，尚無文獻")
    assert free.status_code == 201 and free.json["link"]["note"] is None
    data = listed(owner)
    assert data["rev"] == 2 and [l["issues"] for l in data["links"]] == [[], []]
    assert [n["note_id"] for n in data["notes"]] == [nid]
    used = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]["used_in"]
    assert used and used[0]["section"] == SECTION and used[0]["excerpt"].startswith("我們只在 300 K")


def test_unsaved_paragraph_and_stale_tab_are_refused(env):
    _, _, owner, nid = env
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text="還沒保存的句子").status_code == 409
    assert link(owner, 0, kind="evidence_note", note_id="en_000000000000", paragraph_text=PARA).status_code == 409
    assert link(owner, 0, kind="bogus", paragraph_text=PARA).status_code == 400
    assert link(owner, 0, kind="free_writing", paragraph_text=PARA).status_code == 201
    stale = link(owner, 0, kind="free_writing", paragraph_text=OTHER_PARA)
    assert stale.status_code == 409


def test_changes_are_flagged_but_never_block(env):
    app, root, owner, nid = env
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).status_code == 201
    # 1. 筆記被改寫
    upd = owner.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 1, "note_id": nid,
                                                               "statements": [{"kind": "human_hypothesis", "text": "改寫後的假說"}]})
    assert upd.status_code == 200, upd.get_data(as_text=True)
    assert listed(owner)["links"][0]["issues"] == ["note_revised"]
    # 2. 原文重跑後文字不同
    _write_paper(root, text="An entirely different sentence now.")
    assert set(listed(owner)["links"][0]["issues"]) == {"note_revised", "source_changed"}
    # 3. 段落被改寫並保存成新版本：出處留著，但標示需要重新核對；寫作與保存不受影響
    with app.app_context():
        _save_section("<p>改寫後的段落。</p>")
    item = listed(owner)["links"][0]
    assert "paragraph_changed" in item["issues"] and item["s_ver"] != item["latest_s_ver"]
    assert all(label for label in item["issue_labels"])
    export = owner.get(f"/manuscript/api/provenance/export?pid={PID}")
    text = export.get_data(as_text=True)
    assert export.status_code == 200 and "需要重新核對" in text and "不代表支持" in text and "研究者假說" in text


def test_retract_is_append_only(env):
    _, root, owner, nid = env
    lid = link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).json["link"]["link_id"]
    resp = owner.post("/manuscript/api/provenance/retract", json={"pid": PID, "link_id": lid, "base_rev": 1, "reason": "連錯段"})
    assert resp.status_code == 200 and resp.json["link"]["retracted"]["reason"] == "連錯段"
    assert owner.post("/manuscript/api/provenance/retract", json={"pid": PID, "link_id": lid, "base_rev": 2}).status_code == 409
    item = listed(owner)["links"][0]
    assert item["retracted"] and item["issues"] == [] and item["paragraph_text"] == PARA
    assert owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]["used_in"] == []
    assert (root / PID / "manuscript" / "provenance" / "paragraph_links.json.history" / "1.json").exists()


def test_permissions_and_review_scope(env):
    app, _, owner, nid = env
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).status_code == 201
    viewer, prof, outsider, editor = (login(app, n) for n in ("viewer", "prof", "outsider", "editor"))
    assert link(viewer, 1, kind="free_writing", paragraph_text=OTHER_PARA).status_code == 403
    assert outsider.get(f"/manuscript/api/provenance?pid={PID}&section={SECTION}").status_code == 403
    assert link(editor, 1, kind="free_writing", paragraph_text=OTHER_PARA).status_code == 201
    view = listed(viewer)
    assert view["notes"] == [] and view["links"][0]["note"]["note_id"] == nid
    hidden = listed(prof)
    assert hidden["notes"] == [] and hidden["links"][0]["note"] == {"hidden": True}
    assert "退火時間" not in prof.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)
    owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "request"})
    shown = listed(prof)["links"][0]["note"]
    assert shown.get("note_id") == nid and not shown.get("hidden")
