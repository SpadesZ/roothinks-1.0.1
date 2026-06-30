#路徑(./app/core_proc/manuscript/manuscript_routes.py)
#版本 v1.5 (Unified Asset Gallery Route Bridge + Word direct import API)
#更版時間 20260421-1445
# inner comment: 嚴格遵守人機協作定律，全量保留 v1.3 邏輯。統一 Socket 命名規範以對接前端 ManuUI (v0.3) 的游標注入與素材庫引擎。
# CHANGE_PLAN_STUDY_FLOWB_2026-04-20: MVP prototype - add direct Word(.docx) import API for 2B/2C canvases.

import os
import json
import threading
import time
import logging
import atexit
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, render_template, request, send_from_directory, jsonify, current_app, g
from app import socketio, db
from flask_socketio import emit
from socketio.exceptions import ConnectionRefusedError
from app.llm_service.matching_tasks.task_8drafter import Task8Drafter
from app.core_pro.manuscript.manuscript_io import ManuscriptIO
from app.core_pro.manuscript.manuscript_image import ManuscriptImage
from app.core_pro.manuscript.model_section import ManuSectionConfig
from app.models import Project
from app.security import (
    check_ownership,
    get_request_token,
    load_json_locked,
    require_socket_auth,
    safe_join_under,
    validate_id,
    write_json_locked,
)

bp = Blueprint('manuscript', __name__, url_prefix='/manuscript')
ai_drafter = Task8Drafter()
_job_lock = threading.Lock()
_job_state = {}
_sid_auth_lock = threading.Lock()
_sid_auth_tokens = {}
logger = logging.getLogger("ManuscriptRoutes")
_SOCKET_MISSING_PID_MSG = "Missing project id (pid). Please select a formal project from Manuscript 1.1 list."


def _read_positive_int_env(key, default_val):
    try:
        val = int(os.environ.get(key, str(default_val)).strip())
        return val if val > 0 else default_val
    except Exception:
        return default_val


_CHAT_EXECUTOR = ThreadPoolExecutor(max_workers=_read_positive_int_env("MANUSCRIPT_CHAT_MAX_WORKERS", 4))
_MAX_CHAT_USER_MSG_CHARS = _read_positive_int_env("MANUSCRIPT_CHAT_MAX_INPUT_CHARS", 4000)
_MAX_CHAT_CONTEXT_CHARS = _read_positive_int_env("MANUSCRIPT_CHAT_MAX_CONTEXT_CHARS", 24000)


@atexit.register
def _shutdown_chat_executor():
    try:
        _CHAT_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


