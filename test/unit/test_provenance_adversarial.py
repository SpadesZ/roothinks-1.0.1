# Roothinks source maintenance contract
# 目的：段落出處的對抗測試（R6）：跨專案 id 混用、偽造位置與核對結果、研究者自主（出處狀態再差都不擋保存與匯出）。
#   - 外人以自己的專案為 pid、帶別專案的 note_id：找不到筆記，別專案的出處與 Study 反向連結都不受影響。
#   - query 與 body 的 pid 不同：藍圖層拒絕（不得用一個 pid 過權限、另一個 pid 寫資料）。
#   - 偽造 paragraph_index／s_ver：只能指到該版本真的存在、而且包含這段文字的位置；不存在就拒絕。
#   - 位置不明確的舊連結＋原文已重跑＋插入的引文找不到：章節照常保存、DOCX 與出處紀錄照常匯出。
# 驗證：python -m pytest test/unit/test_provenance_adversarial.py -q
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_paragraph_provenance import (  # noqa: E402,F401  (env 是 pytest fixture)
    DUP, MID, PAPER, PARA, PID, QUOTE, SECTION, _legacy_link, _save_section, _write_paper, env, link, listed, login,
)

OTHER_PID = "PROV2-p"


def _outsider_owns_other_project(app):
    from app import db
    from app.models import Project, User, WorkspaceMember
    with app.app_context():
        db.session.add(Project(project_id=OTHER_PID, name="Other", status="formal"))
        outsider = User.query.filter_by(username="outsider").first()
        db.session.add(WorkspaceMember(user_id=outsider.id, pid=OTHER_PID, role="owner"))
        db.session.commit()
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        ManuscriptIO.save_block_version(OTHER_PID, SECTION, "Intro", f"<p>{PARA}</p>", updated_by="outsider")


def test_cross_project_note_id_and_pid_mixups_are_refused(env):
    app, root, owner, nid = env
    assert link(owner, 0, kind="evidence_note", note_id=nid, paragraph_text=PARA).status_code == 201
    _outsider_owns_other_project(app)
    outsider = login(app, "outsider")
    # 1. 自己的專案、別人的筆記
    r = outsider.post("/manuscript/api/provenance/link", json={"pid": OTHER_PID, "section": SECTION, "base_rev": 0,
                                                               "kind": "evidence_note", "note_id": nid, "paragraph_text": PARA})
    assert r.status_code == 409 and "找不到這則證據筆記" in r.json["message"]
    # 2. query 與 body 的 pid 不同
    r = outsider.post(f"/manuscript/api/provenance/link?pid={OTHER_PID}", json={"pid": PID, "section": SECTION, "base_rev": 1,
                                                                                "kind": "free_writing", "paragraph_text": PARA})
    assert r.status_code in (400, 403)
    # 3. 直接讀、撤回別專案的出處
    assert outsider.get(f"/manuscript/api/provenance?pid={PID}&section={SECTION}").status_code == 403
    lid = listed(owner)["links"][0]["link_id"]
    r = outsider.post("/manuscript/api/provenance/retract", json={"pid": OTHER_PID, "link_id": lid, "base_rev": 0})
    assert r.status_code == 404
    data = listed(owner)
    assert len(data["links"]) == 1 and not data["links"][0]["retracted"]
    used = owner.get(f"/api/study/evidence_notes/{PID}").json["notes"][0]["used_in"]
    assert [u["link_id"] for u in used] == [lid]


def test_forged_location_hints_cannot_point_elsewhere(env):
    app, root, owner, nid = env
    with app.app_context():
        _save_section(f"<p>{DUP}</p><p>{MID}</p><p>{DUP}</p>")
    ver = listed(owner)["latest_s_ver"]
    # 段落編號指到不含這段文字的段落、或不存在的段落：不能硬套
    for index in (1, 99, -1, "2"):
        r = link(owner, 0, kind="free_writing", paragraph_text=DUP, paragraph_index=index, s_ver=ver)
        assert r.status_code == 409, (index, r.get_data(as_text=True))
    # 前後文提示對不上任何一處：拒絕，不默選第一處
    r = link(owner, 0, kind="free_writing", paragraph_text=DUP, context_before="不存在的前文", context_after="不存在的後文")
    assert r.status_code == 409
    # 偽造 anchor 欄位：一律由伺服器重算
    r = link(owner, 0, kind="free_writing", paragraph_text=DUP, paragraph_index=2, s_ver=ver,
             anchor={"occurrence": 0, "paragraphs": [0, 0]})
    assert r.status_code == 201 and r.json["link"]["anchor"]["paragraphs"] == [2, 2]
    # 不存在的版本
    assert link(owner, 1, kind="free_writing", paragraph_text=DUP, paragraph_index=0, s_ver="9.9").status_code == 409


def test_worst_case_provenance_never_blocks_save_or_export(env):
    """研究者自主：位置不明確的舊連結、原文已重跑、引文找不到——保存、DOCX 匯出、出處匯出照常。"""
    app, root, owner, nid = env
    orig_p = f"「{QUOTE}」（{PAPER}，PDF 第 1 頁，原文）"
    with app.app_context():
        _save_section(f"<p>{DUP}</p><p>{orig_p}</p><p>{DUP}</p>")
    ver = listed(owner)["latest_s_ver"]
    _legacy_link(root, DUP, ver)
    rev = listed(owner)["rev"]
    _write_paper(root, text="A different sentence now.")
    assert link(owner, rev, kind="evidence_note", note_id=nid, paragraph_text=orig_p, quote_anchor_index=0).status_code == 201
    data = listed(owner)
    assert data["coverage"]["ambiguous_links"] == 1 and "source_changed" in data["links"][-1]["issues"]
    # 保存新版本（Socket，與 2B 相同路徑）
    from app import socketio
    sio = socketio.test_client(app, namespace="/manu_ws", flask_test_client=owner, auth={"pid": PID})
    sio.emit("cmd_save_block", {"pid": PID, "section": SECTION, "title": "Intro", "base_rev": 0,
                                "content": f"<p>{DUP}</p><p>{orig_p}</p><p>{DUP}</p><p>新的一段。</p>"}, namespace="/manu_ws")
    names = [e["name"] for e in sio.get_received("/manu_ws")]
    sio.disconnect(namespace="/manu_ws")
    assert "save_ack" in names, names
    assert listed(owner)["latest_s_ver"] != ver
    docx = owner.post(f"/manuscript/api/export/docx/{PID}", json={"title": "Paper", "html": f"<p>{DUP}</p><p>{orig_p}</p>"})
    assert docx.status_code == 200 and docx.get_data()[:2] == b"PK"
    export = owner.get(f"/manuscript/api/provenance/export?pid={PID}")
    assert export.status_code == 200 and "目前原文中找不到" in export.get_data(as_text=True)
