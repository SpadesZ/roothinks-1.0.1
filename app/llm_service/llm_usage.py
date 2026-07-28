# 檔案路徑: app/llm_service/llm_usage.py
# 產生時間: 2026-07-28 +08:00
# 版本: v1.0
# 模組定位:
#   LLM token 用量的記錄與查詢。
# 背景:
#   文獻解析整條線都在打雲端 LLM（task_4cv 綁 gpt-4.1、task_5interpret 與
#   task_5b_reflow 綁 Gemini），但系統原本**完全沒有記錄 token 用量**——
#   adapter 拿得到 response.usage 卻直接丟掉。使用者無從得知跑一篇要多少錢，
#   也就無法在解析後決定要不要繼續翻譯。
# 主要責任:
#   1. 提供 usage_context()：讓呼叫端標記「接下來的 LLM 呼叫屬於哪個 pid/paper」。
#   2. record()：把一次呼叫的用量寫進 SQLite。
#   3. summarize_paper()：彙總單篇的用量與費用。
# 呼叫來源:
#   app/llm_service/llm_dispatcher.py（記錄）、
#   app/core_pro/literature/*（設定 context 與查詢）。
# 安全邊界:
#   - **記錄失敗絕不可影響主流程**。所有寫入都包在 try/except 裡：
#     使用者要的是解析結果，不是記帳；記帳壞掉不該讓解析整個失敗。
#   - 不記錄 prompt 或回應內容，只記 token 數與模型名稱——
#     內容可能含未發表的研究資料，落地會擴大外洩面。
# 維護提醒:
#   - pid/paper_id 走 contextvars。FlowA 預設 inline 執行（同一行程），
#     所以 context 傳得到；若日後改成 subprocess 執行，context 不會跨行程，
#     那時要改成把 pid/paper_id 顯式傳進 dispatch_task。
#   - 資料表與 llm_connections 同一個 DB（data/sys/llm_match.db），
#     因為它記的是「LLM 呼叫」而不是「專案資料」。

import contextvars
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.llm_service.llm_pricing import estimate_cost_usd

LOGGER = logging.getLogger("LLMUsage")

# 目前這一串 LLM 呼叫屬於誰。None 代表非文獻流程（例如 Study 對話），
# 照樣記錄但不掛在特定 paper 上。
_ctx_pid: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_usage_pid", default=None)
_ctx_paper: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_usage_paper", default=None)

_init_lock = threading.Lock()
_initialized = False


def get_db_path() -> str:
    """與 llm_connections 同一個 DB——記的是 LLM 呼叫，不是專案資料。"""
    from app.llm_service.llm_model import LLMModel
    return LLMModel.get_db_path()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT    NOT NULL,
            pid          TEXT,
            paper_id     TEXT,
            task_id      TEXT    NOT NULL,
            vendor       TEXT,
            model_name   TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd     REAL,
            price_note   TEXT,
            cache_hit    INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_usage_pid_paper "
                 "ON llm_usage_log (pid, paper_id)")


def init_storage() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        try:
            path = get_db_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            conn = sqlite3.connect(path)
            try:
                _ensure_table(conn)
                conn.commit()
            finally:
                conn.close()
            _initialized = True
        except Exception:
            LOGGER.exception("[usage] 建表失敗；用量記錄將停用，但不影響主流程")


@contextmanager
def usage_context(pid: Optional[str], paper_id: Optional[str] = None):
    """標記接下來的 LLM 呼叫屬於哪個專案/文獻。

    用法：
        with usage_context(pid, paper_id):
            ...跑解析...
    離開時自動還原，巢狀使用安全。
    """
    t1 = _ctx_pid.set(pid or None)
    t2 = _ctx_paper.set(paper_id or None)
    try:
        yield
    finally:
        _ctx_pid.reset(t1)
        _ctx_paper.reset(t2)


def set_context(pid: Optional[str], paper_id: Optional[str] = None) -> None:
    """設定歸屬，不自動還原。

    只用於「同一個 worker 內逐篇切換」的場合（下一篇會覆寫上一篇）。
    **worker 結束時務必呼叫 clear_context()**——這些 worker 跑在
    ThreadPoolExecutor 的池化執行緒上，執行緒會被重複使用，不清的話
    下一個工作的 LLM 呼叫會被算到上一篇的帳上。
    """
    _ctx_pid.set(pid or None)
    _ctx_paper.set(paper_id or None)