@bp.before_request
def _enforce_manuscript_acl():
    if not request.path.startswith("/manuscript/api/"):
        return None
    raw_pid = (
        request.view_args.get("pid") if isinstance(request.view_args, dict) and request.view_args.get("pid") else None
    ) or request.args.get("pid") or ((request.get_json(silent=True) or {}).get("pid") if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
    if not raw_pid:
        return None
    pid = _resolve_formal_project_pid(raw_pid)
    if not pid:
        return jsonify({"ok": False, "message": "Invalid pid"}), 404
    token = getattr(g, "auth_token", "")
    if token and not check_ownership(token, pid):
        return jsonify({"ok": False, "message": "Forbidden"}), 403
    return None


def _set_sid_token(sid, token):
    with _sid_auth_lock:
        _sid_auth_tokens[sid] = token


def _get_sid_token(sid):
    with _sid_auth_lock:
        return _sid_auth_tokens.get(sid, "")


def _del_sid_token(sid):
    with _sid_auth_lock:
        _sid_auth_tokens.pop(sid, None)


def _socket_auth_enabled() -> bool:
    return bool(current_app.config.get("API_AUTH_ENABLED", False))


def _ensure_socket_project_access(pid: str, forbidden_event: str = "", forbidden_payload: dict = None, emit_forbidden_msg: bool = True) -> bool:
    if not _socket_auth_enabled():
        return True
    sid_token = _get_sid_token(request.sid)
    if sid_token and check_ownership(sid_token, pid):
        return True
    if emit_forbidden_msg:
        emit('sys_msg', {'msg': 'Forbidden project access.'})
    if forbidden_event:
        emit(forbidden_event, forbidden_payload or {})
    return False


def _resolve_socket_pid_or_emit(data, missing_msg: str = _SOCKET_MISSING_PID_MSG, missing_event: str = "", missing_payload: dict = None):
    pid = _resolve_pid((data or {}).get('pid') if isinstance(data, dict) else None)
    if pid:
        return pid
    if missing_msg:
        emit('sys_msg', {'msg': missing_msg})
    if missing_event:
        emit(missing_event, missing_payload or {})
    return ""


def _list_formal_projects():
    rows = Project.query.filter_by(status='formal').order_by(Project.created_at.desc()).all()
    token = getattr(g, "auth_token", "")
    return [
        {
            'pid': p.project_id,
            'research_title': (p.research_title or p.name or p.project_id or '').strip(),
            'name': (p.name or '').strip(),
        }
        for p in rows
        if p.project_id and (not token or check_ownership(token, p.project_id))
    ]


def _set_job_state(job_id, **kwargs):
    with _job_lock:
        cur = _job_state.get(job_id, {})
        cur.update(kwargs)
        _job_state[job_id] = cur


def _get_job_state(job_id):
    with _job_lock:
        return dict(_job_state.get(job_id, {}))


def _delete_job_state(job_id):
    with _job_lock:
        _job_state.pop(job_id, None)


def _is_cancelled(job_id):
    st = _get_job_state(job_id)
    return bool(st.get('cancelled', False))


def _mark_jobs_cancelled_by_sid(sid):
    with _job_lock:
        for k, v in _job_state.items():
            if v.get('sid') == sid:
                v['cancelled'] = True


def _sid_connected(sid):
    try:
        return bool(socketio.server.manager.is_connected(sid, '/manu_ws'))
    except Exception:
        return False


def _emit_job_progress(sid, job_id, stage, message='', progress=0):
    if not _sid_connected(sid):
        return
    socketio.emit('job_progress', {
        'job_id': job_id,
        'stage': stage,
        'message': message,
        'progress': progress,
    }, to=sid, namespace='/manu_ws')


def _process_chat_job(app_obj, sid, job_id, payload):
    started_at = time.time()
    try:
        with app_obj.app_context():
            if not _sid_connected(sid):
                _set_job_state(job_id, cancelled=True)
                return
            _emit_job_progress(sid, job_id, 'processing', 'Drafter is processing request.', 25)
            if _is_cancelled(job_id):
                if _sid_connected(sid):
                    socketio.emit('job_cancelled', {'job_id': job_id}, to=sid, namespace='/manu_ws')
                return

            logger.info(
                "[manu_chat] job_start id=%s sid=%s pid=%s section=%s msg_len=%s",
                job_id,
                sid,
                payload.get('pid'),
                payload.get('section'),
                len(str(payload.get('user_msg', '') or '')),
            )
            ai_response = ai_drafter.process_request(
                user_prompt=payload.get('user_msg', ''),
                context_text=payload.get('context_text', ''),
                target_lang=payload.get('target_lang', 'Academic English'),
                attachment=payload.get('attachment'),
                import_type=payload.get('import_type', 'other'),
                pid=payload.get('pid'),
                title=payload.get('title', 'Untitled Paper'),
                section=payload.get('section', 'general'),
                s_ver=payload.get('s_ver', '0.1'),
            )

            if _is_cancelled(job_id):
                if _sid_connected(sid):
                    socketio.emit('job_cancelled', {'job_id': job_id}, to=sid, namespace='/manu_ws')
                return

            _emit_job_progress(sid, job_id, 'finalizing', 'Persisting response.', 85)

            msg_type = ai_response.get('type', 'text') if isinstance(ai_response, dict) else 'text'
            msg_content = ai_response.get('content', '') if isinstance(ai_response, dict) else str(ai_response)
            save_chat_history(payload.get('pid'), payload.get('section'), 'ai', msg_content, msg_type)

            # 向後相容：保留舊事件 ai_response
            if not _sid_connected(sid):
                return
            socketio.emit('ai_response', ai_response, to=sid, namespace='/manu_ws')
            latency_ms = int((time.time() - started_at) * 1000)
            logger.info(
                "[manu_chat] job_done id=%s sid=%s pid=%s section=%s latency_ms=%s",
                job_id,
                sid,
                payload.get('pid'),
                payload.get('section'),
                latency_ms,
            )
            socketio.emit('job_done', {
                'job_id': job_id,
                'stage': 'done',
                'latency_ms': latency_ms,
            }, to=sid, namespace='/manu_ws')

    except Exception as e:
        logger.error("[_process_chat_job] %s", e, exc_info=True)
        if _sid_connected(sid):
            socketio.emit('job_error', {
                'job_id': job_id,
                'stage': 'error',
                'message': "Internal server error",
            }, to=sid, namespace='/manu_ws')
            socketio.emit('sys_msg', {'msg': 'System Error.'}, to=sid, namespace='/manu_ws')
    finally:
        _delete_job_state(job_id)


def _base_pid(pid):
    return pid[:-2] if str(pid).endswith('-p') else str(pid)


def _resolve_formal_project_pid(candidate):
    p = str(candidate or '').strip()
    if not p:
        return ''
    try:
        if p.endswith('-p'):
            validate_id(p[:-2], "project_id")
        else:
            validate_id(p, "project_id")
    except Exception:
        return ''

    candidates = [p]
    if p.endswith('-p'):
        base = p[:-2]
        if base:
            candidates.append(base)
    else:
        candidates.append(f"{p}-p")
    candidates = list(dict.fromkeys([c for c in candidates if c]))

    rows = (
        Project.query
        .filter(Project.status == 'formal', Project.project_id.in_(candidates))
        .all()
    )
    found = {r.project_id for r in rows}
    for c in candidates:
        if c in found:
            return c
    return ''


def _formal_pid(pid):
    return _resolve_formal_project_pid(pid)


def _get_latest_formal_pid():
    try:
        proj = Project.query.filter_by(status='formal').order_by(Project.created_at.desc()).first()
        if proj and proj.project_id:
            return proj.project_id
    except Exception:
        pass
    return ''


def _resolve_pid(candidate=None):
    pid = _resolve_formal_project_pid(candidate)
    if pid:
        return pid
    if str(candidate or '').strip():
        return ''
    return _get_latest_formal_pid()


def _load_sync_status(pid):
    """回傳 Manuscript 與前置模組資料的同步可用性摘要。"""
    bpid = _base_pid(pid)
    paq_candidates = [
        os.path.join('data', bpid, 'paq', 'taxonomy_manual_update.json'),
        os.path.join('data', bpid, 'paq', 'paq', 'taxonomy_manual_update.json'),
    ]
    has_paq = any(os.path.exists(p) for p in paq_candidates)
    has_literature = os.path.exists(os.path.join('data', bpid, 'search_results.json'))
    note_candidates = [
        os.path.join('data', f'{pid}', 'study', f'{pid}_note.json'),
        os.path.join('data', f'{bpid}-p', 'study', f'{bpid}-p_note.json'),
        os.path.join('data', 'note', f'{pid}_note.json'),
        os.path.join('data', 'note', f'{bpid}_note.json'),
        os.path.join('data', bpid, 'study_notes.txt'),
        os.path.join('data', pid, 'study_notes.txt'),
    ]
    has_study = any(os.path.exists(p) for p in note_candidates)
    return {
        'has_paq': has_paq,
        'has_literature': has_literature,
        'has_study': has_study,
    }


def _get_or_init_sections(pid):
    pid = _resolve_formal_project_pid(pid)
    if not pid:
        return []
    rows = ManuSectionConfig.query.filter_by(pid=pid).order_by(ManuSectionConfig.order_index.asc()).all()
    if not rows:
        ManuSectionConfig.init_default_sections(pid)
        rows = ManuSectionConfig.query.filter_by(pid=pid).order_by(ManuSectionConfig.order_index.asc()).all()
    return rows

def save_chat_history(pid, section, role, content, msg_type='text'):
    formal_pid = _formal_pid(pid)
    if not formal_pid:
        return
    dir_path = os.path.join("data", formal_pid, "manuscript", "chat")
    os.makedirs(dir_path, exist_ok=True)
    file_path = os.path.join(dir_path, f"chat_{section}.json")
    
    history = []
    if os.path.exists(file_path):
        try:
            history = load_json_locked(file_path, [])
        except Exception:
            history = []
            
    history.append({"role": role, "content": content, "type": msg_type})
    
    write_json_locked(file_path, history)

@bp.route('/')
def manuscript_page():
    pid = _resolve_pid(request.args.get('pid'))
    if not pid:
        return render_template('manuscript_workspace.html', pid='', initial_title='')
    initial_title = pid
    try:
        proj = Project.query.filter_by(project_id=pid).first()
        if proj:
            initial_title = (proj.research_title or proj.name or pid).strip()
    except Exception:
        pass
    return render_template('manuscript_workspace.html', pid=pid, initial_title=initial_title)


@bp.route('/api/bootstrap/<pid>', methods=['GET'])
def manuscript_bootstrap(pid):
    """提供前端初始化所需資料：標題預填、章節設定、前置模組同步狀態。"""
    formal_projects = []
    active_project = _resolve_formal_project_pid(pid)
    try:
        formal_projects = _list_formal_projects()
        if not active_project and formal_projects:
            active_project = formal_projects[0].get('pid')
    except Exception:
        pass

    bootstrap_pid = _resolve_formal_project_pid(active_project)
    if not bootstrap_pid:
        return jsonify({
            'ok': False,
            'message': 'No valid formal project selected.',
            'pid': '',
            'active_project': '',
            'initial_title': '',
            'sections': [],
            'sync_status': {'has_paq': False, 'has_literature': False, 'has_study': False},
            'formal_projects': formal_projects,
        }), 404

    initial_title = bootstrap_pid
    try:
        from app.models import Project
        bpid = _base_pid(bootstrap_pid)
        proj = Project.query.filter_by(project_id=bootstrap_pid).first() or Project.query.filter_by(project_id=bpid).first()
        if proj:
            initial_title = (proj.research_title or proj.name or bootstrap_pid).strip()
    except Exception:
        pass

    sections = _get_or_init_sections(bootstrap_pid)
    section_payload = [
        {
            'id': s.section_key,
            'label': s.section_name,
            'is_fixed': bool(s.is_fixed),
            'order_index': int(s.order_index),
        }
        for s in sections
    ]
    return jsonify({
        'ok': True,
        'pid': bootstrap_pid,
        'active_project': active_project,
        'initial_title': initial_title,
        'sections': section_payload,
        'sync_status': _load_sync_status(bootstrap_pid),
        'formal_projects': formal_projects,
    })


@bp.route('/api/formal_projects', methods=['GET'])
def manuscript_formal_projects():
    req_pid = (request.args.get('pid') or '').strip()
    projects = _list_formal_projects()
    active = req_pid if any(p.get('pid') == req_pid for p in projects) else ''
    if not active and projects:
        active = projects[0].get('pid', '')
    return jsonify({'ok': True, 'formal_projects': projects, 'active_project': active})


@bp.route('/api/sections/<pid>', methods=['GET'])
def get_sections(pid):
    pid = _resolve_formal_project_pid(pid)
    if not pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404
    rows = _get_or_init_sections(pid)
    payload = [
        {
            'id': s.section_key,
            'label': s.section_name,
            'is_fixed': bool(s.is_fixed),
            'order_index': int(s.order_index),
        }
        for s in rows
    ]
    return jsonify({'ok': True, 'sections': payload})


@bp.route('/api/sections/<pid>', methods=['POST'])
def save_sections(pid):
    pid = _resolve_formal_project_pid(pid)
    if not pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404

    data = request.get_json(silent=True) or {}
    sections = data.get('sections', [])
    if not isinstance(sections, list) or not sections:
        return jsonify({'ok': False, 'message': 'Invalid sections payload'}), 400

    try:
        # 保留固定章節不可改名/不可刪除規則
        existing = ManuSectionConfig.query.filter_by(pid=pid).all()
        existing_fixed = {r.section_key: r.section_name for r in existing if r.is_fixed}

        incoming_keys = {str(s.get('id', '')).strip() for s in sections}
        for fixed_key in existing_fixed:
            if fixed_key not in incoming_keys:
                return jsonify({'ok': False, 'message': f'Fixed section missing: {fixed_key}'}), 400

        # 全量覆蓋，避免排序殘留
        ManuSectionConfig.query.filter_by(pid=pid).delete()

        for idx, sec in enumerate(sections):
            sid = str(sec.get('id', '')).strip()
            label = str(sec.get('label', '')).strip()
            if not sid or not label:
                continue
            is_fixed = bool(sec.get('is_fixed', False))
            if sid in existing_fixed:
                is_fixed = True
                label = existing_fixed[sid]

            row = ManuSectionConfig(
                pid=pid,
                section_key=sid,
                section_name=label,
                order_index=(idx + 1) * 10,
                is_fixed=is_fixed,
            )
            db.session.add(row)

        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        logger.error("[save_sections] %s", e, exc_info=True)
        return jsonify({'ok': False, 'message': 'Internal server error'}), 500


@bp.route('/api/import_word/<pid>', methods=['POST'])
def import_word_to_canvas(pid):
    pid = _resolve_formal_project_pid(pid)
    if not pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404

    data = request.get_json(silent=True) or {}
    filename = str(data.get('filename') or '').strip()
    mime = str(data.get('mime') or '').strip().lower()
    content = str(data.get('content') or '')

    if not filename or not content:
        return jsonify({'ok': False, 'message': 'Missing filename/content'}), 400

    name_lower = filename.lower()
    is_docx = name_lower.endswith('.docx') or mime == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    is_legacy_doc = (name_lower.endswith('.doc') and not name_lower.endswith('.docx')) or mime == 'application/msword'
    if is_legacy_doc:
        return jsonify({'ok': False, 'message': 'Legacy .doc is not supported. Please convert to .docx.'}), 400
    if not is_docx:
        return jsonify({'ok': False, 'message': 'Only .docx is supported for direct import.'}), 400

    max_b64_chars = _read_positive_int_env("MANUSCRIPT_WORD_IMPORT_MAX_B64_CHARS", 12000000)
    if len(content) > max_b64_chars:
        return jsonify({'ok': False, 'message': 'Word file payload too large.'}), 413

    try:
        extracted = ai_drafter._extract_attachment_text({
            'name': filename,
            'mime': mime,
            'content': content,
        })
        clean_text = str(extracted or '').replace('\x00', '').strip()
        if not clean_text:
            return jsonify({'ok': False, 'message': 'Unable to extract text from .docx.'}), 422

        max_text_chars = _read_positive_int_env("MANUSCRIPT_WORD_IMPORT_MAX_TEXT_CHARS", 240000)
        truncated = False
        if len(clean_text) > max_text_chars:
            clean_text = clean_text[:max_text_chars].rstrip()
            truncated = True

        return jsonify({
            'ok': True,
            'text': clean_text,
            'chars': len(clean_text),
            'truncated': truncated,
        })
    except Exception as e:
        logger.error("[import_word_to_canvas] %s", e, exc_info=True)
        return jsonify({'ok': False, 'message': 'Internal server error'}), 500


@bp.route('/image/<pid>/<filename>')
def serve_image(pid, filename):
    formal_pid = _formal_pid(pid)
    if not formal_pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404
    if current_app.config.get("API_AUTH_ENABLED", False):
        token = get_request_token()
        if not token:
            return jsonify({'ok': False, 'message': 'Unauthorized'}), 401
        if not check_ownership(token, formal_pid):
            return jsonify({'ok': False, 'message': 'Forbidden'}), 403
    data_root = os.path.join(os.getcwd(), 'data')
    img_dir = safe_join_under(data_root, formal_pid, 'manuscript', 'image')
    return send_from_directory(img_dir, filename, as_attachment=False)

@socketio.on('connect', namespace='/manu_ws')
def handle_connect(auth=None):
    try:
        token = require_socket_auth(auth)
        if token:
            _set_sid_token(request.sid, token)
    except Exception:
        raise ConnectionRefusedError("Unauthorized")
    emit('sys_msg', {'msg': 'Connected to Roothinks-Manuscript Core (Drafter V1.4).'})

@socketio.on('cmd_load_chat', namespace='/manu_ws')
def handle_load_chat(data):
    data = data or {}
    pid = data.get('pid')
    section = data.get('section', 'title')
    formal_pid = _formal_pid(pid)
    if not formal_pid:
        emit('chat_history', {'section': section, 'history': []})
        return
    if not _ensure_socket_project_access(
        formal_pid,
        forbidden_event='chat_history',
        forbidden_payload={'section': section, 'history': []},
    ):
        return
    
    file_path = os.path.join("data", formal_pid, "manuscript", "chat", f"chat_{section}.json")
    history = []
    if os.path.exists(file_path):
        try:
            history = load_json_locked(file_path, [])
        except Exception:
            history = []
            
    emit('chat_history', {'section': section, 'history': history})

@socketio.on('chat_message', namespace='/manu_ws')
def handle_chat(data):
    data = data or {}
    user_msg = str(data.get('msg', '') or '').strip()
    context_text = str(data.get('context', '') or '')
    if not user_msg:
        emit('sys_msg', {'msg': 'Message is empty.'})
        return
    if len(user_msg) > _MAX_CHAT_USER_MSG_CHARS:
        emit('sys_msg', {'msg': f'Message too long. Max {_MAX_CHAT_USER_MSG_CHARS} chars.'})
        return
    if len(context_text) > _MAX_CHAT_CONTEXT_CHARS:
        context_text = context_text[:_MAX_CHAT_CONTEXT_CHARS]
    target_lang = data.get('target_lang', 'Academic English')
    attachment = data.get('attachment')
    import_type = data.get('import_type', 'other')
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    title = data.get('title', 'Untitled Paper')
    section = data.get('section', 'general')
    s_ver = data.get('s_ver', '0.1')
    
    save_chat_history(pid, section, 'user', user_msg, 'text')

    job_id = f"job_{int(time.time() * 1000)}_{os.getpid()}"
    sid = request.sid
    _set_job_state(job_id, cancelled=False, sid=sid, pid=pid, section=section)

    emit('job_queued', {
        'job_id': job_id,
        'stage': 'queued',
        'section': section,
    })
    logger.info(
        "[manu_chat] job_queued id=%s sid=%s pid=%s section=%s",
        job_id,
        sid,
        pid,
        section,
    )

    app_obj = current_app._get_current_object()
    payload = {
        'user_msg': user_msg,
        'context_text': context_text,
        'target_lang': target_lang,
        'attachment': attachment,
        'import_type': import_type,
        'pid': pid,
        'title': title,
        'section': section,
        's_ver': s_ver,
    }
    _CHAT_EXECUTOR.submit(_process_chat_job, app_obj, sid, job_id, payload)


@socketio.on('cmd_cancel_job', namespace='/manu_ws')
def handle_cancel_job(data):
    job_id = (data or {}).get('job_id', '')
    if not job_id:
        emit('sys_msg', {'msg': 'Cancel failed: missing job_id'})
        return
    st = _get_job_state(job_id)
    if not st:
        emit('job_cancelled', {'job_id': job_id, 'note': 'Job already completed or missing.'})
        return
    _set_job_state(job_id, cancelled=True)
    emit('job_cancelled', {'job_id': job_id})

@socketio.on('cmd_save_block', namespace='/manu_ws')
def handle_save_block(data):
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    title = data.get('title', 'Untitled_Paper') 
    section = data.get('section', 'general')
    content = data.get('content', '')
    s_ver = data.get('s_ver', '1.0')
    
    try:
        saved_filename = ManuscriptIO.save_block(pid, title, section, content, s_ver)
        emit('save_ack', {'target': 'block', 'msg': f'Block saved as {saved_filename}'})
    except Exception as e:
        logger.error("[cmd_save_block] %s", e, exc_info=True)
        emit('sys_msg', {'msg': 'Save Error.'})

@socketio.on('cmd_save_paper', namespace='/manu_ws')
def handle_save_paper(data):
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    title = data.get('title', 'Untitled_Paper')
    content = data.get('content', '')
    g_ver = data.get('ver', '1.0')
    
    try:
        saved_filename = ManuscriptIO.save_paper(pid, title, content, g_ver)
        emit('save_ack', {'target': 'paper', 'msg': f'Paper saved as {saved_filename}'})
    except Exception as e:
        logger.error("[cmd_save_paper] %s", e, exc_info=True)
        emit('sys_msg', {'msg': 'Save Error.'})

@socketio.on('cmd_list_papers', namespace='/manu_ws')
def handle_list_papers(data):
    data = data or {}
    pid = _resolve_socket_pid_or_emit(
        data,
        missing_msg='',
        missing_event='paper_list',
        missing_payload={'files': []},
    )
    if not pid:
        return
    if not _ensure_socket_project_access(
        pid,
        forbidden_event='paper_list',
        forbidden_payload={'files': []},
    ):
        return
    title = data.get('title', 'Untitled_Paper')
    files = ManuscriptIO.list_papers(pid, title)
    emit('paper_list', {'files': files})

@socketio.on('cmd_load_paper', namespace='/manu_ws')
def handle_load_paper(data):
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    filename = data.get('filename')
    content = ManuscriptIO.load_paper(pid, filename)
    if content:
        emit('paper_loaded', {'ok': True, 'content': content, 'filename': filename})
    else:
        emit('sys_msg', {'msg': 'Failed to load paper JSON.'})

@socketio.on('cmd_list_blocks', namespace='/manu_ws')
def handle_list_blocks(data):
    data = data or {}
    section = data.get('section', 'general')
    pid = _resolve_socket_pid_or_emit(
        data,
        missing_msg='',
        missing_event='block_list',
        missing_payload={'files': [], 'section': section},
    )
    if not pid:
        return
    if not _ensure_socket_project_access(
        pid,
        forbidden_event='block_list',
        forbidden_payload={'files': [], 'section': section},
    ):
        return
    files = ManuscriptIO.list_blocks(pid, section)
    emit('block_list', {'files': files, 'section': section})

@socketio.on('cmd_load_block', namespace='/manu_ws')
def handle_load_block(data):
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    section = data.get('section')
    filename = data.get('filename')
    content = ManuscriptIO.load_block(pid, section, filename)
    if content:
        emit('block_loaded', {'ok': True, 'content': content, 'section': section, 'filename': filename})
    else:
        emit('sys_msg', {'msg': 'Failed to load block JSON.'})

# =========================================================================
# Image & Asset Management Socket Routes
# =========================================================================
@socketio.on('cmd_save_image', namespace='/manu_ws')
def handle_save_image(data):
    """接收前端傳來的圖片與屬性，獨立解碼並存檔註冊"""
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data, missing_msg='Image save failed: Missing project id (pid).')
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    base64_data = data.get('image_data')  
    filename = data.get('filename', 'untitled.png')
    fig_id = data.get('fig_id', 'Unassigned')
    caption = data.get('caption', '')
    source = data.get('source', 'upload')
    
    if not base64_data:
        emit('sys_msg', {'msg': 'Image save failed: No image data provided.'})
        return
        
    try:
        meta = ManuscriptImage.save_image_asset(pid, base64_data, filename, fig_id, caption, source)
        emit('image_saved', {'ok': True, 'meta': meta})
        emit('sys_msg', {'msg': f"Image successfully registered as {meta['id']}"})
    except Exception as e:
        # 將錯誤詳細印在後端，並回傳給前端
        logger.error("[cmd_save_image] %s", e, exc_info=True)
        emit('sys_msg', {'msg': "Image saving error."})

