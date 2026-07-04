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
