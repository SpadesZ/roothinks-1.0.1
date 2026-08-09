# Roothinks source maintenance contract
# 上下游: main/create_app 啟動本層，再註冊 Blueprint、DB、Socket、runtime service 供 HTTP/worker 使用。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/utils/json_safe.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   JSON 安全解析工具，讓核心資料欄位解析失敗時可被明確辨識。
# 主要責任: 在不執行任意內容的前提下解析可能含 code fence/雜訊的 LLM JSON，失敗時回傳明確錯誤。
#   1. None / 空字串時回傳 default。
#   2. JSON 格式錯誤時 raise AppError(ErrorCode.INVALID_JSON)。
# 維護提醒:
#   - message 可包含 context，但不要包含大量原始內容或 secret。
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
from typing import Any

from app.errors import AppError, ErrorCode, ErrorSeverity


def safe_json_loads(raw: str | bytes | None, *, default: Any = None, context: str = "") -> Any:
    if raw is None:
        return default
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        label = f" for {context}" if context else ""
        raise AppError(
            ErrorCode.INVALID_JSON,
            f"Invalid JSON{label} at line {exc.lineno}, column {exc.colno}",
            ErrorSeverity.RECOVERABLE,
            exc,
        ) from exc
