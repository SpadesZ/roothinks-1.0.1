# Roothinks source maintenance contract
# 目的：R7 多篇研究矩陣（roothinks.study.matrix/2）的伺服器端行為。
#       涵蓋：順序與基準、維度、每格狀態、引文逐字核對（假引文必須被擋）、輸入涵蓋紀錄、舊記錄可讀、部分失敗。
# 邊界：比較器一律用替身 bus，不呼叫任何模型；檔案只寫 tmp_path。
# 驗證：python -m pytest test/unit/test_study_matrix_r7.py -q
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core_pro.study.study_matrix import (MatrixEngine, normalize_dimensions, paper_blocks, select_blocks,
                                              TEXT_SAFETY_CAP)
from app.llm_service.matching_tasks.task_6comp import Comparator, DEFAULT_DIMENSIONS, dimension_id

PID = "MX-p"
REAL_B = "Samples were annealed for 20, 40, and 60 minutes before measurement."
FAKE = "The improvement was replicated across five independent sample batches."


def _paper(paper_id, pages, title=None):
    """與閱讀器相同的全文結構（pages）＋錨點中繼資料。"""
    return {"paper_id": paper_id, "metadata": {"title": title or paper_id},
            "pages": [{"page": p, "blocks": [{"type": k, "content": t} for k, t in blocks]} for p, blocks in pages],
            "anchor_meta": {"pid": PID, "paper_id": paper_id, "source": "flowa_fusion", "source_sha256": "0" * 64}}


BASE = _paper("base", [(1, [("title", "Base Paper"), ("body", "We report a bounded measurement of thermal response for sample A.")]),
                       (2, [("title", "Methods"), ("body", "All measurements used the same reference plane at 300 K.")]),
                       (3, [("title", "Results"), ("body", "Sample A improved by 12 percent at 300 K relative to baseline."),
                            ("body", "The result may depend on the annealing time, which this study held constant.")])])
PB = _paper("pb", [(1, [("title", "Paper B"), ("body", "We examine whether annealing time changes the thermal response.")]),
                   (2, [("title", "Methods"), ("body", REAL_B)]),
                   (3, [("title", "Results"), ("body", "The 60-minute anneal improved thermal response by 18 percent.")])])
PC = _paper("pc", [(1, [("title", "Paper C"), ("body", "This study reports the thermal response of sample A measured at 77 K.")]),
                   (2, [("title", "Results"), ("body", "The thermal response increased by 0.8 mW/K after calibration at 77 K.")])])


def side(status, text="", quotes=(), reason=""):
    return {"status": status, "text": text, "quotes": [{"ref": r, "quote": q} for r, q in quotes], "uncertain_reason": reason}


class ScriptBus:
    """依 TARGET_PAPER_ID 回覆預先寫好的維度結果；可模擬延遲（反向完成）與失敗。記錄呼叫次數與提示詞。"""

    def __init__(self, script, delays=None, fail=()):
        self.script, self.delays, self.fail = script, delays or {}, set(fail)
        self.prompts = []
        self.lock = threading.Lock()

    def dispatch_task(self, task, prompt, **kw):
        import re
        target = re.search(r"TARGET_PAPER_ID: (\S+)", prompt).group(1)
        dims = json.loads(re.search(r"DIMENSIONS_JSON: (\[.*?\])\n", prompt).group(1))
        with self.lock:
            self.prompts.append((target, prompt))
        time.sleep(self.delays.get(target, 0))
        if target in self.fail:
            return False, "synthetic provider offline"
        rows = self.script.get(target, {})
        out = []
        for d in dims:
            item = rows.get(d["label"])
            if item is None:
                continue  # 模型漏回這個維度
            out.append({"dimension_id": d["id"], **item})
        return True, json.dumps({"dimensions": out, "overall_summary": "合成"}, ensure_ascii=False)


def engine_with(bus):
    engine = MatrixEngine("/nonexistent")
    engine.comparator.bus = bus
    return engine


def dims(*labels):
    return {"label": "", "dimensions": list(labels)}


# ---------------------------------------------------------------- 順序與 id

