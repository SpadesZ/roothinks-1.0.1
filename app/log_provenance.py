# Roothinks source maintenance contract
# 檔案路徑: app/log_provenance.py
# 模組定位: 全站執行日誌的「專案出處」標記；每一行寫入 runtime.log 時帶一個結構化欄位 [pid=<專案>]。
# 主要責任: 讓「只給本專案看的日誌」依結構化欄位過濾，而不是在訊息文字裡找 pid 子字串
#           （子字串會漏掉不含 pid 的本專案行，也會收到同時提到別的專案的行）。
# 出處來源（依序，只用伺服器自己設定的值，不用請求參數）:
#   1. 請求內：security.enforce_project_ownership 授權成功後設定的 g.rt_log_pid（被拒絕的請求不會標成目標專案，
#      所以外人不能把文字寫進別的專案的日誌視圖）。
#   2. 背景工作：llm_usage 的 contextvar（Flow B／批次 worker 已在開工時設定，跨執行緒用 propagate）。
#   3. 子程序：環境變數 ROOTHINKS_LOG_PID（Flow B 子程序由父程序設定）。
#   都沒有 → [pid=-]（全站或未知，不給任何專案）。
# 驗證: python -m pytest test/unit/test_log_provenance.py -q
import logging
import os
import re

NO_PROJECT = "-"
TAG_FORMAT = "%(asctime)s [%(levelname)s] %(process)d/%(threadName)s %(name)s [pid=%(rt_pid)s]: %(message)s"
_TAG = re.compile(r"\[pid=([A-Za-z0-9_-]{1,20})\]: ")


def canonical_pid(pid):
    """同一專案的兩個寫法（ABC 與 ABC-p）視為同一個；比較時不分大小寫。"""
    text = str(pid or "").strip().lower()
    return text[:-2] if text.endswith("-p") else text


def _current_pid():
    try:
        from flask import g, has_request_context
        if has_request_context():
            pid = getattr(g, "rt_log_pid", None)
            if pid:
                return pid
    except Exception:
        pass
    try:
        from app.llm_service.llm_usage import current_context
        pid = current_context().get("pid")
        if pid:
            return pid
    except Exception:
        pass
    return os.environ.get("ROOTHINKS_LOG_PID") or None


class ProjectContextFilter(logging.Filter):
    """在 handler 上加 record.rt_pid；不擋任何紀錄。"""

    def filter(self, record):
        if not hasattr(record, "rt_pid"):
            pid = _current_pid()
            record.rt_pid = canonical_pid(pid) or NO_PROJECT
        return True


def bind_request_pid(pid):
    """授權成功後呼叫：本請求之後的日誌都標成這個專案。"""
    try:
        from flask import g, has_request_context
        if has_request_context() and pid:
            g.rt_log_pid = str(pid)
    except Exception:
        pass


def line_project(line):
    """回傳這一行的結構化專案標記；舊格式（沒有標記）回傳 None。"""
    match = _TAG.search(line or "")
    return match.group(1).lower() if match else None


def lines_for_project(rows, pid):
    """只留下結構化標記等於本專案的行。回傳 (行, 未標記而被排除的行數)。未標記的舊行一律不給（fail closed）。"""
    want = canonical_pid(pid)
    kept, untagged = [], 0
    for row in rows:
        tag = line_project(row)
        if tag is None:
            untagged += 1
        elif want and tag == want:
            kept.append(row)
    return kept, untagged
