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


def _add_coauthor(app, section):
    from app import db
    from app.models import ChapterAssignment, User, WorkspaceMember
    with app.app_context():
        u = User(username="coauthor", email="coauthor@p.local", system_role="user")
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role="coauthor"))
        db.session.add(ChapterAssignment(pid=PID, section_key=section, user_id=u.id))
        db.session.commit()


def test_section_scoped_coauthor_cannot_read_study_note_snapshots(env):
    """F1：限定編輯（coauthor）進不了 Study；段落出處不得變成讀 Study 筆記的側門。寫作與自由寫作照常。"""
    app, _, owner, nid = env
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).status_code == 201
    _add_coauthor(app, SECTION)
    co = login(app, "coauthor")
    assert co.get(f"/api/study/evidence_notes/{PID}").status_code == 403, "前提：coauthor 本來就不能進 Study"
    resp = co.get(f"/manuscript/api/provenance?pid={PID}&section={SECTION}")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "退火時間" not in body and "只支持 300 K" not in body and "improvement was observed" not in body
    item = resp.json["links"][0]
    assert item["note"] == {"hidden": True} and item["paragraph_text"] == PARA and resp.json["notes"] == []
    assert link(co, 1, kind="free_writing", paragraph_text=OTHER_PARA).status_code == 201, "仍可在自己的章節寫作與標記"
    assert link(co, 2, kind="evidence_note", note_id=nid, paragraph_text=OTHER_PARA).status_code == 403
    assert co.get(f"/manuscript/api/provenance/export?pid={PID}").status_code == 403


def test_coverage_shows_each_paragraph_state_without_blocking(env):
    """R5：本章每一段的出處狀態；改寫後舊連結算 orphaned，新段落是尚未標記。"""
    app, root, owner, nid = env
    cov = listed(owner)["coverage"]
    assert cov["total"] == 2 and cov["counts"] == {"evidence_note": 0, "needs_recheck": 0, "free_writing": 0, "unmarked": 2}
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text="只在 300 K 觀察到改善").status_code == 201, "選取一句也算該段"
    assert link(owner, 1, kind="free_writing", paragraph_text=OTHER_PARA).status_code == 201
    cov = listed(owner)["coverage"]
    assert [p["state"] for p in cov["paragraphs"]] == ["evidence_note", "free_writing"] and cov["orphaned_links"] == 0
    owner.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 1, "note_id": nid,
                                                         "statements": [{"kind": "human_hypothesis", "text": "改過"}]})
    assert listed(owner)["coverage"]["paragraphs"][0]["state"] == "needs_recheck"
    with app.app_context():
        _save_section(f"<p>全新的第一段。</p><p>{OTHER_PARA}</p>")
    cov = listed(owner)["coverage"]
    assert [p["state"] for p in cov["paragraphs"]] == ["unmarked", "free_writing"] and cov["orphaned_links"] == 1
    text = owner.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)
    assert "覆蓋（introduction，最新版本共 2 段）" in text and "尚未標記 1" in text


# ---------------- R6：重複／重疊文字、舊連結、跨段選取、部分覆蓋 ----------------
DUP = "這個結果與先前研究一致。"
MID = "中間是另一段不相關的文字。"


