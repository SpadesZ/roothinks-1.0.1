# Roothinks source maintenance contract
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 檔案路徑: roothinks/test/unit/test_reflow_coverage.py
# 產生時間: 2026-07-05 03:40 +08:00
# 版本: v1.0
# 模組定位:
#   reflow 內容零損失防線(v0.2/v3.6)的單元測試。
# 主要責任: 重現並驗收 reflow coverage 的成功、失敗與回歸邊界。
#   1. 覆蓋率驗證器:全分配 -> 無 unassigned;漏 ref -> 正確列出。
#   2. Abstract 保底:第一頁長文 + 輸出無 Abstract -> 注入 fallback。
#   3. running header 過濾:>=3 頁重複 -> 移除;1 頁 -> 保留。
#   4. heading 判定:編號模式與章節錨點豁免。
# 維護提醒:
#   - 純函數測試,不觸 LLM、不碰 data/。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_reflow_coverage.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.literature.literature_flowb_helpers import (
    _flowb_build_document_flow,
    _flowb_collect_reflow_rows,
    _flowb_filter_running_headers,
    _flowb_is_heading_like,
    _flowb_is_section_anchor,
)
from app.core_pro.literature.literature_routes import (
    _ensure_abstract_section,
    _validate_reflow_coverage,
)


def _row(ref, text, text_en=None, text_zh=""):
    return {
        "ref": ref,
        "text": text,
        "text_en": text_en if text_en is not None else text,
        "text_zh": text_zh,
    }


def test_coverage_all_assigned_no_warning():
    rows = [_row("p1-b1", "x" * 100), _row("p1-b2", "y" * 100)]
    sections = [
        {
            "section_label": "Abstract",
            "source_block_refs": ["p1-b1", "p1-b2"],
            "dropped_refs": [],
            "content_en": "x" * 100 + "y" * 100,
            "content_zh": "",
        }
    ]
    cov = _validate_reflow_coverage(rows, sections)
    assert cov["assigned_ratio"] == 1.0
    assert cov["unassigned_refs"] == []
    assert cov["content_ratio"] >= 0.90


def test_coverage_missing_refs_reported():
    rows = [_row(f"p1-b{i}", "z" * 80) for i in range(1, 5)]
    sections = [
        {
            "section_label": "Methods",
            "source_block_refs": ["p1-b1"],
            "dropped_refs": ["p1-b2"],
            "content_en": "z" * 80,
            "content_zh": "",
        }
    ]
    cov = _validate_reflow_coverage(rows, sections)
    assert cov["assigned_ratio"] == 0.5
    assert set(cov["unassigned_refs"]) == {"p1-b3", "p1-b4"}


def test_abstract_fallback_injected():
    rows = [
        _row("p1-b1", "Paper Title Here"),
        _row("p1-b4", "A" * 400),  # abstract 長文
        _row("p2-b1", "intro text " * 10),
    ]
    sections = [
        {"section_label": "Introduction", "source_block_refs": ["p2-b1"], "dropped_refs": [],
         "content_en": "intro", "content_zh": ""}
    ]
    out, injected = _ensure_abstract_section(rows, sections)
    assert injected is True
    assert out[0]["section_label"] == "Abstract"
    assert out[0]["inferred_label"] is True
    assert out[0]["source_block_refs"] == ["p1-b4"]
    assert out[0]["content_en"] == "A" * 400


def test_abstract_fallback_not_injected_when_present():
    rows = [_row("p1-b4", "A" * 400)]
    sections = [
        {"section_label": "Abstract", "source_block_refs": ["p1-b4"], "dropped_refs": [],
         "content_en": "A" * 400, "content_zh": ""}
    ]
    out, injected = _ensure_abstract_section(rows, sections)
    assert injected is False
    assert len(out) == 1


def test_running_header_removed_when_repeated_3_pages():
    rows = [
        _row("p2-b1", "Connectionist Temporal Classification"),
        _row("p3-b1", "Connectionist  Temporal Classification"),  # 空白差異也要命中
        _row("p4-b1", "connectionist temporal classification"),
        _row("p2-b2", "real body content " * 5),
    ]
    kept, removed = _flowb_filter_running_headers(rows)
    assert set(removed) == {"p2-b1", "p3-b1", "p4-b1"}
    assert len(kept) == 1 and kept[0]["ref"] == "p2-b2"


def test_single_occurrence_not_treated_as_header():
    rows = [_row("p1-b1", "Unique Section Title"), _row("p2-b1", "other text " * 5)]
    kept, removed = _flowb_filter_running_headers(rows)
    assert removed == []
    assert len(kept) == 2


def test_numbered_heading_and_anchor():
    assert _flowb_is_heading_like("3.1 Constructing the Classifier") is True
    assert _flowb_is_section_anchor("3.1 Constructing the Classifier") is True
    assert _flowb_is_section_anchor("Abstract") is True
    assert _flowb_is_section_anchor("1. Introduction") is True
    assert _flowb_is_section_anchor("This is a normal sentence that keeps going and going beyond heading style.") is False


def test_collect_rows_keeps_short_anchor_and_drops_vns_header():
    trans_payload = {
        "content": [
            {
                "page": 2,
                "blocks": [
                    {"type": "Header", "content": "Running Header Text", "is_page_noise": True},
                    {"type": "Body", "content": "Abstract"},  # 短錨點,舊版會被清掉
                    {"type": "Body", "content": "This is a full paragraph of body text that is long enough to pass filters."},
                ],
            }
        ]
    }
    rows = _flowb_collect_reflow_rows(trans_payload)
    texts = [r["text"] for r in rows]
    assert "Abstract" in texts, "短章節錨點必須保留"
    assert all("Running Header" not in t for t in texts), "VNS 標記的 Header 不應進 rows"


def test_document_flow_preserves_source_order_and_hides_noise():
    trans_payload = {
        "content": [
            {
                "page": 1,
                "blocks": [
                    {"type": "Title", "content": "1. Introduction", "content_zh": "1. 介紹"},
                    {"type": "Body", "content": "Before equation.", "content_zh": "公式前。"},
                    {"type": "Equation", "content": "x = y", "latex": "x = y", "content_zh": "x = y"},
                    {"type": "Body", "content": "After equation.", "content_zh": "公式後。"},
                    {"type": "Figure", "content": "Figure 1. Pipeline.", "content_zh": "圖 1。流程。", "has_caption": True},
                    {"type": "Unknown", "content": "t def v a(s)= So J]"},
                ],
            }
        ]
    }

    flow = _flowb_build_document_flow(trans_payload, [])
    assert flow["meta"]["counts"]["title"] == 1
    assert flow["meta"]["counts"]["body"] == 2
    assert flow["meta"]["counts"]["equation"] == 1
    assert flow["meta"]["counts"]["figure"] == 1
    assert flow["meta"]["counts"]["noise"] == 1

    visible_kinds = [item["kind"] for item in flow["items"] if not item["hidden"]]
    assert visible_kinds == ["title", "body", "equation", "body", "figure"]
    assert flow["items"][-1]["hidden_reason"] == "ocr_fragment"
    assert flow["items"][2]["latex"] == "x = y"
    assert flow["items"][4]["caption_en"] == "Figure 1. Pipeline."
