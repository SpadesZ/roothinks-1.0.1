# 檔案路徑: app/core_pro/manuscript/chapter_routes.py
# 產生時間: 2026-07-27 15:30 +08:00
# 版本: v2.0
# 模組定位:
#   章節層協作 API：撰寫指派與章節留言。掛在 manuscript Blueprint
#   （url_prefix=/manuscript），實際路徑為 /manuscript/api/chapter/...
# 主要責任:
#   GET    /manuscript/api/chapter/<pid>/assignments          — 列出章節指派（viewer+）
#   POST   /manuscript/api/chapter/<pid>/assignments          — 指派章節（editor+）
#   DELETE /manuscript/api/chapter/<pid>/assignments/<aid>    — 取消指派（editor+）
#   GET    /manuscript/api/chapter/<pid>/comments             — 列出留言（viewer+）
#   POST   /manuscript/api/chapter/<pid>/comments             — 新增留言（viewer+）
#   PATCH  /manuscript/api/chapter/<pid>/comments/<cid>       — 改內容或標記已解決
#   DELETE /manuscript/api/chapter/<pid>/comments/<cid>       — 刪除留言
#   GET    /manuscript/api/chapter/<pid>/my-permissions       — 前端據此鎖定唯讀章節
#   GET    /manuscript/api/chapter/<pid>/settings              — 讀協作開關（全成員）
#   PUT    /manuscript/api/chapter/<pid>/settings              — 切換開關（**owner only**）
# 呼叫來源:
#   manuscript_workspace.html 的章節指派 UI 與留言側欄；
#   test/unit/test_chapter_perm.py
# 輸入輸出契約:
#   POST assignments  body: {"section_key": str, "email": str}（亦接受 "username"）
#   POST comments     body: {"section_key": str, "body": str}
#   PATCH comments    body: {"body": str} 和／或 {"resolved": bool}
#   回應一律 {"success": bool, ...}
# 安全邊界:
#   [v2.0 角色語意] 讀取與寫入是**兩個獨立維度**，不可用 ROLE_ORDER 線性推導：
#     viewer    讀全部章節，不能寫，可留言
#     coauthor  只讀得到也只寫得到被指派章節（讀取範圍比 viewer **還窄**）
#     editor    讀寫全部章節
#     owner     再加上成員管理與協作開關
#   - 讀章節：security.can_read_section；列章節：security.visible_sections。
#   - 寫章節：security.can_write_section。
#   - 留言：can_comment_on_section（跟著讀取範圍走——看不到就不能留言）。
#   - 指派章節：owner 與 editor 皆可（can_assign_sections）。
#   - 協作開關：**僅 owner** 可切換；開啟後放寬 coauthor 的讀取與留言範圍，
#     但**寫入權永遠只限被指派章節**，不受開關影響。
#   - 非本專案成員一律 403，且不透露專案是否存在。
#   - AUTH_MODE != session（dev/TESTING）時放行，維持既有測試相容性。
# 維護提醒:
#   - 路徑位於 /manuscript/api/ 之下，is_api_request_path 為 True，
#     因此不走 csrf.protect()，改以 session cookie 驗身分。
#   - 這裡刻意不呼叫 enforce_project_ownership：它把 POST 一律要求 editor，
#     會讓「viewer 可留言」與「coauthor 可寫被指派章節」都被擋掉。
# 驗證方式:
#   python -m pytest test/unit/test_chapter_perm.py -q
# ------------------------------------------------------------------------------
import logging

from flask import current_app, jsonify, request
from werkzeug.exceptions import BadRequest

from app import db
from app.core_pro.manuscript.manuscript_routes import bp, _resolve_formal_project_pid
from app.models import (
    ChapterAssignment,
    ChapterComment,
    ProjectCollabSetting,
    ROLE_ORDER,
    ROLE_EDITOR,
    ROLE_OWNER,
    User,
)
from app.security import (
    can_assign_sections,
    can_comment,
    can_comment_on_section,
    can_read_section,
    can_write_section,
    coauthor_open_access,
    get_workspace_role,
    validate_id,
    visible_sections,
)

logger = logging.getLogger("chapter_routes")

_MAX_COMMENT_LEN = 4000


