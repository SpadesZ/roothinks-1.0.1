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


def _strip_extended_prefix(path: str) -> str:
    r"""
    去掉 Windows 的 \\?\ 擴充長度前綴，讓兩個路徑能放在同一個形式下比較。

    os.path.realpath 在目標路徑正被另一個執行緒建立時，偶爾會回傳
    \\?\C:\... 形式；與另一次呼叫回傳的 C:\... 混用時，os.path.commonpath
    會判定「不同磁碟」而誤報成路徑穿越 —— 多人同時存檔會隨機噴 400
    「Path traversal detected」。

    \\?\C:\foo 與 C:\foo 指的是同一個檔案，因此把前綴正規化掉不會放寬檢查，
    只是讓包含判斷得以正確進行。
    """
    text = str(path or "")
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\"):]
    return text


def safe_join_under(base_dir: str, *parts: str) -> str:
    base_text = str(base_dir or "")
    if "\x00" in base_text:
        raise BadRequest("Invalid path")
    for p in parts:
        if "\x00" in str(p or ""):
            raise BadRequest("Invalid path")

    base_real = os.path.realpath(base_text)
    target = os.path.realpath(os.path.join(base_real, *parts))

    # 兩側都先正規化掉 \\?\ 前綴再比較，否則併發下會出現一側有前綴、
    # 一側沒有的情況而誤判。
    base_cmp = _strip_extended_prefix(base_real)
    target_cmp = _strip_extended_prefix(target)
    try:
        if os.path.commonpath([base_cmp, target_cmp]) != base_cmp:
            raise BadRequest("Path traversal detected")
    except ValueError:
        # 真正的跨磁碟路徑（C:\ vs D:\）也代表越界使用。
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
    回傳 'owner' | 'editor' | 'coauthor' | 'viewer'，若無 membership 回傳 None。

    會同時比對 base 與 formal 兩種 pid 形式（ABC123 與 ABC123-p）：
    同一個專案在草稿期與正式期用不同 project_id，成員可能只被加在其中一種
    形式上。若只精確比對，「用 base pid 加進來的成員」在任何以 formal pid
    為索引的判定（章節權限、socket 存檔）都會被判成沒有權限。
    兩者視為同一專案與 check_ownership 的 token ACL 行為一致。

    有多筆時取權限最高的那一筆。使用延遲匯入避免循環依賴。
    """
    try:
        from app.models import ROLE_ORDER, WorkspaceMember

        aliases = _normalize_pid_aliases(pid)
        if not aliases:
            return None
        members = WorkspaceMember.query.filter(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.pid.in_(aliases),
        ).all()
        if not members:
            return None
        return max(members, key=lambda m: ROLE_ORDER.get(m.role or "", 0)).role
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


def has_section_assignment(user_id: int, pid: str, section_key: str) -> bool:
    """
    該 user 是否被指派撰寫 pid 專案的 section_key 章節。

    與 get_workspace_role 同規：base 與 formal 兩種 pid 形式都要比對，
    否則以 base pid 建立的指派會在 formal pid 的判定下失效。
    """
    try:
        from app.models import ChapterAssignment

        aliases = _normalize_pid_aliases(pid)
        if not aliases:
            return False
        row = ChapterAssignment.query.filter(
            ChapterAssignment.user_id == user_id,
            ChapterAssignment.pid.in_(aliases),
            ChapterAssignment.section_key == section_key,
        ).first()
        return row is not None
    except Exception:
        LOGGER.exception(
            "has_section_assignment failed user_id=%s pid=%s section=%s",
            user_id, pid, section_key,
        )
        return False


def coauthor_open_access(pid: str) -> bool:
    """
    該專案是否已由 owner 開放「限定編輯檢視全文與留言」。

    預設 False（未開放）。查無設定列時視為未開放，這是刻意的保守預設 ——
    新專案不會意外把全文攤開給只負責單章的人。
    """
    try:
        from app.models import ProjectCollabSetting

        aliases = _normalize_pid_aliases(pid)
        if not aliases:
            return False
        row = ProjectCollabSetting.query.filter(
            ProjectCollabSetting.pid.in_(aliases)
        ).first()
        return bool(row and row.coauthor_open_access)
    except Exception:
        LOGGER.exception("coauthor_open_access failed for pid=%s", pid)
        return False


def is_section_scoped_role(role: Optional[str], pid: Optional[str] = None) -> bool:
    """
    該角色是否為「章節限定」——只看得到也只寫得到被指派的章節。

    目前只有 coauthor（限定編輯）屬於此類。把判斷收斂成一個函式，
    是為了避免各處散落 `role == 'coauthor'` 的字串比較。

    傳入 pid 時會一併檢查 owner 的開放開關：一旦開放，限定編輯在**讀取**上
    就不再受限（寫入仍只限被指派章節，那條規則不受開關影響）。
    """
    from app.models import ROLE_COAUTHOR

    if role != ROLE_COAUTHOR:
        return False
    if pid is not None and coauthor_open_access(pid):
        return False
    return True


def can_read_section(user_id: int, pid: str, section_key: str) -> bool:
    """
    章節層「讀取」判定。

    這條軸刻意與 ROLE_ORDER 的階梯分開：限定編輯（coauthor）的讀取範圍
    比 viewer **還窄** —— viewer 看得到全部章節，coauthor 只看得到被指派的。
    因此不能用 `level >= X` 這種線性比較來判斷讀取權，會得到相反的結果。

    owner 開啟 coauthor_open_access 後，限定編輯的讀取範圍才擴大到全部章節。
    非成員一律 False；viewer / editor / owner 可讀全部。
    """
    role = get_workspace_role(user_id, pid)
    if role is None:
        return False
    if is_section_scoped_role(role, pid):
        return has_section_assignment(user_id, pid, section_key)
    return True


def can_write_section(user_id: int, pid: str, section_key: str) -> bool:
    """
    章節層「寫入」判定。

    viewer   不能寫任何章節
    coauthor 只能寫被指派的章節（讀取範圍亦同，見 can_read_section）
    editor   以上可寫任何章節（總編輯，涵蓋 viewer 的全部讀取能力）
    """
    from app.models import ROLE_ORDER, ROLE_COAUTHOR, ROLE_EDITOR

    role = get_workspace_role(user_id, pid)
    if role is None:
        return False
    # 刻意不傳 pid：開放開關只放寬「讀取」，寫入權永遠只限被指派的章節。
    if is_section_scoped_role(role):
        return has_section_assignment(user_id, pid, section_key)

    level = ROLE_ORDER.get(role, 0)
    if level < ROLE_ORDER[ROLE_COAUTHOR]:
        return False          # viewer
    return level >= ROLE_ORDER[ROLE_EDITOR]


def visible_sections(user_id: int, pid: str, all_section_keys: list) -> list:
    """
    過濾出該使用者看得到的章節清單。

    限定編輯只會拿到自己被指派的那幾章；其餘角色拿到全部。
    章節下拉、bootstrap、manifest 等所有「列出章節」的地方都應該經過這裡，
    否則限定編輯仍會在 UI 上看到不該看到的章節標題。
    """
    role = get_workspace_role(user_id, pid)
    if role is None:
        return []
    if not is_section_scoped_role(role, pid):
        return list(all_section_keys)
    return [k for k in all_section_keys if has_section_assignment(user_id, pid, k)]


def can_comment_on_section(user_id: int, pid: str, section_key: str) -> bool:
    """
    章節留言門檻：看得到該章節就能留言。

    語意跟著讀取範圍走 —— 限定編輯看不到別章，自然也不能對別章留言。
    """
    return can_read_section(user_id, pid, section_key)


def can_comment(user_id: int, pid: str) -> bool:
    """是否為專案成員（可留言的最低門檻，不分章節）。"""
    return get_workspace_role(user_id, pid) is not None


def can_assign_sections(user_id: int, pid: str) -> bool:
    """指派章節的門檻：owner 與 editor 皆可。"""
    from app.models import ROLE_ORDER, ROLE_EDITOR

    role = get_workspace_role(user_id, pid)
    return ROLE_ORDER.get(role or "", 0) >= ROLE_ORDER[ROLE_EDITOR]


# =============================================================================
# 模組層存取控制
#
# 為什麼要獨立這一層：模組／專案層的守衛 enforce_project_ownership 對 GET
# 一律用 min_role="viewer"，再交給 require_workspace_role 做 ROLE_ORDER 線性
# 比較。而 coauthor(2) >= viewer(1) 會通過——於是「限定編輯」讀得到
# Literature / PAQ / Study 的全部資料。
#
# models.ROLE_ORDER 的註解早就寫明「coauthor 的讀取範圍比 viewer 還窄，
# 線性比較會得到相反結果」，章節層也照辦用了 can_read_section 繞開，
# 但模組層一直沒有對應的判斷，就這樣漏了。
#
# 這裡刻意用白名單（只列允許的）而不是黑名單：日後新增模組時，
# 預設是「限定編輯看不到」，忘記更新的後果是少看到東西，而不是外洩。
# =============================================================================

#: 限定編輯（coauthor）唯一進得去的模組。
COAUTHOR_ALLOWED_MODULES = frozenset({"manuscript"})

#: 路徑前綴 → 模組代號。順序有意義：長的前綴要排在前面。
_MODULE_PATH_PREFIXES = (
    ("/api/literature", "literature"),
    ("/api/study", "study"),
    ("/api/paq", "paq"),
    ("/api/submit", "submit"),
    ("/api/manuscript", "manuscript"),
    ("/literature", "literature"),
    ("/study", "study"),
    ("/manuscript", "manuscript"),
    ("/submit", "submit"),
    ("/paq", "paq"),
    ("/setup", "setup"),
    ("/lava_setup", "setup"),
)


def module_of_path(path: Optional[str]) -> Optional[str]:
    """由請求路徑判斷屬於哪個模組；判斷不出來回 None（不擋）。"""
    p = str(path or "").rstrip("/").lower()
    if not p:
        return None
    for prefix, module in _MODULE_PATH_PREFIXES:
        if p == prefix or p.startswith(prefix + "/") or p.startswith(prefix + "."):
            return module
    return None


def can_access_module(user_id: int, pid: Optional[str], module: Optional[str]) -> bool:
    """該使用者能不能進這個模組。

    **刻意不看 coauthor_open_access**：那個開關的語意是「放寬限定編輯能讀到
    哪些章節」，與「能進哪些模組」是兩件事。一個開關同時控制兩件事，
    owner 會誤判自己開放了什麼。
    """
    if not module:
        return True
    role = get_workspace_role(user_id, pid) if pid else None

    if pid:
        if role is None:
            return False          # 非成員：本來就不該進
        # 這裡傳 pid=None：模組權限不受開放開關影響（理由見 docstring）。
        if is_section_scoped_role(role):
            return module in COAUTHOR_ALLOWED_MODULES
        return True

    # 沒帶 pid 的裸頁面（例如 /literature 不帶 ?pid）。此時無從判斷專案，
    # 改看這個人在**任何**專案是否有非限定編輯的身分：若他所有的成員資格
    # 都是限定編輯，那他在任何專案都不該進這些模組。
    if module in COAUTHOR_ALLOWED_MODULES:
        return True
    return _has_any_non_section_scoped_membership(user_id)


def _has_any_non_section_scoped_membership(user_id: int) -> bool:
    """該使用者是否至少在一個專案「不是」限定編輯。

    完全沒有任何成員資格的帳號回 True（放行）。「沒有專案」不等於
    「是限定編輯」——那只是還沒被加入任何專案的新帳號，頁面本來就是空的。
    把新帳號一律擋在模組外，等於註冊完什麼都不能開。
    只有「有成員資格、而且每一筆都是限定編輯」才該擋。
    """
    try:
        from app.models import ROLE_COAUTHOR, WorkspaceMember

        roles = [
            m.role
            for m in WorkspaceMember.query.filter(
                WorkspaceMember.user_id == user_id
            ).all()
        ]
        if not roles:
            return True
        return any(r != ROLE_COAUTHOR for r in roles)
    except Exception:
        LOGGER.exception("_has_any_non_section_scoped_membership failed user_id=%s", user_id)
        # 查不出來時保守放行：這個分支只在「沒帶 pid」時走到，
        # 真正的資料存取仍會帶 pid 再被擋一次。
        return True


def request_pid() -> Optional[str]:
    """從當前請求盡力取出 pid（query / path 參數 / JSON body）。"""
    try:
        pid = request.args.get("pid") or ""
        if not pid and request.view_args:
            for key in ("pid", "project_id"):
                if request.view_args.get(key):
                    pid = str(request.view_args[key])
                    break
        if not pid and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            body = request.get_json(silent=True) or {}
            if isinstance(body, dict):
                pid = str(body.get("pid") or "")
        return pid.strip() or None
    except Exception:
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
