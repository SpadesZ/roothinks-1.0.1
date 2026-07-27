# 檔案路徑: app/core_pro/manuscript/manuscript_routes.py
# 產生時間: 2026-07-19 09:00 +08:00
# 版本: v1.7
# 模組定位:
#   Manuscript 模組 Flask Blueprint + Socket.IO namespace /manu_ws 控制層。
# 主要責任:
#   1. HTTP routes：bootstrap、sections、import_word、citation API、revision_log API。
#   2. Socket handler：connect（session 模式鑑權）、cmd_save_block、cmd_save_paper、
#      cmd_save_image、cmd_load_chat/chat_message（section 參數清洗）等。
#   3. [Batch C] Socket connect handler 增加 AUTH_MODE=session 鑑權邏輯。
#   4. [Batch C] cmd_save_block / cmd_save_paper 成功後寫入 RevisionLog，
#      並廣播 peer_update 到 room("ws:{pid}")（協作預留）。
#   5. [Batch C] GET /manuscript/api/revisions/<pid> 回傳最近版本紀錄。
#   6. [Batch C] cmd_load_chat 中 section 參數強制清洗，防路徑穿越。
#   7. [Batch C] 修復所有 os.path.join('data', ...) 相對路徑；
#      chat 路徑改用 safe_join_under + DATA_ROOT。
#   8. [v1.7] 稿件編輯衝突防護（rev 機制）：
#      - cmd_save_block payload 新增選填 base_rev；
#        衝突時 emit save_conflict {section, current_rev, base_rev, updated_by, updated_at}。
#      - cmd_load_block 回應帶 _rev。
#      - save_ack 帶 _rev（新欄位，舊前端忽略即可）。
# 呼叫來源:
#   app/__init__.py register_blueprint；前端 Socket.IO /manu_ws namespace。
# 輸入輸出契約:
#   - save 類 Socket 事件在 session 模式下需 editor 以上角色。
#   - RevisionLog 寫入失敗只 log warning，不影響主流程。
#   - peer_update 廣播：{kind, section, by: username} 給 room("ws:{pid}")。
# 安全邊界:
#   - session 模式：connect 未登入 → return False（拒連）。
#   - section 參數入路徑前必須通過 _safe_component。
# 維護提醒:
#   - rev 協議（base_rev 語義）：
#       base_rev 未提供 → 舊行為，直接存（向下相容，104 基線測試不破）。
#       base_rev 提供且 == 現有 _rev → 存檔，_rev+1，save_ack 帶 _rev。
#       base_rev 提供且 != 現有 _rev → 不落盤，emit save_conflict。
#   - 前端可先不消費 peer_update，後續即時協作時再接。
# ------------------------------------------------------------------------------

import os
import json
import threading
import time
import logging
import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from flask import Blueprint, render_template, request, send_from_directory, jsonify, current_app, g
from app import socketio, db
from flask_socketio import emit, join_room
from socketio.exceptions import ConnectionRefusedError
from app.llm_service.matching_tasks.task_8drafter import Task8Drafter
from app.core_pro.manuscript.manuscript_io import ManuscriptIO, _get_data_root
from app.core_pro.manuscript.manuscript_image import ManuscriptImage
from app.core_pro.manuscript import presence
from app.core_pro.manuscript.model_section import ManuSectionConfig
from app.models import Project
from app.security import (
    check_ownership,
    get_request_token,
    get_workspace_role,
    load_json_locked,
    require_socket_auth,
    safe_join_under,
    validate_id,
    write_json_locked,
)

import re

bp = Blueprint('manuscript', __name__, url_prefix='/manuscript')
ai_drafter = Task8Drafter()
_job_lock = threading.Lock()
_job_state = {}
_sid_auth_lock = threading.Lock()
_sid_auth_tokens = {}
logger = logging.getLogger("ManuscriptRoutes")
_SOCKET_MISSING_PID_MSG = "Missing project id (pid). Please select a formal project from Manuscript 1.1 list."


