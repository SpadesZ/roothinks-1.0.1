# 目的：重用 RevisionLog 追加科學建議及作者回應，保留原稿、來源與判斷歷史。
# 邊界：沒有核准／封鎖稿件的權力；來源由伺服器取，限定作者不能藉報告讀未交付文獻。
# 驗證：test_scientific_review_workflow.py、test_scientific_security.py 及真瀏覽器。
import json
from uuid import uuid4
from flask import current_app,jsonify,request
from werkzeug.exceptions import BadRequest,Conflict,Forbidden,NotFound
from app import db
from app.models import RevisionLog
from app.security import get_workspace_role,is_section_scoped_role
from app.core_pro.manuscript.chapter_routes import _safe_section
from app.core_pro.manuscript.manuscript_routes import (bp,_session_uid,_resolve_formal_project_pid,
    _socket_can_read_section,_socket_can_write_section,_paragraph_evidence_allowed,_citation_data_root)
from app.core_pro.manuscript.paragraph_context import _evidence,fingerprint
from app.core_pro.manuscript.scientific_advisory import assess_claims
from app.core_pro.manuscript.citation_ledger import list_decisions


def _project(pid):
    pid=_resolve_formal_project_pid(pid)
    if not pid:raise NotFound('找不到研究專案。')
    return pid


def _section(pid,section,write=False):
    section=_safe_section(section)
    uid=_session_uid()
    if uid is not None and not (_socket_can_write_section(uid,pid,section) if write else _socket_can_read_section(uid,pid,section)):
        raise Forbidden('沒有本章的存取權。')
    return section


def _json():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise BadRequest('JSON 必須是物件。')
    return data


def _allowed_report(pid,data):
    _section(pid,data['section'])
    uid=_session_uid()
    if uid is not None and is_section_scoped_role(get_workspace_role(uid,pid),pid):
        handed=[r.get('paragraph_context',{}).get('evidence',{}) for r in list_decisions(_citation_data_root(),pid,section_id=data['section'])]
        if any(not any(e.get('id')==s['id'] and e.get('entry_id')==s['entry_id'] and e.get('fingerprint')==s['fingerprint'] for e in handed) for s in data['sources']):
            raise Forbidden('此報告包含尚未交付本章的來源。')


def report_view(row):
    data=json.loads(row.payload_json)
    _allowed_report(row.pid,data)
    return _view(row,data)


def _view(row,data):
    current=[]
    for old in data['sources']:
        try:current.append(_evidence(_citation_data_root(),row.pid,old['entry_id'],old['id'])['fingerprint'])
        except (BadRequest,Conflict) as exc:current.append('unavailable:'+exc.description)
    stale=current != [s['fingerprint'] for s in data['sources']]
    responses=[dict(json.loads(r.payload_json),actor_id=r.user_id,created_at=r.created_at.isoformat(),id=r.id,scientific_proof=False)
        for r in RevisionLog.query.filter_by(pid=row.pid,entity_type='scientific_response',entity_ref=row.entity_ref).order_by(RevisionLog.id)]
    return dict(data['report'],report_id=row.entity_ref,pid=row.pid,section=data['section'],origin=data['origin'],
        created_at=row.created_at.isoformat(),actor_id=row.user_id,stale=stale,responses=responses,
        basis_version=data.get('basis_version',''),view_snapshot=fingerprint(dict(report=row.payload_json,current_sources=current)))


def save_advisory(pid,section,text,sources,actor_id,*,required_conditions='',origin='author_request',basis_version=''):
    report=assess_claims(text,sources,required_conditions=required_conditions)
    if actor_id is not None and not _socket_can_read_section(actor_id,pid,section):
        raise Forbidden('權限已變更；未保存新的建議報告。')
    rid=uuid4().hex
    data=dict(schema_version=1,section=section,report=report,sources=sources,origin=origin,basis_version=basis_version)
    row=RevisionLog(pid=pid,user_id=actor_id,entity_type='scientific_advisory',entity_ref=rid,action='assess',
        summary='建議式主張與證據對照；不是科學證明',payload_json=json.dumps(data,ensure_ascii=False))
    db.session.add(row);db.session.commit()
    return _view(row,data)


