# 檔案路徑: roothinks/test/unit/test_segmentizer_types.py
# 產生時間: 2026-07-05 02:35 +08:00
# 版本: v1.0
# 模組定位:
#   segmentizer/body_segmenter 分類修復(v2.5/v0.6)的單元測試。
# 主要責任:
#   1. _normalize_llm_label:title/subtitle 落地為真實 heading 類,不再 Unknown。
#   2. BodySegmenter:llm_heading_hint 升格、running header 判定(含首頁豁免)。
#   3. eq_wratio_max 常數與 env 覆寫。
# 維護提醒:
#   - 不觸 LLM、不讀圖;candidates 為手工構造 dict。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_segmentizer_types.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.literature.literature_segmentizer import Segmentizer
from app.core_pro.literature.literature_body_segmenter import BodySegmenter


def _candidate(bbox, page_w=1600, page_h=2200, **extra):
    x1, y1, x2, y2 = bbox
    c = {
        "bbox": bbox,
        "w": x2 - x1,
        "h": y2 - y1,
        "area_ratio": ((x2 - x1) * (y2 - y1)) / float(page_w * page_h),
        "column_id": "full",
        "text_density": 1.5,
        "small_cc_count": 30,
        "table_grid_score": 0.0,
        "grid_intersection_ratio": 0.0,
        "type_scores": {"table": 0.1, "figure": 0.1, "body": 0.8, "equation": 0.1, "reasons": []},
    }
    c.update(extra)
    return c


def _extract(cands, page_num, page_w=1600, page_h=2200):
    return BodySegmenter().extract(
        candidates=cands,
        major_idxs_set=set(),
        used_caption_idxs=set(),
        blocked_idxs=set(),
        major_reject_idxs=set(),
        page_w=page_w,
        page_h=page_h,
        page_num=page_num,
    )


def test_llm_title_labels_no_longer_unknown():
    seg = Segmentizer()
    assert seg._normalize_llm_label("title") == "MainTitle"
    assert seg._normalize_llm_label("MainTitle") == "MainTitle"
    assert seg._normalize_llm_label("subtitle") == "Subtitle"
    assert seg._normalize_llm_label("sub_sub_title") == "SubSubtitle"
    assert seg._normalize_llm_label("unknown") == "Unknown"
    assert seg._normalize_llm_label("body") == "Body"


def test_llm_heading_hint_promotes_type():
    # bbox 選在頁中段、寬 60%:幾何 heading 規則不會命中(w 超出 Subtitle 上限)
    cand = _candidate(
        [200, 1000, 1160, 1050],
        llm_heading_hint="Subtitle",
        llm_confidence=0.85,
    )
    out = _extract([cand], page_num=3)
    assert out[0]["type"] == "Subtitle"
    assert "LLM_HEADING_PROMOTE" in out[0]["reason_codes"]


def test_llm_heading_hint_low_confidence_ignored():
    cand = _candidate(
        [200, 1000, 1160, 1050],
        llm_heading_hint="Subtitle",
        llm_confidence=0.5,
    )
    out = _extract([cand], page_num=3)
    assert out[0]["type"] != "Subtitle" or "LLM_HEADING_PROMOTE" not in out[0]["reason_codes"]


def test_running_header_flagged_on_non_first_page():
    # 頁首 2% 處、單行高、寬 40% -> Header
    cand = _candidate([480, 30, 1120, 75])
    out = _extract([cand], page_num=3)
    assert out[0]["type"] == "Header"
    assert out[0]["is_page_noise"] is True
    assert "RUNNING_HEADER_ZONE" in out[0]["reason_codes"]


def test_first_page_title_zone_never_header():
    cand = _candidate([480, 30, 1120, 75])
    out = _extract([cand], page_num=1)
    assert out[0]["type"] != "Header"
    assert not out[0].get("is_page_noise")


def test_eq_wratio_default_and_env_override(monkeypatch):
    seg = Segmentizer()
    assert abs(seg.eq_wratio_max - 0.92) < 1e-9
    monkeypatch.setenv("LITERATURE_EQ_WRATIO_MAX", "0.6")
    seg2 = Segmentizer()
    assert abs(seg2.eq_wratio_max - 0.6) < 1e-9
