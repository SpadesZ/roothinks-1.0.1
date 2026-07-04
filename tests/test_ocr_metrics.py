# 檔案路徑: tests/test_ocr_metrics.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: pure OCR metric tests; no image or OCR engine dependency.

from app.evaluation.ocr_metrics import box_iou, character_error_rate, normalize_text, word_error_rate


def test_normalize_text():
    assert normalize_text(" A\n B ") == "a b"


def test_cer_wer_identical_zero():
    assert character_error_rate("abc", "abc") == 0
    assert word_error_rate("hello world", "hello world") == 0


def test_cer_wer_different_positive():
    assert character_error_rate("abc", "xyz") > 0
    assert word_error_rate("hello", "world") > 0


def test_empty_ground_truth_handling():
    assert character_error_rate("", "") == 0
    assert character_error_rate("x", "") == 1


def test_box_iou_cases():
    assert box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1
    assert box_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0
    assert 0 < box_iou([0, 0, 10, 10], [5, 5, 15, 15]) < 1
