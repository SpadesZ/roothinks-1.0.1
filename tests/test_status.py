# 檔案路徑: tests/test_status.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: status taxonomy tests; keep legacy aliases compatible.

from app.status import ProcessStatus, normalize_status


def test_legacy_idle_maps_to_pending():
    assert normalize_status("idle") == "pending"


def test_process_status_enum_values_are_stable():
    assert ProcessStatus.PENDING.value == "pending"
    assert ProcessStatus.COMPLETED.value == "completed"
    assert ProcessStatus.FAILED.value == "failed"
