# Roothinks source maintenance contract
# 檔案路徑: app/log_provenance.py
# 模組定位: 全站執行日誌的「專案出處」標記；每一行寫入 runtime.log 時帶一個結構化欄位 [pid=<專案>]。
# 主要責任: 讓「只給本專案看的日誌」依結構化欄位過濾，而不是在訊息文字裡找 pid 子字串
#           （子字串會漏掉不含 pid 的本專案行，也會收到同時提到別的專案的行）。
# 出處來源（只用伺服器自己設定的值，不用請求參數）:
#   1. 請求內：security.enforce_project_ownership 授權成功後設定的 g.rt_log_pid（被拒絕的請求不會標成目標專案，
#      所以外人不能把文字寫進別的專案的日誌視圖）。同一請求授權了兩個不同專案 → 之後的行一律 [pid=-]。
#   2. 背景工作：llm_usage 的 contextvar（Flow B worker 用 llm_usage.run_in_context 在全新的空 context 裡跑）。
#   3. 子程序：Flow B runner CLI 啟動時呼叫 adopt_process_pid()，採用父程序放在子程序 env dict 的 ROOTHINKS_LOG_PID。
#      web 行程從不採用這個環境變數：即使繼承了它，也不會把整個伺服器沒有出處的行標成某專案（R7 安全輪 b）。
#   只有在「所有來源都指向同一個專案」時才標成該專案；來源互相矛盾（混合）、global_scope() 內（共用元件）、
#   或都沒有 → [pid=-]（全站或未知，只給系統管理員看）。
# 多行紀錄（R7 安全輪 f）：讀取端只認「行首是日誌表頭」的行的第一個出處欄位；寫入端把訊息／traceback 裡
#   看起來像日誌表頭的續行加上前綴，讓內嵌換行無法偽造一整行別的專案的日誌。
# 驗證: python -m pytest test/unit/test_log_provenance.py test/unit/test_flowb_log_r7_provenance.py -q
import contextvars
import logging
import os
import re
from contextlib import contextmanager

NO_PROJECT = "-"
PID_ENV = "ROOTHINKS_LOG_PID"
TAG_FORMAT = "%(asctime)s [%(levelname)s] %(process)d/%(threadName)s %(name)s [pid=%(rt_pid)s]: %(message)s"
# 合法的出處值：正規化後的專案 id（security.ID_PATTERNS 的 project_id，小寫）或 "-"。其他值一律寫成 "-"。
_TAG_VALUE = re.compile(r"^[a-z0-9_-]{1,20}$")
# 一行日誌的表頭（asctime 預設格式、等級、行程/執行緒、logger 名稱）之後的第一個 [pid=…]: 才是出處欄位。
# 錨定在行首：traceback 或訊息中段出現的 "[pid=x]: " 字樣、沒有表頭的續行，都不算出處。
_LINE_TAG = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[[^\]\r\n]+\] \d+/.*? \[pid=([A-Za-z0-9_-]{1,20})\]: "
)
# 寫入端加在「看起來像日誌表頭」的續行前面；不以數字開頭，所以讀取端不會把它當成一整行。
CONTINUATION_PREFIX = "  | "

_GLOBAL_SCOPE = contextvars.ContextVar("rt_log_global_scope", default=False)
_PROCESS_PID = None


def canonical_pid(pid):
    """同一專案的兩個寫法（ABC 與 ABC-p）視為同一個；比較時不分大小寫。"""
    text = str(pid or "").strip().lower()
    return text[:-2] if text.endswith("-p") else text


def _tag_value(pid):
    """寫進日誌的出處值：必須是合法的專案 id，否則（含未驗證的請求字串）一律 "-"，不能拿來拼出假的欄位。"""
    value = canonical_pid(pid)
    return value if value and _TAG_VALUE.match(value) else NO_PROJECT


def adopt_process_pid(pid):
    """只給「整個行程只處理一個專案」的子程序呼叫（Flow B runner CLI）。傳 None 取消。

    web 行程不呼叫這個函式，所以 ROOTHINKS_LOG_PID 只在子程序生效：父程序只把它放進子程序的 env dict，
    就算 web 行程被誤設了這個環境變數，也不會把其他專案沒有出處的行（例如 CV 逐頁執行緒）標成該專案。
    """
    global _PROCESS_PID
    _PROCESS_PID = str(pid).strip() if pid else None


@contextmanager
def global_scope():
    """共用元件（模型單例的 lazy init、執行緒池調整等）的區段：不論誰觸發，這裡寫的行都是 [pid=-]。"""
    token = _GLOBAL_SCOPE.set(True)
    try:
        yield
    finally:
        _GLOBAL_SCOPE.reset(token)