def _safe_component(value: str, fallback: str = "untitled", max_len: int = 80) -> str:
    """
    清洗用戶提供的路徑組件（section / filename 等），移除非法字元。
    [Batch C] 用於 cmd_load_chat 中 section 參數清洗，防路徑穿越。
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())[:max_len]
    return safe or fallback


def _socket_can_write(user_id, pid: str) -> bool:
    """
    [Batch C] 純函數：判斷 user 是否有 editor 以上角色可寫入指定 pid。
    AUTH_MODE=none 時永遠回傳 True（dev/TESTING 相容）。
    AUTH_MODE=session 時查 WorkspaceMember，需 editor 或 owner。

    [collab] 這是「專案層」門檻，用於 2C 全篇總裝這類跨章節動作。
    單一章節的寫入請改用 _socket_can_write_section —— coauthor 不到 editor，
    但對被指派的章節有寫入權。
    """
    try:
        from app.models import ROLE_ORDER
        role = get_workspace_role(user_id, pid)
        return ROLE_ORDER.get(role or "", 0) >= ROLE_ORDER.get("editor", 0)
    except Exception:
        return False


def _session_uid():
    """
    session 模式下的登入者 id。

    回傳 None 代表「不套用章節層過濾」——AUTH_MODE=none 的 dev/TESTING 模式，
    或 flask_login 不可用。專案層的把關另由 _ensure_socket_project_access 負責。
    """
    if str(current_app.config.get("AUTH_MODE", "none")).strip().lower() != "session":
        return None
    try:
        from flask_login import current_user
        return current_user.id if current_user.is_authenticated else None
    except ImportError:
        return None


def _broadcast_presence(pid: str) -> None:
    """把在線名單推給同專案的所有連線。

    送到 ws:{pid} room 是安全的：connect 時已驗過 workspace role 才 join，
    room 內必為專案成員，而專案成員本來就在成員清單上看得到彼此。
    刻意不帶章節資訊——見 presence.py 的安全邊界說明。
    """
    try:
        socketio.emit(
            'presence_update',
            {'pid': pid, 'users': presence.online(pid)},
            room=f"ws:{pid}",
            namespace='/manu_ws',
        )
    except Exception:
        # 在線提示是輔助資訊，推送失敗不該影響連線或編輯流程。
        logger.warning("[presence] 廣播失敗 pid=%s", pid, exc_info=True)


def _session_username():
    """session 模式下的登入者顯示名稱；僅供舊格式草稿的歸屬判定使用。

    升級前寫下的 _draft.json 只記了 _updated_by（使用者名稱），沒有 id，
    要判斷那份共用草稿是不是本人的就只能比名稱。新格式一律以 id 為準。
    """
    if str(current_app.config.get("AUTH_MODE", "none")).strip().lower() != "session":
        return None
    try:
        from flask_login import current_user
        return current_user.username if current_user.is_authenticated else None
    except ImportError:
        return None


def _socket_can_read_section(user_id, pid: str, section: str) -> bool:
    """
    [collab] 章節層讀取判定。限定編輯（coauthor）只讀得到被指派的章節。

    這與寫入是各自獨立的判斷：coauthor 的讀取範圍比 viewer 還窄，
    不能用角色階梯的線性比較推導。
    """
    try:
        from app.security import can_read_section
        return can_read_section(user_id, pid, section)
    except Exception:
        logger.warning(
            "[perm] can_read_section 失敗 user=%s pid=%s section=%s",
            user_id, pid, section, exc_info=True,
        )
        return False


def _socket_can_read_paper(user_id, pid: str) -> bool:
    """
    [collab] 是否可讀「2C 全篇主論文」。

    主論文是所有章節組裝出來的成品，內含限定編輯看不到的章節，
    因此限定編輯一律不得讀取整篇——否則章節層的讀取限制會被整篇繞過。
    """
    try:
        from app.security import get_workspace_role, is_section_scoped_role
        role = get_workspace_role(user_id, pid)
        if role is None:
            return False
        return not is_section_scoped_role(role, pid)
    except Exception:
        logger.warning("[perm] can_read_paper 失敗 user=%s pid=%s", user_id, pid, exc_info=True)
        return False


def _socket_can_write_section(user_id, pid: str, section: str) -> bool:
    """
    [collab] 章節層寫入判定：editor 以上寫全部；coauthor 只能寫被指派的章節。

    這是章節權限的伺服器端唯一把關點。前端的唯讀鎖定只是體驗優化，
    不能取代這裡。
    """
    try:
        from app.security import can_write_section
        return can_write_section(user_id, pid, section)
    except Exception:
        logger.warning(
            "[perm] can_write_section 失敗 user=%s pid=%s section=%s",
            user_id, pid, section, exc_info=True,
        )
        return False


def _write_revision_log(pid: str, user_id, entity_type: str, entity_ref: str, action: str, summary: str = ""):
    """
    [Batch C] 寫入一筆 RevisionLog。
    寫入失敗只 log warning，不影響主流程。
    dev 模式 user_id=None 亦可寫入。
    """
    try:
        from app.models import RevisionLog
        entry = RevisionLog(
            pid=pid,
            user_id=user_id,
            entity_type=entity_type,
            entity_ref=entity_ref,
            action=action,
            summary=summary[:300] if summary else "",
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as exc:
        logger.warning("[RevisionLog] write failed pid=%s entity=%s: %s", pid, entity_ref, exc)
        try:
            db.session.rollback()
        except Exception:
            pass


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

    # [collab] session 模式必須是專案成員。原本這裡只驗 Bearer token，
    # 在 API_AUTH_ENABLED=0 的部署下 /manuscript/api/bootstrap/<pid> 對
    # 任何登入者都回 200，等於整份手稿對外開放。
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({"ok": False, "message": "Unauthorized"}), 401
            if get_workspace_role(current_user.id, pid) is None:
                # 不區分「無此專案」與「非成員」，避免用 pid 列舉試探。
                return jsonify({"ok": False, "message": "Forbidden"}), 403
        except ImportError:
            pass

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
    """
    Socket 事件的專案存取守衛（讀寫皆適用）。

    [collab] session 模式必須驗 WorkspaceMember。原本這裡只認 Bearer token，
    在 API_AUTH_ENABLED=0 的部署下等同直接放行 —— 任何登入者只要知道 pid，
    就能用 cmd_list_blocks / cmd_load_block 讀走別人專案的完整手稿內文。
    連線階段的 handle_connect 也擋不住：前端連線時 auth payload 不帶 pid，
    那段成員檢查根本不會執行。因此逐事件檢查才是真正的防線。
    """
    def _deny():
        if emit_forbidden_msg:
            emit('sys_msg', {'msg': 'Forbidden project access.'})
        if forbidden_event:
            emit(forbidden_event, forbidden_payload or {})
        return False

    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return _deny()
            if get_workspace_role(current_user.id, pid) is None:
                return _deny()
            return True
        except ImportError:
            pass

    # AUTH_MODE=none（dev/TESTING）維持既有行為，避免破壞未啟用帳號系統的部署。
    if not _socket_auth_enabled():
        return True
    sid_token = _get_sid_token(request.sid)
    if sid_token and check_ownership(sid_token, pid):
        return True
    return _deny()


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

# 合法的 section_key：只允許 ASCII 英數與 _ - ，長度 1~50，且至少要有一個英數字。
# 這個字集是刻意保守的——section_key 同時是儲存目錄名、章節指派索引、
# 留言索引，三處都以它為鍵，放寬會製造跨層對不齊的破口。
#
# 「至少一個英數字」這條前瞻是必要的：舊前端把中文逐字換成底線，
# 「緒論」會變成 '__'。純分隔符的 key 不帶任何語意，而且正是那個缺陷的產物，
# 直接拒收才不會把它寫進資料庫。
_SECTION_KEY_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9_-]{1,50}$")


def _normalize_section_key(raw: str, used: set, idx: int) -> str:
    """
    產生保證合法且在同一專案內唯一的 section_key。

    合法且未被占用 → 原樣沿用（預設英文 key 走這條，既有資料不受影響）。
    否則改發 sec_<序號>，並在仍衝突時往後遞增。

    為什麼不能直接沿用前端送的值：前端的 id 產生規則會把每個非 ASCII 字元
    換成一個底線，任兩個等長的中文章節名因此得到完全相同的 key。
    """
    candidate = str(raw or "").strip()
    if _SECTION_KEY_RE.fullmatch(candidate) and candidate not in used:
        return candidate

    n = idx + 1
    while True:
        generated = f"sec_{n}"
        if generated not in used:
            return generated
        n += 1


def _filter_visible_sections(pid, rows):
    """
    [collab] 依讀取範圍過濾 ManuSectionConfig 列表。

    限定編輯（coauthor）只留下被指派的章節；其餘角色與 dev 模式原樣回傳。
    所有「列出章節」的端點都要經過這裡，否則 UI 會顯示點不進去的章節。
    """
    uid = _session_uid()
    if uid is None:
        return rows
    try:
        from app.security import visible_sections
        allowed = set(visible_sections(uid, pid, [r.section_key for r in rows]))
        return [r for r in rows if r.section_key in allowed]
    except Exception:
        logger.warning("[perm] 章節清單過濾失敗 pid=%s", pid, exc_info=True)
        return rows


def _current_section_versions(pid):
    """
    [v1.8] 取得每個章節「目前最新版本號」的對照表，供主論文 manifest 使用。

    只收錄真的有版本快照的章節；沒存過檔的章節不放進 manifest，
    以免還原時試圖回復一個不存在的版本。
    """
    manifest = {}
    try:
        for row in _get_or_init_sections(pid):
            versions = ManuscriptIO.list_block_versions(pid, row.section_key)
            if versions:
                manifest[row.section_key] = versions[0]['ver']
    except Exception:
        logger.warning("[manifest] 蒐集章節版本失敗 pid=%s", pid, exc_info=True)
    return manifest


def save_chat_history(pid, section, role, content, msg_type='text'):
    formal_pid = _formal_pid(pid)
    if not formal_pid:
        return
    # [Batch C] 使用 safe_join_under + DATA_ROOT 防相對路徑繞過
    safe_section = _safe_component(section, "general")
    data_root = _get_data_root()
    dir_path = safe_join_under(data_root, formal_pid, "manuscript", "chat")
    os.makedirs(dir_path, exist_ok=True)
    file_path = safe_join_under(dir_path, f"chat_{safe_section}.json")

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
    # [collab] 章節清單本身也要過濾：限定編輯連未被指派的章節「標題」都不該看到，
    # 否則 UI 上仍會列出他點不進去的章節。
    sections = _filter_visible_sections(bootstrap_pid, sections)
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
    # [collab] 與 bootstrap 同規：限定編輯只拿得到被指派的章節。
    rows = _filter_visible_sections(pid, _get_or_init_sections(pid))
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

        used_keys = set()
        for idx, sec in enumerate(sections):
            sid = str(sec.get('id', '')).strip()
            label = str(sec.get('label', '')).strip()
            if not label:
                continue
            # section_key 由前端送來但不能信任：中文章節名在舊前端會被逐字換成
            # 底線，導致「緒論」與「討論」都變成 '__' 而互撞——版本目錄共用、
            # 章節指派也等同失效（指派其一等於指派另一個）。
            # 這裡是權威把關點：格式不合或重複一律改發序號式 key。
            sid = _normalize_section_key(sid, used_keys, idx)
            used_keys.add(sid)

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
    """
    [Batch C] Socket connect handler。
    AUTH_MODE=session：要求 flask_login current_user 已登入；
        若 auth payload 或 query 帶 pid，驗 workspace role 後 join_room("ws:{pid}")。
    AUTH_MODE=none：維持既有 require_socket_auth 邏輯（Bearer token）。
    """
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()

    if auth_mode == "session":
        try:
            from flask_login import current_user
        except ImportError:
            raise ConnectionRefusedError("flask-login not available")

        if not current_user.is_authenticated:
            return False  # 拒連：未登入

        # 若攜帶 pid，驗 workspace role 並 join room
        pid_hint = (
            (auth or {}).get("pid") if isinstance(auth, dict) else None
        ) or request.args.get("pid", "")
        if pid_hint:
            pid_hint = str(pid_hint).strip()
            role = get_workspace_role(current_user.id, pid_hint)
            if role is None:
                return False  # 拒連：無 workspace membership
            join_room(f"ws:{pid_hint}")
            # 在線提示：登記後廣播給同專案成員，讓大家知道還有誰在。
            # 只記專案層級，不記誰在哪一章——見 presence.py 的安全邊界說明。
            presence.enter(request.sid, pid_hint, current_user.id,
                           getattr(current_user, 'username', None))
            _broadcast_presence(pid_hint)

        emit('sys_msg', {'msg': 'Connected to Roothinks-Manuscript Core (Drafter V1.4).'})
        return True

    # AUTH_MODE=none / token 模式
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
    # [Batch C] section 參數強制清洗，防路徑穿越（真漏洞修復）
    section_raw = data.get('section', 'title')
    section = _safe_component(section_raw, "title")
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

    # [Batch C] 使用 safe_join_under + DATA_ROOT 防相對路徑繞過
    data_root = _get_data_root()
    dir_path = safe_join_under(data_root, formal_pid, "manuscript", "chat")
    file_path = safe_join_under(dir_path, f"chat_{section}.json")
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
    """
    [Batch C] 儲存 2B 段落 Block。
    [collab] session 模式下改採章節層判定：editor 以上可寫全部章節，
    coauthor 只能寫被指派的章節。成功後寫 RevisionLog 並廣播 peer_update。
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return

    title = data.get('title', 'Untitled_Paper')
    section = data.get('section', 'general')
    content = data.get('content', '')

    # [Batch C] session 模式寫入權限檢查（section 必須先取出才能做章節層判定）
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    current_uid = None
    current_username = None
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                current_uid = current_user.id
                current_username = current_user.username
                if not _socket_can_write_section(current_uid, pid, section):
                    emit('sys_msg', {
                        'msg': f'權限不足：你沒有「{section}」章節的撰寫權限，'
                               '可改用留言功能提供意見。',
                    })
                    return
        except ImportError:
            pass

    # [v1.8] from_ver 記錄「這一版是從哪一版改出來的」；前端傳入目前檢視中的版本。
    # 前端送來的 s_ver 一律忽略——版本號改由伺服器指派（見下方註解）。
    from_ver = data.get('from_ver') or None

    try:
        # [v1.8] 版本由伺服器指派為 max+0.1，並以 O_EXCL 原子占用檔名。
        # 不再有 save_conflict：每次存檔都是新檔案，兩人同時存只會得到兩個相鄰
        # 版本（各自的 from_ver 記錄分支來源），不會互相覆蓋，也不需要使用者處理衝突。
        save_result = ManuscriptIO.save_block_version(
            pid, section, title, content,
            from_ver=from_ver,
            updated_by=current_username,
        )
        saved_filename = save_result.get("filename", "")
        new_ver = save_result.get("ver")

        # 正式版本已建立，草稿完成任務。留著會讓下次開啟時誤判「有未存檔內容」。
        # 只清自己那份：同章節可能有別人正在打字，不能連他的未存檔內容一起刪。
        try:
            ManuscriptIO.clear_draft(pid, section,
                                     owner_key=_session_uid(),
                                     owner_name=current_username)
        except Exception:
            logger.warning("[cmd_save_block] 清除草稿失敗 pid=%s section=%s", pid, section)

        emit('save_ack', {
            'target': 'block',
            'msg': f'Block saved as V{new_ver}',
            'section': save_result.get('section', section),
            'ver': new_ver,
            'from_ver': save_result.get('from_ver'),
            'filename': saved_filename,
            'updated_at': save_result.get('updated_at'),
            '_rev': 1,
            'versions': ManuscriptIO.list_block_versions(pid, section),
        })

        # [Batch C] RevisionLog
        summary = f"block save: section={section} ver={new_ver} chars={len(str(content))}"
        _write_revision_log(
            pid=pid,
            user_id=current_uid,
            entity_type='manuscript_block',
            entity_ref=f"{section}/{saved_filename}",
            action='save',
            summary=summary,
        )

        # [Batch C] 廣播 peer_update（協作預留）
        socketio.emit(
            'peer_update',
            {
                'kind': 'block',
                'section': section,
                'ver': new_ver,
                'by': current_username or 'anonymous',
            },
            room=f"ws:{pid}",
            namespace='/manu_ws',
        )
    except Exception as e:
        logger.error("[cmd_save_block] %s", e, exc_info=True)
        emit('sys_msg', {'msg': 'Save Error.'})