def test_column_order_follows_input_even_when_pairs_finish_in_reverse():
    row = {"dimension": None}
    script = {t: {"結果": {"baseline": side("not_reported"), "target": side("not_reported")}} for t in ("pb", "pc")}
    bus = ScriptBus(script, delays={"pb": 0.3, "pc": 0.0})  # pb 比較晚完成
    result = engine_with(bus).generate_matrix(PID, [BASE, PB, PC], dims("結果"))
    assert result["paper_order"] == ["base", "pb", "pc"]
    assert [c["target_paper_id"] for c in result["comparisons"]] == ["pb", "pc"]
    assert [(p["paper_id"], p["position"], p["column_id"], p["role"]) for p in result["papers"]] == [
        ("base", 0, "col-0", "baseline"), ("pb", 1, "col-1", "compared"), ("pc", 2, "col-2", "compared")]
    assert result["baseline_id"] == "base" and row["dimension"] is None


def test_dimension_ids_are_deterministic_and_researcher_dimensions_are_kept():
    first = normalize_dimensions(["樣本溫度", " 樣本溫度 ", "結果與證據強度", ""])
    again = normalize_dimensions(["結果與證據強度", "樣本溫度"])
    assert [d["label"] for d in first] == ["樣本溫度", "結果與證據強度"]
    assert {d["label"]: d["id"] for d in first} == {d["label"]: d["id"] for d in again}
    assert first[0]["id"] == dimension_id("樣本溫度")
    assert [d["label"] for d in normalize_dimensions([])] == DEFAULT_DIMENSIONS
    assert len(normalize_dimensions([f"維度{i}" for i in range(20)])) == 8


# ---------------------------------------------------------------- 狀態

def test_cell_statuses_never_fill_or_rank_missing_data():
    script = {
        "pb": {
            "方法": {"baseline": side("reported", "固定參考平面", [("p2-b2", "All measurements used the same reference plane at 300 K.")]),
                   "target": side("reported", "三種退火時間", [("p2-b2", REAL_B)]),
                   "comparability": {"status": "comparable"}, "relation": "對照論文改變退火時間。"},
            "結果": {"baseline": side("reported", "改善 12%", [("p3-b2", "Sample A improved by 12 percent at 300 K relative to baseline.")]),
                   "target": side("not_reported", "N/A"), "relation": "基準論文的結果明顯優於對照論文。", "significance": "High"},
        },
        "pc": {
            "方法": {"baseline": side("not_reported"), "target": side("reported", "77 K", [("p1-b2", "This study reports the thermal response of sample A measured at 77 K.")]),
                   "relation": "對照論文的方法較好。"},
            "結果": {"baseline": side("reported", "改善 12%", [("p3-b2", "Sample A improved by 12 percent at 300 K relative to baseline.")]),
                   "target": side("reported", "增加 0.8 mW/K", [("p2-b2", "The thermal response increased by 0.8 mW/K after calibration at 77 K.")]),
                   "comparability": {"status": "not_comparable", "reason": "77 K 對 300 K；mW/K 對百分比"},
                   "relation": "對照論文改善幅度較小，基準較優。"},
        },
    }
    result = engine_with(ScriptBus(script)).generate_matrix(PID, [BASE, PB, PC], dims("方法", "結果", "限制"))
    ids = {d["label"]: d["id"] for d in result["dimensions"]}
    pb = result["comparisons"][0]["cells"]
    pc = result["comparisons"][1]["cells"]
    # 未報告：不留文字、不留差異描述、不留 significance
    assert pb[ids["結果"]]["status"] == "not_reported" and pb[ids["結果"]]["text"] == ""
    assert pb[ids["結果"]]["relation"] == "" and pb[ids["結果"]]["relation_withheld"] == "missing"
    assert "significance" not in json.dumps(result["comparisons"][0]["cells"])
    # 基準未報告：對照有資料也不比較
    assert pc[ids["方法"]]["status"] == "reported" and pc[ids["方法"]]["relation_withheld"] == "missing"
    # 不可比較：狀態、理由、差異描述不顯示
    assert pc[ids["結果"]]["status"] == "not_comparable" and "77 K" in pc[ids["結果"]]["reason"]
    assert pc[ids["結果"]]["relation"] == "" and pc[ids["結果"]]["evidence"]
    # 可比較且兩邊都有：保留中性描述
    assert pb[ids["方法"]]["relation"] == "對照論文改變退火時間。"
    # 模型漏回的維度：標失敗並說明，不補 N/A
    assert pb[ids["限制"]]["status"] == "failed" and "缺少" in pb[ids["限制"]]["reason"]
    assert result["baseline_cells"][ids["限制"]]["status"] == "failed"
    # 沒有任何畫面可見欄位含排名字眼
    visible = json.dumps([{k: v for k, v in c.items() if k != "relation_withheld_text"}
                          for comp in result["comparisons"] for c in comp["cells"].values()], ensure_ascii=False)
    assert "優於" not in visible and "較優" not in visible and "較好" not in visible


