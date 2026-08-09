# 檔案路徑: app/mentor/routes.py
# 產生時間: 2026-07-26 04:05 +08:00
# 版本: v1.1（2C reviewer workspace 授權）
# 更新時間: 2026-08-10 +08:00
# 模組定位:
#   Mentor 視角的資料彙整與派工 API，以及 mentor 儀表板頁面。
# 主要責任:
#   GET    /mentor/                          — 儀表板頁面（HTML）
#   GET    /api/mentor/mentees               — 我的 mentee 清單 + 活動統計摘要
#   POST   /api/mentor/mentees               — 以 email 加入 mentee
#   DELETE /api/mentor/mentees/<uid>         — 解除歸屬
#   GET    /api/mentor/mentees/<uid>         — 單一 mentee 詳情（專案／手稿／筆記／統計）
#   GET    /api/mentor/mentees/<uid>/tasks   — 該 mentee 的工作清單
#   POST   /api/mentor/mentees/<uid>/tasks   — 指派工作
#   PATCH  /api/mentor/tasks/<tid>           — 更新工作狀態或內容
#   DELETE /api/mentor/tasks/<tid>           — 刪除工作
#   GET    /api/mentor/mentees/<uid>/comments — 評論清單
#   POST   /api/mentor/mentees/<uid>/comments — 新增評論
#   GET    /api/mentor/my-tasks              — 我（身為 mentee）收到的工作
#   GET/POST /api/mentor/mentees/<uid>/reviews/<pid>[/items] — 2C 審閱與資源
# 呼叫來源:
#   app/templates/mentor/dashboard.html + app/static/js/mentor.js；
#   test/unit/test_mentor_scope.py
# 輸入輸出契約:
#   POST mentees  body: {"email": str}
#   POST tasks    body: {"title": str, "body": str?, "due_date": str?, "pid": str?}
#   PATCH task    body: {"status": str?, "title": str?, "body": str?, "due_date": str?}
#   回應一律 {"success": bool, ...}
# 安全邊界:
#   *** 本檔是全系統唯一「繞過 WorkspaceMember 讀取他人資料」的路徑，為高風險區。 ***
#   - 每一個帶 mentee 的端點都必須先過 _require_mentee(mentee_id)，
#     它會確認 (a) 呼叫者是 mentor 身分、(b) 該 mentee 確實掛在呼叫者名下。
#     少一次檢查就是一個跨租戶越權漏洞（參見 commit 36fc14b 修過的同類問題）。
#   - mentor 對 mentee 資料一律唯讀；寫入僅限自己建立的 task / comment。
#   - 不是 mentor 的使用者存取任何 /api/mentor/mentees* 一律 403。
#   - MentorLink 只表達關係；2C 內容權限仍由雙方 WorkspaceMember 決定。
# 維護提醒:
#   - Study 筆記與對話存在 data/<pid>/study/ 之下，只掛 pid 沒有 user_id，
#     因此 mentee 的「筆記」只能經其所屬專案反查；同專案多人時內容是共用的，
#     無法分辨作者。要真正按人歸戶，需先在 study 寫入端補記 author_id。
#   - mentor 同時保有一般 user 身分，本檔不影響其原有專案權限。
# 驗證方式:
#   python -m pytest test/unit/test_mentor_scope.py -q
# ------------------------------------------------------------------------------
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import current_user, login_required
from werkzeug.exceptions import BadRequest

from app import db
from app.mentor import mentor_bp
from app.models import (
    MentorComment,
    MentorLink,
    MentorReviewItem,
    MentorTask,
    Project,
    RevisionLog,
    SESSION_IDLE_TIMEOUT_SEC,
    SYSTEM_ROLE_ADMIN,
    User,
    UserSession,
    WorkspaceMember,
    _as_utc,
)
from app.security import (
    get_workspace_role,
    is_section_scoped_role,
    safe_join_under,
    validate_id,
)

logger = logging.getLogger("mentor_routes")

mentor_api_bp = Blueprint("mentor_api", __name__, url_prefix="/api/mentor")

_MAX_COMMENT_LEN = 4000
_MAX_TITLE_LEN = 200
_MAX_RESOURCE_URL_LEN = 2048
_MAX_RESOURCE_PDF_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# 授權輔助
# ---------------------------------------------------------------------------


def _forbidden():
    return jsonify({"success": False, "error": "forbidden"}), 403