@socketio.on('cmd_autosave_block', namespace='/manu_ws')
def handle_autosave_block(data):
    """
    [v1.8] 自動存檔：前端停止編輯 1.5 秒後觸發。

    與 cmd_save_block 的差異是刻意的：
      - 寫入單一 _draft.json 就地覆寫，**不產生版本**。
        若每次自動存檔都跳版，一次編輯就會噴出幾十個版本，版本選單失去意義。
      - 不寫 RevisionLog（每 1.5 秒一筆會把審計表灌爆）。
      - 不做 _rev 樂觀鎖（高頻觸發會在多人情境噴 save_conflict 風暴），
        草稿採 last-writer-wins。
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data, missing_msg='')
    if not pid:
        return
    if not _ensure_socket_project_access(pid, emit_forbidden_msg=False):
        return

    section = data.get('section', 'general')
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    current_username = None
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                current_username = current_user.username
                if not _socket_can_write_section(current_user.id, pid, section):
                    # 無權章節的自動存檔安靜略過，不要每 1.5 秒彈一次權限警告。
                    return
        except ImportError:
            pass

    try:
        # owner_key 用使用者 id 而非顯示名稱：改名不該把自己的草稿弄丟，
        # 也不會因為兩人同名而互相覆蓋。
        result = ManuscriptIO.save_draft(
            pid, section,
            data.get('title', 'Untitled_Paper'),
            data.get('content', ''),
            updated_by=current_username,
            owner_key=_session_uid(),
        )
        emit('autosave_ack', {
            'ok': True,
            'section': result.get('section', section),
            'saved_at': result.get('saved_at'),
        })
    except Exception as e:
        logger.error("[cmd_autosave_block] %s", e, exc_info=True)
        emit('autosave_ack', {'ok': False, 'section': section})


@socketio.on('cmd_load_draft', namespace='/manu_ws')
def handle_load_draft(data):
    """[v1.8] 讀取自動存檔草稿，供重新進入頁面時提示是否復原。"""
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data, missing_msg='')
    if not pid:
        return
    if not _ensure_socket_project_access(pid, emit_forbidden_msg=False):
        return

    section = data.get('section', 'general')

    # [collab] 草稿內容等同章節內容，同樣受讀取範圍限制。
    uid = _session_uid()
    if uid is not None and not _socket_can_read_section(uid, pid, section):
        emit('draft_loaded', {'ok': False, 'section': section, 'draft': None})
        return

    draft = ManuscriptIO.load_draft(pid, section,
                                    owner_key=uid,
                                    owner_name=_session_username())
    emit('draft_loaded', {'ok': draft is not None, 'section': section, 'draft': draft})


@socketio.on('cmd_delete_block_version', namespace='/manu_ws')
def handle_delete_block_version(data):
    """
    [v1.8] 刪除單一章節版本快照。

    版本預設全部保留不自動淘汰；只有使用者明確要求才刪。刪除需 editor 以上權限。
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data, missing_msg='')
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return

    section = data.get('section', 'general')
    ver = data.get('ver')

    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    current_uid = None
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                current_uid = current_user.id
                # 刪版本沿用章節層判定：能寫該章節的人才能刪該章節的版本。
                if not _socket_can_write_section(current_uid, pid, section):
                    emit('sys_msg', {'msg': f'權限不足：你沒有「{section}」章節的編輯權限。'})
                    return
        except ImportError:
            pass

    ok = ManuscriptIO.delete_block_version(pid, section, ver)
    if ok:
        _write_revision_log(
            pid=pid,
            user_id=current_uid,
            entity_type='manuscript_block',
            entity_ref=f"{section}/V{ver}.json",
            action='delete',
            summary=f"block version delete: section={section} ver={ver}",
        )
    emit('block_version_deleted', {
        'ok': ok,
        'section': section,
        'ver': ver,
        'versions': ManuscriptIO.list_block_versions(pid, section),
    })