def test_reported_without_found_quote_becomes_uncertain():
    script = {"pb": {"限制": {"baseline": side("reported", "沒有引文"), "target": side("reported", "只有假引文", [("p2-b2", FAKE)])}}}
    result = engine_with(ScriptBus(script)).generate_matrix(PID, [BASE, PB], dims("限制"))
    did = result["dimensions"][0]["id"]
    cell = result["comparisons"][0]["cells"][did]
    assert cell["status"] == "uncertain" and cell["status_from_model"] == "reported"
    assert not cell["evidence"] and cell["rejected_quotes"][0]["quote"] == FAKE
    assert result["baseline_cells"][did]["status"] == "uncertain"


# ---------------------------------------------------------------- 引文核對

def test_fabricated_quote_is_rejected_and_real_quote_gets_page_and_ref():
    script = {"pb": {"方法": {"baseline": side("reported", "x", [("p3-b2", "The result may depend on the annealing time, which this study held constant.")]),
                              "target": side("reported", "y", [("p2-b2", REAL_B), ("p2-b2", FAKE), ("p2-b2", "20, 40"),
                                                               ("p9-b9", "measurement")])}}}
    result = engine_with(ScriptBus(script)).generate_matrix(PID, [BASE, PB], dims("方法"))
    did = result["dimensions"][0]["id"]
    target = result["comparisons"][0]["cells"][did]
    assert [e["quote"] for e in target["evidence"]] == [REAL_B]
    ev = target["evidence"][0]
    assert (ev["page"], ev["ref"], ev["status"], ev["quote_match"], ev["claim_support"]) == (2, "p2-b2", "exact", "found", "not_assessed")
    reasons = {r["quote"]: r["reason"] for r in target["rejected_quotes"]}
    assert reasons[FAKE] == "原文中找不到這段文字"
    assert "太短" in reasons["20, 40"] and "太短" in reasons["measurement"]
    # 基準引文 AI 標錯段落（真的在 p3-b3）：改到實際段落並保留原標記
    base_ev = result["baseline_cells"][did]["evidence"][0]
    assert base_ev["ref"] == "p3-b3" and base_ev["ref_corrected_from"] == "p3-b2"
    assert result["system_check"]["quotes_rejected"] == 3


def test_quote_check_is_exact_not_nfkc_and_rejects_ambiguous_blocks():
    special = _paper("sp", [(1, [("body", "The dose was 10² Gy for every sample in this cohort."),
                                 ("body", "Repeated sentence used in two different places here."),
                                 ("body", "Repeated sentence used in two different places here."),
                                 ("body", "It said “the reference plane was fixed” during all runs.")])])
    script = {"sp": {"資料": {"baseline": side("not_reported"),
                              "target": side("reported", "x", [("p1-b1", "The dose was 102 Gy for every sample"),
                                                               ("p9-b9", "Repeated sentence used in two different places here."),
                                                               ("p1-b4", 'It said "the reference plane was fixed" during all runs.')])}}}
    result = engine_with(ScriptBus(script)).generate_matrix(PID, [BASE, special], dims("資料"))
    cell = result["comparisons"][0]["cells"][result["dimensions"][0]["id"]]
    reasons = {r["quote"][:12]: r["reason"] for r in cell["rejected_quotes"]}
    assert reasons["The dose was"] == "原文中找不到這段文字"          # 10² 不能被 NFKC 成 102
    assert reasons["Repeated sen"] == "多個段落都有這段文字，無法歸屬"   # 標錯位置又出現在兩段：不猜
    assert [e["ref"] for e in cell["evidence"]] == ["p1-b4"]          # 只做排版等價（彎引號）


# ---------------------------------------------------------------- 輸入涵蓋

def _long_paper():
    intro = [("body", f"Introduction paragraph {i} " + "background sentence. " * 40) for i in range(12)]
    pages = [(1, [("title", "Long Paper"), ("body", "Abstract: we study annealing.")] + [("title", "Introduction")] + intro[:6]),
             (2, intro[6:]),
             (3, [("title", "Methods"), ("body", "Samples were annealed for three durations before measurement at 300 K.")]),
             (4, [("title", "Results"), ("body", "The longest anneal improved response by 18 percent at 300 K.")]),
             (5, [("title", "Limitations"), ("body", "Only one sample batch was used, so batch variation was not assessed.")]),
             (6, [("title", "References"), ("body", "[1] A. Author. A cited paper about thermal response. 2020.")])]
    return _paper("long", pages)