def _require_mentor():
    """呼叫者必須具備 mentor 身分。回傳 None 代表通過。"""
    if not current_user.is_authenticated or not current_user.is_mentor:
        return _forbidden()
    return None


def _require_mentee(mentee_id: int):
    """
    確認 mentee_id 確實掛在目前 mentor 名下。

    這是唯一阻擋「mentor 讀到別人的 mentee」的檢查點，
    每個接受 mentee_id 的端點都必須呼叫它。
    """
    guard = _require_mentor()
    if guard is not None:
        return guard, None

    link = MentorLink.query.filter_by(
        mentor_id=current_user.id, mentee_id=mentee_id
    ).first()
    if link is None:
        # 不區分「沒這個人」與「不是我的 mentee」，避免用列舉試出他人歸屬。
        return _forbidden(), None

    mentee = db.session.get(User, mentee_id)
    if mentee is None:
        return _forbidden(), None
    return None, mentee


def _require_review_project(mentee_id: int, pid: str):
    """確認歸屬，且雙方都獲授權讀該專案 2C 全篇；admin 可跨專案審閱。"""
    # NOTE(NOTE-001): MentorLink 不是內容 ACL；目標專案必須再次驗雙方 membership。
    guard, mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard, None
    try:
        clean_pid = validate_id(pid, "project_id")
    except BadRequest:
        return (jsonify({"success": False, "message": "Invalid project id"}), 400), None

    role = get_workspace_role(mentee_id, clean_pid)
    if role is None or is_section_scoped_role(role, clean_pid):
        return _forbidden(), None
    if current_user.system_role != SYSTEM_ROLE_ADMIN:
        reviewer_role = get_workspace_role(current_user.id, clean_pid)
        if reviewer_role is None or is_section_scoped_role(reviewer_role, clean_pid):
            return _forbidden(), None
    return None, (mentee, clean_pid)


def _has_shared_review_project(mentor_id: int, mentee_id: int) -> bool:
    """MentorLink 只表達關係；真正的 2C 授權必須來自共同 workspace。"""
    mentor_rows = WorkspaceMember.query.filter_by(user_id=mentor_id).all()
    mentor_pids = {
        row.pid for row in mentor_rows
        if not is_section_scoped_role(row.role, row.pid)
    }
    if not mentor_pids:
        return False
    return any(
        row.pid in mentor_pids and not is_section_scoped_role(row.role, row.pid)
        for row in WorkspaceMember.query.filter_by(user_id=mentee_id).all()
    )


def _review_item_dict(row: MentorReviewItem) -> dict:
    payload = row.to_dict()
    if row.kind == MentorReviewItem.KIND_RESOURCE_PDF:
        payload["download_url"] = f"/api/mentor/review-items/{row.id}/download"
    return payload


# ---------------------------------------------------------------------------
# 活動統計
# ---------------------------------------------------------------------------