@socketio.on('cmd_save_paper', namespace='/manu_ws')
def handle_save_paper(data):
    """
    [Batch C] 儲存 2C 全篇 Paper。
    session 模式下檢查 user 對 pid 的角色 >= editor；
    成功後寫 RevisionLog 並廣播 peer_update 給 room("ws:{pid}")。
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return

    # [Batch C] session 模式寫入權限檢查
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    current_uid = None
    current_username = None
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                current_uid = current_user.id
                current_username = current_user.username
                # 2C 全篇總裝跨所有章節，維持專案層 editor 門檻，
                # 不因為 coauthor 被指派了某一章就能總裝整篇。
                if not _socket_can_write(current_uid, pid):
                    emit('sys_msg', {'msg': '權限不足：全篇總裝需要 editor 以上角色。'})
                    return
        except ImportError:
            pass

    title = data.get('title', 'Untitled_Paper')
    content = data.get('content', '')
    from_ver = data.get('from_ver') or None

    # [v1.8] sections manifest：記錄這一版由哪些章節的哪一版組成。
    # 前端若沒給，就以每個章節「目前的最新版本」推算，確保 manifest 不會是空的
    # ——沒有它就無法一鍵回退整組章節，也回答不出「投出去那版 Method 是第幾版」。
    sections = data.get('sections')
    if not isinstance(sections, dict) or not sections:
        sections = _current_section_versions(pid)

    try:
        save_result = ManuscriptIO.save_paper_version(
            pid, title, content,
            sections=sections,
            from_ver=from_ver,
            updated_by=current_username,
        )
        saved_filename = save_result.get('filename', '')
        emit('save_ack', {
            'target': 'paper',
            'msg': f"Paper saved as {save_result.get('g_ver')}",
            'g_ver': save_result.get('g_ver'),
            'from_ver': save_result.get('from_ver'),
            'sections': save_result.get('sections'),
            'filename': saved_filename,
            'updated_at': save_result.get('updated_at'),
            'versions': ManuscriptIO.list_paper_versions(pid),
        })

        # [Batch C] RevisionLog
        summary = (
            f"paper save: title={title} ver={save_result.get('g_ver')} "
            f"chars={len(str(content))}"
        )
        _write_revision_log(
            pid=pid,
            user_id=current_uid,
            entity_type='manuscript_paper',
            entity_ref=saved_filename,
            action='save',
            summary=summary,
        )

        # [Batch C] 廣播 peer_update（協作預留）
        socketio.emit(
            'peer_update',
            {'kind': 'paper', 'section': 'paper', 'by': current_username or 'anonymous'},
            room=f"ws:{pid}",
            namespace='/manu_ws',
        )
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
    # [collab] 主論文是全章節組裝的成品，限定編輯不得讀取整篇。
    uid = _session_uid()
    if uid is not None and not _socket_can_read_paper(uid, pid):
        emit('paper_list', {'files': [], 'versions': [], 'forbidden': True})
        return

    title = data.get('title', 'Untitled_Paper')
    # files 保留舊契約；versions 帶 manifest 摘要供新版本選單使用。
    emit('paper_list', {
        'files': ManuscriptIO.list_papers(pid, title),
        'versions': ManuscriptIO.list_paper_versions(pid),
    })


@socketio.on('cmd_restore_paper_version', namespace='/manu_ws')
def handle_restore_paper_version(data):
    """
    [v1.8] 還原主論文版本：依 manifest 一次取回該版全文與各章節當時的內容。

    這是 manifest 設計的主要價值——沒有 sections 對照表，就只能還原 2C 的合併
    全文，無法把各章節編輯區也回到當時的狀態。
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data, missing_msg='')
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return

    uid = _session_uid()
    if uid is not None and not _socket_can_read_paper(uid, pid):
        emit('paper_restored', {'ok': False, 'msg': '權限不足：限定編輯無法還原全篇主論文。'})
        return

    manifest = ManuscriptIO.load_paper_version(pid, data.get('ver'))
    if not manifest:
        emit('paper_restored', {'ok': False, 'msg': '找不到該版本。'})
        return

    # 逐章節取回當時版本的內容；某章節的該版已被刪除時只略過該章，不整批失敗。
    restored_sections = {}
    missing = []
    for section_key, ver in (manifest.get('sections') or {}).items():
        target = next(
            (
                entry for entry in ManuscriptIO.list_block_versions(pid, section_key)
                if entry['ver'] == ver
            ),
            None,
        )
        if not target:
            missing.append(f"{section_key}@V{ver}")
            continue
        block = ManuscriptIO.load_block(pid, section_key, target['filename'])
        if block:
            restored_sections[section_key] = {
                'ver': ver,
                'content': block.get('content', ''),
            }
        else:
            missing.append(f"{section_key}@V{ver}")

    emit('paper_restored', {
        'ok': True,
        'g_ver': manifest.get('g_ver'),
        'content': manifest.get('content', ''),
        'title': manifest.get('title'),
        'sections': restored_sections,
        'missing': missing,
    })