def test_section_aware_selection_reaches_methods_results_limitations_and_records_coverage(monkeypatch):
    monkeypatch.setenv("STUDY_MATRIX_PAPER_CHAR_BUDGET", "4000")
    paper = _long_paper()
    blocks = paper_blocks(paper)
    given, cov = select_blocks(blocks, normalize_dimensions(None))
    refs = [b["ref"] for b in given]
    assert {"p3-b2", "p4-b2", "p5-b2"} <= set(refs)                 # 舊版 [:2500] 只會看到緒論
    assert "p6-b2" not in refs and "references" not in cov["sections_given"]
    assert cov["chars_given"] <= cov["budget_chars"] == 4000
    assert cov["complete"] is False and cov["blocks_given"] == len(refs) < cov["text_blocks_total"]
    assert [b["ref"] for b in cov["blocks"]] == refs and cov["page_span"] == [min(cov["pages"]), max(cov["pages"])]
    assert "introduction" in cov["sections_given"]

    bus = ScriptBus({"long": {}})
    result = engine_with(bus).generate_matrix(PID, [BASE, paper], dims("方法", "結果", "限制"))
    prompt = bus.prompts[0][1]
    assert "[p4-b2 | results] The longest anneal improved response by 18 percent" in prompt
    assert "of 16 text blocks" in prompt                               # 告訴模型它只看到部分
    recorded = next(p for p in result["papers"] if p["paper_id"] == "long")["coverage"]
    assert recorded["source"] == "flowa_fusion" and recorded["blocks"]


def test_short_paper_is_complete_and_paper_title_is_not_a_section():
    blocks = paper_blocks(_paper("t", [(1, [("title", "Calibrated Response in a Synthetic Sample Set"),
                                            ("body", "We report a bounded measurement.")])],
                                 title="Calibrated Response in a Synthetic Sample Set"))
    assert blocks[0]["section"] == "front"
    _, cov = select_blocks(blocks, normalize_dimensions(None))
    assert cov["complete"] is True and cov["pages"] == [1]


# ---------------------------------------------------------------- 失敗與呼叫次數

def test_partial_failure_marks_only_that_column_and_calls_model_once_per_paper():
    no_text = {"paper_id": "empty", "metadata": {"title": "Empty"}, "pages": [], "anchor_meta": BASE["anchor_meta"]}
    script = {"pc": {"結果": {"baseline": side("reported", "改善 12%", [("p3-b2", "Sample A improved by 12 percent at 300 K relative to baseline.")]),
                              "target": side("reported", "0.8 mW/K", [("p2-b2", "The thermal response increased by 0.8 mW/K after calibration at 77 K.")])}}}
    bus = ScriptBus(script, fail={"pb"})
    result = engine_with(bus).generate_matrix(PID, [BASE, PB, PC, no_text], dims("結果"))
    assert len(bus.prompts) == 2                                        # 沒有全文的不呼叫模型
    assert result["status"] == "partial" and result["success_count"] == 1 and result["failure_count"] == 2
    did = result["dimensions"][0]["id"]
    pb, pc, empty = result["comparisons"]
    assert pb["status"] == "failed" and pb["cells"][did]["status"] == "failed" and pb["cells"][did]["reason"]
    assert "模型比較失敗" in pb["analysis"]                              # 舊欄位保留失敗訊息，不被格子內容蓋掉
    assert empty["error"] == "no_fulltext" and empty["cells"][did]["status"] == "failed"
    assert pc["cells"][did]["status"] == "reported"
    base = result["baseline_cells"][did]
    assert base["status"] == "reported" and base["source_pair"] == "pc"
    assert base["pair_statuses"] == {"pb": "failed", "pc": "reported", "empty": "failed"}
    assert next(p for p in result["papers"] if p["paper_id"] == "empty")["fulltext_available"] is False


