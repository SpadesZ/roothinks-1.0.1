# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/provenance_routes.py
# 模組定位: Manuscript 段落出處 API（R4）；資料與語意在 paragraph_provenance.py。
# 權限: 藍圖層 _enforce_manuscript_acl 先要求專案成員；這裡再做章節層：讀 = _socket_can_read_section，
#       寫 = _socket_can_write_section，整份匯出 = _socket_can_read_paper。教授審閱身分只看到已請求審閱的筆記內容。
#       證據筆記選單只給 owner／editor（限定編輯 coauthor 進不了 Study，不能藉這裡讀 Study 筆記）。
# 不阻擋: 出處狀態（段落改了、筆記改了、原文改了）只是提示；寫作、保存、匯出照常。
# 驗證: python -m pytest test/unit/test_paragraph_provenance.py -q
from flask import Response, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict

from app.core_pro.manuscript import paragraph_provenance as prov
from app.core_pro.manuscript.manuscript_routes import (
    _citation_data_root, _resolve_formal_project_pid, _session_uid, _socket_can_read_paper,
    _socket_can_read_section, _socket_can_write_section, bp,
)


def _actor():
    from flask_login import current_user
    try:
        if current_user.is_authenticated:
            return {"user_id": current_user.id, "username": current_user.username}
    except AttributeError:
        pass
    return {"user_id": None, "username": "local-user"}


def _notes(pid):
    from app.core_pro.study import study_evidence_notes
    return study_evidence_notes.load(study_evidence_notes.notes_path(_citation_data_root(), pid), pid)["notes"]


def _anchor_status(pid, notes):
    from app.core_pro.study.study_routes import _attach_current_anchor_status
    copies = [dict(n, anchors=[dict(a) for a in n.get("anchors") or []]) for n in notes]
    _attach_current_anchor_status(pid, copies)
    return {n.get("note_id"): [(a.get("current") or {}).get("status") for a in n.get("anchors") or []
                               if a.get("status") != "unavailable"] for n in copies}


def _review_scoped(pid):
    from app.security import current_user_review_scoped
    return current_user_review_scoped(pid)


def _can_pick_notes(uid, pid):
    if uid is None:
        return True
    from app.security import get_workspace_role
    return get_workspace_role(uid, pid) in {"owner", "editor"}


def _error(exc):
    status = 409 if isinstance(exc, Conflict) else 400
    return jsonify(success=False, message=exc.description), status


@bp.route('/api/provenance', methods=['GET'])
def list_paragraph_provenance():
    pid = _resolve_formal_project_pid(request.args.get('pid'))
    section = str(request.args.get('section') or '').strip()
    uid = _session_uid()
    if not pid:
        return jsonify(success=False, message='Invalid pid'), 404
    if uid is not None and not (_socket_can_read_section(uid, pid, section) if section else _socket_can_read_paper(uid, pid)):
        return jsonify(success=False, message='Forbidden'), 403
    doc = prov.load(_citation_data_root(), pid)
    links = [l for l in doc["links"] if not section or l.get("section") == section]
    notes = _notes(pid)
    described = prov.describe(pid, links, notes, _anchor_status(pid, notes), redact_unshared=_review_scoped(pid))
    choices = []
    if _can_pick_notes(uid, pid) and not _review_scoped(pid):
        choices = [{"note_id": n["note_id"], "title": n.get("title") or "", "paper_id": n.get("paper_id") or "",
                    "accepted_by": (n.get("accepted_by") or {}).get("username"),
                    "summary": [{"kind": st.get("kind"), "text": (st.get("text") or "")[:160],
                                 "claim_support": st.get("claim_support")} for st in n.get("statements") or []]}
                   for n in notes]
    latest_ver, _ = prov.section_text(pid, section) if section else (None, None)
    return jsonify(success=True, rev=doc.get("_rev", 0), links=described, notes=choices, latest_s_ver=latest_ver)


@bp.route('/api/provenance/link', methods=['POST'])
def link_paragraph_provenance():
    data = request.get_json(silent=True) or {}
    pid = _resolve_formal_project_pid(data.get('pid'))
    section = str(data.get('section') or '').strip()
    uid = _session_uid()
    if not pid:
        return jsonify(success=False, message='Invalid pid'), 404
    if uid is not None and not _socket_can_write_section(uid, pid, section):
        return jsonify(success=False, message='Forbidden'), 403
    if data.get('kind') == 'evidence_note' and not _can_pick_notes(uid, pid):
        return jsonify(success=False, message='只有 owner／editor 可以連結 Study 證據筆記。'), 403
    try:
        link, rev = prov.add_link(_citation_data_root(), pid, data, _actor(), data.get('base_rev'), _notes(pid))
    except (BadRequest, Conflict) as exc:
        return _error(exc)
    return jsonify(success=True, link=link, rev=rev), 201


@bp.route('/api/provenance/retract', methods=['POST'])
def retract_paragraph_provenance():
    data = request.get_json(silent=True) or {}
    pid = _resolve_formal_project_pid(data.get('pid'))
    uid = _session_uid()
    if not pid:
        return jsonify(success=False, message='Invalid pid'), 404
    doc = prov.load(_citation_data_root(), pid)
    target = next((l for l in doc["links"] if l.get("link_id") == data.get('link_id')), None)
    if target is None:
        return jsonify(success=False, message='找不到這筆段落出處'), 404
    if uid is not None and not _socket_can_write_section(uid, pid, target.get("section") or ""):
        return jsonify(success=False, message='Forbidden'), 403
    try:
        link, rev = prov.retract_link(_citation_data_root(), pid, data.get('link_id'), data.get('reason'), _actor(),
                                      data.get('base_rev'))
    except (BadRequest, Conflict) as exc:
        return _error(exc)
    return jsonify(success=True, link=link, rev=rev)


@bp.route('/api/provenance/export', methods=['GET'])
def export_paragraph_provenance():
    pid = _resolve_formal_project_pid(request.args.get('pid'))
    uid = _session_uid()
    if not pid:
        return jsonify(success=False, message='Invalid pid'), 404
    if uid is not None and not _socket_can_read_paper(uid, pid):
        return jsonify(success=False, message='Forbidden'), 403
    doc = prov.load(_citation_data_root(), pid)
    notes = _notes(pid)
    described = prov.describe(pid, doc["links"], notes, _anchor_status(pid, notes), redact_unshared=_review_scoped(pid))
    return Response(prov.to_markdown(pid, described), mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={pid}_paragraph_provenance.md"})