@socketio.on('cmd_load_paper', namespace='/manu_ws')
def handle_load_paper(data):
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data)
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return
    uid = _session_uid()
    if uid is not None and not _socket_can_read_paper(uid, pid):
        emit('sys_msg', {'msg': '權限不足：限定編輯無法檢視全篇主論文。'})
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

    # [collab] 章節層讀取過濾：限定編輯看不到未被指派的章節，連版本清單都不給。
    uid = _session_uid()
    if uid is not None and not _socket_can_read_section(uid, pid, section):
        emit('block_list', {'files': [], 'section': section, 'versions': [],
                            'draft': None, 'next_ver': None, 'forbidden': True})
        return

    # files 保留舊契約（前端與 e2e 測試以檔名載入舊版）；
    # versions 是 v1.8 新增的結構化清單，供版本選單顯示 from_ver / 時間 / 作者。
    emit('block_list', {
        'files': ManuscriptIO.list_blocks(pid, section),
        'section': section,
        'versions': ManuscriptIO.list_block_versions(pid, section),
        'draft': ManuscriptIO.load_draft(pid, section,
                                         owner_key=uid,
                                         owner_name=_session_username()),
        'next_ver': ManuscriptIO.next_block_version(pid, section),
    })

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

    # [collab] 讀取過濾：限定編輯不得載入未被指派章節的任何版本內容。
    uid = _session_uid()
    if uid is not None and not _socket_can_read_section(uid, pid, section):
        emit('sys_msg', {'msg': f'權限不足：你沒有「{section}」章節的存取權限。'})
        return

    content = ManuscriptIO.load_block(pid, section, filename)
    if content:
        # [v1.7] 帶 _rev 給前端，前端記住作為下次存檔的 base_rev
        emit('block_loaded', {
            'ok': True,
            'content': content,
            'section': section,
            'filename': filename,
            '_rev': content.get('_rev', 0),
        })
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
    # 先移除再廣播，否則剛離線的人還會出現在清單上。
    left_pid = presence.drop(sid)
    if left_pid:
        _broadcast_presence(left_pid)


