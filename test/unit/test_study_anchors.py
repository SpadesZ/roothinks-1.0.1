# Roothinks source maintenance contract
# 目的：來源錨點契約 roothinks.study.anchor/1 與 Tutor 引文核對的純函式行為。
# 重點：前端不能宣告驗證狀態；模型給的假引文一律排除；原文改版後不假裝仍是原文。
# 驗證：python -m pytest test/unit/test_study_anchors.py -q
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core_pro.study import study_anchors as anchors

QUOTE = "The improvement was observed only at 300 K under one calibration protocol."
PAGES = [
    {"page": 1, "blocks": [
        {"type": "title", "content": "Calibrated Thermal Response", "seq_id": "p1_title_1"},
        {"type": "body", "seq_id": "p1_body_2", "content": "We report a bounded measurement of thermal response for sample A. " + QUOTE,
         "translation": "我們報告樣本 A 熱響應的有界量測。改善只在 300 K 觀察到。"},
        "a bare string block still counts for numbering",
        {"type": "figure", "content": "Figure 1: setup", "seq_id": "p1_figure_4"},
        {"type": "body", "seq_id": "p1_body_5", "content": "No claim is made about samples B or C."},
    ]},
    {"page": 2, "blocks": [
        {"type": "body", "seq_id": "p2_body_1", "content": "All measurements used the same reference plane."},
        {"type": "body", "seq_id": "p2_body_2", "content": "Sample A was prepared with a fixed annealing time."},
        {"type": "body", "seq_id": "p2_body_3", "content": "Repeated phrase. Repeated phrase."},
        {"type": "body", "seq_id": "p2_body_4", "content": "Repeated phrase appears here too."},
    ]},
]
META = {"pid": "P-p", "paper_id": "paper1", "source": "flowb_trans", "source_sha256": "a" * 64}


def test_locate_ignores_whitespace_and_typographic_quotes_but_returns_raw_offsets():
    text = "He said “the  value\nwas\tsmall” today."
    [(start, end)] = anchors.locate(text, '"the value was small"')
    assert text[start:end] == "“the  value\nwas\tsmall”"
    assert anchors.locate(text, "not here") == []
    assert anchors.locate(text, "   ") == []


def test_block_numbering_matches_the_reader_including_non_dict_blocks():
    refs = [f"p{p}-b{i}" for p, i, _ in anchors.iter_blocks(PAGES)]
    assert refs[:5] == ["p1-b1", "p1-b2", "p1-b3", "p1-b4", "p1-b5"] and refs[5] == "p2-b1"
    assert [f"p{p}-b{i}" for p, i, _ in anchors.iter_blocks([{"blocks": [{"content": "x"}]}])] == ["p1-b1"]


def test_exact_anchor_is_built_by_the_server_from_the_source_text():
    got = anchors.resolve(PAGES, {"quote": QUOTE, "side": "original", "ref": "p1-b2", "source_sha256": "a" * 64,
                                  "status": "exact", "start": 0, "end": 3}, META)
    block = PAGES[0]["blocks"][1]["content"]
    assert got["status"] == "exact" and got["ref"] == "p1-b2" and got["seq_id"] == "p1_body_2"
    assert block[got["start"]:got["end"]] == QUOTE == got["quote"]
    assert got["prefix"].endswith("sample A. ") and got["version_changed"] is False
    assert got["schema"] == anchors.ANCHOR_SCHEMA and len(got["quote_sha256"]) == 64


def test_wrong_hint_is_relocated_and_changed_version_is_never_exact():
    moved = anchors.resolve(PAGES, {"quote": "fixed annealing time", "ref": "p1-b2"}, META)
    assert moved["status"] == "relocated" and moved["ref"] == "p2-b2"
    changed = anchors.resolve(PAGES, {"quote": QUOTE, "ref": "p1-b2", "source_sha256": "b" * 64}, META)
    assert changed["status"] == "relocated" and changed["version_changed"] is True


def test_ambiguous_and_missing_quotes_are_not_verified():
    amb = anchors.resolve(PAGES, {"quote": "Repeated phrase"}, META)
    assert amb["status"] == "ambiguous" and set(amb["candidates"]) == {"p2-b3", "p2-b4"}
    gone = anchors.resolve(PAGES, {"quote": "replicated in three laboratories", "ref": "p1-b2"}, META)
    assert gone["status"] == "not_found" and gone["status"] not in anchors.VERIFIED and "start" not in gone


def test_repeated_text_inside_the_hinted_block_uses_prefix_to_pick_the_occurrence():
    got = anchors.resolve(PAGES, {"quote": "Repeated phrase.", "ref": "p2-b3", "prefix": "Repeated phrase. "}, META)
    assert got["status"] == "exact" and got["start"] == len("Repeated phrase. ")


def test_translation_selection_is_marked_and_maps_to_the_whole_original_block():
    got = anchors.resolve(PAGES, {"quote": "改善只在 300 K 觀察到", "side": "translation", "ref": "p1-b2"}, META)
    assert got["status"] == "exact" and got["side"] == "translation"
    original = anchors.original_for(PAGES, got, META)
    assert original["side"] == "original" and original["quote"] == PAGES[0]["blocks"][1]["content"]
    assert original["derived_from"] == "translation_selection"


