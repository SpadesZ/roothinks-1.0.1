# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 metadata service 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_metadata_service.py -q
# 檔案路徑: tests/test_metadata_service.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: metadata normalization tests; keep export semantics stable.

from app.services.metadata_service import normalize_paper_metadata


def test_normalize_paper_metadata():
    meta = normalize_paper_metadata({"title": "A Study", "authors": "Alice Chen and Bob Lin", "publish_date": "2024-01-01"})
    assert meta["title"] == "A Study"
    assert meta["authors"] == ["Alice Chen", "Bob Lin"]
    assert meta["year"] == "2024"
