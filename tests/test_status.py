# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 status 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_status.py -q
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
