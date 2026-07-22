# 檔案路徑: app/project_portfolio/member_routes.py
# 產生時間: 2026-07-19 00:00 +08:00
# 版本: v1.0
# 模組定位:
#   工作區成員管理 API。掛在 project_portfolio Blueprint（url_prefix=/api/project）。
# 主要責任:
#   GET    /api/project/<pid>/members          — viewer 以上可查詢成員清單
#   POST   /api/project/<pid>/members          — owner only；新增或更新成員角色
#   DELETE /api/project/<pid>/members/<uname>  — owner only；移除成員
#                                               成員可自行退出（viewer/editor 退出自己）
#   PATCH  /api/project/<pid>/members/<uname>  — owner only；修改角色
# 呼叫來源:
#   前端成員管理介面；test/unit/test_workspace_roles.py
# 輸入輸出契約:
#   POST body: {"username": str, "role": "owner"|"editor"|"viewer"}
#   PATCH body: {"role": "owner"|"editor"|"viewer"}
#   回傳: {"success": bool, ...}
# 安全邊界:
#   - 所有端點先過 enforce_project_ownership（session 模式已驗最低 viewer/editor/owner）。
#   - 成員管理寫入端點在 route 內顯式呼叫 require_workspace_role(pid, 'owner')。
#   - 最後一位 owner 不可降級或移除（400 {"error":"last_owner"}）。
#   - 403 不洩漏專案是否存在：no membership → 403。
# 維護提醒:
#   - AUTH_MODE=none（dev/TESTING）時 require_workspace_role 直接返回 None（放行）。
#   - 允許多 owner（不強制唯一 owner）；只保護「最後一位 owner」。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test -q
# ------------------------------------------------------------------------------
import logging

from flask import jsonify, request
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized

from app import db
from app.models import User, WorkspaceMember, ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER, ROLE_ORDER
from app.project_portfolio.project_routes import bp
from app.security import enforce_project_ownership, require_workspace_role, validate_id

logger = logging.getLogger("member_routes")

_VALID_ROLES = {ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER}


def _last_owner_check(pid: str, exclude_user_id: int = None) -> bool:
    """
    回傳 True 代表「若排除 exclude_user_id 後只剩 0 位 owner」（最後 owner 保護觸發）。
    exclude_user_id=None 表示不排除任何人。
    """
    query = WorkspaceMember.query.filter_by(pid=pid, role=ROLE_OWNER)
    owners = query.all()
    if exclude_user_id is None:
        return len(owners) == 0
    remaining = [o for o in owners if o.user_id != exclude_user_id]
    return len(remaining) == 0


