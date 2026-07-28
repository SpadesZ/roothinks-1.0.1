"""
路徑(./test/unit/test_literature_progress_masking.py)
版本 v1.0
更版時間 20260728

模組定位：文獻狀態 API 的內部流程遮蔽測試。
背景：畫面上把「OCR-A / 規則仲裁 / 校對」換成百分比之後，
      /api/literature/status/<pid> 的 JSON 仍原封不動吐出 stages.s2、
      flow_status="gold_ready"，任何人開開發者工具就能還原整條流程。
      前端遮蔽只擋得住不看原始碼的人，真正的邊界要放在回應本身。
重點：這組測試的用意是防止日後有人為了除錯方便，把內部欄位又加回預設回應。
"""
import pytest

from app.core_pro.literature.literature_processing_ops import (
    _INTERNAL_FIELDS,
    build_milestones,
    classify_error,
    progress_pct_of,
    progress_state_of,
    strip_internal_fields,
)

ALL_DONE = {f"s{i}": "done" for i in [1, 2, 3, 4, 10, 11]}


def test_milestones_are_percentages_only():
    """里程碑只能有百分比，不能出現任何步驟名稱。"""
    ms = build_milestones(ALL_DONE, True, True)
    labels = [m["label"] for m in ms]
    assert labels == ["PDF", "15%", "30%", "40%", "60%", "70%", "80%", "100% 完成"]
    blob = " ".join(labels)
    for leak in ["OCR", "仲裁", "校對", "Flow", "reflow", "gold", "rules"]:
        assert leak not in blob


def test_progress_pct_tracks_highest_done():
    assert progress_pct_of(build_milestones({}, False, False)) == 0
    assert progress_pct_of(build_milestones({"s1": "done", "s2": "done"}, False, False)) == 15
    assert progress_pct_of(build_milestones(ALL_DONE, True, False)) == 80
    assert progress_pct_of(build_milestones(ALL_DONE, True, True)) == 100


def test_processing_state_is_preserved():
    ms = build_milestones({"s1": "done", "s2": "processing"}, False, False)
    assert ms[1]["state"] == "processing"
    # 進行中不算已達成
    assert progress_pct_of(ms) == 0


def test_unknown_stage_value_falls_back_to_pending():
    ms = build_milestones({"s2": "weird-value"}, False, False)
    assert ms[1]["state"] == "pending"


@pytest.mark.parametrize("internal,expected", [
    ("gold_ready", "analyzed"),
    ("rules_ready", "analyzed"),
    ("analyzing", "analyzing"),
    ("bilingual_ready", "completed"),
    ("processing_B", "translating"),
    ("need_retry", "failed"),
    ("pending", "uploaded"),
    (None, "uploaded"),
])
def test_state_mapping_hides_internal_names(internal, expected):
    assert progress_state_of(internal) == expected


def test_mapped_states_never_leak_internal_vocabulary():
    for internal in ["gold_ready", "rules_ready", "bilingual_ready",
                     "processing_A", "processing_B", "need_retry"]:
        out = progress_state_of(internal)
        for leak in ["gold", "rules", "bilingual", "_A", "_B", "retry"]:
            assert leak not in out


def test_internal_fields_stripped_by_default(monkeypatch):
    monkeypatch.delenv("LITERATURE_EXPOSE_INTERNALS", raising=False)
    row = {f: "x" for f in _INTERNAL_FIELDS}
    row.update({"paper_id": "p1", "progress_pct": 60})
    out = strip_internal_fields(row)
    assert out == {"paper_id": "p1", "progress_pct": 60}
    for f in _INTERNAL_FIELDS:
        assert f not in out


def test_internal_fields_kept_when_flag_on(monkeypatch):
    """除錯與既有測試需要時可明確開啟；正式部署不該開。"""
    monkeypatch.setenv("LITERATURE_EXPOSE_INTERNALS", "1")
    row = {"paper_id": "p1", "stages": {"s1": "done"}, "flow_status": "gold_ready"}
    assert strip_internal_fields(row) == row


def test_flag_only_accepts_explicit_truthy(monkeypatch):
    for val in ["0", "", "no", "off", "false"]:
        monkeypatch.setenv("LITERATURE_EXPOSE_INTERNALS", val)
        assert "stages" not in strip_internal_fields({"stages": {}, "paper_id": "p"})


# --- 第一版漏掉的部分（由 code review 指出） -------------------------------

def test_flow_ready_flags_are_stripped():
    """[迴歸] flow_a_ready / flow_b_ready 第一版沒剝，等於「不外洩」只做一半。

    原本的測試只逐一檢查我當初列進清單的欄位，自然抓不到漏列的。
    這裡改成「白名單」：明確列出允許外流的欄位，其餘一律不得出現。
    """
    row = {
        "paper_id": "p1", "filename": "a.pdf",
        "progress_pct": 60, "progress_state": "analyzed",
        "milestones": [], "llm_usage": None, "error_type": None,
        # 以下都不該外流
        "flow_a_ready": True, "flow_b_ready": False,
        "stages": {"s1": "done"}, "flow_status": "gold_ready",
        "db_status": "gold_ready", "status_cv": "done",
        "status_fix": "done", "status_trans": "pending",
        "flowb_generation_mode": "heuristic_fallback",
        "flowb_llm_ready": False, "has_fixed": True, "has_trans": False,
    }
    allowed = {"paper_id", "filename", "progress_pct", "progress_state",
               "milestones", "llm_usage", "error_type"}
    out = strip_internal_fields(row)
    assert set(out) == allowed, f"多出不該外流的欄位: {set(out) - allowed}"


def test_every_internal_field_is_actually_listed():
    """清單本身要涵蓋所有內部欄位——防止日後新增欄位時又漏列。"""
    for f in ("flow_a_ready", "flow_b_ready", "stages", "flow_status",
              "db_status", "status_cv", "status_fix", "status_trans",
              "flowb_generation_mode", "flowb_llm_ready"):
        assert f in _INTERNAL_FIELDS, f"{f} 不在剝除清單中"


@pytest.mark.parametrize("raw,expected", [
    ("quota_error", "quota"),
    ("degraded_summary", "degraded"),
    ("general_error", "failed"),
    ("flowb_reflow_missing", "translation_incomplete"),
    ("flowb_reflow_heuristic_fallback", "translation_incomplete"),
    ("flowb_reflow_unknown_mode", "translation_incomplete"),
    ("flowb_artifact_missing", "translation_incomplete"),
    (None, None),
    ("", None),
])
def test_error_type_is_classified_not_raw(raw, expected):
    """[迴歸] error_type 原本直接吐 flowb_reflow_{generation_mode}，
    等於把 Flow B 的內部生成模式名稱交出去。"""
    assert classify_error(raw) == expected


def test_error_class_never_leaks_internal_vocabulary():
    for raw in ["flowb_reflow_heuristic_fallback", "flowb_reflow_missing",
                "flowb_artifact_missing", "flowb_reflow_not_ready"]:
        out = classify_error(raw)
        for leak in ["flowb", "reflow", "artifact", "heuristic"]:
            assert leak not in out