def _legacy_link(root, text, s_ver, kind="free_writing"):
    """模擬 R6 以前的連結（沒有 anchor）。"""
    from app.core_pro.manuscript import paragraph_provenance as prov
    path = Path(prov.links_path(str(root), PID))
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": prov.SCHEMA, "pid": PID, "links": [], "_rev": 0}
    doc["links"].append({"link_id": "pl_0123456789ab", "kind": kind, "section": SECTION, "s_ver": s_ver, "paragraph_text": text,
                         "paragraph_hash": prov.paragraph_hash(text), "reason": "", "created_by": {"username": "owner"},
                         "created_at": "2026-10-01T00:00:00+00:00", "retracted": None, "note": None})
    doc["_rev"] = int(doc.get("_rev") or 0) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_duplicate_paragraphs_never_mark_the_copy_as_covered(env):
    """同一段文字出現兩次：沒有提示時拒絕猜；「選這段」只覆蓋選的那一處，另一處仍是尚未標記。"""
    app, root, owner, nid = env
    with app.app_context():
        _save_section(f"<p>開頭。</p><p>{DUP}</p><p>{MID}</p><p>{DUP}</p><p>結尾。</p>")
    ver = listed(owner)["latest_s_ver"]
    refused = link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=DUP)
    assert refused.status_code == 409 and "出現 2 次" in refused.json["message"]
    ok = link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=DUP, paragraph_index=3, s_ver=ver)
    assert ok.status_code == 201, ok.get_data(as_text=True)
    assert ok.json["link"]["anchor"]["occurrence"] == 1 and ok.json["link"]["anchor"]["paragraphs"] == [3, 3]
    cov = listed(owner)["coverage"]
    assert [p["state"] for p in cov["paragraphs"]] == ["unmarked", "unmarked", "unmarked", "evidence_note", "unmarked"]
    # 之後在前面再加一段：位置由前後文決定，仍然只有原本那一處
    with app.app_context():
        _save_section(f"<p>新加的開頭。</p><p>開頭。</p><p>{DUP}</p><p>{MID}</p><p>{DUP}</p><p>結尾。</p>")
    cov = listed(owner)["coverage"]
    assert [p["state"] for p in cov["paragraphs"]] == ["unmarked"] * 4 + ["evidence_note", "unmarked"]
    # 把這段複製到自己後面：兩處前後文都只對一半、段落雜湊相同，無法確定 → 不覆蓋任何一處，標示位置不明確
    with app.app_context():
        _save_section(f"<p>新加的開頭。</p><p>開頭。</p><p>{DUP}</p><p>{MID}</p><p>{DUP}</p><p>{DUP}</p><p>結尾。</p>")
    data = listed(owner)
    assert data["coverage"]["counts"]["evidence_note"] == 0 and data["coverage"]["ambiguous_links"] == 1
    assert "paragraph_ambiguous" in data["links"][0]["issues"]
    assert [p["ambiguous_link_ids"] != [] for p in data["coverage"]["paragraphs"]] == [False, False, True, False, True, True, False]
    # 研究者自主：拒絕連結與位置不明確都不擋保存與匯出
    export = owner.get(f"/manuscript/api/provenance/export?pid={PID}")
    assert export.status_code == 200 and "無法確定出處屬於哪一處" in export.get_data(as_text=True)


def test_short_fragment_shared_by_two_paragraphs_uses_editor_context(env):
    app, root, owner, nid = env
    with app.app_context():
        _save_section(f"<p>第一段的開頭。{DUP}第一段的結尾。</p><p>第二段的開頭。{DUP}第二段的結尾。</p>")
    assert link(owner, 0, kind="free_writing", paragraph_text=DUP).status_code == 409
    ok = link(owner, 0, kind="free_writing", paragraph_text=DUP, context_before="第二段的開頭。", context_after="第二段的結尾。")
    assert ok.status_code == 201 and ok.json["link"]["anchor"]["chosen_by"] == "context"
    rows = listed(owner)["coverage"]["paragraphs"]
    assert [r["state"] for r in rows] == ["unmarked", "free_writing"]
    assert rows[1]["partial"] is True and 0 < rows[1]["covered_ratio"] < 0.9, "只連一句，不能宣稱整段都有出處"


def test_short_paragraph_inside_longer_one_does_not_cover_the_longer(env):
    """重疊：短段落的文字也出現在另一段中。連結短段落不得讓長段落算有出處。"""
    app, root, owner, nid = env
    with app.app_context():
        _save_section(f"<p>{DUP}</p><p>前文說明。{DUP}後文延伸。</p>")
    ver = listed(owner)["latest_s_ver"]
    assert link(owner, 0, kind="free_writing", paragraph_text=DUP, paragraph_index=0, s_ver=ver).status_code == 201
    rows = listed(owner)["coverage"]["paragraphs"]
    assert [r["state"] for r in rows] == ["free_writing", "unmarked"] and rows[0]["partial"] is False