def _auth_mode() -> str:
    return str(current_app.config.get("AUTH_MODE", "none")).strip().lower()


def _session_user():
    """
    取得目前登入者；AUTH_MODE != session 或未登入時回 None。

    回傳 None 代表「dev/TESTING 模式」，呼叫端一律放行 —— 與既有 security 模組
    的三模式分派保持一致，不破壞未啟用帳號系統時的行為。
    """
    if _auth_mode() != "session":
        return None
    try:
        from flask_login import current_user
        return current_user if current_user.is_authenticated else None
    except ImportError:
        return None


def _forbidden():
    return jsonify({"success": False, "error": "forbidden"}), 403


def _safe_section(raw: str) -> str:
    """章節鍵值清洗。留言與指派都以它為索引，需擋住異常長度與空值。"""
    value = str(raw or "").strip()
    if not value or len(value) > 50:
        raise BadRequest("Invalid section_key")
    return value


# 2C 全文留言沒有章節，但 chapter_comments.section_key 是 NOT NULL（既有結構，
# 改成可空要重建整張表，風險遠大於收益）。用一個保留鍵值填欄位，真正的判別依據
# 是 scope 欄位，不是這個字串。
_PAPER_SCOPE_KEY = "__paper__"
_SCOPE_SECTION = "section"
_SCOPE_PAPER = "paper"


def _safe_scope(raw) -> str:
    value = str(raw or _SCOPE_SECTION).strip().lower()
    if value not in (_SCOPE_SECTION, _SCOPE_PAPER):
        raise BadRequest("Invalid scope")
    return value


def _safe_ver(raw):
    """版本字串清洗。允許 None（未指定），其餘限長度，避免塞入超長字串。"""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) > 20:
        raise BadRequest("Invalid version")
    return value


def _can_read_paper(user_id, pid: str) -> bool:
    """
    是否可讀 2C 全篇（連帶決定能否讀寫 2C 全文留言）。

    與 manuscript_routes._socket_can_read_paper 同一條規則：主論文是所有章節組
    裝出來的成品，限定編輯若能讀整篇，章節層的讀取限制就被整篇繞過了；全文留言
    會引用全篇內容，因此適用同一道門檻。
    """
    try:
        from app.security import is_section_scoped_role
        role = get_workspace_role(user_id, pid)
        if role is None:
            return False
        return not is_section_scoped_role(role, pid)
    except Exception:
        logger.warning("[perm] _can_read_paper 失敗 user=%s pid=%s", user_id, pid, exc_info=True)
        return False


def _resolve_pid(raw_pid: str) -> str:
    """驗證並正規化為正式專案 pid。"""
    validate_id(raw_pid, "project_id")
    pid = _resolve_formal_project_pid(raw_pid)
    if not pid:
        raise BadRequest("Invalid pid")
    return pid


def _is_editor_or_above(user_id: int, pid: str) -> bool:
    return ROLE_ORDER.get(get_workspace_role(user_id, pid) or "", 0) >= ROLE_ORDER[ROLE_EDITOR]


# ---------------------------------------------------------------------------
# 章節指派
# ---------------------------------------------------------------------------


