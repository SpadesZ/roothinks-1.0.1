# Roothinks source maintenance contract
# 目的：R6 Manuscript 授權／整合縫隙的回歸鎖定，用真 create_app、session 登入、tmp 資料根。
#   1. 章節結構（POST /manuscript/api/sections/<pid>）會整批刪除重建 ManuSectionConfig，只有 owner／editor 可改；
#      教授審閱身分、團隊 viewer、限定編輯（coauthor）一律 403 且資料不變；GET 章節清單仍照舊可讀。
#   2. 教授審閱身分經 Manuscript 讀不到研究者的私人閱讀筆記（reading_note）：段落脈絡選單、引用決策、
#      變更影響、導師審閱工作台都要去掉；owner 自己照舊看得到（與 Literature 的 _without_reading_notes 同規則）。
#   3. 2B 存檔按鈕只在伺服器回 save_ack（同章節、同一次請求）後才顯示成功；伺服器拒絕或失敗回 save_error；
#      逾時顯示「尚未確認保存」，不得用計時器假裝成功（以 node vm 執行真實前端程式）。
#   4. 限定編輯看得到自己被指派章節的版本紀錄（RevisionLog.entity_ref 是「章節/檔名」），看不到別章的。
# 驗證：python -m pytest test/unit/test_manuscript_r6_seams.py -q
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PID, PAPER, SECTION, OTHER_SECTION = "R6SEAM-p", "paper1", "introduction", "discussion"
QUOTE = "The improvement was observed only at 300 K under one calibration protocol."
PARA = "我們只在 300 K 觀察到改善，這個結論不能外推到其他溫度。"
# 哨兵刻意只用 ASCII：jsonify 會把中文轉成 \uXXXX，中文哨兵在原始回應文字裡永遠找不到，測試會假性通過。
SECRET_NOTE = "PRIVATE-READING-NOTE-r6-suspect-calibration"


