# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 evaluate ocr 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_evaluate_ocr.py -q
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
