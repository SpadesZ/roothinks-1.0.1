# 檔案路徑: app/core_pro/manuscript/manuscript_routes.py
# 產生時間: 2026-07-19 09:00 +08:00
# 版本: v1.9（provider-aware chat cancellation）
# 更新時間: 2026-08-10 +08:00
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
#   9. [v1.8] chat_message handler 的「無聲黑洞」修復：
#      - job_queued 提前到所有可能失敗的工作之前（原本排在 save_chat_history 之後，
#        該函式的 FileLock 逾時會讓 handler 死在 ack 之前，前端永久轉圈）。
#      - save_chat_history 失敗只 log，不中斷生成。
#      - ack 之後任何例外一律補 emit job_error，確保前端一定收得到終止事件。
#  10. [v1.9] 每個 chat job 保存 cancel_event；取消／斷線會同步通知實際
#      provider transport，中止 quota wait、retry 與進行中的網路請求。
# 呼叫來源:
#   app/__init__.py register_blueprint；前端 Socket.IO /manu_ws namespace。
# 輸入輸出契約:
#   - [v1.8] chat_message 事件契約（前端 manuscript_soed.js 依賴此保證）：
#       通過驗證 → 必定先收到 job_queued，之後必定收到
#       job_done / job_error / job_cancelled 其中之一；
#       未通過驗證 → 必定收到 sys_msg。兩者皆不得「什麼都不回」。
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
from urllib.parse import quote
from uuid import uuid4

from flask import Blueprint, render_template, request, send_from_directory, jsonify, current_app, g
from app import socketio, db
from flask_socketio import emit, join_room
from socketio.exceptions import ConnectionRefusedError
from app.llm_service.matching_tasks.task_8drafter import Task8Drafter
from app.core_pro.manuscript.manuscript_io import ManuscriptIO, _get_data_root
from app.core_pro.manuscript.manuscript_image import ManuscriptImage
from app.core_pro.manuscript import presence
from app.core_pro.manuscript.model_section import (
    ManuSectionConfig,
    ManuSectionProgress,
)
from app.core_pro.manuscript.source_context import build_drafter_corpus
from app.core_pro.manuscript.coc_bundle import build_coc_bundle, resolve_section_version
from app.core_pro.manuscript.manuscript_docx import (
    DOCX_MIME,
    DocxExportError,
    build_docx,
    safe_download_name,
)
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

# 全域 prompt token 預算（任務 1）
# 計算方式：
#   - Google Gemini TPM 上限 25,000 tokens
#   - 扣除 system prompt 估算值約 2,000 tokens
#   - 再扣除 user_msg 上限（4,000 chars ≈ 1,400 tokens）
#   - 留 1,600 tokens 的安全餘裕（防止估算偏差與 prompt 格式開銷）
#   → 25,000 - 2,000 - 1,400 - 1,600 = 20,000 tokens 給 context
# 之所以不用 25,000：估算函式用 CJK×1.5 + 其他×0.35，英文學術文字實際上
# 約是 chars/4，估算偏低，因此要留足夠的安全餘裕。
_PROMPT_MAX_TOKENS = _read_positive_int_env("MANUSCRIPT_PROMPT_MAX_TOKENS", 20000)


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


def _cancel_job_for_sid(job_id, sid):
    """原子地限制取消權限在建立該工作的 Socket。"""
    with _job_lock:
        state = _job_state.get(job_id)
        if not state:
            return 'missing'
        if state.get('sid') != sid:
            return 'forbidden'
        state['cancelled'] = True
        # NOTE(NOTE-002): UI 狀態與 provider abort 共用同一 event，不能只丟棄回傳。
        cancel_event = state.get('cancel_event')
        if cancel_event is not None:
            cancel_event.set()
        return 'cancelled'


def _mark_jobs_cancelled_by_sid(sid):
    with _job_lock:
        for k, v in _job_state.items():
            if v.get('sid') == sid:
                v['cancelled'] = True
                cancel_event = v.get('cancel_event')
                if cancel_event is not None:
                    cancel_event.set()


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
                # cmd_cancel_job 已同步通知原 Socket；worker 只需停止，避免 UI 顯示兩次。
                return

            logger.info(
                "[manu_chat] job_start id=%s sid=%s pid=%s section=%s msg_len=%s",
                job_id,
                sid,
                payload.get('pid'),
                payload.get('section'),
                len(str(payload.get('user_msg', '') or '')),
            )
            cancel_event = _get_job_state(job_id).get('cancel_event')
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
                cancel_event=cancel_event,
                # 任務 1：全域預算管控，retrieval 用剩餘量而非寫死的 18000
                coc_items=payload.get('coc_items', []),
                coc_notes=payload.get('coc_notes', []),
                remaining_budget=payload.get('remaining_budget'),
            )

            if _is_cancelled(job_id):
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