def close_stale_sessions(user_id: int = None) -> None:
    """
    結算逾時未回報 heartbeat 的 session。

    刻意在「讀取統計時」順手做，而不是另外跑排程：這個系統沒有常駐排程器，
    多一個背景程序等於多一個維運面。
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=SESSION_IDLE_TIMEOUT_SEC)
        query = UserSession.query.filter(UserSession.ended_at.is_(None))
        if user_id is not None:
            query = query.filter(UserSession.user_id == user_id)
        for row in query.all():
            if _as_utc(row.last_seen_at) < cutoff:
                row.close()
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("close_stale_sessions failed", exc_info=True)


def _activity_stats(user_id: int) -> dict:
    """上線次數、總停留時間、最近一次上線、目前是否在線。"""
    close_stale_sessions(user_id)
    sessions = (
        UserSession.query.filter_by(user_id=user_id)
        .order_by(UserSession.login_at.desc())
        .all()
    )
    total_sec = sum(int(s.duration_sec or 0) for s in sessions)
    active = any(s.ended_at is None for s in sessions)
    return {
        "login_count": len(sessions),
        "total_seconds": total_sec,
        "total_minutes": round(total_sec / 60, 1),
        "avg_minutes": round((total_sec / len(sessions)) / 60, 1) if sessions else 0,
        "last_login": sessions[0].login_at.isoformat() if sessions else None,
        "online_now": active,
    }


# ---------------------------------------------------------------------------
# mentee 資料彙整
# ---------------------------------------------------------------------------


def _mentee_projects(user_id: int) -> list:
    """mentee 有 membership 的專案（含其在該專案的角色）。"""
    rows = WorkspaceMember.query.filter_by(user_id=user_id).all()
    out = []
    for row in rows:
        project = Project.query.filter_by(project_id=row.pid).first()
        reviewer_role = get_workspace_role(current_user.id, row.pid)
        reviewer_can_read_2c = (
            current_user.system_role == SYSTEM_ROLE_ADMIN
            or (
                reviewer_role is not None
                and not is_section_scoped_role(reviewer_role, row.pid)
            )
        )
        out.append({
            "pid": row.pid,
            "role": row.role,
            "reviewable_2c": (
                reviewer_can_read_2c
                and not is_section_scoped_role(row.role, row.pid)
            ),
            "name": project.name if project else None,
            "status": project.status if project else None,
        })
    return out


def _data_root() -> str:
    from app.core_pro.manuscript.manuscript_io import _get_data_root
    return _get_data_root()


def _study_notes(pid: str) -> dict:
    """
    讀取專案的 Study 筆記與對話摘要。

    注意：這些檔案只掛 pid、沒有作者欄位，因此無法分辨同專案中是誰寫的。
    """
    result = {"pid": pid, "note": None, "conversations": []}
    try:
        study_dir = safe_join_under(_data_root(), pid, "study")
    except Exception:
        return result
    if not os.path.isdir(study_dir):
        return result

    note_path = os.path.join(study_dir, f"{pid}_note.json")
    if os.path.exists(note_path):
        try:
            with open(note_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            result["note"] = {
                "text": payload.get("notes"),
                "updated_at": payload.get("updated_at"),
            }
        except Exception:
            logger.warning("讀取 study note 失敗 pid=%s", pid, exc_info=True)

    conv_dir = os.path.join(study_dir, "conversations")
    if os.path.isdir(conv_dir):
        for name in sorted(os.listdir(conv_dir), reverse=True)[:20]:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(conv_dir, name), "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                messages = payload.get("messages") or []
                result["conversations"].append({
                    "conv_id": payload.get("conv_id"),
                    "title": payload.get("title"),
                    "updated_at": payload.get("updated_at"),
                    "message_count": len(messages),
                    "messages": messages,
                })
            except Exception:
                logger.warning("讀取 study 對話失敗 %s", name, exc_info=True)
    return result


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


@mentor_bp.route("/")
@login_required
def dashboard():
    """Mentor 儀表板。非 mentor 身分會看到說明而非資料。"""
    return render_template("mentor/dashboard.html")


# ---------------------------------------------------------------------------
# mentee 管理
# ---------------------------------------------------------------------------


@mentor_api_bp.route("/mentees", methods=["GET"])
@login_required
def list_mentees():
    guard = _require_mentor()
    if guard is not None:
        return guard

    links = MentorLink.query.filter_by(mentor_id=current_user.id).all()
    out = []
    for link in links:
        payload = link.to_dict()
        payload["stats"] = _activity_stats(link.mentee_id)
        payload["project_count"] = WorkspaceMember.query.filter_by(
            user_id=link.mentee_id
        ).count()
        payload["open_tasks"] = MentorTask.query.filter_by(
            mentee_id=link.mentee_id, mentor_id=current_user.id
        ).filter(MentorTask.status != MentorTask.STATUS_DONE).count()
        out.append(payload)
    return jsonify({"success": True, "mentees": out}), 200


@mentor_api_bp.route("/mentees", methods=["POST"])
@login_required
def add_mentee():
    """以 email 加入 mentee；一般 mentor 必須先由 owner 授予共同專案權限。"""
    guard = _require_mentor()
    if guard is not None:
        return guard

    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip()
    if not email:
        return jsonify({"success": False, "message": "email is required"}), 400

    target = User.find_by_email(email)
    if not target:
        return jsonify({"success": False, "message": f"找不到 Email 為「{email}」的帳號"}), 404
    if target.id == current_user.id:
        return jsonify({"success": False, "message": "不能把自己加為 mentee"}), 400

    if (
        current_user.system_role != SYSTEM_ROLE_ADMIN
        and not _has_shared_review_project(current_user.id, target.id)
    ):
        return _forbidden()

    existing = MentorLink.query.filter_by(
        mentor_id=current_user.id, mentee_id=target.id
    ).first()
    if existing:
        return jsonify({"success": True, "mentee": existing.to_dict()}), 200

    link = MentorLink(mentor_id=current_user.id, mentee_id=target.id)
    db.session.add(link)
    db.session.commit()
    return jsonify({"success": True, "mentee": link.to_dict()}), 201


@mentor_api_bp.route("/mentees/<int:mentee_id>", methods=["DELETE"])
@login_required
def remove_mentee(mentee_id):
    guard, _mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard

    MentorLink.query.filter_by(
        mentor_id=current_user.id, mentee_id=mentee_id
    ).delete()
    db.session.commit()
    return jsonify({"success": True}), 200


@mentor_api_bp.route("/mentees/<int:mentee_id>", methods=["GET"])
@login_required
def mentee_detail(mentee_id):
    """
    單一 mentee 的完整檢視：帳號、活動統計、專案清單、手稿異動、Study 筆記。
    對 mentor 而言一律唯讀。
    """
    guard, mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard

    projects = _mentee_projects(mentee_id)
    pids = [p["pid"] for p in projects]

    revisions = []
    if pids:
        revisions = [
            row.to_dict()
            for row in RevisionLog.query.filter(
                RevisionLog.user_id == mentee_id, RevisionLog.pid.in_(pids)
            )
            .order_by(RevisionLog.created_at.desc())
            .limit(50)
            .all()
        ]

    include_notes = request.args.get("notes", "1") not in {"0", "false", "no"}
    notes = [_study_notes(p["pid"]) for p in projects] if include_notes else []

    return jsonify({
        "success": True,
        "mentee": {
            "id": mentee.id,
            "username": mentee.username,
            "email": mentee.email,
            "system_role": mentee.system_role,
        },
        "stats": _activity_stats(mentee_id),
        "sessions": [
            s.to_dict()
            for s in UserSession.query.filter_by(user_id=mentee_id)
            .order_by(UserSession.login_at.desc())
            .limit(20)
            .all()
        ],
        "projects": projects,
        "revisions": revisions,
        "study_notes": notes,
    }), 200


# ---------------------------------------------------------------------------
# 派工
# ---------------------------------------------------------------------------


@mentor_api_bp.route("/mentees/<int:mentee_id>/tasks", methods=["GET"])
@login_required
def list_tasks(mentee_id):
    guard, _mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard

    rows = (
        MentorTask.query.filter_by(mentee_id=mentee_id, mentor_id=current_user.id)
        .order_by(MentorTask.created_at.desc())
        .all()
    )
    return jsonify({"success": True, "tasks": [r.to_dict() for r in rows]}), 200


@mentor_api_bp.route("/mentees/<int:mentee_id>/tasks", methods=["POST"])
@login_required
def create_task(mentee_id):
    guard, _mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard

    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "message": "工作標題不可為空"}), 400
    if len(title) > _MAX_TITLE_LEN:
        return jsonify({"success": False, "message": f"標題上限 {_MAX_TITLE_LEN} 字"}), 400

    row = MentorTask(
        mentor_id=current_user.id,
        mentee_id=mentee_id,
        pid=(str(data.get("pid")).strip() or None) if data.get("pid") else None,
        title=title,
        body=str(data.get("body") or "").strip() or None,
        due_date=str(data.get("due_date") or "").strip() or None,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "task": row.to_dict()}), 201


@mentor_api_bp.route("/tasks/<int:task_id>", methods=["PATCH"])
@login_required
def update_task(task_id):
    """
    更新工作。mentor 可改全部欄位；mentee 只能改 status
    （讓被指派者能回報進度，但不能竄改工作內容）。
    """
    row = db.session.get(MentorTask, task_id)
    if row is None:
        return jsonify({"success": False, "message": "Task not found"}), 404

    is_owner_mentor = (
        current_user.is_authenticated
        and current_user.is_mentor
        and row.mentor_id == current_user.id
    )
    is_assignee = current_user.is_authenticated and row.mentee_id == current_user.id
    if not (is_owner_mentor or is_assignee):
        return _forbidden()

    data = request.get_json(silent=True) or {}

    if "status" in data:
        status = str(data.get("status") or "").strip()
        if status not in MentorTask.STATUSES:
            return jsonify({
                "success": False,
                "message": f"status must be one of {sorted(MentorTask.STATUSES)}",
            }), 400
        row.status = status

    if not is_owner_mentor:
        db.session.commit()
        return jsonify({"success": True, "task": row.to_dict()}), 200

    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "message": "工作標題不可為空"}), 400
        row.title = title[:_MAX_TITLE_LEN]
    if "body" in data:
        row.body = str(data.get("body") or "").strip() or None
    if "due_date" in data:
        row.due_date = str(data.get("due_date") or "").strip() or None

    db.session.commit()
    return jsonify({"success": True, "task": row.to_dict()}), 200


@mentor_api_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    row = db.session.get(MentorTask, task_id)
    if row is None:
        return jsonify({"success": False, "message": "Task not found"}), 404
    if not (current_user.is_mentor and row.mentor_id == current_user.id):
        return _forbidden()

    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True}), 200


@mentor_api_bp.route("/my-tasks", methods=["GET"])
@login_required
def my_tasks():
    """我（身為 mentee）收到的工作。任何登入者都能看自己的。"""
    rows = (
        MentorTask.query.filter_by(mentee_id=current_user.id)
        .order_by(MentorTask.created_at.desc())
        .all()
    )
    return jsonify({"success": True, "tasks": [r.to_dict() for r in rows]}), 200


# ---------------------------------------------------------------------------
# 評論
# ---------------------------------------------------------------------------


@mentor_api_bp.route("/mentees/<int:mentee_id>/comments", methods=["GET"])
@login_required
def list_mentor_comments(mentee_id):
    guard, _mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard

    rows = (
        MentorComment.query.filter_by(mentee_id=mentee_id, mentor_id=current_user.id)
        .order_by(MentorComment.created_at.asc())
        .all()
    )
    return jsonify({"success": True, "comments": [r.to_dict() for r in rows]}), 200


@mentor_api_bp.route("/mentees/<int:mentee_id>/comments", methods=["POST"])
@login_required
def create_mentor_comment(mentee_id):
    guard, _mentee = _require_mentee(mentee_id)
    if guard is not None:
        return guard

    data = request.get_json(silent=True) or {}
    body = str(data.get("body") or "").strip()
    if not body:
        return jsonify({"success": False, "message": "評論內容不可為空"}), 400
    if len(body) > _MAX_COMMENT_LEN:
        return jsonify({"success": False, "message": f"評論上限 {_MAX_COMMENT_LEN} 字"}), 400

    row = MentorComment(mentor_id=current_user.id, mentee_id=mentee_id, body=body)
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "comment": row.to_dict()}), 201


# ---------------------------------------------------------------------------
# 2C review workbench
# ---------------------------------------------------------------------------


@mentor_api_bp.route("/mentees/<int:mentee_id>/reviews/<pid>", methods=["GET"])
@login_required
def get_review_workbench(mentee_id, pid):
    guard, target = _require_review_project(mentee_id, pid)
    if guard is not None:
        return guard
    _mentee, clean_pid = target

    from app.core_pro.manuscript.manuscript_io import ManuscriptIO

    versions = ManuscriptIO.list_paper_versions(clean_pid)
    latest_meta = versions[0] if versions else None
    manuscript = None
    if latest_meta:
        loaded = ManuscriptIO.load_paper_version(clean_pid, latest_meta.get("g_ver"))
        if loaded:
            manuscript = {
                "title": loaded.get("title") or latest_meta.get("title") or "Untitled Paper",
                "content": str(loaded.get("content") or ""),
                "version": loaded.get("g_ver") or loaded.get("version") or latest_meta.get("g_ver"),
                "updated_at": loaded.get("_updated_at") or loaded.get("timestamp"),
                "updated_by": loaded.get("_updated_by"),
            }

    rows = (
        MentorReviewItem.query.filter_by(
            mentor_id=current_user.id,
            mentee_id=mentee_id,
            pid=clean_pid,
        )
        .order_by(MentorReviewItem.created_at.asc())
        .all()
    )
    legacy = (
        MentorComment.query.filter_by(mentor_id=current_user.id, mentee_id=mentee_id)
        .order_by(MentorComment.created_at.asc())
        .all()
    )
    return jsonify({
        "success": True,
        "pid": clean_pid,
        "manuscript": manuscript,
        "versions": versions,
        "items": [_review_item_dict(row) for row in rows],
        "legacy_comments": [row.to_dict() for row in legacy],
    })


@mentor_api_bp.route("/mentees/<int:mentee_id>/reviews/<pid>/items", methods=["POST"])
@login_required
def create_review_item(mentee_id, pid):
    guard, target = _require_review_project(mentee_id, pid)
    if guard is not None:
        return guard
    _mentee, clean_pid = target

    is_multipart = request.mimetype == "multipart/form-data"
    data = request.form if is_multipart else (request.get_json(silent=True) or {})
    kind = str(data.get("kind") or "").strip()
    paper_version = str(data.get("paper_version") or "").strip()[:20] or None

    if kind not in MentorReviewItem.KINDS:
        return jsonify({"success": False, "message": "Unsupported review item type"}), 400

    row = MentorReviewItem(
        mentor_id=current_user.id,
        mentee_id=mentee_id,
        pid=clean_pid,
        paper_version=paper_version,
        kind=kind,
    )
    saved_path = None

    if kind in {MentorReviewItem.KIND_COMMENT, MentorReviewItem.KIND_SUGGESTION}:
        body = str(data.get("body") or "").strip()
        if not body:
            return jsonify({"success": False, "message": "內容不可為空"}), 400
        if len(body) > _MAX_COMMENT_LEN:
            return jsonify({"success": False, "message": f"內容上限 {_MAX_COMMENT_LEN} 字"}), 400
        row.body = body

    elif kind == MentorReviewItem.KIND_RESOURCE_URL:
        raw_url = str(data.get("url") or "").strip()
        if not raw_url or len(raw_url) > _MAX_RESOURCE_URL_LEN:
            return jsonify({"success": False, "message": "網址不可為空或超過上限"}), 400
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return jsonify({"success": False, "message": "網址僅接受 http 或 https"}), 400
        row.url = raw_url
        row.title = str(data.get("title") or raw_url).strip()[:_MAX_TITLE_LEN]

    else:
        upload = request.files.get("file")
        original_name = os.path.basename(str(upload.filename or "")).strip() if upload else ""
        if not upload or not original_name.lower().endswith(".pdf"):
            return jsonify({"success": False, "message": "請上傳 PDF 檔案"}), 400
        raw = upload.stream.read(_MAX_RESOURCE_PDF_BYTES + 1)
        if len(raw) > _MAX_RESOURCE_PDF_BYTES:
            return jsonify({"success": False, "message": "PDF 上限 20 MB"}), 413
        if not raw.startswith(b"%PDF-"):
            return jsonify({"success": False, "message": "檔案內容不是有效的 PDF"}), 400

        from app.core_pro.manuscript.manuscript_io import _get_data_root

        root = _get_data_root()
        relative_dir = os.path.join(
            "mentor_resources", str(current_user.id), str(mentee_id), clean_pid
        )
        target_dir = safe_join_under(root, relative_dir)
        os.makedirs(target_dir, exist_ok=True)
        generated_name = f"{uuid4().hex}.pdf"
        saved_path = safe_join_under(target_dir, generated_name)
        temp_path = f"{saved_path}.tmp"
        with open(temp_path, "wb") as handle:
            handle.write(raw)
        os.replace(temp_path, saved_path)

        row.title = str(data.get("title") or original_name).strip()[:_MAX_TITLE_LEN]
        row.file_name = original_name[:255]
        row.file_path = os.path.relpath(saved_path, root).replace(os.sep, "/")
        row.size_bytes = len(raw)

    try:
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if saved_path:
            try:
                os.remove(saved_path)
            except OSError:
                pass
        raise

    return jsonify({"success": True, "item": _review_item_dict(row)}), 201


@mentor_api_bp.route("/review-items/<int:item_id>/download", methods=["GET"])
@login_required
def download_review_pdf(item_id):
    row = db.session.get(MentorReviewItem, item_id)
    if row is None or row.kind != MentorReviewItem.KIND_RESOURCE_PDF:
        return jsonify({"success": False, "message": "Resource not found"}), 404
    guard, _target = _require_review_project(row.mentee_id, row.pid)
    if guard is not None:
        return guard
    if row.mentor_id != current_user.id:
        return _forbidden()

    from app.core_pro.manuscript.manuscript_io import _get_data_root

    root = _get_data_root()
    file_path = safe_join_under(root, *str(row.file_path or "").split("/"))
    if not os.path.isfile(file_path):
        return jsonify({"success": False, "message": "Resource not found"}), 404
    response = send_file(
        file_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=row.file_name or "review-resource.pdf",
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
