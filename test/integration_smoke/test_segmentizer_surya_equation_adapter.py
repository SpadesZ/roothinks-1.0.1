# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_segmentizer_surya_equation_adapter.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 segmentizer surya equation adapter 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_segmentizer_surya_equation_adapter.py -q
import json
from pathlib import Path

import cv2
import numpy as np


def _make_candidate(*, bbox, pred, confidence, equation, body, table=0.10, figure=0.10, table_grid=0.004):
    x1, y1, x2, y2 = bbox
    return {
        "bbox": [x1, y1, x2, y2],
        "w": x2 - x1,
        "h": y2 - y1,
        "area_ratio": ((x2 - x1) * (y2 - y1)) / float(320 * 280),
        "fg_ratio": 0.18,
        "small_cc_count": 22.0,
        "text_density": 1.25,
        "table_grid_score": table_grid,
        "line_h_ratio": 0.0012,
        "line_v_ratio": 0.0010,
        "grid_intersection_ratio": 0.00010,
        "column_id": "full",
        "type_scores": {
            "table": table,
            "figure": figure,
            "body": body,
            "equation": equation,
            "pred": pred,
            "confidence": confidence,
            "reasons": [],
        },
    }


def _make_image_and_bin():
    image = np.full((280, 320, 3), 255, dtype=np.uint8)
    cv2.putText(image, "x = y + z", (70, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, bin_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return image, bin_inv


def test_surya_equation_hint_disabled_by_default(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("VNS_SURYA_EQ_ENABLE", "0")
    monkeypatch.delenv("VNS_SURYA_EQ_HINT_FILE", raising=False)

    from app.core_pro.literature.literature_segmentizer import Segmentizer

    seg = Segmentizer()
    image, bin_inv = _make_image_and_bin()
    candidate = _make_candidate(
        bbox=[64, 98, 258, 176],
        pred="Body",
        confidence=0.59,
        equation=0.24,
        body=0.59,
    )

    seg._inject_surya_equation_hints(
        image=image,
        bin_inv=bin_inv,
        candidates=[candidate],
        page_profile={"is_two_column": False, "split_x": 160, "gutter_half_w": 8},
        pid="PTEST",
        paper_id="SURYA_OFF",
        page_num=1,
        page_w=320,
        page_h=280,
    )

    assert candidate["type_scores"]["equation"] == 0.24
    assert "surya_eq_hint_score" not in candidate


def test_surya_equation_hint_merges_and_locks_equation(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")

    hint_path = tmp_path / "surya_hints.json"
    hint_payload = {
        "pages": [
            {
                "page": 1,
                "hints": [
                    {
                        "label": "Equation",
                        "score": 0.93,
                        "bbox": [0.20, 0.34, 0.79, 0.63],
                    }
                ],
            }
        ]
    }
    hint_path.write_text(json.dumps(hint_payload), encoding="utf-8")

    monkeypatch.setenv("VNS_SURYA_EQ_ENABLE", "1")
    monkeypatch.setenv("VNS_SURYA_EQ_HINT_FILE", str(hint_path))
    monkeypatch.setenv("VNS_SURYA_EQ_CONF_THRESHOLD", "0.60")

    from app.core_pro.literature.literature_segmentizer import Segmentizer

    seg = Segmentizer()
    image, bin_inv = _make_image_and_bin()
    candidate = _make_candidate(
        bbox=[64, 98, 258, 166],
        pred="Body",
        confidence=0.60,
        equation=0.27,
        body=0.60,
    )

    candidates = [candidate]
    seg._inject_surya_equation_hints(
        image=image,
        bin_inv=bin_inv,
        candidates=candidates,
        page_profile={"is_two_column": False, "split_x": 160, "gutter_half_w": 8},
        pid="PTEST",
        paper_id="SURYA_ON",
        page_num=1,
        page_w=320,
        page_h=280,
    )

    assert candidate["type_scores"]["equation"] >= 0.90
    assert "SURYA_EQ_HINT_MERGED" in candidate["type_scores"].get("reasons", [])

    phase1 = seg._extract_equation_placeholders(
        image=image,
        candidates=candidates,
        page_w=320,
        page_h=280,
    )
    assert 0 in set(phase1.get("locked_idxs", set()))


def test_surya_equation_hint_does_not_override_strong_figure(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")

    hint_path = tmp_path / "surya_hints_blocked.json"
    hint_payload = {
        "pages": [
            {
                "page": 1,
                "hints": [
                    {
                        "label": "Equation",
                        "score": 0.95,
                        "bbox": [0.17, 0.30, 0.82, 0.70],
                    }
                ],
            }
        ]
    }
    hint_path.write_text(json.dumps(hint_payload), encoding="utf-8")

    monkeypatch.setenv("VNS_SURYA_EQ_ENABLE", "1")
    monkeypatch.setenv("VNS_SURYA_EQ_HINT_FILE", str(hint_path))

    from app.core_pro.literature.literature_segmentizer import Segmentizer

    seg = Segmentizer()
    image, bin_inv = _make_image_and_bin()
    candidate = _make_candidate(
        bbox=[60, 84, 264, 196],
        pred="Figure",
        confidence=0.90,
        equation=0.22,
        body=0.32,
        table=0.16,
        figure=0.90,
        table_grid=0.003,
    )
    candidate["embedded_caption_target"] = "Figure"

    seg._inject_surya_equation_hints(
        image=image,
        bin_inv=bin_inv,
        candidates=[candidate],
        page_profile={"is_two_column": False, "split_x": 160, "gutter_half_w": 8},
        pid="PTEST",
        paper_id="SURYA_BLOCK",
        page_num=1,
        page_w=320,
        page_h=280,
    )

    assert candidate["type_scores"]["pred"] == "Figure"
    assert candidate["type_scores"]["equation"] == 0.22
    assert "SURYA_EQ_HINT_MERGED" not in candidate["type_scores"].get("reasons", [])


def test_phase1_equation_skips_major_locked_candidates(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("VNS_SURYA_EQ_ENABLE", "0")

    from app.core_pro.literature.literature_segmentizer import Segmentizer

    seg = Segmentizer()
    image, _bin_inv = _make_image_and_bin()
    candidate = _make_candidate(
        bbox=[64, 98, 258, 166],
        pred="Equation",
        confidence=0.72,
        equation=0.74,
        body=0.44,
    )

    phase1_unblocked = seg._extract_equation_placeholders(
        image=image,
        candidates=[candidate],
        page_w=320,
        page_h=280,
    )
    assert 0 in set(phase1_unblocked.get("locked_idxs", set()))

    phase1_blocked = seg._extract_equation_placeholders(
        image=image,
        candidates=[candidate],
        page_w=320,
        page_h=280,
        blocked_idxs={0},
    )
    assert 0 not in set(phase1_blocked.get("locked_idxs", set()))
