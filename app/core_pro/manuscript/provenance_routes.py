# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/provenance_routes.py
# 模組定位: Manuscript 段落出處 API（R4）；資料與語意在 paragraph_provenance.py。
# 權限: 藍圖層 _enforce_manuscript_acl 先要求專案成員；這裡再做章節層：讀 = _socket_can_read_section，
#       寫 = _socket_can_write_section，整份匯出 = _socket_can_read_paper。教授審閱身分只看到已請求審閱的筆記內容。
#       證據筆記選單只給 owner／editor；進不了 Study 的人（限定編輯 coauthor）看不到任何筆記內容，
#       只看到「這段連到一則證據筆記」（F1：段落出處不得變成讀 Study 筆記的側門）。
# 不阻擋: 出處狀態（段落改了、筆記改了、原文改了、位置不明確）只是提示；寫作、保存、匯出照常。
#       唯一會拒絕的是「無法確定是哪一處」的新連結（R6）：出處必須指到確定的位置，不能猜。
# 驗證: python -m pytest test/unit/test_paragraph_provenance.py -q
from flask import Response, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict

from app.core_pro.manuscript import paragraph_provenance as prov
from app.core_pro.study.study_anchors import TEXT_FOUND as _TEXT_FOUND
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


def _current_anchors(pid, notes):
    """用目前的原文重新定位每個錨點（不寫回）；note_id → [每個錨點目前的 {status, ref, page, version_changed}]。

    建立筆記時就讀不到原文的錨點（status=unavailable）從未核對過，目前狀態一律是 unavailable。
    """
    from app.core_pro.study.study_routes import _attach_current_anchor_status
    copies = [dict(n, anchors=[dict(a) for a in n.get("anchors") or []]) for n in notes]
    _attach_current_anchor_status(pid, copies)
    return {n.get("note_id"): [a.get("current") or {"status": "unavailable"} for a in n.get("anchors") or []] for n in copies}


def _anchor_status(notes, current):
    return {n.get("note_id"): [c.get("status") for a, c in zip(n.get("anchors") or [], current.get(n.get("note_id")) or [])
                               if a.get("status") != "unavailable"] for n in notes}


_QUOTE_BLOCKED = {"not_found": "目前原文中找不到這段引文（原文可能已重跑）；請回 Study 重新核對。",
                  "ambiguous": "目前原文中有多處相同文字，無法唯一定位；請回 Study 重新選取。",
                  "unavailable": "目前讀不到原文，無法重新核對這段引文。"}


def _quote_choices(note, current):
    """R6：每個有引文的證據都列出來，但只有「用目前原文重新核對仍找得到」的可以插入；譯文一律標成機器翻譯。"""
    out = []
    for index, (a, cur) in enumerate(zip(note.get("anchors") or [], current or [])):
        if not a.get("quote"):
            continue
        status = cur.get("status") or "unavailable"
        side = a.get("side") if a.get("side") in prov.QUOTE_SIDES else "original"
        out.append({"anchor_index": index, "quote": a.get("quote"), "side": side, "machine_translation": side == "translation",
                    "match_kind": a.get("match_kind") or "quote", "paper_id": a.get("paper_id") or note.get("paper_id") or "",
                    "page": cur.get("page") or a.get("page"), "ref": cur.get("ref") or a.get("ref"),
                    "current_status": status, "version_changed": bool(cur.get("version_changed")),
                    "status_label": prov.QUOTE_STATUS_LABELS.get(status, status),
                    "insertable": status in _TEXT_FOUND, "blocked_reason": _QUOTE_BLOCKED.get(status)})
    return out


def _review_scoped(pid):
    from app.security import current_user_review_scoped
    return current_user_review_scoped(pid)


def _no_study_access(uid, pid):
    """進不了 Study 的成員（限定編輯 coauthor）看不到任何 Study 筆記內容（F1）。"""
    if uid is None:
        return False
    from app.security import can_access_module
    return not can_access_module(uid, pid, "study")


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
    current = _current_anchors(pid, notes)
    described = prov.describe(pid, links, notes, _anchor_status(notes, current), redact_unshared=_review_scoped(pid),
                              redact_all=_no_study_access(uid, pid))
    choices = []
    if _can_pick_notes(uid, pid) and not _review_scoped(pid):
        choices = [{"note_id": n["note_id"], "title": n.get("title") or "", "paper_id": n.get("paper_id") or "",
                    "accepted_by": (n.get("accepted_by") or {}).get("username"),
                    "summary": [{"kind": st.get("kind"), "text": (st.get("text") or "")[:160],
                                 "claim_support": st.get("claim_support")} for st in n.get("statements") or []],
                    # R6：引文用目前的原文重新核對；找不到的不能插入，譯文標成機器翻譯
                    "quotes": _quote_choices(n, current.get(n["note_id"]))}
                   for n in notes]
    latest_ver, latest_text = prov.section_text(pid, section) if section else (None, None)
    cov = prov.coverage(latest_text, described) if section and latest_text is not None else None
    return jsonify(success=True, rev=doc.get("_rev", 0), links=described, notes=choices, latest_s_ver=latest_ver,
                   coverage=cov)


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
    data = {k: v for k, v in data.items() if k != 'quote_current'}  # 核對結果只能由伺服器產生
    notes = _notes(pid)
    if data.get('kind') == 'evidence_note' and data.get('quote_anchor_index') is not None:
        note = next((n for n in notes if n.get("note_id") == str(data.get('note_id') or '')), None)
        index = data.get('quote_anchor_index')
        if note is not None and type(index) is int and 0 <= index < len(note.get("anchors") or []):
            data['quote_current'] = _current_anchors(pid, [note])[note["note_id"]][index]
    try:
        link, rev = prov.add_link(_citation_data_root(), pid, data, _actor(), data.get('base_rev'), notes)
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
    described = prov.describe(pid, doc["links"], notes, _anchor_status(notes, _current_anchors(pid, notes)),
                              redact_unshared=_review_scoped(pid), redact_all=_no_study_access(uid, pid))
    cov = {}
    for section in sorted({d.get("section") for d in described if d.get("section")}):
        _, text = prov.section_text(pid, section)
        if text is not None:
            cov[section] = prov.coverage(text, [d for d in described if d.get("section") == section])
    return Response(prov.to_markdown(pid, described, cov), mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={pid}_paragraph_provenance.md"})