@bp.route("/api/chapter/<pid>/assignments", methods=["GET"])
def list_assignments(pid):
    """列出該專案的所有章節指派。專案成員皆可查看（要知道誰負責哪一章）。"""
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_comment(user.id, pid):
            # 借用 can_comment 作為「是否為專案成員」的判定（門檻同為 viewer）。
            return _forbidden()

        rows = ChapterAssignment.query.filter_by(pid=pid).all()
        return jsonify({"success": True, "assignments": [r.to_dict() for r in rows]}), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        logger.error("[assignments GET] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route("/api/chapter/<pid>/assignments", methods=["POST"])
def create_assignment(pid):
    """指派某人撰寫某章節。owner 與 editor 皆可指派。"""
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_assign_sections(user.id, pid):
            return _forbidden()

        data = request.get_json(silent=True) or {}
        section_key = _safe_section(data.get("section_key"))
        identifier = str(data.get("email") or data.get("username") or "").strip()
        if not identifier:
            return jsonify({"success": False, "message": "email is required"}), 400

        # email 是登入識別，優先用它查；username 只是顯示名，作為退路。
        target = User.find_by_email(identifier) or User.query.filter_by(
            username=identifier
        ).first()
        if not target:
            return jsonify({"success": False, "message": f"找不到使用者「{identifier}」"}), 404

        # 被指派者必須先是專案成員，否則指派了也讀不到專案。
        if get_workspace_role(target.id, pid) is None:
            return jsonify({
                "success": False,
                "message": f"「{identifier}」尚未加入此專案，請先將他加為成員。",
            }), 400

        existing = ChapterAssignment.query.filter_by(
            pid=pid, section_key=section_key, user_id=target.id
        ).first()
        if existing:
            return jsonify({"success": True, "assignment": existing.to_dict()}), 200

        row = ChapterAssignment(
            pid=pid,
            section_key=section_key,
            user_id=target.id,
            assigned_by=user.id if user is not None else None,
        )
        db.session.add(row)
        db.session.commit()
        return jsonify({"success": True, "assignment": row.to_dict()}), 201
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[assignments POST] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route("/api/chapter/<pid>/assignments/<int:assignment_id>", methods=["DELETE"])
def delete_assignment(pid, assignment_id):
    """取消章節指派。owner 與 editor 皆可。"""
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_assign_sections(user.id, pid):
            return _forbidden()

        row = ChapterAssignment.query.filter_by(id=assignment_id, pid=pid).first()
        if not row:
            return jsonify({"success": False, "message": "Assignment not found"}), 404

        db.session.delete(row)
        db.session.commit()
        return jsonify({"success": True}), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[assignments DELETE] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# 章節留言
# ---------------------------------------------------------------------------


@bp.route("/api/chapter/<pid>/comments", methods=["GET"])
def list_comments(pid):
    """
    列出留言。

    篩選參數：
      ?section=<key>   2B 章節留言，限定該章
      ?s_ver=<ver>     限定該章的某一版（scope=section）
      ?scope=paper     2C 全文留言
      ?g_ver=<ver>     限定某一個 G.Ver（scope=paper）
      ?include_legacy=1  一併回傳本次改動前留下的、沒有版本定位的舊留言

    NOTE(NOTE-010) 有給版本就「嚴格」篩選：切版本後只會看到綁在該版的留言。
    舊留言（s_ver/g_ver 皆 NULL）不會混進來，除非呼叫端明確要求 include_legacy
    ——把來歷不明的留言預設顯示在每一版旁邊，正是「留言漂移」本身。
    """
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_comment(user.id, pid):
            return _forbidden()

        scope = _safe_scope(request.args.get("scope"))
        include_legacy = str(request.args.get("include_legacy") or "").strip() in ("1", "true", "yes")
        query = ChapterComment.query.filter_by(pid=pid)

        if scope == _SCOPE_PAPER:
            # 2C 全文留言：門檻是「能不能讀整篇」，不是任一章節。
            if user is not None and not _can_read_paper(user.id, pid):
                return _forbidden()
            query = query.filter(ChapterComment.scope == _SCOPE_PAPER)
            g_ver = _safe_ver(request.args.get("g_ver"))
            if g_ver:
                query = query.filter(
                    db.or_(ChapterComment.g_ver == g_ver,
                           ChapterComment.g_ver.is_(None)) if include_legacy
                    else ChapterComment.g_ver == g_ver
                )
        else:
            query = query.filter(
                db.or_(ChapterComment.scope == _SCOPE_SECTION, ChapterComment.scope.is_(None))
            )
            section = request.args.get("section")
            if section:
                section = _safe_section(section)
                if user is not None and not can_read_section(user.id, pid, section):
                    return _forbidden()
                query = query.filter_by(section_key=section)
            s_ver = _safe_ver(request.args.get("s_ver"))
            if s_ver:
                query = query.filter(
                    db.or_(ChapterComment.s_ver == s_ver,
                           ChapterComment.s_ver.is_(None)) if include_legacy
                    else ChapterComment.s_ver == s_ver
                )

        rows = query.order_by(ChapterComment.created_at.asc()).all()
        # [collab] 未指定章節時逐筆過濾，避免限定編輯讀到別章的留言內容。
        # 全文留言沒有真正的章節，改以「能否讀整篇」把關（上面已擋過）。
        if user is not None:
            rows = [
                r for r in rows
                if (r.scope == _SCOPE_PAPER) or can_read_section(user.id, pid, r.section_key)
            ]
        return jsonify({"success": True, "comments": [r.to_dict() for r in rows]}), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        logger.error("[comments GET] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route("/api/chapter/<pid>/comments", methods=["POST"])
def create_comment(pid):
    """
    新增章節留言。只要是專案成員就能留言 —— 包含未被指派該章節的 coauthor，
    這正是「其他手稿章節可以提供 comment 留言功能」的需求。
    """
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_comment(user.id, pid):
            return _forbidden()

        data = request.get_json(silent=True) or {}
        scope = _safe_scope(data.get("scope"))

        # NOTE(NOTE-010) 版本定位在「新增當下」就寫死，之後不再更動：留言要能永遠
        # 指回它當初評論的那一版。前端送出目前檢視的 S.Ver / G.Ver。
        if scope == _SCOPE_PAPER:
            # 2C 全文留言：與讀整篇同一道門檻。
            if user is not None and not _can_read_paper(user.id, pid):
                return _forbidden()
            section_key = _PAPER_SCOPE_KEY
            s_ver = None
            g_ver = _safe_ver(data.get("g_ver"))
        else:
            section_key = _safe_section(data.get("section_key"))
            # [collab] 留言權限跟著讀取範圍走：看不到的章節不能留言。
            if user is not None and not can_comment_on_section(user.id, pid, section_key):
                return _forbidden()
            s_ver = _safe_ver(data.get("s_ver"))
            g_ver = None

        body = str(data.get("body") or "").strip()
        if not body:
            return jsonify({"success": False, "message": "留言內容不可為空"}), 400
        if len(body) > _MAX_COMMENT_LEN:
            return jsonify({
                "success": False,
                "message": f"留言長度上限 {_MAX_COMMENT_LEN} 字",
            }), 400

        row = ChapterComment(
            pid=pid,
            section_key=section_key,
            scope=scope,
            s_ver=s_ver,
            g_ver=g_ver,
            author_id=user.id if user is not None else None,
            body=body,
        )
        db.session.add(row)
        db.session.commit()
        return jsonify({"success": True, "comment": row.to_dict()}), 201
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[comments POST] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route("/api/chapter/<pid>/comments/<int:comment_id>", methods=["PATCH"])
def update_comment(pid, comment_id):
    """改留言內容或標記已解決。內容限作者本人；已解決狀態 editor 以上也可切換。"""
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_comment(user.id, pid):
            return _forbidden()

        row = ChapterComment.query.filter_by(id=comment_id, pid=pid).first()
        if not row:
            return jsonify({"success": False, "message": "Comment not found"}), 404

        data = request.get_json(silent=True) or {}
        is_author = user is None or row.author_id == user.id
        is_editor = user is None or _is_editor_or_above(user.id, pid)

        if "body" in data:
            # 只有作者本人能改自己說過的話；editor 也不行，避免竄改他人發言。
            if not is_author:
                return _forbidden()
            body = str(data.get("body") or "").strip()
            if not body:
                return jsonify({"success": False, "message": "留言內容不可為空"}), 400
            if len(body) > _MAX_COMMENT_LEN:
                return jsonify({
                    "success": False,
                    "message": f"留言長度上限 {_MAX_COMMENT_LEN} 字",
                }), 400
            row.body = body

        if "resolved" in data:
            if not (is_author or is_editor):
                return _forbidden()
            row.resolved = bool(data.get("resolved"))

        db.session.commit()
        return jsonify({"success": True, "comment": row.to_dict()}), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[comments PATCH] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route("/api/chapter/<pid>/comments/<int:comment_id>", methods=["DELETE"])
def delete_comment(pid, comment_id):
    """刪除留言。作者本人或 editor 以上（後者供清理離職成員的留言）。"""
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_comment(user.id, pid):
            return _forbidden()

        row = ChapterComment.query.filter_by(id=comment_id, pid=pid).first()
        if not row:
            return jsonify({"success": False, "message": "Comment not found"}), 404

        if user is not None:
            if row.author_id != user.id and not _is_editor_or_above(user.id, pid):
                return _forbidden()

        db.session.delete(row)
        db.session.commit()
        return jsonify({"success": True}), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[comments DELETE] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# 前端權限查詢
# ---------------------------------------------------------------------------


@bp.route("/api/chapter/<pid>/my-permissions", methods=["GET"])
def my_permissions(pid):
    """
    回傳目前使用者在各章節的可寫狀態，供前端把無權章節鎖成唯讀。

    這只是 UI 提示，真正的把關在 cmd_save_block 的伺服器端判定；
    前端鎖定僅為避免使用者白打一段字才被拒絕。
    """
    try:
        pid = _resolve_pid(pid)
        user = _session_user()

        from app.core_pro.manuscript.manuscript_routes import _get_or_init_sections
        section_keys = [row.section_key for row in _get_or_init_sections(pid)]

        if user is None:
            # dev/TESTING：無帳號系統，一律可寫。
            return jsonify({
                "success": True,
                "role": None,
                "can_assign": True,
                "can_comment": True,
                "sections": {key: True for key in section_keys},
                "readable": {key: True for key in section_keys},
                "scoped": False,
                "open_access": True,
                "is_owner": True,
            }), 200

        role = get_workspace_role(user.id, pid)
        if role is None:
            return _forbidden()

        readable = set(visible_sections(user.id, pid, section_keys))
        return jsonify({
            "success": True,
            "role": role,
            # [presence] 前端過濾在線名單時要認得自己。
            "user_id": user.id,
            "can_assign": can_assign_sections(user.id, pid),
            "can_comment": can_comment(user.id, pid),
            # sections    = 可「寫」的章節
            # readable    = 可「讀」的章節（限定編輯比 viewer 還窄，兩者不可互推）
            # scoped      = 是否為章節限定角色，前端據此隱藏其餘章節與 2C 全篇
            "sections": {
                key: can_write_section(user.id, pid, key) for key in section_keys
            },
            "readable": {key: (key in readable) for key in section_keys},
            "scoped": len(readable) < len(section_keys),
            # owner 的開放開關狀態；限定編輯需要知道自己被放行了沒。
            "open_access": coauthor_open_access(pid),
            "is_owner": role == ROLE_OWNER,
        }), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        logger.error("[my-permissions GET] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# 協作開關（owner 專屬）
# ---------------------------------------------------------------------------


def _is_owner(user_id: int, pid: str) -> bool:
    return get_workspace_role(user_id, pid) == ROLE_OWNER


@bp.route("/api/chapter/<pid>/settings", methods=["GET"])
def get_collab_settings(pid):
    """
    讀取專案協作開關。所有成員都看得到目前狀態（限定編輯需要知道自己被放行了沒）。
    """
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not can_comment(user.id, pid):
            return _forbidden()

        row = ProjectCollabSetting.query.filter_by(pid=pid).first()
        return jsonify({
            "success": True,
            "coauthor_open_access": bool(row and row.coauthor_open_access),
            "can_toggle": True if user is None else _is_owner(user.id, pid),
            "updated_by": row.editor.username if (row and row.editor) else None,
            "updated_at": row.updated_at.isoformat() if row else None,
        }), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        logger.error("[settings GET] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


@bp.route("/api/chapter/<pid>/settings", methods=["PUT"])
def update_collab_settings(pid):
    """
    切換「開放限定編輯檢視全文與留言」。**僅 owner 可操作**。

    開啟後限定編輯的讀取範圍擴大到所有章節與 2C 全篇，並可對任何章節留言；
    寫入權不受影響，仍只能改被指派的章節（見 security.can_write_section）。
    """
    try:
        pid = _resolve_pid(pid)
        user = _session_user()
        if user is not None and not _is_owner(user.id, pid):
            return _forbidden()

        data = request.get_json(silent=True) or {}
        if "coauthor_open_access" not in data:
            return jsonify({
                "success": False,
                "message": "coauthor_open_access is required",
            }), 400
        enabled = bool(data.get("coauthor_open_access"))

        row = ProjectCollabSetting.query.filter_by(pid=pid).first()
        if row is None:
            row = ProjectCollabSetting(pid=pid)
            db.session.add(row)
        row.coauthor_open_access = enabled
        row.updated_by = user.id if user is not None else None
        db.session.commit()

        return jsonify({"success": True, "settings": row.to_dict()}), 200
    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[settings PUT] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500