# [v1.4 核心修改] 同步前端請求事件名稱為 cmd_get_image_registry
@socketio.on('cmd_get_image_registry', namespace='/manu_ws')
def handle_get_image_registry(data):
    """
    [Task 9] 處理前端請求：讀取論文素材庫 (Asset Gallery) 清單
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(
        data,
        missing_msg='',
        missing_event='image_registry_data',
        missing_payload={'ok': False, 'error': 'Missing PID', 'registry': []},
    )
    if not pid:
        return
    if not _ensure_socket_project_access(
        pid,
        forbidden_event='image_registry_data',
        forbidden_payload={'ok': False, 'error': 'Forbidden', 'registry': []},
        emit_forbidden_msg=False,
    ):
        return

    try:
        registry_list = ManuscriptImage.get_image_registry(pid)
        # 同步前端預期的回傳事件名稱
        emit('image_registry_data', {'ok': True, 'registry': registry_list})
    except Exception as e:
        logger.error("[cmd_get_image_registry] %s", e, exc_info=True)
        emit('image_registry_data', {'ok': False, 'error': 'Internal server error', 'registry': []})

@socketio.on('disconnect', namespace='/manu_ws')
def handle_disconnect():
    sid = request.sid
    _mark_jobs_cancelled_by_sid(sid)
    _del_sid_token(sid)
