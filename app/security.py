# 檔案路徑: app/security.py
# 產生時間: 2026-07-04 00:00 +08:00
# 版本: v1.1
# 模組定位:
#   Roothinks 安全核心：請求驗證、專案存取控制、工作區角色授權。
# 主要責任:
#   1. validate_id / safe_join_under — 輸入驗證與路徑安全。
#   2. require_request_auth / require_socket_auth — Bearer token 守衛。
#   3. [Batch B] get_workspace_role(user_id, pid) — 查詢成員角色。
#   4. [Batch B] require_workspace_role(pid, min_role) — 角色最低門檻守衛。
#   5. [Batch B] enforce_project_ownership(pid) — 三模式分派決策：
#
#      ┌─────────────────────────────┬───────────────────────────────────────────┐
#      │ 條件                         │ 動作                                       │
#      ├─────────────────────────────┼───────────────────────────────────────────┤
#      │ AUTH_MODE=session 且         │ 依 HTTP method 查 WorkspaceMember 角色：   │
#      │ current_user.is_authenticated│   GET/HEAD/OPTIONS → viewer               │
#      │                             │   POST/PUT/PATCH   → editor               │
#      │                             │   DELETE           → owner                │
#      │                             │ 角色不足 → raise Forbidden                 │
#      ├─────────────────────────────┼───────────────────────────────────────────┤
#      │ API_AUTH_ENABLED=True 且     │ 原 TOKEN_ACL_MAP 邏輯照舊                  │
#      │ 無 session user              │ 無 token / 不匹配 → raise Unauthorized/   │
#      │                             │ Forbidden                                  │
#      ├─────────────────────────────┼───────────────────────────────────────────┤
#      │ 兩者皆關（dev/TESTING）       │ 直接放行（zero-impact 相容既有測試）       │
#      └─────────────────────────────┴───────────────────────────────────────────┘
#
#   安全原則：
#   - 403 不洩漏存在性：pid 不在 workspace_members 中一律回 403，
#     不以 404 透露專案是否存在。
#   - session 與 Bearer 不共存：有 session user 時跳過 Bearer 邏輯。
# 維護提醒:
#   - 個別 route 若需更嚴（成員管理必須 owner）：在 route 內顯式呼叫
#     require_workspace_role(pid, 'owner')。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test -q
# ------------------------------------------------------------------------------
import json
import hmac
import hashlib
import logging
import os
import re
import tempfile
from ipaddress import ip_address
from typing import Any, Dict, Iterable, Optional, Tuple

from filelock import FileLock
from flask import current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized

LOGGER = logging.getLogger("security")


ID_PATTERNS = {
    "project_id": re.compile(r"^[a-zA-Z0-9_-]{1,20}$"),
    "paper_id": re.compile(r"^[a-zA-Z0-9_.-]{1,120}$"),
    "conv_id": re.compile(r"^[a-zA-Z0-9_-]{1,50}$"),
    "matrix_id": re.compile(r"^(legacy::)?[a-zA-Z0-9_.-]{1,100}$"),
}