def test_legacy_comparator_result_is_not_presented_as_evidence(tmp_path):
    engine = MatrixEngine(str(tmp_path))
    engine.comparator = SimpleNamespace(compare_pair=lambda a, b, c: {"analysis": "supported result"})
    result = engine.generate_matrix(PID, [{"paper_id": "a", "content": "A text"}, {"paper_id": "b", "content": "B text"}])
    assert result["status"] == "completed"
    cells = result["comparisons"][0]["cells"]
    assert all(c["status"] == "uncertain" and not c["evidence"] for c in cells.values())
    assert [d["label"] for d in result["dimensions"]] == DEFAULT_DIMENSIONS   # 舊字串 criteria → 預設五維
    assert result["criteria"] == "Methodology"                              # 舊預設值不變


# ---------------------------------------------------------------- 比較器本身

def test_comparator_keeps_full_text_and_parses_unlabelled_fence():
    long_text = "甲" * 500
    over = "乙" * (TEXT_SAFETY_CAP + 50)
    dims_spec = {"label": "x", "dimensions": [{"id": dimension_id("方法"), "label": "方法"}]}
    reply = "```\n" + json.dumps({"dimensions": [{"dimension_id": dimension_id("方法"),
                                                  "baseline": side("reported", long_text), "target": side("uncertain", over, reason="?")}]}) + "\n```"
    comparator = Comparator()
    comparator.bus = SimpleNamespace(dispatch_task=lambda *a, **k: (True, reply))
    out = comparator.compare_pair({"id": "a", "title": "A", "text": "x"}, {"id": "b", "title": "B", "text": "y"}, dims_spec)
    assert out["status"] == "completed"
    assert out["dimension_results"][0]["baseline"]["text"] == long_text     # 舊版截在 220 字
    engine = engine_with(SimpleNamespace(dispatch_task=lambda *a, **k: (True, reply)))
    result = engine.generate_matrix(PID, [BASE, PB], {"label": "x", "dimensions": ["方法"]})
    cell = result["comparisons"][0]["cells"][dimension_id("方法")]
    assert cell["truncated"] is True and cell["full_chars"] == len(over) and len(cell["text"]) == TEXT_SAFETY_CAP


def test_comparator_accepts_old_format_reply_without_inventing_dimensions():
    reply = json.dumps({"comparison_points": [{"dimension": "研究問題與目標", "baseline_view": "A", "target_view": "N/A", "synthesis": "A 較好"}]})
    comparator = Comparator()
    comparator.bus = SimpleNamespace(dispatch_task=lambda *a, **k: (True, reply))
    out = comparator.compare_pair({"id": "a", "title": "A", "text": "x"}, {"id": "b", "title": "B", "text": "y"}, "General")
    assert [r["label"] for r in out["dimension_results"]] == ["研究問題與目標"]
    assert out["dimension_results"][0]["target"]["status"] == "not_reported"
    assert len(out["missing_dimensions"]) == 4


# ---------------------------------------------------------------- route：順序、維度、舊記錄

@pytest.fixture
def matrix_app(monkeypatch, tmp_path):
    from app.core_pro.study import study_routes as routes
    app = Flask(__name__)
    app.config.update(TESTING=True, LOCK_ROOT=str(tmp_path / "locks"))
    (tmp_path / "app").mkdir()
    app.root_path = str(tmp_path / "app")
    data_root = tmp_path / "data_root"
    monkeypatch.setattr(routes, "_normalize_study_pid", lambda pid: str(pid or ""))
    monkeypatch.setattr(routes, "enforce_project_ownership", lambda pid: None)
    monkeypatch.setattr(routes, "resolve_data_root", lambda: str(data_root))
    for paper in (BASE, PB, PC):
        fusion = data_root / PID / "literature" / "papers" / paper["paper_id"] / "05_interprets" / "fusion"
        fusion.mkdir(parents=True)
        (fusion / "full_text.json").write_text(json.dumps({"paper_id": paper["paper_id"], "content": paper["pages"]}), encoding="utf-8")
    app.register_blueprint(routes.study_bp)
    return app, routes, data_root