@pytest.mark.parametrize("selection,message", [
    ("text", "物件"), ({"quote": " "}, "沒有選取"), ({"quote": "x" * 2001}, "上限"),
    ({"quote": "x", "side": "margin"}, "side"), ({"quote": "x", "ref": "../etc"}, "ref"),
])
def test_bad_selections_are_rejected(selection, message):
    with pytest.raises(anchors.AnchorError, match=message):
        anchors.clean_selection(selection)


def test_evidence_window_puts_the_selection_first_and_skips_figures():
    anchor = anchors.resolve(PAGES, {"quote": QUOTE, "ref": "p1-b2"}, META)
    window = anchors.evidence_window(PAGES, anchor, radius=2)
    # 前面只有一個正文段落（標題），後面兩個；圖說 p1-b4 不算正文。
    assert [w["label"] for w in window] == ["E1", "E2", "E3", "E4"]
    assert [w["ref"] for w in window] == ["p1-b2", "p1-b1", "p1-b3", "p1-b5"]
    figure = anchors.resolve(PAGES, {"quote": "Figure 1: setup", "ref": "p1-b4"}, META)
    assert [w["ref"] for w in anchors.evidence_window(PAGES, figure)] == ["p1-b4"]


def _structured(payload):
    anchor = anchors.resolve(PAGES, {"quote": QUOTE, "ref": "p1-b2"}, META)
    window = anchors.evidence_window(PAGES, anchor)
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return anchors.verify_structured(raw, window, anchor, META)


def test_fabricated_quotes_are_rejected_and_never_become_evidence():
    out = _structured({"interpretation": "解讀", "points": [
        {"text": "有原文", "evidence": [{"ref": "E1", "quote": QUOTE}]},
        {"text": "捏造", "evidence": [{"ref": "E1", "quote": "replicated in three independent laboratories"}]},
        {"text": "轉述不是引文", "evidence": [{"ref": "E1", "quote": "improvement only seen at 300 K"}]},
    ], "uncertainty": {"level": "medium", "reasons": ["只有一個溫度"]}, "open_questions": ["其他溫度？"]})
    assert [p["support"] for p in out["points"]] == ["source_quoted", "ai_inference", "ai_inference"]
    assert [p["rejected_evidence"] for p in out["points"]] == [0, 1, 1]
    assert out["points"][0]["evidence"][0]["quote"] == QUOTE
    assert [r["quote"] for r in out["rejected_quotes"]] == ["replicated in three independent laboratories",
                                                           "improvement only seen at 300 K"]
    assert out["system_check"] == {"selection_status": "exact", "selection_side": "original", "version_changed": False,
                                   "translation_based": False, "format_ok": True, "verified_quotes": 1, "rejected_quotes": 2}
    assert out["uncertainty"] == {"level": "medium", "reasons": ["只有一個溫度"], "source": "model_self_report"}


def test_mislabelled_quote_is_corrected_but_quotes_outside_the_window_are_rejected():
    out = _structured({"interpretation": "x", "points": [
        {"text": "a", "evidence": [{"ref": "E1", "quote": "No claim is made about samples B or C."}]},
        {"text": "b", "evidence": [{"ref": "E2", "quote": "Repeated phrase appears here too."}]},
    ]})
    first = out["points"][0]["evidence"][0]
    assert first["ref"] == "p1-b5" and first["ref_corrected_from"] == "E1"
    assert out["points"][1]["evidence"] == [] and out["rejected_quotes"][0]["ref"] == "E2"


@pytest.mark.parametrize("raw", ["純文字回答，沒有 JSON", "[1, 2]", "{broken json"])
def test_unstructured_model_output_never_produces_evidence(raw):
    out = _structured(raw)
    assert out["points"] == [] and out["system_check"]["format_ok"] is False
    assert out["uncertainty"]["level"] == "unknown" and out["uncertainty"]["source"] == "system"


def test_fenced_json_and_unknown_levels_are_normalised():
    out = _structured("```json\n" + json.dumps({"interpretation": "x", "uncertainty": {"level": "certain"}}) + "\n```")
    assert out["system_check"]["format_ok"] is True and out["uncertainty"]["level"] == "unknown"


def test_plain_text_rendering_labels_ai_and_source():
    out = _structured({"interpretation": "解讀", "points": [
        {"text": "有原文", "evidence": [{"ref": "E1", "quote": QUOTE}]},
        {"text": "沒原文", "evidence": [{"ref": "E1", "quote": "made up"}]}],
        "uncertainty": {"level": "high", "reasons": ["樣本少"]}})
    text = anchors.render_text(out, "critique")
    assert text.startswith("【AI 批判】解讀") and "（有原文引文）" in text and "（AI 推論，無原文支持）" in text
    assert "已排除" in text and "【不確定性・AI 自評】高：樣本少" in text and "已驗證引文 1、排除 1" in text
