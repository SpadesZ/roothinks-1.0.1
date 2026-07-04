# 檔案路徑: tests/test_evaluate_ocr.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: OCR evaluation CLI smoke tests; keep sample files tiny.

import json

from scripts.evaluate_ocr import evaluate_ocr


def test_evaluate_ocr_writes_summary(tmp_path):
    gt = tmp_path / "gt"
    pred = tmp_path / "pred"
    out = tmp_path / "summary.json"
    gt.mkdir()
    pred.mkdir()
    (gt / "sample_001_page_001.gt.txt").write_text("hello world", encoding="utf-8")
    (pred / "sample_001_page_001.pred.txt").write_text("hello world", encoding="utf-8")

    summary = evaluate_ocr(pred, gt, out)

    assert summary["average_cer"] == 0
    assert json.loads(out.read_text(encoding="utf-8"))["samples"][0]["sample_id"] == "sample_001_page_001"