def test_legacy_link_ambiguous_at_link_time_is_not_counted(env):
    """R6 以前的連結沒有位置；連結當時文字就出現兩次的，不能把兩段都算有出處。"""
    app, root, owner, nid = env
    with app.app_context():
        _save_section(f"<p>{DUP}</p><p>{MID}</p><p>{DUP}</p>")
    ver = listed(owner)["latest_s_ver"]
    _legacy_link(root, DUP, ver)
    data = listed(owner)
    cov = data["coverage"]
    assert cov["counts"]["free_writing"] == 0 and cov["ambiguous_links"] == 1 and cov["orphaned_links"] == 0
    assert data["links"][0]["anchor"]["legacy"] is True and data["links"][0]["anchor"]["ambiguous_at_link"] is True
    assert data["links"][0]["issues"] == ["paragraph_ambiguous"]


def test_legacy_link_unique_at_link_time_survives_a_later_copy(env):
    app, root, owner, nid = env
    with app.app_context():
        _save_section(f"<p>開頭。</p><p>{DUP}</p><p>{MID}</p>")
    _legacy_link(root, DUP, listed(owner)["latest_s_ver"])
    assert [p["state"] for p in listed(owner)["coverage"]["paragraphs"]] == ["unmarked", "free_writing", "unmarked"]
    with app.app_context():
        _save_section(f"<p>開頭。</p><p>{DUP}</p><p>{MID}</p><p>結論重複一次：</p><p>{DUP}</p>")
    rows = listed(owner)["coverage"]["paragraphs"]
    assert [p["state"] for p in rows] == ["unmarked", "free_writing", "unmarked", "unmarked", "unmarked"]


def test_selection_across_two_paragraphs_counts_both_not_orphaned(env):
    app, root, owner, nid = env
    resp = link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=f"{PARA}\n{OTHER_PARA}")
    assert resp.status_code == 201 and resp.json["link"]["anchor"]["paragraphs"] == [0, 1]
    cov = listed(owner)["coverage"]
    assert [p["state"] for p in cov["paragraphs"]] == ["evidence_note", "evidence_note"] and cov["orphaned_links"] == 0
    assert "位置（最新版本" in owner.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)


# ---------------- R6：引文插入（原文／機器翻譯、重新核對來源、頁碼與來源出處） ----------------
MT = "改善只在 300 K 一種校準協定下觀察到。"


def _write_bilingual_paper(root, original=QUOTE, translation=MT):
    base = root / PID / "literature" / "papers" / PAPER / "05_interprets" / "fusion"
    base.mkdir(parents=True, exist_ok=True)
    content = [{"page": 1, "blocks": [{"type": "title", "content": "T", "seq_id": "p1_title_1"},
                                      {"type": "body", "seq_id": "p1_body_2", "content": "Bounded result. " + original,
                                       "translation": "有界的結果。" + translation}]}]
    (base / "full_text.json").write_text(json.dumps({"paper_id": PAPER, "content": content}), encoding="utf-8")


def _bilingual_note(owner, base_rev=1):
    note = {"paper_id": PAPER, "anchors": [{"quote": QUOTE, "side": "original", "ref": "p1-b2", "source": "flowa_fusion"},
                                           {"quote": MT, "side": "translation", "ref": "p1-b2", "source": "flowa_fusion"}],
            "statements": [{"kind": "source_fact", "evidence": [0]}, {"kind": "human_hypothesis", "text": "雙語筆記"}]}
    resp = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": base_rev, "accepted": True, "note": note})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.json["note"]["note_id"]


def _choices(owner, nid):
    return next(n for n in listed(owner)["notes"] if n["note_id"] == nid)["quotes"]


def test_quote_choices_are_rechecked_against_the_current_source(env):
    app, root, owner, _ = env
    _write_bilingual_paper(root)
    nid = _bilingual_note(owner)
    orig, mt = _choices(owner, nid)
    assert orig["side"] == "original" and orig["machine_translation"] is False and orig["insertable"] is True
    assert orig["current_status"] == "exact" and orig["page"] == 1 and orig["ref"] == "p1-b2"
    assert mt["machine_translation"] is True and mt["insertable"] is True
    # 原文重跑、引文已不存在：仍列出，但不能插入，並說明原因（不默默消失、也不假裝仍有效）
    _write_bilingual_paper(root, original="A different sentence now.", translation="完全不同的譯文。")
    orig, mt = _choices(owner, nid)
    assert orig["insertable"] is False and orig["current_status"] == "not_found" and "找不到" in orig["blocked_reason"]
    assert mt["insertable"] is False


