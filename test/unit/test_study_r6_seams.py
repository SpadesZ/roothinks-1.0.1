# Roothinks source maintenance contract
# 目的：Study R6 兩個接縫的 API 行為（真 create_app、session 登入、tmp SQLite 與 tmp 資料根）。
#   - 匯出不能把「建立時」的錨點狀態當成現在的事實：原文重跑後，Markdown 匯出要重新核對並同時列出
#     建立時與目前的狀態；譯文錨點標示為機器翻譯、非原文；讀不到原文時照常匯出並明說無法核對。
#   - Study 的「已用在稿件」反向連結不能比段落活得久：段落在較新版本被刪除或改寫後，用在稿件的列要帶
#     location=missing；仍在的是 matched。位置每次請求只算一次；出處讀取失敗不影響筆記清單。
# 驗證：python -m pytest test/unit/test_study_r6_seams.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PID, PAPER, SECTION = "SEAM-p", "paper1", "introduction"
QUOTE = "The improvement was observed only at 300 K under one calibration protocol."
ZH = "只有在 300 K 的單一校準流程下觀察到改善。"
PARA = "我們只在 300 K 觀察到改善，這個結論不能外推到其他溫度。"
OTHER_PARA = "第二段是研究者自己的推論。"
EXACT_ORIGINAL = "（exact，原文）"      # 舊匯出格式：只印建立時的狀態，原文重跑後會變成假的「exact」
FOUND_SUMMARY = "至少一個引文在原文中找到"


def _write_paper(root, text=QUOTE, zh=ZH, lead="Bounded result. "):
    base = root / PID / "literature" / "papers" / PAPER / "05_interprets"
    (base / "fusion").mkdir(parents=True, exist_ok=True)
    content = [{"page": 1, "blocks": [{"type": "title", "content": "T", "seq_id": "p1_title_1"},
                                      {"type": "body", "seq_id": "p1_body_2", "content": lead + text, "content_zh": zh}]}]
    (base / "fusion" / "full_text.json").write_text(json.dumps({"paper_id": PAPER, "content": content}), encoding="utf-8")


def _save_section(html):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    return ManuscriptIO.save_block_version(PID, SECTION, "Intro", html, updated_by="owner")


def login(app, name):
    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": f"{name}@p.local", "password": "password123"}).status_code == 200
    return client


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
        db.session.add(Project(project_id=PID, name="Seams", status="formal"))
        user = User(username="owner", email="owner@p.local", system_role="user")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role="owner"))
        db.session.commit()
        _save_section(f"<p>{PARA}</p><p>{OTHER_PARA}</p>")
    _write_paper(tmp_path)
    yield app, tmp_path, login(app, "owner")
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def accept(owner, base_rev, note):
    resp = owner.post("/api/study/evidence_notes/accept", json={"pid": PID, "base_rev": base_rev, "accepted": True, "note": note})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.json["note"]


def link(owner, base_rev, **body):
    resp = owner.post("/manuscript/api/provenance/link", json={"pid": PID, "section": SECTION, "base_rev": base_rev, **body})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.json["link"]


def export_of(owner):
    resp = owner.get(f"/api/study/evidence_notes/{PID}/export")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_data(as_text=True)


def evidence_lines(text):
    return [l for l in text.splitlines() if l.startswith("- [證據 ")]


# 證據 1＝原文選段；證據 2＝譯文選段（機器翻譯）。
NOTE = {"paper_id": PAPER, "origin": {"type": "tutor", "mode": "explain"},
        "anchors": [{"quote": QUOTE, "side": "original", "ref": "p1-b2", "source": "flowa_fusion"},
                    {"quote": ZH, "side": "translation", "ref": "p1-b2", "source": "flowa_fusion"}],
        "statements": [{"kind": "source_fact", "evidence": [0]},
                       {"kind": "ai_interpretation", "text": "只支持 300 K。", "evidence": [1], "claim_support": "supports"}]}


# ---- 接縫 A：Study Markdown 匯出重新核對目前原文 ----

def test_export_rechecks_source_after_rerun_and_labels_translation(env):
    _, root, owner = env
    note = accept(owner, 0, NOTE)
    assert [a["status"] for a in note["anchors"]] == ["exact", "exact"]
    fresh = export_of(owner)
    fresh_lines = evidence_lines(fresh)
    assert FOUND_SUMMARY in fresh
    assert "建立時：exact" in fresh_lines[0] and "目前原文：exact" in fresh_lines[0]
    assert "機器翻譯，非原文" in fresh_lines[1] and "機器翻譯，非原文" not in fresh_lines[0]

    _write_paper(root, text="An entirely different sentence now.", zh="完全不同的一句話。")   # Literature 重跑 PDF
    ui = owner.get(f"/api/study/evidence_notes/{PID}?verify=1").json["notes"][0]["anchors"][0]
    assert ui["current"]["status"] == "not_found"
    text = export_of(owner)
    lines = evidence_lines(text)
    assert EXACT_ORIGINAL not in text and FOUND_SUMMARY not in text
    assert "沒有在原文中找到的引文" in text
    assert "建立時：exact" in lines[0] and "目前原文：not_found" in lines[0]
    assert "機器翻譯，非原文" in lines[1] and "目前原文：not_found" not in lines[1]
    assert "不代表它支持任何陳述" in text            # 「找到 ≠ 支持」的說明不能因為改動而消失
    fact = next(l for l in text.splitlines() if l.startswith("- **原文事實**"))
    assert "目前原文核對：證據 1＝not_found" in fact        # 原文事實本身也不能看起來仍然成立
    # 匯出不寫回：筆記裡保存的仍是建立時的狀態。
    stored = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]["anchors"][0]
    assert stored["status"] == "exact" and "current" not in stored