def _drafter_corpus_enabled() -> bool:
    """整包倒語料是否啟用（預設關閉，理由見 handle_chat 內的說明）。

    保留成環境變數是為了出事能立刻回退：VM 上設 DRAFTER_CORPUS_ENABLED=1
    再 `docker compose up -d` 即可，不必改碼重新部署。
    """
    raw = str(os.environ.get("DRAFTER_CORPUS_ENABLED", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


# ---------------------------------------------------------------------------
# 章節完成度（NOTE-036）
# ---------------------------------------------------------------------------

def _progress_payload(pid):
    """組出「這個專案每一章的完成度」。

    章節清單走 `_filter_visible_sections`，與 bootstrap／sections 同一個過濾器
    —— coauthor 看不到未被指派的章節，進度 API 也**不得洩漏那些章節的存在**。
    """
    rows = _filter_visible_sections(pid, _get_or_init_sections(pid))
    stored = ManuSectionProgress.map_for_pid(pid)
    sections = [
        {
            'id': s.section_key,
            'label': s.section_name,
            'order_index': int(s.order_index),
            # 沒有紀錄回 None（還沒填），**不是 0**（填了 0%）。
            # 兩者在進度回報上是完全不同的事。
            'progress': stored.get(s.section_key),
        }
        for s in rows
    ]
    return {
        'ok': True,
        'pid': pid,
        'sections': sections,
        # NOTE(NOTE-036) 整體比例現階段一律 null。各章不等重（Abstract 與 Results
        # 差很多），有些章節在特定研究裡根本不會寫，權重規則尚未定案。
        # 給一個看起來合理但其實錯的數字，比明白說「還沒算」更糟 ——
        # 使用者會拿它去回報進度。
        'overall': None,
        'overall_status': 'not_calculated',
    }


@bp.route('/api/progress/<pid>', methods=['GET'])
def get_section_progress(pid):
    """讀取單一專案的章節完成度。專案成員皆可讀（含 viewer）。"""
    formal_pid = _resolve_formal_project_pid(pid)
    if not formal_pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404

    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
            if get_workspace_role(current_user.id, formal_pid) is None:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
        except ImportError:
            pass

    try:
        return jsonify(_progress_payload(formal_pid))
    except Exception as e:
        logger.error("[get_section_progress] pid=%s %s", formal_pid, e, exc_info=True)
        return jsonify({'ok': False, 'message': 'Internal server error'}), 500


@bp.route('/api/progress/<pid>', methods=['POST'])
def set_section_progress(pid):
    """設定單一章節的完成度。

    NOTE(NOTE-036) 授權用 `_socket_can_write_section`，不是專案層的 editor 門檻：
      - viewer 一律擋掉（前端唯讀只是體驗，**這裡才是把關**）。
      - owner／editor 可寫所有章節。
      - coauthor 可寫**自己被指派的**章節 —— 被指派的人才知道那一章寫到哪裡；
        要他回報給 owner 再代填，等於讓最不知情的人填最需要準確的欄位。

    安全邊界：本路徑在 `/manuscript/api/` 之下，`is_api_request_path()` 會讓它
    跳過 CSRF 守衛，因此授權必須在這裡自己做，不能靠 method 推導。
    """
    formal_pid = _resolve_formal_project_pid(pid)
    if not formal_pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404

    data = request.get_json(silent=True) or {}
    section = _safe_component(data.get('section'), '')
    if not section:
        return jsonify({'ok': False, 'message': 'Missing section'}), 400

    percent = ManuSectionProgress.clamp(data.get('progress'))
    if percent is None:
        # 看不懂的輸入不得當成 0 —— 那會讓使用者以為自己填的被記錄了。
        return jsonify({'ok': False, 'message': '完成比例必須是 0～100 的數字。'}), 400

    uid = None
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
            if get_workspace_role(current_user.id, formal_pid) is None:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
            uid = current_user.id
            if not _socket_can_write_section(uid, formal_pid, section):
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
        except ImportError:
            pass

    # 章節必須真的存在於這個專案。否則任何字串都能在表裡長出一列孤兒紀錄，
    # 而且 coauthor 可以藉此探測別人章節的 key。
    known = {s.section_key for s in _filter_visible_sections(
        formal_pid, _get_or_init_sections(formal_pid))}
    if section not in known:
        return jsonify({'ok': False, 'message': 'Unknown section'}), 404

    try:
        row = ManuSectionProgress.query.filter_by(
            pid=formal_pid, section_key=section).first()
        if row is None:
            row = ManuSectionProgress(pid=formal_pid, section_key=section)
            db.session.add(row)
        row.progress_percent = percent
        row.updated_by = uid
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("[set_section_progress] pid=%s section=%s %s",
                     formal_pid, section, e, exc_info=True)
        return jsonify({'ok': False, 'message': 'Internal server error'}), 500

    return jsonify({'ok': True, 'section': section, 'progress': percent})


@bp.route('/api/progress_summary', methods=['GET'])
def get_progress_summary():
    """Dashboard 用：一次拿回登入者看得到的所有正式專案的章節完成度。

    刻意做成一支批次端點而不是讓 Dashboard 逐張卡片打一次 —— 卡片數量等於
    專案數量，N+1 會在專案一多時把首頁拖垮。
    """
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    pids = []
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
            from app.models import WorkspaceMember
            pids = [m.pid for m in
                    WorkspaceMember.query.filter_by(user_id=current_user.id).all()]
        except ImportError:
            pids = []
    if not pids:
        # AUTH_MODE=none（dev/TESTING）：沒有成員概念，就以有章節設定的專案為準。
        pids = [row[0] for row in
                db.session.query(ManuSectionConfig.pid).distinct().all()]

    projects = {}
    for candidate in sorted(set(pids)):
        formal_pid = _resolve_formal_project_pid(candidate)
        if not formal_pid:
            continue
        try:
            projects[formal_pid] = _progress_payload(formal_pid)
        except Exception:
            # 一個專案讀不出來不得讓整張 Dashboard 空白（NOTE-026 的同一條原則：
            # 子資源失敗不得改寫其他責任的結果）。
            logger.warning("[progress_summary] 略過 pid=%s", formal_pid, exc_info=True)
    return jsonify({'ok': True, 'projects': projects})


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


@bp.route('/api/export/docx/<pid>', methods=['POST'])
def export_fusion_docx(pid):
    """把 2C 全篇匯出成真正的 .docx。

    NOTE(NOTE-031): 轉換刻意放在伺服器端。瀏覽器拿不到圖片的二進位
    （`<img src>` 只是一條需要授權的 URL），所以在前端組不出可攜的檔案 ——
    舊作法把 `fusionCanvas.innerHTML` 包一層 `<html>` 標成 `application/msword`
    就是因為這個限制，結果是一份沒有樣式表的 HTML：Bootstrap class 全部失效、
    `S.Ver` 編輯徽章被印進論文、圖片離線就是破圖。

    NOTE(NOTE-032): 圖片只從本專案 image registry 解析，這個模組沒有任何
    HTTP client —— 匯出的 HTML 由使用者控制，若伺服器照著 src 去抓就是 SSRF。

    安全邊界：
      - 路徑在 `/manuscript/api/` 之下，`is_api_request_path()` 會讓它跳過
        CSRF 守衛，因此**授權必須在這裡自己做**。
      - 讀取門檻用 `_socket_can_read_paper`，不是 `enforce_project_ownership`：
        2C 全篇跨所有章節，coauthor（限定編輯）本來就讀不到整篇，
        不能讓他用匯出繞過章節層的讀取限制。viewer 讀得到，所以匯得出來。
    """
    formal_pid = _resolve_formal_project_pid(pid)
    if not formal_pid:
        return jsonify({'ok': False, 'message': 'Invalid pid'}), 404

    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
            if get_workspace_role(current_user.id, formal_pid) is None:
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
            if not _socket_can_read_paper(current_user.id, formal_pid):
                return jsonify({'ok': False, 'message': 'Forbidden'}), 403
        except ImportError:
            pass
    elif current_app.config.get("API_AUTH_ENABLED", False):
        token = get_request_token()
        if not token or not check_ownership(token, formal_pid):
            return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    html = str(data.get('html') or '')
    title = str(data.get('title') or '').strip()

    max_html = _read_positive_int_env("MANUSCRIPT_DOCX_MAX_HTML_CHARS", 4000000)
    if len(html) > max_html:
        return jsonify({'ok': False, 'message': '2C 內容過長，無法匯出。'}), 413
    if not html.strip():
        return jsonify({'ok': False, 'message': '目前 2C 畫布沒有可匯出的內容。'}), 400

    try:
        blob = build_docx(formal_pid, title, html)
    except DocxExportError as e:
        # 這些訊息是寫給使用者看的（缺圖、跨專案、格式不支援），
        # 必須原樣回去 —— 靜默略過一張圖等於交出一份不完整的論文。
        return jsonify({'ok': False, 'message': str(e)}), 422
    except Exception as e:
        logger.error("[export_fusion_docx] pid=%s %s", formal_pid, e, exc_info=True)
        return jsonify({'ok': False, 'message': 'Internal server error'}), 500

    filename = f"{safe_download_name(title or formal_pid)}.docx"
    response = current_app.response_class(blob, mimetype=DOCX_MIME)
    # filename* 用 RFC 5987：論文標題常含中文，只給 ASCII filename 會變亂碼。
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', '_', filename) or 'manuscript.docx'
    response.headers['Content-Disposition'] = (
        f"attachment; filename=\"{ascii_name}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    response.headers['Content-Length'] = str(len(blob))
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


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

def _build_coc_for_request(pid: str, section: str) -> dict:
    """
    在 request context 內組出人類決策鏈（COC）。

    NOTE(NOTE-012): 權限與版本都必須在這裡定案。生成工作跑在 _CHAT_EXECUTOR 的
    worker thread，那裡沒有 flask request 也沒有 current_user；把可讀章節的判斷
    留到下游，等於在沒有身分的情境下決定要餵哪些章節給 LLM —— 限定編輯看不到的
    章節會經由 prompt 外流。

    任何一段讀取失敗都只降級不中斷：少一段脈絡可以事後補，
    讓整個草稿請求消失不行（與本檔既有的 fail-soft 慣例一致）。
    """
    try:
        uid = _session_uid()
        readable = []
        for row in _get_or_init_sections(pid):
            key = row.section_key
            if uid is None or _socket_can_read_section(uid, pid, key):
                readable.append(key)

        # 目前章節不從 readable 推導。'general' 這種預設值、以及剛新增還沒進
        # sections 資料表的章節都不會出現在清單裡，用「不在清單裡就是不可讀」
        # 會把合法請求的正文靜默丟掉 —— 而正文丟失沒有任何外顯症狀。
        can_read_current = uid is None or _socket_can_read_section(uid, pid, section)

        # 2C 是所有章節組裝出來的成品，內含限定編輯看不到的章節。
        # 這裡若不判定，章節層的讀取限制會被 prompt 這條管道整篇繞過。
        include_paper = uid is None or _socket_can_read_paper(uid, pid)

        # 只帶「綁在目前版本」的章節留言：換版後舊意見不該再被當成待處理事項，
        # 這與留言版本化的篩選語意一致（NOTE-010）。
        #
        # NOTE(NOTE-010, 任務 3): 有給 s_ver 就嚴格篩選。
        # 舊邏輯：`if s_ver and row.s_ver and str(row.s_ver) != str(s_ver): continue`
        # 問題：row.s_ver 為 NULL 的留言不滿足 `row.s_ver` 的真值，
        #       於是完全跳過篩選、穿透進 comments，然後被注入 prompt。
        #       這破壞了「有給版本就嚴格篩選」的語意，而且作者無從得知
        #       那些留言是針對未知版本的（可能是整個月前的 V0.1 意見）。
        # 修法：`row.s_ver` 為 NULL（未標版本）的留言一律排除；
        #       除非呼叫端明確傳 `include_legacy=True`（目前不開放，與 chapter_routes 一致）。
        comments: list[dict] = []
        comment_notes: list[str] = []
        if can_read_current:
            try:
                from app.models import ChapterComment
                s_ver, _fn = resolve_section_version(pid, section)
                rows = (ChapterComment.query
                        .filter_by(pid=pid, section_key=section, resolved=False)
                        .order_by(ChapterComment.created_at.asc())
                        .all())
                excluded_count = 0
                for row in rows:
                    if s_ver:
                        # 有目前版本的情況：嚴格篩選
                        # NULL s_ver 的舊留言也排除（不混入未知版本的意見）
                        if not row.s_ver or str(row.s_ver) != str(s_ver):
                            excluded_count += 1
                            continue
                    # 沒有目前版本（章節從未存檔）：接受所有留言（沒有版本可以比對）
                    comments.append(row.to_dict())
                if excluded_count:
                    # 不靜默丟掉：作者需要知道有多少留言因版本不符而被排除，
                    # 否則他會以為「今天的審閱意見消失了」。
                    comment_notes.append(
                        f"excluded_comments={excluded_count} "
                        f"(s_ver=NULL 或版本不符，目前 s_ver={s_ver}；"
                        f"未標版本留言須由作者手動確認)"
                    )
                    logger.info(
                        "[coc] 排除未標版本或版本不符留言 pid=%s section=%s "
                        "excluded=%d current_s_ver=%s",
                        pid, section, excluded_count, s_ver,
                    )
            except Exception:
                logger.warning("[coc] 讀取章節留言失敗 pid=%s section=%s", pid, section, exc_info=True)

        bundle = build_coc_bundle(
            pid,
            section,
            readable_sections=readable,
            can_read_current_section=can_read_current,
            include_paper=include_paper,
            formal_pid=_formal_pid(pid) or "",
            review_comments=comments,
            budget_tokens=_read_coc_budget(),
        )
        # 任務 3：把留言排除紀錄合併進 bundle["notes"]，讓它一路跟進 audit。
        if comment_notes:
            bundle["notes"] = list(bundle.get("notes") or []) + comment_notes
        return bundle
    except Exception:
        logger.warning("[coc] COC 組裝失敗（降級為無 COC） pid=%s section=%s", pid, section, exc_info=True)
        return {"text": "", "s_ver": None, "g_ver": None, "items": [], "notes": ["coc_build_failed"]}


def _read_coc_budget() -> int:
    """COC 的 token 預算。壞值回預設，理由同 ManuscriptRuling._read_int_env。"""
    try:
        val = int(str(os.environ.get("MANUSCRIPT_COC_MAX_TOKENS", 12000)).strip())
        return val if val > 0 else 12000
    except Exception:
        return 12000


def _normalize_context_text(text: str) -> str:
    """
    正規化 context 文字，用於去重比較。
    前端送來的是 HTML；COC 載入版本經 html_to_text() 轉換。
    必須把兩者都壓平到同一個空白標準才能比較。
    只用於去重判斷，不用於輸出。
    """
    import re as _re
    # 剝 HTML 標籤（前端送來的 context 可能含標籤）
    text = _re.sub(r"<[^>]+>", " ", str(text or ""))
    # 壓縮所有空白成單個空格
    text = _re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _pack_prompt_context(
    *,
    frontend_context: str,
    coc_bundle: dict,
    total_budget: int,
    section: str,
    job_id: str,
) -> tuple[str, int, list[str], list[dict]]:
    """
    全域 token 預算 packer（NOTE-012）。

    這裡只負責 handler 側的兩段；**預算邊界的最後一段在
    ManuscriptRuling.validate_and_prepare**（upstream context 與檢索共用回傳的
    剩餘額度）。兩邊合起來才是「全域」—— 只看這個函式會誤以為預算已經收斂。

    優先序（由高到低）：
      1. 本輪指令（user_msg）— 由 handle_chat 獨立發送，不在這裡
      2. 前端未存草稿（frontend_context）
      3. COC 組裝的伺服器脈絡（coc_bundle["text"]）
      4. upstream context（在 ManuscriptRuling 內另外讀，此函式只管 route 層）

    去重邏輯：
      - 前端送來的目前章節文字（含在 frontend_context 最前面）與 COC 伺服器版本比較。
      - 比較前正規化（剝標籤、壓縮空白）。
      - 實質相同 → 只保留前端版本（未存草稿優先），從 coc_bundle 的 text 中移除
        伺服器章節正文。
      - 不同 → 前端版本標明「未存草稿」，COC 版本（伺服器讀到的）標明「伺服器已存版本」；
        只保留前端版本，捨棄 COC 的目前章節（因為前端是更新的）。
      - 「只保留一份」的理由：同時送兩個版本讓 LLM 看到衝突正文，它無從判斷哪個優先；
        而前端的未存修改一定比伺服器已存版本更新，前端優先是正確的語意。

    回傳：(packed_context_text, remaining_budget_tokens, packer_notes, effective_coc_items)

    remaining_budget_tokens 是**扣掉這兩段之後的剩餘全域額度**，不是「檢索的預算」。
    下游還要先安置 upstream context（研究筆記）才輪到檢索。
    **0 是有效值**，意思是用盡；不得在任何一層被當成「沒給」而放大成預設值。
    packer_notes 是被裁切的紀錄，要進 audit。
    effective_coc_items 是**去重後真正留在 prompt 裡**的 COC 來源清單 ——
    呼叫端必須用這一份寫 audit，不能用原始 bundle 的 items：目前章節被前端未存
    草稿取代後，原始清單仍宣稱伺服器版本是來源，那份 provenance 是假的。
    """
    from app.core_pro.manuscript.source_context import _estimate_tokens, _truncate_to_budget
    from app.core_pro.manuscript.coc_bundle import html_to_text

    packer_notes: list[str] = []
    parts: list[str] = []
    used_tokens = 0

    # ── 1. 前端未存草稿（優先） ──
    # _MAX_CHAT_CONTEXT_CHARS 的靜默截斷已在 handle_chat 發生，這裡補紀錄。
    frontend_text = str(frontend_context or "").strip()
    if frontend_text:
        # frontend_context 可能已被 _MAX_CHAT_CONTEXT_CHARS 截斷（chars 不等於 tokens），
        # 但我們要用 token 計算以維持全域預算一致性。
        fe_tokens = _estimate_tokens(frontend_text)
        # 跟 COC 伺服器正文去重
        coc_server_section = _extract_coc_section_text(coc_bundle, section)
        if coc_server_section:
            norm_fe = _normalize_context_text(frontend_text)
            norm_srv = _normalize_context_text(coc_server_section)
            if norm_fe == norm_srv:
                # 完全相同：COC 版本移除，只留前端
                packer_notes.append(
                    f"dedup:current_section={section}:identical → 只保留前端版本"
                )
            else:
                # 不同（前端有未存修改）：標記前端版本，並從 COC text 移除該章節
                frontend_text = f"[作者目前未存草稿（覆蓋伺服器版本）]\n{frontend_text}"
                packer_notes.append(
                    f"dedup:current_section={section}:diverged → 前端未存版本優先，"
                    f"伺服器版本({coc_bundle.get('s_ver')})已捨棄"
                )
            # 不論哪條路，COC 的目前章節正文都不再放入輸出
            coc_bundle = _strip_coc_section(coc_bundle, section)

        # 依全域預算截斷前端草稿
        available = total_budget - used_tokens
        clipped_fe = _truncate_to_budget(frontend_text, available)
        if len(clipped_fe) < len(frontend_text):
            packer_notes.append(
                f"truncated:frontend_context:chars={len(frontend_text)}→"
                f"{len(clipped_fe)} by global token budget"
            )
            logger.info(
                "[manu_chat][packer] 前端草稿依全域預算截斷 id=%s before=%d after=%d",
                job_id, len(frontend_text), len(clipped_fe),
            )
        parts.append(clipped_fe)
        used_tokens += _estimate_tokens(clipped_fe)

    # ── 2. COC 伺服器脈絡（已去重的版本） ──
    coc_text = str(coc_bundle.get("text") or "").strip()
    if coc_text:
        available = total_budget - used_tokens
        clipped_coc = _truncate_to_budget(coc_text, available)
        if len(clipped_coc) < len(coc_text):
            packer_notes.append(
                f"truncated:coc_context:tokens_available={available}"
            )
            logger.info(
                "[manu_chat][packer] COC 依全域預算截斷 id=%s available=%d",
                job_id, available,
            )
        parts.append(clipped_coc)
        used_tokens += _estimate_tokens(clipped_coc)

    # ── 3. 結算剩餘的全域額度 ──
    # upstream context 與 retrieval 都在 ManuscriptRuling 內才加入，
    # 剩餘額度在這裡算好傳過去，由那一層做最後的收斂。
    remaining_budget = max(0, total_budget - used_tokens)

    packed = "\n\n".join(p for p in parts if p)
    return packed, remaining_budget, packer_notes, list(coc_bundle.get("items") or [])


_COC_CURRENT_SECTION_TIER = "current_section"


def _extract_coc_section_text(coc_bundle: dict, section: str) -> str:
    """
    取出 COC 裡「目前章節伺服器版本」的純內容，供去重比對。

    從 bundle["blocks"] 的結構化欄位取，**不從 bundle["text"] 用正規表示式撈標頭**。
    先前的作法是比對 "[目前章節 {section} · S.Ver ...]" 這個顯示字串，於是
    coc_bundle.py 只要改一個標籤文字，去重就會靜默失效、目前章節被送兩份 ——
    而測試是手工組同樣格式的字串，永遠不會紅。這是「改了常數但行為沒變」那一類
    的無聲故障，所以改成依賴結構而非文字。
    """
    for block in (coc_bundle.get("blocks") or []):
        if block.get("tier") == _COC_CURRENT_SECTION_TIER:
            return str(block.get("body") or "").strip()
    return ""


def _strip_coc_section(coc_bundle: dict, section: str) -> dict:
    """
    去重後把「目前章節伺服器版本」整段從 bundle 移除。回傳淺拷貝，不改原物件。

    items 必須一起移除：那份清單是寫進 context_audit 的來源證據，
    留著會讓 audit 宣稱伺服器版本進了 prompt，但它其實已被前端未存草稿取代 ——
    provenance 說謊比沒有 provenance 更糟。
    """
    kept = [b for b in (coc_bundle.get("blocks") or [])
            if b.get("tier") != _COC_CURRENT_SECTION_TIER]
    result = dict(coc_bundle)
    result["blocks"] = kept
    result["text"] = "\n\n".join(b["text"] for b in kept)
    # 從 blocks 的 `items`（複數）攤平。舊版讀 `b["item"]` 單數，
    # 而「已納入文獻」那種一段對應 N 篇的 block 只放得下一筆 —— 去重一觸發，
    # 逐篇 provenance 就整批消失，audit 只剩一筆合成的彙總來源。
    result["items"] = [i for b in kept for i in (b.get("items") or [])]
    return result


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
    # 這裡的截斷是 chars 層面，為的是避免超大 payload 炸記憶體，不是 token 預算控制。
    # **但它必須留下紀錄**：被砍掉的是作者剛寫、還沒存檔的尾端內容，
    # 而 packer 收到的已經是截斷後的字串，它無從得知這件事發生過
    # ——先前這裡的註解宣稱「packer 會補進 packer_notes」，那是錯的，實測 notes 是空的。
    pre_pack_notes: list[str] = []
    if len(context_text) > _MAX_CHAT_CONTEXT_CHARS:
        pre_pack_notes.append(
            f"truncated:frontend_context_chars:{len(context_text)}->{_MAX_CHAT_CONTEXT_CHARS} "
            f"(MANUSCRIPT_CHAT_MAX_CONTEXT_CHARS；被截掉的是草稿尾端)"
        )
        logger.info(
            "[manu_chat] 前端 context 超過字元上限，依字元截斷 before=%d max=%d",
            len(context_text), _MAX_CHAT_CONTEXT_CHARS,
        )
        context_text = context_text[:_MAX_CHAT_CONTEXT_CHARS]
    target_lang = data.get('target_lang', 'Academic English')
    attachment = data.get('attachment')
    import_type = data.get('import_type', 'other')

    # 專案解析與權限檢查會碰 DB，例外不能讓 handler 靜默死亡：
    # 這段在 ack 之前，前端此時只有轉圈、沒有 job_id，
    # 唯一還能通知它的管道就是 sys_msg。
    try:
        pid = _resolve_socket_pid_or_emit(data)
        if not pid:
            return
        if not _ensure_socket_project_access(pid):
            return
    except Exception:
        logger.error("[manu_chat] 專案解析／權限檢查失敗", exc_info=True)
        emit('sys_msg', {'msg': 'Server error while resolving project. Please retry.'})
        return
    title = data.get('title', 'Untitled Paper')
    section = data.get('section', 'general')

    # NOTE(NOTE-015): 章節層授權。_ensure_socket_project_access 只驗到「是不是這個專案的成員」，
    # 但 section 完全來自請求體 —— 限定編輯只要偽造 payload 指定未被指派的章節，
    # 就能拿到該章的生成結果，並把聊天紀錄寫進他無權編輯的章節。
    # 用「寫入」而非「讀取」當門檻，與 cmd_save_block 同一條線：這個事件會
    # save_chat_history() 到該章、產出的草稿也是要寫進該章的。
    #
    # 刻意不在這裡把 section 做 _safe_component 清洗：ManuscriptIO._block_dir 對
    # 非 ASCII 章節另有對應邏輯，在這層先清洗會讓中文章節指到錯的目錄。
    # 清洗留在各自的儲存層，這裡只負責授權。
    try:
        chat_uid = _session_uid()
        if chat_uid is not None and not _socket_can_write_section(chat_uid, pid, section):
            emit('sys_msg', {
                'msg': f'權限不足：你沒有「{section}」章節的撰寫權限，'
                       '可改用留言功能提供意見。',
            })
            return
    except Exception:
        # ACL 判定本身失敗一律當作不通過。這裡 fail-open 的代價是把別人的章節
        # 內容送進 LLM，比讓使用者重試一次嚴重得多。
        logger.error("[manu_chat] 章節權限檢查失敗（已拒絕） pid=%s section=%s", pid, section, exc_info=True)
        emit('sys_msg', {'msg': 'Server error while checking section permission. Please retry.'})
        return

    # NOTE(NOTE-012): 版本真相由伺服器解析，不採用請求體帶來的 s_ver。
    # 前端長期送死值 '0.1'（manuscript_soed.js 兩處），連這裡的舊預設也是 '0.1'，
    # 於是 ManuscriptRuling 讀歷史永遠讀 V0.1、audit 也綁到錯的版本。
    # 仍保留呼叫端送來的值，只為了在 audit 裡比對「前端說的」與「實際的」。
    client_s_ver = data.get('s_ver')
    s_ver, _s_ver_filename = resolve_section_version(pid, section)
    if client_s_ver and s_ver and str(client_s_ver) != str(s_ver):
        logger.info(
            "[manu_chat] 忽略前端宣告的版本 client=%s server=%s pid=%s section=%s",
            client_s_ver, s_ver, pid, section,
        )

    job_id = f"job_{uuid4().hex}"
    sid = request.sid
    _set_job_state(
        job_id,
        cancelled=False,
        cancel_event=threading.Event(),
        sid=sid,
        pid=pid,
        section=section,
    )

    # [v1.8] ack 必須排在所有會失敗的工作之前。
    # 原本 save_chat_history() 排在這個 emit 之前，而它會走 security.write_json_locked
    # 的 FileLock(timeout=10)；鎖逾時／磁碟寫入失敗都是例外，handler 直接死在這裡，
    # 前端於是收不到 job_queued、收不到 job_error、收不到任何東西 ——
    # 轉圈指示器沒有任何人會收掉，實測可以無聲轉三小時以上。
    # 先發 job_queued，前端才有 job_id 可以對帳、可以取消、可以逾時。
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

    try:
        # NOTE(NOTE-012): COC bundle 必須在「本輪訊息寫進歷史之前」組好，
        # 否則剛送出的這句話會同時出現在 user_msg 與歷史裡，白白吃掉預算。
        # 也必須在這裡（socket handler 仍有 request context）算可讀章節 ——
        # 實際生成跑在 _CHAT_EXECUTOR 的 worker thread，那裡沒有 current_user，
        # 在下游才判權限會拿不到身分而預設放行。
        coc_bundle = _build_coc_for_request(pid, section)

        # 使用者訊息寫不進聊天歷史，不該連帶讓整個草稿請求消失：
        # 使用者要的是草稿，歷史少一筆可以事後補，靜默吞掉請求不行。
        try:
            save_chat_history(pid, section, 'user', user_msg, 'text')
        except Exception:
            logger.warning(
                "[manu_chat] save_chat_history(user) 失敗（已忽略，不影響生成） id=%s pid=%s section=%s",
                job_id,
                pid,
                section,
                exc_info=True,
            )

        app_obj = current_app._get_current_object()

        # [2026-08-09] 整包倒語料**預設停用**，改由檢索供給論文依據。
        #
        # 為什麼停用（都是實測數字，不是推測）：
        #   1. 它固定吃滿 DRAFTER_CORPUS_MAX_TOKENS=16000，不管幾篇論文；
        #      加上檢索約 9000，合計 25000 剛好撞 GOOGLE_LLM_GLOBAL_TPM_LIMIT。
        #      補齊目前缺 full_text.json 的兩篇之後必定被節流擋死。
        #   2. 預算是 (16000 - 筆記) // 篇數 再「取開頭」。論文越多每篇越淺：
        #      4 篇剩 16000 字元、16 篇剩 4000 字元 —— 而論文的具體數據幾乎都在
        #      章節尾巴（實測 SEBASR 的 7.56% 在 6865 字元段落的第 6824 字元）。
        #      也就是說它花最貴的預算，買到的是每篇的前言。
        #   3. 檢索的注入量不隨論文數成長，且已證明帶得出 7.56%。
        #
        # 研究筆記不會因此遺失：ManuscriptRuling._load_upstream_context 另外會讀，
        # 實測 source_manifest 仍有 study_note / project_background。
        #
        # 保留開關而非刪除程式碼，是為了不必重新部署就能回退。
        if _drafter_corpus_enabled():
            # 伺服器端語料建構：讀取研究筆記與論文全文，合併進 context_text。
            # 瀏覽器傳來的 context_text（目前 2B 編輯器文字）是「使用者正在寫的草稿」，
            # 與語料庫是互補而非替代關係——草稿非空時仍保留，放在語料之後作為即時脈絡。
            # 任何語料建構失敗只 log，不中斷生成。
            try:
                corpus_result = build_drafter_corpus(pid=pid, title=title)
                corpus_text = corpus_result.get("corpus", "")
                if corpus_result.get("skipped_papers"):
                    logger.info(
                        "[manu_chat] 語料略過論文（缺 full_text.json）: %s",
                        corpus_result["skipped_papers"],
                    )
                if corpus_text:
                    # 語料放前面（研究筆記 > 論文全文），草稿放後面作補充
                    if context_text.strip():
                        context_text = corpus_text + "\n\n[目前草稿 / Current Draft]\n" + context_text
                    else:
                        context_text = corpus_text
            except Exception:
                logger.warning(
                    "[manu_chat] build_drafter_corpus 失敗（已忽略，用原始 context） id=%s pid=%s",
                    job_id, pid, exc_info=True,
                )
        else:
            logger.info(
                "[manu_chat] 整包倒語料已停用，論文依據改由檢索供給 id=%s pid=%s",
                job_id, pid,
            )

        # NOTE(任務 1): 全域 token 預算 packer。
        # 取代舊版「COC text 直接前置到 context_text」的做法。
        # 舊版：COC(12K) + context(6K) + upstream(1.5K) + retrieval(18K) ≈ 37.5K，超過 TPM。
        # 新版：共用一份 _PROMPT_MAX_TOKENS(20K) 預算，retrieval 只能拿剩餘量。
        # 去重邏輯見 _pack_prompt_context 的 docstring。
        if coc_bundle.get("notes"):
            logger.info("[manu_chat] COC 降級紀錄 id=%s notes=%s", job_id, coc_bundle["notes"])

        context_text, remaining_budget, packer_notes, coc_items = _pack_prompt_context(
            frontend_context=context_text,
            coc_bundle=coc_bundle,
            total_budget=_PROMPT_MAX_TOKENS,
            section=section,
            job_id=job_id,
        )
        # 字元層截斷發生在 packer 之前，packer 看不到，必須在這裡併回來，
        # 否則作者尾端的內容會靜默消失且 audit 上沒有任何痕跡。
        packer_notes = pre_pack_notes + packer_notes
        if packer_notes:
            logger.info("[manu_chat] Packer 裁切紀錄 id=%s notes=%s", job_id, packer_notes)

        payload = {
            'user_msg': user_msg,
            'context_text': context_text,
            # 去重後的來源清單，不是原始 bundle 的 —— 見 _pack_prompt_context 的回傳說明。
            'coc_items': coc_items,
            'coc_notes': (coc_bundle.get("notes") or []) + packer_notes,
            # 「剩餘的全域預算」，不是「檢索的預算」—— 下游還要在同一個額度內
            # 容納 upstream context（研究筆記）才輪到檢索。0 是有效值。
            'remaining_budget': remaining_budget,
            'target_lang': target_lang,
            'attachment': attachment,
            'import_type': import_type,
            'pid': pid,
            'title': title,
            'section': section,
            's_ver': s_ver,
        }
        _CHAT_EXECUTOR.submit(_process_chat_job, app_obj, sid, job_id, payload)
    except Exception:
        # ack 之後才炸的話前端已經在等這個 job_id，一定要補一個終止事件，
        # 否則就退化回「永遠轉圈」——這正是本次修的病。
        logger.error("[manu_chat] job 派送失敗 id=%s pid=%s", job_id, pid, exc_info=True)
        _delete_job_state(job_id)
        emit('job_error', {
            'job_id': job_id,
            'stage': 'error',
            'message': 'Failed to queue job',
        })


@socketio.on('cmd_cancel_job', namespace='/manu_ws')
def handle_cancel_job(data):
    job_id = (data or {}).get('job_id', '')
    if not job_id:
        emit('sys_msg', {'msg': 'Cancel failed: missing job_id'})
        return
    result = _cancel_job_for_sid(job_id, request.sid)
    if result == 'missing':
        emit('job_cancelled', {'job_id': job_id, 'note': 'Job already completed or missing.'})
        return
    if result == 'forbidden':
        emit('sys_msg', {'msg': 'Cancel failed: job belongs to another connection.'})
        return
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


@bp.route('/api/draft/flush', methods=['POST'])
def flush_draft_on_unload():
    """
    離開頁面前保住未落地的編輯。**只給 `navigator.sendBeacon()` 用。**

    NOTE(NOTE-029): 為什麼需要一條 HTTP 路徑，而不是沿用 socket 的
    `cmd_autosave_block` —— autosave 是 1500ms debounce，使用者在這個視窗內
    點掉導覽列（`_navbar.html` 的品牌連結就浮在編輯區正上方）就會永久丟失
    最新輸入；實測打字後 6ms 內導頁，哨兵在整個 data/ 裡一個字都找不到。
    而 unload 期間 socket 連線正在拆除，`emit()` 不保證送達，
    `sendBeacon` 才是為這個時機設計的傳輸。
    這不是第二套儲存邏輯：底下呼叫的是**同一個** `ManuscriptIO.save_draft`。

    安全邊界：
      - `sendBeacon` **無法設定自訂標頭**，因此帶不了 CSRF token。本路徑掛在
        `/manuscript/api/` 之下讓 `is_api_request_path()` 認得它、跳過 CSRF 守衛，
        所以**授權必須在這裡自己做**，不能靠 method 推導。
      - ACL 與 `cmd_autosave_block` 逐條相同：專案成員 + 該章節可寫。
        「只是草稿」不是放寬的理由 —— 草稿內容就是稿件內容。
      - 只寫 `_draft` 檔：不產生版本、不寫 RevisionLog（與 socket 路徑同語意）。
    """
    # sendBeacon 送出的 Blob 常常帶 text/plain，get_json() 會直接拒收，
    # 因此一律用 force=True 自己解析。
    data = request.get_json(silent=True, force=True) or {}

    try:
        pid = validate_id(data.get('pid'), "project_id")
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid request'}), 400

    section = str(data.get('section') or 'general')

    # 授權刻意**不用** enforce_project_ownership：它依 HTTP method 推 min_role，
    # POST 一律要 editor，而 coauthor（限定編輯）低於 editor —— 那會讓限定編輯
    # 在自己被指派的章節上反而存不了草稿。這裡改成與 socket 版逐條相同的兩道：
    # 專案成員（任何角色）+ 該章節可寫。
    current_username = None
    auth_mode = str(current_app.config.get("AUTH_MODE", "none")).strip().lower()
    if auth_mode == "session":
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'success': False, 'message': 'Forbidden'}), 403
            if get_workspace_role(current_user.id, pid) is None:
                return jsonify({'success': False, 'message': 'Forbidden'}), 403
            current_username = current_user.username
            if not _socket_can_write_section(current_user.id, pid, section):
                return jsonify({'success': False, 'message': 'Forbidden'}), 403
        except ImportError:
            pass

    try:
        result = ManuscriptIO.save_draft(
            pid, section,
            data.get('title', 'Untitled_Paper'),
            data.get('content', ''),
            updated_by=current_username,
            owner_key=_session_uid(),
        )
        return jsonify({
            'success': True,
            'section': result.get('section', section),
            'saved_at': result.get('saved_at'),
        }), 200
    except Exception as e:
        logger.error("[draft/flush] pid=%s section=%s %s", pid, section, e, exc_info=True)
        return jsonify({'success': False, 'message': 'Draft flush failed'}), 500


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

def _req_echo(data: dict) -> dict:
    """
    [v2.0] 把前端的切章請求識別碼原樣回送。

    2B 切章是「先問版本清單、再載入版本」的兩段式非同步流程，而 block_list /
    block_loaded 同時服務四種來源（切章、2A 章節同步、開啟舊版 modal、衝突列
    重新載入）。少了識別碼，前端無法分辨「這份回應屬於哪一次切章」，快速
    A→B→C 時遲到的 A 回應就會覆蓋 C 的畫布。

    req_token 只回送給發問的那條連線，不做跨連線廣播，因此不是注入面；
    仍截斷長度，避免前端塞入超大字串佔用回應體積。
    """
    echo = {}
    token = data.get('req_token')
    if token is not None:
        echo['req_token'] = str(token)[:64]
    intent = data.get('intent')
    if intent is not None:
        echo['intent'] = str(intent)[:32]
    return echo


@socketio.on('cmd_list_blocks', namespace='/manu_ws')
def handle_list_blocks(data):
    data = data or {}
    section = data.get('section', 'general')
    # 每一條 return 路徑都必須帶上 echo：前端切章時會先把畫布切成 loading，
    # 只有在收到「符合目前 token」的回應後才會離開該狀態。任何一條沒帶
    # req_token 的失敗回應，都會讓使用者的畫布永久卡在載入中。
    echo = _req_echo(data)
    pid = _resolve_socket_pid_or_emit(
        data,
        missing_msg='',
        missing_event='block_list',
        missing_payload=dict({'files': [], 'section': section}, **echo),
    )
    if not pid:
        return
    if not _ensure_socket_project_access(
        pid,
        forbidden_event='block_list',
        forbidden_payload=dict({'files': [], 'section': section}, **echo),
    ):
        return

    # [collab] 章節層讀取過濾：限定編輯看不到未被指派的章節，連版本清單都不給。
    uid = _session_uid()
    if uid is not None and not _socket_can_read_section(uid, pid, section):
        emit('block_list', dict({'files': [], 'section': section, 'versions': [],
                                 'draft': None, 'next_ver': None, 'forbidden': True}, **echo))
        return

    # files 保留舊契約（前端與 e2e 測試以檔名載入舊版）；
    # versions 是 v1.8 新增的結構化清單，供版本選單顯示 from_ver / 時間 / 作者。
    emit('block_list', dict({
        'files': ManuscriptIO.list_blocks(pid, section),
        'section': section,
        'versions': ManuscriptIO.list_block_versions(pid, section),
        'draft': ManuscriptIO.load_draft(pid, section,
                                         owner_key=uid,
                                         owner_name=_session_username()),
        'next_ver': ManuscriptIO.next_block_version(pid, section),
    }, **echo))

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
    echo = _req_echo(data)

    # [collab] 讀取過濾：限定編輯不得載入未被指派章節的任何版本內容。
    uid = _session_uid()
    if uid is not None and not _socket_can_read_section(uid, pid, section):
        emit('sys_msg', {'msg': f'權限不足：你沒有「{section}」章節的存取權限。'})
        # [v2.0] 失敗也要回 block_loaded：切章的自動載入正等這個事件才離開
        # loading 狀態，只送 sys_msg 會讓畫布永遠停在「載入中」。
        # 舊前端讀 data.ok，收到 ok=False 會自然忽略，契約相容。
        emit('block_loaded', dict({'ok': False, 'section': section,
                                   'filename': filename, 'reason': 'forbidden'}, **echo))
        return

    content = ManuscriptIO.load_block(pid, section, filename)
    if content:
        # [v1.7] 帶 _rev 給前端，前端記住作為下次存檔的 base_rev
        emit('block_loaded', dict({
            'ok': True,
            'content': content,
            'section': section,
            'filename': filename,
            '_rev': content.get('_rev', 0),
        }, **echo))
    else:
        emit('sys_msg', {'msg': 'Failed to load block JSON.'})
        emit('block_loaded', dict({'ok': False, 'section': section,
                                   'filename': filename, 'reason': 'not_found'}, **echo))

# =========================================================================
# Image & Asset Management Socket Routes
# =========================================================================
@socketio.on('cmd_save_image', namespace='/manu_ws')
def handle_save_image(data):
    """接收前端傳來的圖片與屬性，解碼、驗型別後存檔註冊。

    NOTE(NOTE-033) 這裡原本只有 `_ensure_socket_project_access`，那只驗「是不是
    專案成員」—— 也就是 **viewer 可以上傳圖片**並佔用專案容量。圖片是稿件內容
    的一部分，寫入門檻必須與 `cmd_save_block` 一致。

    NOTE(NOTE-034) `fig_id` 一律由伺服器依 registry 現況推導，不接受前端指定
    編號。前端唯一能提供的是 caption。
    """
    data = data or {}
    pid = _resolve_socket_pid_or_emit(data, missing_msg='Image save failed: Missing project id (pid).')
    if not pid:
        return
    if not _ensure_socket_project_access(pid):
        return

    # 圖片沒有自己的章節欄位，插入點是「目前章節」。用它做章節層判定，
    # 讓 coauthor 在自己被指派的章節仍然可以上傳（與 NOTE-029 的草稿保底同一個判斷）。
    section = _safe_component(data.get('section'), 'general')
    uid = _session_uid()
    if uid is not None and not _socket_can_write_section(uid, pid, section):
        emit('image_saved', {'ok': False, 'error': 'forbidden'})
        emit('sys_msg', {'msg': '權限不足：唯讀角色不能上傳圖片。'})
        return

    base64_data = data.get('image_data')
    filename = data.get('filename', 'untitled.png')
    caption = data.get('caption', '')
    source = data.get('source', 'upload')

    if not base64_data:
        emit('sys_msg', {'msg': 'Image save failed: No image data provided.'})
        return

    try:
        fig_id = ManuscriptImage.next_figure_label(pid)
        meta = ManuscriptImage.save_image_asset(pid, base64_data, filename, fig_id, caption, source)
        emit('image_saved', {'ok': True, 'meta': meta})
        emit('sys_msg', {'msg': f"Image successfully registered as {meta['id']}"})
    except ValueError as e:
        # 型別／大小這類使用者自己可以修正的問題，訊息要回得去；
        # 只 log 不回報會讓上傳看起來「沒反應」。
        logger.info("[cmd_save_image] rejected pid=%s: %s", pid, e)
        emit('image_saved', {'ok': False, 'error': str(e)})
        emit('sys_msg', {'msg': f"圖片未被接受：{e}"})
    except Exception as e:
        logger.error("[cmd_save_image] %s", e, exc_info=True)
        emit('image_saved', {'ok': False, 'error': 'internal_error'})
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
