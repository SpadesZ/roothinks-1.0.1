# 檔案路徑: app/errors.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   Roothinks 中央錯誤分類模組，提供可被 API、背景任務、LLM dispatcher 與
#   Context Chain 共用的結構化錯誤碼。
# 主要責任:
#   1. 定義 ErrorSeverity 與 ErrorCode。
#   2. 定義 AppError，避免核心路徑只靠自由文字判斷錯誤。
# 維護提醒:
#   - 不要在 AppError.message 放入 secret、完整 prompt 或大量原始 OCR/LLM 內容。
# -----------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorSeverity(str, Enum):
    RECOVERABLE = "recoverable"
    USER_ACTION_REQUIRED = "user_action_required"
    FATAL = "fatal"


class ErrorCode(str, Enum):
    UNKNOWN = "UNKNOWN"
    INVALID_JSON = "INVALID_JSON"
    DB_SCHEMA_MISMATCH = "DB_SCHEMA_MISMATCH"
    LLM_NOT_BOUND = "LLM_NOT_BOUND"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    CONTEXT_CHAIN_INVALID = "CONTEXT_CHAIN_INVALID"
    CONTEXT_INJECTION_FAILED = "CONTEXT_INJECTION_FAILED"
    OCR_DEPENDENCY_MISSING = "OCR_DEPENDENCY_MISSING"
    OCR_TIMEOUT = "OCR_TIMEOUT"
    TRANSLATION_TIMEOUT = "TRANSLATION_TIMEOUT"
    SECRET_CONFIG_ERROR = "SECRET_CONFIG_ERROR"


@dataclass
class AppError(Exception):
    code: ErrorCode
    message: str
    severity: ErrorSeverity = ErrorSeverity.RECOVERABLE
    original_error: Optional[Exception] = None

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "severity": self.severity.value,
        }