def test_inserted_quote_link_keeps_page_side_and_server_recheck(env):
    app, root, owner, _ = env
    _write_bilingual_paper(root)
    nid = _bilingual_note(owner)
    orig_p = f"「{QUOTE}」（{PAPER}，PDF 第 1 頁，原文）"
    mt_p = f"［機器翻譯，非原文引文］{MT}（{PAPER}，PDF 第 1 頁，譯文）"
    with app.app_context():
        _save_section(f"<p>{PARA}</p><p>{orig_p}</p><p>{mt_p}</p>")
    rev = listed(owner)["rev"]
    # 用戶端偽造的核對結果一律忽略
    r = link(owner, rev, kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=0,
             quote_current={"status": "exact", "page": 99})
    assert r.status_code == 201, r.get_data(as_text=True)
    q = r.json["link"]["quote"]
    assert (q["side"], q["machine_translation"], q["page"], q["ref"], q["status_at_link"]) == ("original", False, 1, "p1-b2", "exact")
    r = link(owner, rev + 1, kind="evidence_note", note_id=nid, paragraph_text=mt_p, quote_anchor_index=1)
    assert r.status_code == 201 and r.json["link"]["quote"]["machine_translation"] is True
    text = owner.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)
    assert "插入的引文：原文" in text and "機器翻譯，非原文（不可當作原文引用）" in text and "PDF 第 1 頁" in text
    # 引文被改寫：不能再宣稱是引文
    altered = "「The improvement was observed at all temperatures.」（paper1，PDF 第 1 頁，原文）"
    with app.app_context():
        _save_section(f"<p>{PARA}</p><p>{orig_p}</p><p>{mt_p}</p><p>{altered}</p>")
    r = link(owner, rev + 2, kind="evidence_note", note_id=nid, paragraph_text=altered, quote_anchor_index=0)
    assert r.status_code == 409 and "不再逐字相同" in r.json["message"]
    assert link(owner, rev + 2, kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=7).status_code == 400


def test_quote_linked_after_source_rerun_is_recorded_not_blocked(env):
    """連結當下原文已重跑：照樣連結（研究者自主），但記錄核對結果並標示需要重新核對；偽造的核對結果被忽略。"""
    app, root, owner, _ = env
    _write_bilingual_paper(root)
    nid = _bilingual_note(owner)
    orig_p = f"「{QUOTE}」（{PAPER}，PDF 第 1 頁，原文）"
    with app.app_context():
        _save_section(f"<p>{PARA}</p><p>{orig_p}</p>")
    _write_bilingual_paper(root, original="A different sentence now.")
    r = link(owner, listed(owner)["rev"], kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=0,
             quote_current={"status": "exact"})
    assert r.status_code == 201 and r.json["link"]["quote"]["status_at_link"] == "not_found"
    item = listed(owner)["links"][-1]
    assert "source_changed" in item["issues"]
    assert "目前原文中找不到" in owner.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)


def test_same_quote_inserted_twice_links_the_new_copy(env):
    """同一段引文插入兩次（重複段落）：自動連結用插入點前文確定是新的那一處，舊的那處不被重複計算。"""
    app, root, owner, nid = env
    orig_p = f"「{QUOTE}」（{PAPER}，PDF 第 1 頁，原文）"
    with app.app_context():
        _save_section(f"<p>{PARA}</p><p>{orig_p}</p>")
    first = link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=0)
    assert first.status_code == 201
    with app.app_context():
        _save_section(f"<p>{PARA}</p><p>{orig_p}</p><p>{orig_p}</p>")
    second = link(owner, 1, kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=0,
                  context_before=f"{PARA}{orig_p}"[-80:], context_after="")
    assert second.status_code == 201, second.get_data(as_text=True)
    assert second.json["link"]["anchor"]["paragraphs"] == [2, 2]
    cov = listed(owner)["coverage"]
    assert [p["state"] for p in cov["paragraphs"]] == ["unmarked", "evidence_note", "evidence_note"]
    assert [p["link_ids"] for p in cov["paragraphs"]][1:] == [[first.json["link"]["link_id"]], [second.json["link"]["link_id"]]]


