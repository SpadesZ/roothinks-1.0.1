# 目的：獨立研究預設可用；教授與團隊授權由雙方確認，學術身分只作背景。
# 上下游：協作頁／帳號頁 → session 與邀請 → WorkspaceMember、ChapterAssignment、MentorLink、RevisionLog。
# 邊界：邀請碼只回傳 owner 一次；不發信、不信任職銜、不授予 admin，不刪研究或審閱歷史。
# 驗證：python -m pytest test/unit/test_sai_invitations.py test/unit/test_sai_autonomy.py -q
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from uuid import uuid4

from filelock import FileLock
from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from werkzeug.exceptions import BadRequest, Conflict, Forbidden

from app import csrf, db, limiter
from app.models import (AcademicProfile, ChapterAssignment, MentorLink, Project, ProjectInvitation,
                        RevisionLog, User, WorkspaceMember, _as_utc)
from app.security import _normalize_pid_aliases, build_lock_path, get_workspace_role, validate_id
from app.services.formal_project_sync import resolve_data_root

bp = Blueprint('collaboration', __name__)


def canonical_project(pid):
    pid = validate_id(pid, 'project_id')
    project = Project.query.filter_by(project_id=pid).first()
    if not project:
        project = Project.query.filter(Project.project_id.in_(_normalize_pid_aliases(pid))).first()
    return project.project_id if project else pid


def membership_lock(pid):
    # ponytail: 同一專案授權變更串行化；不鎖研究內容，也不聲稱支援多主機。
    key = str(pid).removesuffix('-p')
    return FileLock(build_lock_path(os.path.join(resolve_data_root(), key, 'membership')), timeout=10)


def serialized_membership(fn):
    @wraps(fn)
    def wrapped(pid, *args, **kwargs):
        pid = canonical_project(pid)
        with membership_lock(pid):
            db.session.expire_all()
            return fn(pid, *args, **kwargs)
    return wrapped


def _owner(pid):
    if not current_user.is_authenticated or get_workspace_role(current_user.id, pid) != 'owner':
        raise Forbidden('只有本專案擁有者可以管理邀請。')


def _event(row, action, reason=''):
    db.session.add(RevisionLog(pid=row.pid, user_id=current_user.id, entity_type='project_invitation',
        entity_ref=row.id, action=action, summary=f'{row.kind}: {action}',
        payload_json=json.dumps({'reason':reason, 'role':row.role, 'sections':json.loads(row.sections_json)},ensure_ascii=False)))


def _serialize(row):
    result = row.to_dict()
    result['expired'] = _as_utc(row.expires_at) <= datetime.now(timezone.utc)
    result['history'] = [r.to_dict() | {'detail':json.loads(r.payload_json or '{}')} for r in
        RevisionLog.query.filter_by(entity_type='project_invitation',entity_ref=row.id).order_by(RevisionLog.id).all()]
    return result


def remove_project_access(pid, user_id, reason='成員已被移除'):
    """呼叫者必須已取得 membership_lock 並驗 owner／自行退出；不提交，與撤銷事件同交易。"""
    aliases = _normalize_pid_aliases(pid)
    if WorkspaceMember.query.filter(WorkspaceMember.user_id==user_id,WorkspaceMember.pid.in_(aliases),WorkspaceMember.role=='owner').first():
        if not WorkspaceMember.query.filter(WorkspaceMember.user_id!=user_id,WorkspaceMember.pid.in_(aliases),WorkspaceMember.role=='owner').first():
            raise Conflict('最後一位專案擁有者不可被移除；請先明確移交管理權。')
    WorkspaceMember.query.filter(WorkspaceMember.user_id==user_id, WorkspaceMember.pid.in_(aliases)).delete(synchronize_session=False)
    ChapterAssignment.query.filter(ChapterAssignment.user_id==user_id, ChapterAssignment.pid.in_(aliases)).delete(synchronize_session=False)
    for row in ProjectInvitation.query.filter(ProjectInvitation.pid.in_(aliases),
            ProjectInvitation.accepted_by==user_id, ProjectInvitation.status=='accepted').all():
        row.status = 'revoked'
        _event(row, 'revoked', reason)
    # MentorLink 是歷史關係，保留。所有內容入口仍需當下雙方 project ACL。


@bp.before_request
def protect_collaboration_write():
    if request.method in {'POST','PATCH','DELETE'} and current_app.config.get('WTF_CSRF_ENABLED',True):
        csrf.protect()


@bp.route('/collaboration')
@login_required
def workspace():
    return render_template('collaboration.html')


@bp.route('/api/collaboration/profile', methods=['GET','POST'])
@login_required
def academic_profile():
    profile = db.session.get(AcademicProfile,current_user.id)
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if not isinstance(data,dict):
            raise BadRequest('JSON 必須是物件。')
        role = data.get('academic_role')
        affiliation = str(data.get('affiliation') or '').strip()
        if role not in {'researcher','professor'} or len(affiliation)>200:
            raise BadRequest('請選研究者或教授；所屬單位上限 200 字。')
        if profile is None:
            profile = AcademicProfile(user_id=current_user.id)
            db.session.add(profile)
        profile.academic_role, profile.affiliation = role, affiliation
        db.session.commit()
    return jsonify(success=True, profile=profile.to_dict() if profile else dict(
        academic_role='researcher',affiliation='',verification='self_declared'))