def test_route_persists_selected_order_explicit_baseline_and_dimensions(matrix_app, monkeypatch):
    app, routes, data_root = matrix_app
    bus = ScriptBus({t: {"結果與證據強度": {"baseline": side("not_reported"), "target": side("not_reported")}} for t in ("pb", "pc")})
    monkeypatch.setattr(routes.matrix_engine.comparator, "bus", bus)
    client = app.test_client()
    resp = client.post("/api/study/compare", json={"pid": PID, "paper_ids": ["pc", "base", "pb", "pc", "../x"],
                                                   "baseline_id": "base", "dimensions": ["樣本溫度", "結果與證據強度"]})
    assert resp.status_code == 200, resp.json
    body = resp.json
    assert body["data"]["paper_order"] == ["base", "pc", "pb"]
    assert body["skipped_papers"] == [{"paper_id": "pc", "reason": "duplicate"}, {"paper_id": "../x", "reason": "invalid_id"}]
    assert len(bus.prompts) == 2
    record = json.loads((data_root / PID / "study" / "matrix_history" / f"{body['matrix_id']}.json").read_text(encoding="utf-8"))
    assert record["paper_order"] == ["base", "pc", "pb"] and record["baseline_id"] == "base"
    assert [d["label"] for d in record["dimensions"]] == ["樣本溫度", "結果與證據強度"]
    assert record["criteria"] == "樣本溫度、結果與證據強度" and record["schema"] == "roothinks.study.matrix/2"
    # 證據的 source／sha 與閱讀器同一份全文
    cov = record["data"]["papers"][0]["coverage"]
    assert cov["source"] == "flowa_fusion" and len(cov["source_sha256"]) == 64
    again = client.get(f"/api/study/{PID}/matrix/latest").json["record"]
    assert again["data"]["paper_order"] == ["base", "pc", "pb"]
    listed = client.get(f"/api/study/{PID}/matrix/history").json["history"][0]
    assert listed["paper_count"] == 3 and listed["baseline_id"] == "base" and listed["dimensions"] == ["樣本溫度", "結果與證據強度"]
    assert client.post("/api/study/compare", json={"pid": PID, "paper_ids": ["base", "pb"], "baseline_id": "pc"}).status_code == 400


def test_route_without_dimensions_keeps_legacy_string_criteria(matrix_app, monkeypatch):
    app, routes, _ = matrix_app
    seen = []
    monkeypatch.setattr(routes.matrix_engine, "generate_matrix",
                        lambda pid, papers, criteria: seen.append((criteria, [p["paper_id"] for p in papers]))
                        or {"comparisons": [{"analysis": "ok"}]})
    resp = app.test_client().post("/api/study/compare", json={"pid": PID, "paper_ids": ["pb", "base"], "criteria": "Methodology"})
    assert resp.status_code == 200
    assert seen == [("Methodology", ["pb", "base"])]


def test_legacy_records_still_list_and_load(matrix_app):
    app, routes, data_root = matrix_app
    study = data_root / PID / "study"
    (study / "matrix_history").mkdir(parents=True)
    old_data = {"pid": PID, "criteria": "General", "baseline": {"id": "base", "title": "Base"},
                "comparisons": [{"target_paper_id": "pb", "target_paper_title": "Paper B",
                                 "comparison_points": [{"dimension": "結果與證據強度", "baseline_view": "12%", "target_view": "N/A",
                                                        "synthesis": "資料不足，待補充比較證據。", "significance": "Low"}],
                                 "analysis": "old", "status": "completed"}], "status": "completed"}
    old = {"matrix_id": "mx_1_old", "pid": PID, "criteria": "General", "created_at": "2026-10-01 09:00:00", "data": old_data}
    (study / "matrix_history" / "mx_1_old.json").write_text(json.dumps(old), encoding="utf-8")
    (study / "latest_matrix.json").write_text(json.dumps(old), encoding="utf-8")
    (study / "papers_comp").mkdir()
    (study / "papers_comp" / "v16.json").write_text(json.dumps({"criteria": "Methodology", "baseline": {"id": "base", "title": "Base"},
                                                                "comparisons": [{"target_paper_id": "pc", "content": "old text"}]}), encoding="utf-8")
    client = app.test_client()
    history = {h["matrix_id"]: h for h in client.get(f"/api/study/{PID}/matrix/history").json["history"]}
    assert history["mx_1_old"]["paper_titles"] == ["Base", "Paper B"]     # 舊記錄沒有 paper_titles 時第二篇不再是 Paper B 佔位字
    assert history["mx_1_old"]["paper_count"] == 2 and history["legacy::v16"]["is_legacy"] is True
    assert client.get(f"/api/study/{PID}/matrix/mx_1_old").json["record"]["data"] == old_data
    assert client.get(f"/api/study/{PID}/matrix/latest").json["record"]["data"] == old_data
    legacy = client.get(f"/api/study/{PID}/matrix/legacy::v16").json["record"]
    assert legacy["data"]["comparisons"][0]["content"] == "old text"
