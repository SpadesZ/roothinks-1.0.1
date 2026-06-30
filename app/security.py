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


def enforce_project_ownership(pid: str) -> None:
    if not _auth_enabled():
        return
    token = getattr(g, "auth_token", "") or get_request_token()
    if not token:
        raise Unauthorized("Unauthorized")
    if not check_ownership(token, pid):
        raise Forbidden("Forbidden")


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