def validate_id(value: Any, id_type: str, required: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise BadRequest(f"Missing {id_type}")
        return ""
    pattern = ID_PATTERNS.get(id_type)
    if not pattern:
        raise BadRequest(f"Unknown id_type: {id_type}")
    if not pattern.fullmatch(raw):
        raise BadRequest(f"Invalid {id_type}")
    return raw


def safe_join_under(base_dir: str, *parts: str) -> str:
    base_text = str(base_dir or "")
    if "\x00" in base_text:
        raise BadRequest("Invalid path")
    for p in parts:
        if "\x00" in str(p or ""):
            raise BadRequest("Invalid path")

    base_real = os.path.realpath(base_text)
    target = os.path.realpath(os.path.join(base_real, *parts))
    try:
        if os.path.commonpath([base_real, target]) != base_real:
            raise BadRequest("Path traversal detected")
    except ValueError:
        # Windows cross-drive path (e.g., C:\ vs D:\) also indicates out-of-bound path usage.
        raise BadRequest("Path traversal detected")
    return target


def mask_secret(secret: str, show_last: int = 4) -> str:
    s = str(secret or "")
    if not s:
        return ""
    if len(s) <= show_last:
        return "*" * len(s)
    return "*" * (len(s) - show_last) + s[-show_last:]


def _configured_tokens() -> list[str]:
    tokens = current_app.config.get("API_BEARER_TOKENS") or []
    out = []
    for t in tokens:
        s = str(t).strip()
        if s:
            out.append(s)
    return out


def _token_matches_configured(token: str, candidates: list[str]) -> bool:
    token_str = str(token or "")
    if not token_str or not candidates:
        return False
    matched = False
    for c in candidates:
        # Constant-time comparison per candidate, without early loop exit.
        if hmac.compare_digest(token_str, c):
            matched = True
    return matched


def _auth_enabled() -> bool:
    return bool(current_app.config.get("API_AUTH_ENABLED", False))


def _extract_bearer_from_header() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


def _extract_bearer_fallback() -> str:
    return (
        request.headers.get("X-API-Token", "").strip()
        or request.args.get("access_token", "").strip()
        or request.args.get("token", "").strip()
    )


def get_request_token() -> str:
    tok = _extract_bearer_from_header() or _extract_bearer_fallback()
    return tok.strip()


def is_api_request_path(path: str) -> bool:
    p = str(path or "")
    return p.startswith("/api/") or p.startswith("/manuscript/api/")


def require_request_auth() -> Optional[Tuple[Any, int]]:
    if not _auth_enabled():
        return None
    if not is_api_request_path(request.path):
        return None

    token = get_request_token()
    tokens = _configured_tokens()
    if not token:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    if not _token_matches_configured(token, tokens):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    g.auth_token = token
    return None


def require_socket_auth(auth_payload: Optional[Dict[str, Any]]) -> str:
    if not _auth_enabled():
        return ""

    token = ""
    if isinstance(auth_payload, dict):
        token = (
            str(auth_payload.get("token") or "").strip()
            or str(auth_payload.get("access_token") or "").strip()
        )
        bearer = str(auth_payload.get("Authorization") or "").strip()
        if not token and bearer.startswith("Bearer "):
            token = bearer.split(" ", 1)[1].strip()

    if not token:
        token = (
            request.args.get("token", "").strip()
            or request.args.get("access_token", "").strip()
            or _extract_bearer_from_header()
        )

    if not _token_matches_configured(token, _configured_tokens()):
        raise Forbidden("Unauthorized socket connection")
    return token


def _token_acl_map() -> Dict[str, Dict[str, Any]]:
    m = current_app.config.get("TOKEN_ACL_MAP") or {}
    if isinstance(m, dict):
        return m
    return {}


def _normalize_pid_aliases(pid: str) -> set[str]:
    s = str(pid or "").strip()
    if not s:
        return set()
    aliases = {s}
    if s.endswith("-p"):
        aliases.add(s[:-2])
    else:
        aliases.add(f"{s}-p")
    return aliases


def check_ownership(token: str, pid: str) -> bool:
    acl_map = _token_acl_map()
    if not acl_map:
        return not bool(current_app.config.get("API_AUTH_ENABLED", False))

    entry = acl_map.get(token) or {}
    allowed = entry.get("allowed_pids") or []
    if not isinstance(allowed, Iterable):
        return False
    allowed_set = {str(x).strip() for x in allowed if str(x).strip()}
    if "*" in allowed_set:
        return True
    aliases = _normalize_pid_aliases(pid)
    return any(a in allowed_set for a in aliases)


# =============================================================================
# [Batch B] 工作區角色授權函數
# =============================================================================

def _get_auth_mode() -> str:
    """取得目前 AUTH_MODE 設定（none / session）。"""
    return str(current_app.config.get("AUTH_MODE", "none")).strip().lower()


def get_workspace_role(user_id: int, pid: str) -> Optional[str]:
    """
    查詢指定 user 在 pid 專案中的角色。
    回傳 'owner' | 'editor' | 'viewer'，若無 membership 回傳 None。
    使用延遲匯入避免循環依賴。
    """
    try:
        from app.models import WorkspaceMember
        member = WorkspaceMember.query.filter_by(user_id=user_id, pid=pid).first()
        return member.role if member else None
    except Exception:
        LOGGER.exception("get_workspace_role failed for user_id=%s pid=%s", user_id, pid)
        return None


def require_workspace_role(pid: str, min_role: str) -> Optional[Any]:
    """
    AUTH_MODE=session 且已登入時，驗證 current_user 在 pid 專案的角色 >= min_role。
    角色不足或無 membership → 回傳 403 JSON（不洩漏專案是否存在）。
    AUTH_MODE != session 或未登入時，回傳 None（放行，讓呼叫者決定後續）。

    :param pid:      專案 ID（project_id 字串）。
    :param min_role: 最低所需角色 'viewer' | 'editor' | 'owner'。
    :return:         None（通過）或 (Response, int) 元組（拒絕）。
    """
    if _get_auth_mode() != "session":
        return None

    try:
        from flask_login import current_user
    except ImportError:
        return None

    if not current_user.is_authenticated:
        return None

    from app.models import ROLE_ORDER

    user_role = get_workspace_role(current_user.id, pid)
    user_level = ROLE_ORDER.get(user_role or "", 0)
    min_level = ROLE_ORDER.get(min_role, 0)

    if user_level < min_level:
        return jsonify({"error": "forbidden", "required": min_role}), 403

    return None


def enforce_project_ownership(pid: str) -> None:
    """
    三模式分派的專案存取守衛（見模組標頭決策表）。

    模式 A（session）: current_user.is_authenticated
        依 HTTP method 決定 min_role，呼叫 require_workspace_role。
    模式 B（Bearer）: API_AUTH_ENABLED=True 且無 session user
        原 TOKEN_ACL_MAP 邏輯。
    模式 C（dev/TESTING）: 兩者皆關
        直接放行，確保 69 舊測試不破。
    """
    # ── 模式 A：session 使用者 ────────────────────────────────────────────
    try:
        from flask_login import current_user as _cu
        _session_active = _cu.is_authenticated
    except Exception:
        _session_active = False

    if _get_auth_mode() == "session" and _session_active:
        method = request.method.upper()
        if method in {"GET", "HEAD", "OPTIONS"}:
            min_role = "viewer"
        elif method in {"POST", "PUT", "PATCH"}:
            min_role = "editor"
        else:  # DELETE
            min_role = "owner"
        result = require_workspace_role(pid, min_role)
        if result is not None:
            # require_workspace_role 回傳 (Response, status)，轉成 Forbidden 讓 route 捕捉
            raise Forbidden("Forbidden")
        return

    # ── 模式 B：Bearer token（機器對機器） ────────────────────────────────
    if _auth_enabled():
        token = getattr(g, "auth_token", "") or get_request_token()
        if not token:
            raise Unauthorized("Unauthorized")
        if not check_ownership(token, pid):
            raise Forbidden("Forbidden")
        return

    # ── 模式 C：dev / TESTING — 放行 ─────────────────────────────────────
    return


def get_acl_context(default_access: str = "internal") -> Tuple[Optional[str], str]:
    token = getattr(g, "auth_token", "") or get_request_token()
    acl_map = _token_acl_map()
    entry = acl_map.get(token) if token else None
    if not isinstance(entry, dict):
        return None, default_access
    tenant = str(entry.get("tenant_id") or "").strip() or None
    access = str(entry.get("access_level") or default_access).strip().lower() or default_access
    return tenant, access


def client_error(message: str, status: int = 400):
    return jsonify({"success": False, "message": message}), status


def _lock_path(path: str) -> str:
    return build_lock_path(path)


def _default_lock_root() -> str:
    env_value = (os.environ.get("LOCK_ROOT") or "").strip()
    if env_value:
        return os.path.abspath(env_value)
    if os.name == "nt":
        return os.path.join(tempfile.gettempdir(), "roothinks-locks")
    return "/tmp/roothinks-locks"


def _resolve_lock_root() -> str:
    try:
        from flask import has_app_context

        if has_app_context():
            cfg_root = str(current_app.config.get("LOCK_ROOT") or "").strip()
            if cfg_root:
                return os.path.abspath(cfg_root)
    except Exception:
        pass
    return _default_lock_root()


def build_lock_path(path: str) -> str:
    """
    Build deterministic lock-file path under configurable LOCK_ROOT.
    This avoids writing *.lock inside bind-mounted /app/data where permission may differ.
    """
    target = os.path.realpath(str(path or ""))
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", os.path.basename(target) or "lock_target")
    digest = hashlib.sha1(target.encode("utf-8")).hexdigest()[:16]
    lock_root = _resolve_lock_root()
    os.makedirs(lock_root, exist_ok=True)
    return os.path.join(lock_root, f"{base}.{digest}.lock")


def load_json_locked(path: str, fallback: Any):
    if not os.path.exists(path):
        return fallback
    lock = FileLock(_lock_path(path), timeout=5)
    try:
        with lock:
            if not os.path.exists(path):
                return fallback
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        LOGGER.warning("Failed to read JSON file: %s", path, exc_info=True)
        return fallback


def write_json_locked(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = FileLock(_lock_path(path), timeout=10)
    tmp_path = f"{path}.tmp"
    with lock:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def parse_allowed_origins(raw: str) -> list[str]:
    out = []
    for part in str(raw or "").split(","):
        s = part.strip()
        if s:
            out.append(s)
    return out


def is_private_or_loopback(host: str) -> bool:
    h = str(host or "").strip().lower()
    if not h:
        return True
    if h in {"localhost"}:
        return True
    try:
        ip = ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        return False