def _current_pid():
    """所有出處來源都指向同一個專案時回傳它（正規化後）；混合、共用區段或沒有來源時回傳 None。"""
    if _GLOBAL_SCOPE.get():
        return None
    found = set()
    try:
        from flask import g, has_request_context
        if has_request_context():
            if getattr(g, "rt_log_mixed", False):
                return None
            pid = getattr(g, "rt_log_pid", None)
            if pid:
                found.add(canonical_pid(pid))
    except Exception:
        pass
    try:
        from app.llm_service.llm_usage import current_context
        pid = current_context().get("pid")
        if pid:
            found.add(canonical_pid(pid))
    except Exception:
        pass
    if _PROCESS_PID:
        found.add(canonical_pid(_PROCESS_PID))
    found.discard("")
    return found.pop() if len(found) == 1 else None


def _guard_continuations(text, first_is_continuation):
    """把會被讀取端當成一整行的續行（行首是日誌表頭）加上前綴；分行規則與讀取端相同（str.splitlines）。"""
    parts = str(text).splitlines(keepends=True)
    changed = False
    for index, part in enumerate(parts):
        if (index or first_is_continuation) and _LINE_TAG.match(part):
            parts[index] = CONTINUATION_PREFIX + part
            changed = True
    return "".join(parts), changed


def _guard_record(record):
    """訊息、traceback、stack 內嵌的換行不能偽造別的專案的日誌行。只在真的出現偽造表頭時才改寫紀錄。"""
    try:
        message, changed = _guard_continuations(record.getMessage(), False)
        if changed:
            record.msg, record.args = message, None
        exc_text = record.exc_text
        if not exc_text and record.exc_info:
            exc_text = logging.Formatter().formatException(record.exc_info)
        if exc_text:
            guarded, changed = _guard_continuations(exc_text, True)
            if changed:
                record.exc_text = guarded
        if record.stack_info:
            guarded, changed = _guard_continuations(record.stack_info, True)
            if changed:
                record.stack_info = guarded
    except Exception:
        pass


class ProjectContextFilter(logging.Filter):
    """在 handler 上加 record.rt_pid（只接受合法值）並擋掉內嵌的偽造表頭；不擋任何紀錄。"""

    def filter(self, record):
        preset = getattr(record, "rt_pid", None)
        record.rt_pid = _tag_value(preset if preset is not None else _current_pid())
        _guard_record(record)
        return True


def bind_request_pid(pid):
    """授權成功後呼叫：本請求之後的日誌都標成這個專案。

    同一請求授權了第二個不同的專案（例如一次查多個專案的狀態）→ 之後的行同時涉及兩個專案，標成 [pid=-]。
    """
    try:
        from flask import g, has_request_context
        if has_request_context() and pid:
            previous = getattr(g, "rt_log_pid", None)
            if previous and canonical_pid(previous) != canonical_pid(pid):
                g.rt_log_mixed = True
            g.rt_log_pid = str(pid)
    except Exception:
        pass


def line_project(line):
    """回傳這一行的結構化專案標記；沒有表頭的行（舊格式、續行、被切斷的行）回傳 None。"""
    match = _LINE_TAG.match(line or "")
    return match.group(1).lower() if match else None


def lines_for_project(rows, pid):
    """只留下結構化標記等於本專案的行。回傳 (行, 未標記而被排除的行數)。未標記的行一律不給（fail closed）。"""
    want = canonical_pid(pid)
    kept, untagged = [], 0
    for row in rows:
        tag = line_project(row)
        if tag is None:
            untagged += 1
        elif want and tag == want:
            kept.append(row)
    return kept, untagged


def read_tail_lines(path, lines=200, max_bytes=512000):
    """讀日誌尾端的完整行。

    從檔案中間開始讀時，第一段是被切斷的行尾；它可能剛好從訊息內嵌的偽造表頭開始，看起來像完整的一行，一律丟掉。
    """
    safe_lines = max(1, min(int(lines or 200), 1000))
    safe_bytes = max(4096, min(int(max_bytes or 512000), 2 * 1024 * 1024))
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size <= 0:
                return []
            start = max(0, size - safe_bytes)
            previous = b"\n"
            if start:
                fh.seek(start - 1)
                previous = fh.read(1)
            else:
                fh.seek(0)
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows = chunk.splitlines()
    if previous != b"\n" and rows:
        rows = rows[1:]
    return rows[-safe_lines:]
