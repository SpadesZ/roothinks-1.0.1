# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_segmentizer_llm_dispatch_writeback.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 segmentizer llm dispatch writeback 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_segmentizer_llm_dispatch_writeback.py -q
import numpy as np
import cv2
from pathlib import Path


def test_segmentizer_llm_dispatch_hit_and_writeback(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")

    import app.llm_service.llm_dispatcher as llm_dispatcher
    from app.core_pro.literature.literature_segmentizer import Segmentizer

    def _fake_dispatch(task_id, prompt, priority=0, images=None, max_retries=0):
        assert task_id == "task_4cv"
        return {
            "ok": True,
            "text": (
                '{"label":"Table","confidence":0.91,'
                '"reasons":["unit_test_dispatch"],"caption_target":"Table"}'
            ),
            "msg": "",
        }

    monkeypatch.setattr(llm_dispatcher, "dispatch_task", _fake_dispatch, raising=True)

    seg = Segmentizer()
    seg.llm_enabled = True
    seg.llm_conf_threshold = 0.85
    seg.llm_accept_threshold = 0.60
    seg.llm_max_candidates_per_page = 5

    image = np.full((280, 320, 3), 255, dtype=np.uint8)
    cv2.putText(image, "Table 1. Unit test", (22, 135), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

    candidate = {
        "bbox": [20, 90, 300, 180],
        "w": 280,
        "h": 90,
        "area_ratio": (280 * 90) / float(320 * 280),
        "fg_ratio": 0.18,
        "small_cc_count": 26.0,
        "text_density": 1.1,
        "table_grid_score": 0.006,
        "line_h_ratio": 0.0015,
        "line_v_ratio": 0.0013,
        "grid_intersection_ratio": 0.00012,
        "column_id": "full",
        "type_scores": {
            "table": 0.48,
            "figure": 0.31,
            "body": 0.56,
            "equation": 0.21,
            "pred": "Body",
            "confidence": 0.56,
            "reasons": [],
        },
    }

    out_dirs = {
        "pages": str(tmp_path / "pages"),
        "base": str(tmp_path),
        "audit": str(tmp_path / "audit"),
        "body": str(tmp_path / "body"),
        "fig": str(tmp_path / "fig"),
        "table": str(tmp_path / "table"),
    }
    for p in out_dirs.values():
        import os
        os.makedirs(p, exist_ok=True)

    seg._refine_uncertain_candidates_with_llm(
        image=image,
        candidates=[candidate],
        page_profile={"is_two_column": False, "split_x": 160, "gutter_half_w": 8},
        out_dirs=out_dirs,
        pid="TESTPID",
        paper_id="TESTPAPER",
        paper_tag="TESTPAPER",
        page_num=1,
        page_w=320,
        page_h=280,
        blocked_idxs=set(),
    )

    assert candidate.get("llm_dispatch_hit") is True
    assert candidate.get("llm_writeback_applied") is True
    assert candidate.get("llm_label") == "Table"
    assert candidate.get("type_scores", {}).get("pred") == "Table"