# ---------------------------------------------------------------------------
# Citation suggestion + decision ledger (v0.1)
# 建議唯讀、決策必須由人明確確認；系統永不自動把 citation 寫入正文。
# ---------------------------------------------------------------------------

def _citation_data_root():
    # 與 workflow/paq 一致，由 DB URI 推導 data root；tests 指向隔離資料夾。
    from app.services.formal_project_sync import resolve_data_root

    return resolve_data_root()


def _evidence_pid_candidates(pid):
    """Literature 以 formal pid（-p）為主索引；查詢時兩個變體都試。"""
    out = [pid]
    if str(pid).endswith('-p'):
        out.append(pid[:-2])
    else:
        out.append(f"{pid}-p")
    return list(dict.fromkeys([p for p in out if p]))


@bp.route('/api/citation/suggest', methods=['POST'])
def citation_suggest_api():
    data = request.get_json(silent=True) or {}
    pid = _resolve_formal_project_pid(data.get('pid'))
    text = str(data.get('text') or '').strip()
    section = str(data.get('section') or '').strip()

    if not pid:
        return jsonify({"success": False, "message": "Invalid pid"}), 404
    if not text:
        return jsonify({"success": False, "message": "Missing text"}), 400

    try:
        from app.core_pro.manuscript.citation_suggest import detect_citation_needed, suggest_citations_for_paragraph

        suggestions = []
        for candidate_pid in _evidence_pid_candidates(pid):
            suggestions = suggest_citations_for_paragraph(candidate_pid, text, section_title=section or None)
            if suggestions:
                break
        return jsonify(
            {
                "success": True,
                "citation_needed": detect_citation_needed(text),
                "suggestions": suggestions,
            }
        )
    except Exception as e:
        logger.error("[citation_suggest_api] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route('/api/citation/decide', methods=['POST'])
def citation_decide_api():
    data = request.get_json(silent=True) or {}
    pid = _resolve_formal_project_pid(data.get('pid'))
    if not pid:
        return jsonify({"success": False, "message": "Invalid pid"}), 404

    try:
        from app.core_pro.manuscript.citation_ledger import record_decision

        decision = record_decision(
            _citation_data_root(),
            pid,
            section_id=str(data.get('section') or ''),
            status=str(data.get('status') or ''),
            paper_id=str(data.get('paper_id') or ''),
            entry_id=str(data.get('entry_id') or ''),
            claim_text=str(data.get('claim_text') or ''),
            snippet=str(data.get('snippet') or ''),
            segment_ids=data.get('segment_ids') if isinstance(data.get('segment_ids'), list) else [],
            decided_by=str(data.get('decided_by') or 'user'),
        )
        return jsonify({"success": True, "decision": decision})
    except ValueError as ve:
        return jsonify({"success": False, "message": str(ve)}), 400
    except Exception as e:
        logger.error("[citation_decide_api] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route('/api/citation/decisions', methods=['GET'])
def citation_decisions_api():
    pid = _resolve_formal_project_pid(request.args.get('pid'))
    if not pid:
        return jsonify({"success": False, "message": "Invalid pid"}), 404
    try:
        from app.core_pro.manuscript.citation_ledger import list_decisions

        decisions = list_decisions(
            _citation_data_root(),
            pid,
            section_id=str(request.args.get('section') or ''),
            paper_id=str(request.args.get('paper_id') or ''),
        )
        return jsonify({"success": True, "count": len(decisions), "decisions": decisions})
    except Exception as e:
        logger.error("[citation_decisions_api] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# [Batch C] Revision Log API
# ---------------------------------------------------------------------------

@bp.route('/api/revisions/<pid>', methods=['GET'])
def get_revisions(pid):
    """
    [Batch C] 取得 manuscript 版本紀錄。
    viewer 以上可存取；dev/TESTING 模式直接放行。
    回傳最近 limit 筆（預設 50）。
    """
    formal_pid = _resolve_formal_project_pid(pid)
    if not formal_pid:
        return jsonify({"ok": False, "message": "Invalid pid"}), 404

    try:
        limit = min(int(request.args.get('limit', 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    try:
        from app.models import RevisionLog
        rows = (
            RevisionLog.query
            .filter_by(pid=formal_pid)
            .order_by(RevisionLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return jsonify({
            "ok": True,
            "pid": formal_pid,
            "count": len(rows),
            "revisions": [r.to_dict() for r in rows],
        })
    except Exception as e:
        logger.error("[get_revisions] %s", e, exc_info=True)
        return jsonify({"ok": False, "message": "Internal server error"}), 500