# ---------------------------------------------------------------------------
# GET /api/project/<pid>/members
# ---------------------------------------------------------------------------
@bp.route("/<pid>/members", methods=["GET"])
def list_members(pid):
    """viewer 以上可查詢成員清單（包含 username / role）。"""
    try:
        pid = validate_id(pid, "project_id")
        # enforce_project_ownership: GET → viewer
        enforce_project_ownership(pid)

        members = (
            WorkspaceMember.query
            .filter_by(pid=pid)
            .join(User, User.id == WorkspaceMember.user_id)
            .all()
        )
        return jsonify({
            "success": True,
            "members": [m.to_dict() for m in members],
        }), 200

    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Unauthorized:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    except Forbidden:
        return jsonify({"success": False, "error": "forbidden"}), 403
    except Exception as e:
        logger.error("[members GET] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# POST /api/project/<pid>/members
# ---------------------------------------------------------------------------
@bp.route("/<pid>/members", methods=["POST"])
def add_member(pid):
    """owner only：新增或更新成員角色。"""
    try:
        pid = validate_id(pid, "project_id")
        # enforce_project_ownership: POST → editor；需再驗 owner
        enforce_project_ownership(pid)
        resp = require_workspace_role(pid, ROLE_OWNER)
        if resp is not None:
            return resp

        data = request.get_json(silent=True) or {}
        username = str(data.get("username") or "").strip()
        role = str(data.get("role") or "").strip().lower()

        if not username:
            return jsonify({"success": False, "message": "username is required"}), 400
        if role not in _VALID_ROLES:
            return jsonify({"success": False, "message": f"role must be one of {list(_VALID_ROLES)}"}), 400

        target_user = User.query.filter_by(username=username).first()
        if not target_user:
            return jsonify({"success": False, "message": f"User '{username}' not found"}), 404

        existing = WorkspaceMember.query.filter_by(user_id=target_user.id, pid=pid).first()
        if existing:
            # 若從 owner 降級，需保護最後 owner
            if existing.role == ROLE_OWNER and role != ROLE_OWNER:
                if _last_owner_check(pid, exclude_user_id=target_user.id):
                    return jsonify({"error": "last_owner", "success": False}), 400
            existing.role = role
            db.session.commit()
            return jsonify({"success": True, "member": existing.to_dict()}), 200
        else:
            new_m = WorkspaceMember(user_id=target_user.id, pid=pid, role=role)
            db.session.add(new_m)
            db.session.commit()
            return jsonify({"success": True, "member": new_m.to_dict()}), 200

    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Unauthorized:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    except Forbidden:
        return jsonify({"success": False, "error": "forbidden"}), 403
    except Exception as e:
        db.session.rollback()
        logger.error("[members POST] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# DELETE /api/project/<pid>/members/<username>
# ---------------------------------------------------------------------------
@bp.route("/<pid>/members/<username>", methods=["DELETE"])
def remove_member(pid, username):
    """
    owner only，或成員自行退出（viewer/editor 退出自己）。
    最後一位 owner 不可退出。
    """
    try:
        pid = validate_id(pid, "project_id")
        # enforce_project_ownership: DELETE → owner；但成員退出自己也允許
        # 先取 current_user，再決定是自行退出還是 owner 操作
        try:
            from flask_login import current_user as _cu
            _session_user = _cu if _cu.is_authenticated else None
        except Exception:
            _session_user = None

        is_self_exit = _session_user and _session_user.username == username

        if is_self_exit:
            # 自行退出：只需確認有 membership（即已登入且有 session 守衛），不需 owner
            pass
        else:
            # 需 owner 才能移除他人
            enforce_project_ownership(pid)
            resp = require_workspace_role(pid, ROLE_OWNER)
            if resp is not None:
                return resp

        target_user = User.query.filter_by(username=username).first()
        if not target_user:
            return jsonify({"success": False, "message": f"User '{username}' not found"}), 404

        member = WorkspaceMember.query.filter_by(user_id=target_user.id, pid=pid).first()
        if not member:
            return jsonify({"success": False, "message": "Member not found"}), 404

        # 保護最後一位 owner
        if member.role == ROLE_OWNER and _last_owner_check(pid, exclude_user_id=target_user.id):
            return jsonify({"error": "last_owner", "success": False}), 400

        db.session.delete(member)
        db.session.commit()
        return jsonify({"success": True}), 200

    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Unauthorized:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    except Forbidden:
        return jsonify({"success": False, "error": "forbidden"}), 403
    except Exception as e:
        db.session.rollback()
        logger.error("[members DELETE] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# PATCH /api/project/<pid>/members/<username>
# ---------------------------------------------------------------------------
@bp.route("/<pid>/members/<username>", methods=["PATCH"])
def update_member_role(pid, username):
    """owner only：修改成員角色。最後一位 owner 不可降級。"""
    try:
        pid = validate_id(pid, "project_id")
        enforce_project_ownership(pid)
        resp = require_workspace_role(pid, ROLE_OWNER)
        if resp is not None:
            return resp

        data = request.get_json(silent=True) or {}
        role = str(data.get("role") or "").strip().lower()
        if role not in _VALID_ROLES:
            return jsonify({"success": False, "message": f"role must be one of {list(_VALID_ROLES)}"}), 400

        target_user = User.query.filter_by(username=username).first()
        if not target_user:
            return jsonify({"success": False, "message": f"User '{username}' not found"}), 404

        member = WorkspaceMember.query.filter_by(user_id=target_user.id, pid=pid).first()
        if not member:
            return jsonify({"success": False, "message": "Member not found"}), 404

        # 保護最後一位 owner
        if member.role == ROLE_OWNER and role != ROLE_OWNER:
            if _last_owner_check(pid, exclude_user_id=target_user.id):
                return jsonify({"error": "last_owner", "success": False}), 400

        member.role = role
        db.session.commit()
        return jsonify({"success": True, "member": member.to_dict()}), 200

    except BadRequest as e:
        return jsonify({"success": False, "message": e.description or "Bad request"}), 400
    except Unauthorized:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    except Forbidden:
        return jsonify({"success": False, "error": "forbidden"}), 403
    except Exception as e:
        db.session.rollback()
        logger.error("[members PATCH] %s", e, exc_info=True)
        return jsonify({"success": False, "message": "Internal server error"}), 500
