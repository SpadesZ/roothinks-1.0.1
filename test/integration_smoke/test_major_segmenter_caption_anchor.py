# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_major_segmenter_caption_anchor.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 major segmenter caption anchor 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_major_segmenter_caption_anchor.py -q
from pathlib import Path


def _candidate(*, bbox, table, figure, body, pred, confidence, text_density, table_grid, line_h, line_v, small_cc):
    x1, y1, x2, y2 = bbox
    return {
        "bbox": [x1, y1, x2, y2],
        "w": x2 - x1,
        "h": y2 - y1,
        "area_ratio": ((x2 - x1) * (y2 - y1)) / float(1600 * 2200),
        "text_density": text_density,
        "small_cc_count": small_cc,
        "table_grid_score": table_grid,
        "line_h_ratio": line_h,
        "line_v_ratio": line_v,
        "grid_intersection_ratio": 0.00022,
        "column_id": "full",
        "type_scores": {
            "table": table,
            "figure": figure,
            "body": body,
            "equation": 0.10,
            "pred": pred,
            "confidence": confidence,
            "reasons": [],
        },
    }


def test_major_segmenter_promotes_table_with_caption_anchor(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    from app.core_pro.literature.literature_major_segmenter import MajorSegmenter

    # Table body: moderate table score + structural evidence, but starts as Body.
    table_body = _candidate(
        bbox=[860, 360, 1480, 570],
        table=0.46,
        figure=0.08,
        body=0.61,
        pred="Body",
        confidence=0.61,
        text_density=2.55,
        table_grid=0.024,
        line_h=0.0034,
        line_v=0.0032,
        small_cc=88,
    )

    # Caption line: has embedded caption target and should be used as caption anchor only.
    caption_line = _candidate(
        bbox=[850, 220, 1510, 335],
        table=0.78,
        figure=0.10,
        body=0.84,
        pred="Table",
        confidence=0.78,
        text_density=2.10,
        table_grid=0.018,
        line_h=0.0029,
        line_v=0.0027,
        small_cc=72,
    )
    caption_line["embedded_caption_target"] = "Table"
    caption_line["embedded_caption_source"] = "ocr"

    seg = MajorSegmenter()
    out = seg.extract(
        image=None,
        candidates=[table_body, caption_line],
        page_profile={"is_two_column": False, "split_x": 800, "gutter_half_w": 20},
        page_w=1600,
        page_h=2200,
        blocked_idxs=set(),
        score_caption_pair=lambda *_args, **_kwargs: 0.74,
        validate_caption_pair=lambda *_args, **_kwargs: {"ok": True},
        infer_column_id=lambda _bbox, _prof, _w: "full",
        infer_caption_target_from_region=lambda _img, _bbox: "None",
        llm_accept_threshold=0.60,
    )

    cooked = list(out.get("cooked_major", []))
    assert len(cooked) == 1
    assert cooked[0]["type"] == "Table"
    assert cooked[0]["has_caption"] is True
    assert cooked[0]["pair_id"].startswith("table.")
    assert 0 in set(out.get("accepted_major_idxs", set()))
    assert 1 in set(out.get("used_caption_idxs", set()))


def test_major_segmenter_accepts_embedded_figure_with_head_ocr(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    from app.core_pro.literature.literature_major_segmenter import MajorSegmenter

    # Text-heavy merged region containing figure + caption text in one block.
    # This intentionally fails strict figure-structural check but should pass
    # when OCR head strongly indicates "Figure N".
    figure_block = _candidate(
        bbox=[150, 640, 1510, 900],
        table=0.12,
        figure=0.78,
        body=0.92,
        pred="Figure",
        confidence=0.78,
        text_density=2.40,
        table_grid=0.014,
        line_h=0.0032,
        line_v=0.0031,
        small_cc=96,
    )
    figure_block["embedded_caption_target"] = "Figure"
    figure_block["embedded_caption_source"] = "ocr"

    seg = MajorSegmenter()
    out = seg.extract(
        image=None,
        candidates=[figure_block],
        page_profile={"is_two_column": False, "split_x": 800, "gutter_half_w": 20},
        page_w=1600,
        page_h=2200,
        blocked_idxs=set(),
        score_caption_pair=lambda *_args, **_kwargs: 0.0,
        validate_caption_pair=lambda *_args, **_kwargs: {"ok": False},
        infer_column_id=lambda _bbox, _prof, _w: "full",
        infer_caption_target_from_region=lambda _img, _bbox: "Figure",
        llm_accept_threshold=0.60,
    )

    cooked = list(out.get("cooked_major", []))
    assert len(cooked) == 1
    assert cooked[0]["type"] == "Figure"
    assert cooked[0]["has_caption"] is True
    assert cooked[0]["pair_id"].startswith("fig.")
    assert cooked[0]["caption_bbox"] == [150, 640, 1510, 900]


def test_major_segmenter_expands_embedded_figure_with_neighbor_visual(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    from app.core_pro.literature.literature_major_segmenter import MajorSegmenter

    # Visual block above: should be merged into final figure bbox.
    visual_block = _candidate(
        bbox=[230, 220, 1420, 600],
        table=0.18,
        figure=0.40,
        body=0.62,
        pred="Body",
        confidence=0.62,
        text_density=0.95,
        table_grid=0.006,
        line_h=0.0012,
        line_v=0.0010,
        small_cc=30,
    )

    # Caption block below starts with "Figure N" and is accepted by embedded path.
    caption_block = _candidate(
        bbox=[150, 640, 1510, 900],
        table=0.12,
        figure=0.78,
        body=0.92,
        pred="Figure",
        confidence=0.78,
        text_density=2.40,
        table_grid=0.014,
        line_h=0.0032,
        line_v=0.0031,
        small_cc=96,
    )
    caption_block["embedded_caption_target"] = "Figure"
    caption_block["embedded_caption_source"] = "ocr"

    seg = MajorSegmenter()
    out = seg.extract(
        image=None,
        candidates=[visual_block, caption_block],
        page_profile={"is_two_column": False, "split_x": 800, "gutter_half_w": 20},
        page_w=1600,
        page_h=2200,
        blocked_idxs=set(),
        score_caption_pair=lambda *_args, **_kwargs: 0.0,
        validate_caption_pair=lambda *_args, **_kwargs: {"ok": False},
        infer_column_id=lambda _bbox, _prof, _w: "full",
        infer_caption_target_from_region=lambda _img, _bbox: "Figure",
        llm_accept_threshold=0.60,
    )

    cooked = list(out.get("cooked_major", []))
    assert len(cooked) == 1
    assert cooked[0]["type"] == "Figure"
    assert cooked[0]["bbox"] == [150, 220, 1510, 900]
    assert cooked[0]["caption_bbox"] == [150, 640, 1510, 900]
    assert "EMBEDDED_FIGURE_EXPAND_NEIGHBOR" in cooked[0]["reason_codes"]