def test_export_marks_changed_source_version_but_keeps_found_quote(env):
    _, root, owner = env
    accept(owner, 0, NOTE)
    _write_paper(root, lead="Bounded result, revised after re-extraction. ")    # 引文還在，但原文版本變了
    ui = owner.get(f"/api/study/evidence_notes/{PID}?verify=1").json["notes"][0]["anchors"][0]["current"]
    assert ui["status"] == "relocated" and ui["version_changed"] is True
    text = export_of(owner)
    assert "原文已改版" in evidence_lines(text)[0]
    assert "目前原文：relocated" in evidence_lines(text)[0]
    assert FOUND_SUMMARY in text                  # relocated 仍算「在原文中找到」


def test_export_never_fails_when_source_is_unreadable(env):
    _, root, owner = env
    accept(owner, 0, NOTE)
    (root / PID / "literature").rename(root / PID / "literature-gone")
    text = export_of(owner)                       # export_of 已斷言 200
    assert "目前讀不到原文，無法重新核對" in text
    assert FOUND_SUMMARY not in text and EXACT_ORIGINAL not in text
    assert "建立時：exact" in evidence_lines(text)[0]


# ---- 接縫 B：「已用在稿件」反向連結不能比段落活得久 ----

def test_used_in_marks_link_missing_when_paragraph_removed_in_newer_version(env):
    app, _, owner = env
    nid = accept(owner, 0, NOTE)["note_id"]
    link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA)
    link(owner, 1, kind="evidence_note", note_id=nid, paragraph_text=OTHER_PARA)
    with app.app_context():
        newest = _save_section(f"<p>{OTHER_PARA}</p>")        # 研究者刪掉第一段
    rows = {r["excerpt"][:6]: r for r in owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]["used_in"]}
    gone, kept = rows[PARA[:6]], rows[OTHER_PARA[:6]]
    assert gone["location"] == "missing" and kept["location"] == "matched"
    latest = gone["latest_s_ver"]
    assert latest == kept["latest_s_ver"] and str(latest) != str(gone["s_ver"]) and latest is not None, newest


def test_locations_are_computed_once_per_request(env, monkeypatch):
    _, _, owner = env
    first = accept(owner, 0, NOTE)["note_id"]
    second = accept(owner, 1, NOTE)["note_id"]
    link(owner, 0, kind="evidence_note", note_id=first, paragraph_text=PARA)
    link(owner, 1, kind="evidence_note", note_id=second, paragraph_text=OTHER_PARA)
    from app.core_pro.manuscript import paragraph_provenance
    real, calls = paragraph_provenance.locations_for, []
    monkeypatch.setattr(paragraph_provenance, "locations_for", lambda *a, **k: calls.append(a) or real(*a, **k))
    notes = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"]
    assert len(notes) == 2 and all(n["used_in"] and "location" in n["used_in"][0] for n in notes)
    assert len(calls) == 1


def test_provenance_read_failure_never_breaks_the_notes_list(env, monkeypatch):
    _, _, owner = env
    nid = accept(owner, 0, NOTE)["note_id"]
    link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA)
    from app.core_pro.manuscript import paragraph_provenance

    def boom(*a, **k):
        raise RuntimeError("provenance offline")
    monkeypatch.setattr(paragraph_provenance, "locations_for", boom)
    resp = owner.get(f"/api/study/evidence_notes/{PID}")
    assert resp.status_code == 200 and [n["note_id"] for n in resp.json["notes"]] == [nid]


def test_one_corrupt_anchor_does_not_break_verify_list_or_provenance(monkeypatch):
    """R6：study_anchors.resolve 對單一錨點拋例外時，該錨點標 unavailable，其他照常。"""
    from app.core_pro.study import study_routes

    calls = {"n": 0}

    def flaky(pages, anchor, meta):
        calls["n"] += 1
        if anchor.get("quote") == "BROKEN":
            raise ValueError("corrupt anchor")
        return {"status": "exact", "version_changed": False, "ref": anchor.get("ref"), "page": 1}

    monkeypatch.setattr(study_routes, "_anchor_source", lambda pid, paper_id, hint=None: ([], {"pid": pid}))
    monkeypatch.setattr(study_routes.study_anchors, "resolve", flaky)
    notes = [{"note_id": "n1", "anchors": [{"paper_id": "paper1", "quote": "BROKEN", "ref": "p1-b1"},
                                           {"paper_id": "paper1", "quote": "fine", "ref": "p1-b2"}]}]
    study_routes._attach_current_anchor_status("X-p", notes)
    assert [a["current"]["status"] for a in notes[0]["anchors"]] == ["unavailable", "exact"] and calls["n"] == 2