def _save_section(html, section=SECTION, by="owner"):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    return ManuscriptIO.save_block_version(PID, section, "Intro", html, updated_by=by)


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
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    # Literature 的資料根是匯入時決定的模組全域（預設 repo 的 data/）；不改的話 Literature 對照組讀到空庫，比較會假性通過。
    from app.core_pro.literature import literature_routes
    monkeypatch.setattr(literature_routes, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(literature_routes, "literature_library_service", None)
    from app import create_app, db
    from app.models import ChapterAssignment, Project, User, WorkspaceMember
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    ids = {}
    with app.app_context():
        db.create_all()
        db.session.add(Project(project_id=PID, name="R6", status="formal"))
        for name, role, system_role in [("owner", "owner", "user"), ("editor", "editor", "user"), ("viewer", "viewer", "user"),
                                        ("coauthor", "coauthor", "user"), ("prof", None, "mentor"), ("outsider", None, "user")]:
            user = User(username=name, email=f"{name}@p.local", system_role=system_role)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            ids[name] = user.id
            if role:
                db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        # 限定編輯只被指派 introduction。
        db.session.add(ChapterAssignment(pid=PID, section_key=SECTION, user_id=ids["coauthor"]))
        db.session.commit()
        _save_section(f"<p>{PARA}</p>")
    owner = login(app, "owner")
    # 教授審閱身分：經教授邀請加入的 viewer（同時建立 MentorLink 教授→owner）。
    r = owner.post(f"/api/collaboration/project/{PID}/invitations", json={"email": "prof@p.local", "kind": "professor", "role": "viewer"})
    assert login(app, "prof").post(f"/api/collaboration/invitations/{r.json['invitation']['id']}/accept",
                                   json={"token": r.json["token"]}).status_code == 200
    yield app, tmp_path, owner, ids
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


# ---------------------------------------------------------------------------
# 1. 章節結構只限 owner／editor
# ---------------------------------------------------------------------------

def _sections(client):
    resp = client.get(f"/manuscript/api/sections/{PID}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.json["sections"]


def _rewrite_payload(before, label):
    keep = [dict(s) for s in before if s["is_fixed"]]
    assert keep, "預設章節應含固定章節（title/author/abstract/keyword）"
    return {"sections": keep + [{"id": "hijack", "label": label, "is_fixed": False}]}


@pytest.mark.parametrize("who", ["prof", "viewer", "coauthor", "outsider"])
def test_non_writer_cannot_rewrite_section_structure(env, who):
    app, _, owner, _ = env
    before = _sections(owner)
    client = login(app, who)
    resp = client.post(f"/manuscript/api/sections/{PID}", json=_rewrite_payload(before, f"renamed-by-{who}"))
    assert resp.status_code == 403, (resp.status_code, resp.get_data(as_text=True))
    assert _sections(owner) == before   # 刪除重建一列都不能發生
    if who != "outsider":
        # 讀取章節清單照舊：審閱者、viewer、限定編輯都還看得到（限定編輯只看到被指派的章節）。
        visible = [s["id"] for s in _sections(client)]
        assert visible == ([SECTION] if who == "coauthor" else [s["id"] for s in before])


@pytest.mark.parametrize("who", ["owner", "editor"])
def test_owner_and_editor_can_rewrite_section_structure(env, who):
    app, _, owner, _ = env
    before = _sections(owner)
    resp = login(app, who).post(f"/manuscript/api/sections/{PID}", json=_rewrite_payload(before, f"added-by-{who}"))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    after = _sections(owner)
    assert [s["label"] for s in after][-1] == f"added-by-{who}"
    assert {s["id"] for s in after if s["is_fixed"]} == {s["id"] for s in before if s["is_fixed"]}


# ---------------------------------------------------------------------------
# 2. 教授審閱身分讀不到閱讀筆記
# ---------------------------------------------------------------------------

def _seed_confirmed_paragraph(app, root, owner):
    """included 文獻（含私人閱讀筆記）＋證據段落＋owner 確認的段落脈絡＋包含該章版本的全文版本。"""
    from app import db
    from app.models import EvidenceSegment
    from app.services.literature_library import LiteratureLibrary
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    with app.app_context():
        lib = LiteratureLibrary(str(root))
        lib.merge_candidates(PID, [{"title": "Calibration paper", "doi": "10.1/r6"}], default_source="search")
        eid = next(iter(lib.load(PID)["entries"]))
        lib.update_entry(PID, eid, {"paper_id": PAPER, "reading_note": SECRET_NOTE,
                                    "screening_status": "included", "screening_note": "納入理由"}, actor="owner")
        seg = EvidenceSegment(project_id=PID, source_type="paper", source_id=PAPER, paper_id=PAPER,
                              segment_id="s1", text=QUOTE, content_hash="x" * 64, metadata_json='{"page": 1}')
        db.session.add(seg)
        db.session.commit()
        seg_id = seg.id
        ver = ManuscriptIO.list_block_versions(PID, SECTION)[0]["ver"]
    resp = owner.post("/manuscript/api/paragraph/confirm", json={
        "pid": PID, "section": SECTION, "s_ver": ver, "paragraph_text": PARA, "purpose": "說明量測範圍",
        "claim": "只在 300 K 有改善", "conditions": "300 K；單一校準流程", "forbidden": "不可外推到其他溫度",
        "entry_id": eid, "evidence_id": seg_id, "confirmed": True})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    with app.app_context():
        ManuscriptIO.save_paper_version(PID, "Full", f"<p>{PARA}</p>", sections={SECTION: ver})
    return resp.json["decision"]


def _text(resp):
    assert resp.status_code in (200, 201), (resp.status_code, resp.get_data(as_text=True))
    return resp.get_data(as_text=True)


def test_review_scoped_professor_cannot_read_reading_notes_through_manuscript(env):
    app, root, owner, ids = env
    decision = _seed_confirmed_paragraph(app, root, owner)
    prof = login(app, "prof")
    # Literature 本身已去掉（對照組）。
    assert SECRET_NOTE not in _text(prof.get(f"/api/literature/library?pid={PID}"))
    urls = [f"/manuscript/api/paragraph/context?pid={PID}&section={SECTION}",
            f"/manuscript/api/citation/decisions?pid={PID}",
            f"/manuscript/api/citation/decisions?pid={PID}&section={SECTION}",
            f"/manuscript/api/change-impact?pid={PID}"]
    for url in urls:
        body = _text(prof.get(url))
        assert SECRET_NOTE not in body, url
        assert decision["decision_id"] in body or "sources" in body, url   # 端點本身仍正常回資料
    # 段落脈絡選單仍列出來源與採用理由（審閱可見），只是沒有閱讀筆記。
    ctx = prof.get(f"/manuscript/api/paragraph/context?pid={PID}&section={SECTION}").json
    assert ctx["sources"] and ctx["sources"][0]["selection_reason"] == "納入理由"
    assert ctx["decisions"][0]["paragraph_context"]["evidence"]["reading_note_hidden"] is True
    # 導師審閱工作台：paragraph_decisions 與審閱項目錨定的段落決策都不得帶出閱讀筆記。
    base = f"/api/mentor/mentees/{ids['owner']}/reviews/{PID}"
    created = prof.post(base + "/items", json={"kind": "comment", "body": "請說明適用範圍",
                                              "paper_version": "V1", "decision_id": decision["decision_id"]})
    assert SECRET_NOTE not in _text(created)
    workbench = prof.get(base)
    body = _text(workbench)
    assert SECRET_NOTE not in body
    assert workbench.json["paragraph_decisions"] and workbench.json["items"][0]["paragraph_decision"]
    # owner 自己的檢視照舊（不可因遮蔽而弄壞作者本人的資料）。
    for url in urls:
        assert SECRET_NOTE in _text(owner.get(url)), url
    assert SECRET_NOTE in _text(owner.get(f"/api/mentor/my-reviews/{PID}"))


def test_team_viewer_keeps_reading_notes_like_literature(env):
    """一般團隊 viewer 不是審閱身分，Literature 給他看閱讀筆記；Manuscript 不可多收窄（行為一致）。"""
    app, root, owner, _ = env
    _seed_confirmed_paragraph(app, root, owner)
    viewer = login(app, "viewer")
    assert SECRET_NOTE in _text(viewer.get(f"/api/literature/library?pid={PID}"))
    assert SECRET_NOTE in _text(viewer.get(f"/manuscript/api/paragraph/context?pid={PID}&section={SECTION}"))


# ---------------------------------------------------------------------------
# 3. 存檔回應：伺服器拒絕要回 save_error，成功的 save_ack 帶回請求識別碼
# ---------------------------------------------------------------------------

def _socket(app, name):
    from app import socketio
    return socketio.test_client(app, namespace="/manu_ws", flask_test_client=login(app, name), auth={"pid": PID})


def _events(sio, name):
    return [e["args"][0] for e in sio.get_received("/manu_ws") if e["name"] == name]


def test_save_block_refusal_emits_save_error_and_ack_echoes_request(env):
    app, _, _, _ = env
    viewer = _socket(app, "viewer")
    viewer.emit("cmd_save_block", {"pid": PID, "section": SECTION, "content": "<p>x</p>", "req_token": "v-1"}, namespace="/manu_ws")
    received = viewer.get_received("/manu_ws")
    errors = [e["args"][0] for e in received if e["name"] == "save_error"]
    assert not [e for e in received if e["name"] == "save_ack"]
    assert errors and errors[0]["target"] == "block" and errors[0]["section"] == SECTION and errors[0]["req_token"] == "v-1"
    viewer.disconnect(namespace="/manu_ws")
    owner = _socket(app, "owner")
    owner.emit("cmd_save_block", {"pid": PID, "section": SECTION, "content": "<p>ok</p>", "req_token": "o-1"}, namespace="/manu_ws")
    acks = _events(owner, "save_ack")
    assert acks and acks[0]["target"] == "block" and acks[0]["section"] == SECTION and acks[0]["req_token"] == "o-1"
    owner.disconnect(namespace="/manu_ws")


# 以 node vm 跑真實的 manuscript_soed.js：假的 DOM、socket 與計時器（可手動推進），記錄存檔圖示的每一次變化。
_JS_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
function setup() {
    let timers = [], nextId = 1;
    const status = {innerText: '', textContent: ''};
    const iconLog = [];
    const icon = {_c: 'bi bi-floppy', get className() { return this._c; }, set className(v) { this._c = v; iconLog.push(v); }};
    const card = {getAttribute: () => 'introduction', querySelector: () => ({innerHTML: '<p>body</p>'})};
    const btn = {closest: () => card, querySelector: () => icon, title: 'Save Block'};
    const context = vm.createContext({console, Date, Math,
        setTimeout(fn, ms) { const id = nextId++; timers.push({id, fn, ms}); return id; },
        clearTimeout(id) { timers = timers.filter(t => t.id !== id); },
        window: {localStorage: {length: 0, key() {}, getItem() { return null; }, setItem() {}, removeItem() {}}},
        document: {getElementById: () => null, querySelectorAll: () => []}});
    vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
    const Soed = vm.runInContext('ManuSoed', context);
    const soed = Object.create(Soed.prototype);
    const handlers = {}, emitted = [];
    Object.assign(soed, {
        app: {pid: 'R6-p', paperTitleInput: {value: 'T'}, saveStatus: status,
              socket: {connected: true, on: (ev, fn) => { handlers[ev] = fn; }, emit: (ev, p) => emitted.push({ev, p})}},
        _draftRevMap: {}, _draftConflicts: {}, _currentSectionId: () => 'introduction',
        _setConnectionBadge() {}, addSystemMessage() {}, renderSectionVersions() {}, renderPaperVersions() {},
    });
    soed.setupSocketEvents();
    const runTimers = () => { for (let i = 0; i < 50 && timers.length; i++) {
        timers.sort((a, b) => a.ms - b.ms); const t = timers.shift(); t.fn(); } };
    return {soed, handlers, emitted, icon, iconLog, status, btn, runTimers};
}
const OK = 'bi bi-check-lg text-success';
const out = {};
{   // 沒有任何回應：時間過去也不得顯示成功，要顯示「尚未確認保存」。
    const h = setup(); h.soed.cardActionSave(h.btn); h.runTimers();
    out.no_ack = {success_shown: h.iconLog.includes(OK), status: h.status.innerText, final_icon: h.icon.className};
}
{   // 伺服器拒絕：save_error（同章節、同請求）→ 錯誤狀態，不得顯示成功。
    const h = setup(); h.soed.cardActionSave(h.btn);
    const req = h.emitted[0].p.req_token;
    if (h.handlers.save_error) h.handlers.save_error({target: 'block', section: 'introduction', req_token: req, msg: '權限不足'});
    h.runTimers();
    out.refused = {success_shown: h.iconLog.includes(OK), status: h.status.innerText, has_req_token: !!req,
                   error_shown: h.iconLog.some(c => c.includes('text-danger'))};
}
{   // 別章的 ack 與同章舊請求的 ack 都不算；只有本次請求的 save_ack 才顯示成功。
    const h = setup(); h.soed.cardActionSave(h.btn);
    const req = h.emitted[0].p.req_token;
    h.handlers.save_ack({target: 'block', section: 'discussion', req_token: req, msg: 'Block saved as V0.2', ver: '0.2', versions: []});
    h.handlers.save_ack({target: 'block', section: 'introduction', req_token: 'stale-other-request', msg: 'Block saved as V0.2', ver: '0.2', versions: []});
    const before = h.iconLog.includes(OK);
    h.handlers.save_ack({target: 'block', section: 'introduction', req_token: req, msg: 'Block saved as V0.3', ver: '0.3', versions: []});
    out.ack = {success_before_matching_ack: before, success_after: h.iconLog.includes(OK), status: h.status.innerText};
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不在 PATH")
def test_save_button_success_only_after_matching_server_ack(tmp_path):
    harness = tmp_path / "save_button_harness.cjs"
    harness.write_text(_JS_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(REPO / "app/static/js/manuscript_soed.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["no_ack"]["success_shown"] is False, out
    assert "尚未確認保存" in out["no_ack"]["status"], out
    assert out["refused"]["has_req_token"] and out["refused"]["success_shown"] is False and out["refused"]["error_shown"], out
    assert "失敗" in out["refused"]["status"], out
    assert out["ack"]["success_before_matching_ack"] is False and out["ack"]["success_after"] is True, out
    assert "V0.3" in out["ack"]["status"], out


# ---------------------------------------------------------------------------
# 4. 限定編輯看得到自己章節的版本紀錄
# ---------------------------------------------------------------------------

def test_coauthor_sees_own_section_revisions_but_not_other_sections(env):
    app, _, _, ids = env
    from app import db
    from app.models import RevisionLog
    coauthor = _socket(app, "coauthor")
    coauthor.emit("cmd_save_block", {"pid": PID, "section": SECTION, "content": "<p>coauthor text</p>"}, namespace="/manu_ws")
    assert _events(coauthor, "save_ack"), "限定編輯應可存自己被指派的章節"
    coauthor.disconnect(namespace="/manu_ws")
    owner = _socket(app, "owner")
    owner.emit("cmd_save_block", {"pid": PID, "section": OTHER_SECTION, "content": "<p>owner discussion</p>"}, namespace="/manu_ws")
    assert _events(owner, "save_ack")
    owner.disconnect(namespace="/manu_ws")
    with app.app_context():
        # 章節 key 不含 "/"；含 "/" 的舊紀錄只能以完整章節比對，不能用前綴猜成被指派的章節。
        db.session.add(RevisionLog(pid=PID, user_id=ids["owner"], entity_type="manuscript_block",
                                   entity_ref=f"{SECTION}/nested/V9.json", action="save", summary="nested"))
        db.session.commit()
    rows = login(app, "coauthor").get(f"/manuscript/api/revisions/{PID}").json["revisions"]
    refs = [r["entity_ref"] for r in rows]
    assert any(ref.startswith(f"{SECTION}/V") for ref in refs), refs
    assert not any(ref.startswith(f"{OTHER_SECTION}/") for ref in refs), refs
    assert f"{SECTION}/nested/V9.json" not in refs
    # 對照：editor 看得到全部。
    all_refs = [r["entity_ref"] for r in login(app, "editor").get(f"/manuscript/api/revisions/{PID}").json["revisions"]]
    assert any(ref.startswith(f"{OTHER_SECTION}/") for ref in all_refs)
