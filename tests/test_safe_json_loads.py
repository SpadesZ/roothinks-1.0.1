# 檔案路徑: tests/test_safe_json_loads.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: safe JSON parser tests; malformed inputs should stay non-fatal by default.

from app.errors import AppError, ErrorCode
from app.utils.json_safe import safe_json_loads


def test_valid_json_returns_dict():
    assert safe_json_loads('{"a": 1}', default={}) == {"a": 1}


def test_none_returns_default():
    assert safe_json_loads(None, default={"ok": True}) == {"ok": True}


def test_invalid_json_raises_app_error():
    try:
        safe_json_loads("{bad", default={}, context="unit")
    except AppError as exc:
        assert exc.code == ErrorCode.INVALID_JSON
    else:
        raise AssertionError("expected AppError")