def clear_context() -> None:
    """清除歸屬。worker 收尾時必須呼叫（放在 finally）。"""
    _ctx_pid.set(None)
    _ctx_paper.set(None)


def current_context() -> Dict[str, Optional[str]]:
    return {"pid": _ctx_pid.get(), "paper_id": _ctx_paper.get()}


def normalize_usage(raw: Any) -> Dict[str, int]:
    """把各家 adapter 回傳的 usage 統一成 input/output/total。

    OpenAI    : prompt_tokens / completion_tokens / total_tokens
    Google    : prompt_token_count / candidates_token_count / total_token_count
    OpenRouter: 舊版只給 total（token_usage），輸入輸出無從拆分。
    """
    if not isinstance(raw, dict):
        return {"input": 0, "output": 0, "total": 0}

    def pick(*keys):
        for k in keys:
            v = raw.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                return int(v)
        return 0

    i = pick("input_tokens", "prompt_tokens", "prompt_token_count")
    o = pick("output_tokens", "completion_tokens", "candidates_token_count")
    t = pick("total_tokens", "total_token_count")
    if not t:
        t = i + o
    return {"input": i, "output": o, "total": t}


def record(
    task_id: str,
    vendor: str,
    model_name: str,
    usage: Any,
    cache_hit: bool = False,
) -> None:
    """記錄一次 LLM 呼叫的用量。任何失敗都只寫 log，不往外拋。"""
    try:
        norm = normalize_usage(usage)
        if norm["total"] <= 0 and not cache_hit:
            # 沒拿到用量就別寫一筆全 0 的假資料進去，那會讓總計失真。
            return

        cost, note = estimate_cost_usd(vendor, model_name, norm["input"], norm["output"])
        ctx = current_context()

        init_storage()
        if not _initialized:
            return
        conn = sqlite3.connect(get_db_path(), timeout=10)
        try:
            conn.execute(
                "INSERT INTO llm_usage_log (created_at, pid, paper_id, task_id, vendor,"
                " model_name, input_tokens, output_tokens, total_tokens, cost_usd,"
                " price_note, cache_hit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    ctx["pid"], ctx["paper_id"], str(task_id or ""),
                    str(vendor or ""), str(model_name or ""),
                    norm["input"], norm["output"], norm["total"],
                    cost, note, 1 if cache_hit else 0,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("[usage] 記錄失敗（已忽略，不影響主流程）")


def summarize_paper(pid: str, paper_id: str) -> Dict[str, Any]:
    """單篇的用量彙總。

    has_unpriced=True 代表其中有模型查不到單價，此時 cost_usd 只是「已知部分」
    的合計，UI 必須標明不完整——否則使用者會把它當成全部花費。
    """
    empty = {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cost_usd": 0.0, "calls": 0, "has_unpriced": False, "models": [],
    }
    try:
        init_storage()
        if not _initialized:
            return empty
        conn = sqlite3.connect(get_db_path(), timeout=10)
        try:
            rows = conn.execute(
                "SELECT input_tokens, output_tokens, total_tokens, cost_usd,"
                " vendor, model_name FROM llm_usage_log"
                " WHERE pid = ? AND paper_id = ?",
                (str(pid), str(paper_id)),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        LOGGER.exception("[usage] 查詢失敗")
        return empty

    if not rows:
        return empty

    out = dict(empty)
    models = set()
    for i, o, t, cost, vendor, model in rows:
        out["input_tokens"] += int(i or 0)
        out["output_tokens"] += int(o or 0)
        out["total_tokens"] += int(t or 0)
        out["calls"] += 1
        if cost is None:
            out["has_unpriced"] = True
        else:
            out["cost_usd"] += float(cost)
        if model:
            models.add(f"{vendor}/{model}" if vendor else str(model))
    out["cost_usd"] = round(out["cost_usd"], 6)
    out["models"] = sorted(models)
    return out