def test_coauthor_sees_no_quote_provenance_from_study(env):
    app, root, owner, nid = env
    orig_p = f"「{QUOTE}」（{PAPER}，PDF 第 1 頁，原文）"
    with app.app_context():
        _save_section(f"<p>{PARA}</p><p>{orig_p}</p>")
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=0).status_code == 201
    _add_coauthor(app, SECTION)
    item = listed(login(app, "coauthor"))["links"][0]
    assert item["note"] == {"hidden": True} and item["quote"] == {"hidden": True}


# ---------------- R6：流程接縫缺陷（稽核發現） ----------------
def test_review_scope_sees_current_shared_note_not_link_time_snapshot(env):
    """教授審閱身分：連結當時的快照含之後被取代的陳述，不能經由段落出處看到（與 Study review_shared 一致）。"""
    app, _, owner, nid = env
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).status_code == 201
    upd = owner.post("/api/study/evidence_notes/update", json={"pid": PID, "base_rev": 1, "note_id": nid,
                                                               "statements": [{"kind": "human_hypothesis", "text": "修訂後的假說"}]})
    assert upd.status_code == 200, upd.get_data(as_text=True)
    owner.post("/api/study/evidence_notes/review", json={"pid": PID, "note_id": nid, "action": "request"})
    prof = login(app, "prof")
    body = prof.get(f"/manuscript/api/provenance?pid={PID}&section={SECTION}")
    note = body.json["links"][0]["note"]
    assert note["revised_since_link"] is True and note["shown_version"] == "current_shared"
    assert [s["text"] for s in note["statements"]] == ["修訂後的假說"]
    for text in (body.get_data(as_text=True), prof.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)):
        assert "退火時間可能是混淆因素" not in text and "只支持 300 K" not in text
    assert listed(owner)["links"][0]["note"]["statements"][2]["text"] == "退火時間可能是混淆因素", "owner 仍看到連結時的快照"


def test_anchor_ambiguous_at_creation_is_not_reported_as_source_change(env):
    """建立筆記時引文在同一段出現兩次（ambiguous_in_block）：原文沒變就不能說「原文已重跑或改版」。"""
    app, root, owner, _ = env
    _write_paper(root, text=f"{QUOTE} {QUOTE}")
    note = {"paper_id": PAPER, "anchors": [{"quote": QUOTE, "side": "original", "ref": "p1-b2", "source": "flowa_fusion"}],
            "statements": [{"kind": "source_fact", "evidence": [0]}]}
    resp = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": 1, "accepted": True, "note": note})
    assert resp.status_code == 200 and resp.json["note"]["anchors"][0]["status"] == "ambiguous_in_block"
    assert link(owner, 0, kind="evidence_note", note_id=resp.json["note"]["note_id"], paragraph_text=PARA).status_code == 201
    assert listed(owner)["links"][0]["issues"] == [] and listed(owner)["coverage"]["paragraphs"][0]["state"] == "evidence_note"
    _write_paper(root, text="A different sentence now.")
    assert listed(owner)["links"][0]["issues"] == ["source_changed"]


def test_export_labels_machine_translation_and_unverified_anchors(env):
    app, root, owner, _ = env
    _write_bilingual_paper(root)
    nid = _bilingual_note(owner)
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).status_code == 201
    text = owner.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)
    mt_line = next(l for l in text.splitlines() if MT in l)
    assert "機器翻譯（非原文）" in mt_line and "原文中逐字找到" not in mt_line and "在機器翻譯中逐字找到" in mt_line
    orig_line = next(l for l in text.splitlines() if QUOTE in l and "證據" in l)
    assert "原文" in orig_line and "PDF 第 1 頁" in orig_line and "不代表支持" in orig_line


def test_export_coverage_lists_sections_without_links(env):
    app, root, owner, _ = env
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    from app.core_pro.manuscript.manuscript_routes import _get_or_init_sections
    with app.app_context():
        _get_or_init_sections(PID)
        ManuscriptIO.save_block_version(PID, "method", "Method", "<p>方法第一段。</p><p>方法第二段。</p>", updated_by="owner")
    assert link(owner, 0, kind="free_writing", paragraph_text=OTHER_PARA).status_code == 201
    text = owner.get(f"/manuscript/api/provenance/export?pid={PID}").get_data(as_text=True)
    assert "覆蓋（introduction" in text and "覆蓋（method，最新版本共 2 段）" in text and "尚未標記 2" in text