@bp.route('/api/collaboration/project/<pid>/invitations',methods=['GET','POST'])
@login_required
@serialized_membership
def project_invitations(pid):
    _owner(pid)
    if request.method == 'GET':
        return jsonify(success=True,invitations=[_serialize(r) for r in ProjectInvitation.query.filter(
            ProjectInvitation.pid.in_(_normalize_pid_aliases(pid))).order_by(ProjectInvitation.created_at).all()])
    data = request.get_json(silent=True) or {}
    if not isinstance(data,dict):
        raise BadRequest('JSON 必須是物件。')
    email = User.normalize_email(data.get('email'))
    kind, role = data.get('kind'), data.get('role')
    sections = data.get('sections',[])
    if not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',email) or len(email)>254:
        raise BadRequest('請填寫有效的受邀 Email。')
    if email == current_user.email:
        raise BadRequest('不能邀請自己。')
    if kind not in {'professor','team'} or role not in {'viewer','editor','coauthor'}:
        raise BadRequest('請選教授審閱或團隊協作及合法權限。')
    if kind == 'professor' and role != 'viewer':
        raise BadRequest('教授邀請只授予審閱權；編輯權必須另外由 owner 明確設定。')
    if not isinstance(sections,list) or len(sections)>50 or any(not isinstance(s,str) or not re.fullmatch(r'[\w\-]{1,50}',s) for s in sections):
        raise BadRequest('章節清單格式錯誤。')
    if (role=='coauthor' and not sections) or (role!='coauthor' and sections):
        raise BadRequest('限定編輯必須指定章節，其他權限不能假設章節限制。')
    target = User.find_by_email(email)
    if target and get_workspace_role(target.id,pid):
        raise Conflict('此帳號已有專案權限；請先核對既有授權，不能由邀請覆蓋。')
    active = ProjectInvitation.query.filter_by(pid=pid,invited_email=email).filter(
        ProjectInvitation.status.in_(['pending','accepted'])).all()
    if any(r.status=='accepted' or _as_utc(r.expires_at)>datetime.now(timezone.utc) for r in active):
        raise Conflict('已有有效邀請；請先撤銷或完成。')
    token = secrets.token_urlsafe(32)
    row = ProjectInvitation(id=uuid4().hex,pid=pid,invited_email=email,created_by=current_user.id,
        kind=kind,role=role,sections_json=json.dumps(sorted(set(sections))),token_hash=hashlib.sha256(token.encode()).hexdigest(),
        status='pending',expires_at=datetime.now(timezone.utc)+timedelta(days=7))
    db.session.add(row)
    _event(row,'created')
    db.session.commit()
    return jsonify(success=True,invitation=_serialize(row),token=token),201


@bp.route('/api/collaboration/invitations')
@login_required
def invitation_inbox():
    return jsonify(success=True,invitations=[_serialize(r) for r in ProjectInvitation.query.filter_by(
        invited_email=current_user.email).order_by(ProjectInvitation.created_at.desc()).all()])


@bp.route('/api/collaboration/invitations/<iid>/<action>',methods=['POST'])
@login_required
@limiter.limit('20 per minute')
def invitation_action(iid,action):
    if action not in {'accept','decline','revoke'}:
        raise BadRequest('未知邀請動作。')
    row = db.session.get(ProjectInvitation,iid)
    if row is None:
        raise Forbidden('邀請不可用。')
    with membership_lock(row.pid):
        db.session.expire_all()
        row = db.session.get(ProjectInvitation,iid)
        data = request.get_json(silent=True) or {}
        if not isinstance(data,dict):
            raise BadRequest('JSON 必須是物件。')
        if action == 'revoke':
            if current_user.id != row.accepted_by:
                _owner(row.pid)
            if row.status not in {'pending','accepted'}:
                raise Conflict('此邀請已結束。')
            reason = str(data.get('reason') or '').strip()
            if not reason or len(reason)>1000:
                raise BadRequest('請填寫撤銷理由（上限 1000 字）。')
            if row.status == 'accepted':
                remove_project_access(row.pid,row.accepted_by,reason)
            else:
                row.status = 'revoked'
                _event(row,'revoked',reason)
        else:
            if current_user.email != row.invited_email:
                raise Forbidden('邀請不可用。')
            if row.status != 'pending' or _as_utc(row.expires_at)<=datetime.now(timezone.utc):
                raise Conflict('邀請已使用、過期或撤銷。')
            if action == 'decline':
                row.status = 'declined'
                _event(row,'declined')
            else:
                token = str(data.get('token') or '')
                if len(token)>200 or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),row.token_hash):
                    raise Forbidden('邀請碼無效。')
                if get_workspace_role(row.created_by,row.pid) != 'owner':
                    raise Conflict('邀請者已無專案管理權，請重新取得邀請。')
                if get_workspace_role(current_user.id,row.pid):
                    raise Conflict('已有其他授權，不能由邀請覆蓋。')
                db.session.add(WorkspaceMember(pid=row.pid,user_id=current_user.id,role=row.role))
                for section in json.loads(row.sections_json):
                    db.session.add(ChapterAssignment(pid=row.pid,section_key=section,user_id=current_user.id,assigned_by=row.created_by))
                if row.kind == 'professor' and not MentorLink.query.filter_by(mentor_id=current_user.id,mentee_id=row.created_by).first():
                    db.session.add(MentorLink(mentor_id=current_user.id,mentee_id=row.created_by))
                row.accepted_by, row.status = current_user.id, 'accepted'
                _event(row,'accepted')
        db.session.commit()
        return jsonify(success=True,invitation=_serialize(row))
