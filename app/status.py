# 檔案路徑: app/status.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   Roothinks process / interpretation status 的集中定義。
# 主要責任:
#   1. 提供穩定 enum，避免新程式繼續散落自由字串。
#   2. 保留 legacy status mapping，讓舊前端與舊資料可逐步遷移。
# 維護提醒:
#   - 不要在本輪硬改所有呼叫點；先修 model default 與新邏輯。
# -----------------------------------------------------------------------------

from __future__ import annotations

from enum import Enum


class ProcessStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"


class InterpretationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"


LEGACY_STATUS_MAP = {
    "idle": ProcessStatus.PENDING.value,
    "done": ProcessStatus.COMPLETED.value,
    "error": ProcessStatus.FAILED.value,
}


def normalize_status(value: str | None, *, default: str = ProcessStatus.PENDING.value) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return LEGACY_STATUS_MAP.get(raw, raw)