@bp.route('/api/scientific/assess',methods=['POST'])
def scientific_assess():
    data=_json();pid=_project(data.get('pid'));section=_section(pid,data.get('section'))
    choices=data.get('evidence',[])
    if not isinstance(choices,list) or len(choices)>8:raise BadRequest('最多選八份證據。')
    sources=[]
    for choice in choices:
        if not isinstance(choice,dict) or type(choice.get('evidence_id')) is not int or not isinstance(choice.get('entry_id'),str):
            raise BadRequest('請選擇有效的證據身分。')
        if not _paragraph_evidence_allowed(_session_uid(),pid,section,choice):raise Forbidden('本章未獲授權使用此證據。')
        evidence=_evidence(_citation_data_root(),pid,choice['entry_id'],choice['evidence_id'])
        if not any(s['id']==evidence['id'] and s['entry_id']==evidence['entry_id'] for s in sources):sources.append(evidence)
    conditions=data.get('required_conditions','')
    if not isinstance(conditions,str) or len(conditions)>4000:raise BadRequest('作者條件上限 4000 字。')
    try:report=save_advisory(pid,section,data.get('text'),sources,_session_uid(),required_conditions=conditions)
    except ValueError as exc:raise BadRequest(str(exc))
    return jsonify(report),201


@bp.route('/api/scientific/reports',methods=['GET'])
def scientific_reports():
    pid=_project(request.args.get('pid'));section=_section(pid,request.args.get('section'))
    reports=[]
    for row in RevisionLog.query.filter_by(pid=pid,entity_type='scientific_advisory').order_by(RevisionLog.id.desc()):
        data=json.loads(row.payload_json)
        if data['section']!=section:continue
        try:_allowed_report(pid,data)
        except Forbidden:continue
        reports.append(dict(report_id=row.entity_ref,origin=data['origin'],created_at=row.created_at.isoformat()))
        if len(reports)==30:break
    return jsonify(reports=reports)


def _report(pid,rid):
    row=RevisionLog.query.filter_by(pid=pid,entity_type='scientific_advisory',entity_ref=rid).first()
    if row is None:raise NotFound('找不到本專案的建議報告。')
    return row


@bp.route('/api/scientific/reports/<rid>',methods=['GET'])
def scientific_report(rid):
    return jsonify(report_view(_report(_project(request.args.get('pid')),rid)))


@bp.route('/api/scientific/reports/<rid>/responses',methods=['POST'])
def scientific_response(rid):
    data=_json();pid=_project(data.get('pid'));row=_report(pid,rid);view=report_view(row)
    _section(pid,view['section'],write=True)
    if data.get('view_snapshot')!=view['view_snapshot']:raise Conflict('來源已變更；原輸入保留，請重讀報告後再回應。')
    if data.get('claim_id') not in {c['id'] for c in view['claims']}:raise BadRequest('主張不屬於此報告。')
    if data.get('stance') not in {'retain_hypothesis','revise','disagree','need_evidence'}:raise BadRequest('請明確選擇你的處理方式。')
    reason=data.get('reason')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>4000:raise BadRequest('請填理由，最多 4000 字。')
    response=dict(claim_id=data['claim_id'],stance=data['stance'],reason=reason.strip(),view_snapshot=view['view_snapshot'],stale=view['stale'],scientific_proof=False)
    event=RevisionLog(pid=pid,user_id=_session_uid(),entity_type='scientific_response',entity_ref=rid,action=data['stance'],summary='作者對證據建議的回應',payload_json=json.dumps(response,ensure_ascii=False))
    db.session.add(event);db.session.commit()
    return jsonify(response=dict(response,actor_id=event.user_id,id=event.id)),201
