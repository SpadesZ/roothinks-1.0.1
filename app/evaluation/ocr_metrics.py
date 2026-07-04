# 檔案路徑: app/evaluation/ocr_metrics.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   OCR evaluation metrics，支援 CER/WER 與 layout IoU。
# 主要責任:
#   1. 不依賴外部重型套件。
#   2. 對空字串與異常 box 輸入保持安全。
# 維護提醒:
#   - 這是評測 utility，不應被 pipeline 反向依賴成啟動必要條件。
# -----------------------------------------------------------------------------

from __future__ import annotations

import re
from collections.abc import Sequence


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def levenshtein_distance(a: Sequence | str, b: Sequence | str) -> int:
    left = list(a) if not isinstance(a, str) else list(a)
    right = list(b) if not isinstance(b, str) else list(b)
    if not left:
        return len(right)
    if not right:
        return len(left)
    prev = list(range(len(right) + 1))
    for i, ca in enumerate(left, start=1):
        cur = [i]
        for j, cb in enumerate(right, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def character_error_rate(prediction: str, ground_truth: str) -> float:
    pred = normalize_text(prediction)
    gt = normalize_text(ground_truth)
    if not gt:
        return 0.0 if not pred else 1.0
    return levenshtein_distance(pred, gt) / max(1, len(gt))


def word_error_rate(prediction: str, ground_truth: str) -> float:
    pred_words = normalize_text(prediction).split()
    gt_words = normalize_text(ground_truth).split()
    if not gt_words:
        return 0.0 if not pred_words else 1.0
    return levenshtein_distance(pred_words, gt_words) / max(1, len(gt_words))


def box_iou(box_a: list[float], box_b: list[float]) -> float:
    if len(box_a or []) != 4 or len(box_b or []) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [float(x) for x in box_a]
    bx1, by1, bx2, by2 = [float(x) for x in box_b]
    if ax2 < ax1:
        ax1, ax2 = ax2, ax1
    if ay2 < ay1:
        ay1, ay2 = ay2, ay1
    if bx2 < bx1:
        bx1, bx2 = bx2, bx1
    if by2 < by1:
        by1, by2 = by2, by1
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union
