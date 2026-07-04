# 檔案路徑: tests/test_segmentizer_golden.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: segmentizer golden invariant tests; fixtures should stay deterministic.

import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "segmentizer"


def _assert_segment_invariants(records):
    assert isinstance(records, list)
    assert records
    for idx, row in enumerate(records, start=1):
        assert "text" in row
        assert "index" in row or "id" in row
        assert str(row["text"]).strip()


def test_simple_text_fixture_expected_invariants():
    records = json.loads((FIXTURE_DIR / "simple_paper_expected.json").read_text(encoding="utf-8"))
    _assert_segment_invariants(records)
    assert records[0]["heading"] == "Title"


def test_noisy_ocr_fixture_does_not_collapse():
    records = json.loads((FIXTURE_DIR / "noisy_ocr_expected.json").read_text(encoding="utf-8"))
    _assert_segment_invariants(records)
    assert len(records) >= 2
