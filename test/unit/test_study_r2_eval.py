# Roothinks source maintenance contract
# 目的：Study R2 真模型評估工具本身的正確性（不呼叫任何模型）。
# 重點：資料集雜湊與協議一致（預先登記不可事後改）；每段選取都能被伺服器精確定位；
#       工具對「忠實」替身判全過、對「作假」替身判全不過，證明門檻量得到它要量的東西。
# 驗證：python -m pytest test/unit/test_study_r2_eval.py -q
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.study_r2_eval as ev

# 協議 docs/engineering/STUDY_R2_EVAL_PROTOCOL_2026-10-26.md 登記的雜湊；改資料集必須同時改協議並重新登記。
DATASET_SHA256 = "e4ef1759258e946b052dab5d2df739da59e48ca6f3a3be8ebafa83c789414c1d"


def test_dataset_is_the_preregistered_one_and_every_selection_resolves_exactly():
    dataset, digest = ev.load_dataset()
    assert digest == DATASET_SHA256
    assert len(dataset["items"]) == 30
    for item in dataset["items"]:
        _, anchor, window = ev.prepare(item)
        assert anchor["status"] == "exact" and window[0]["ref"] == "p1-b2"


def test_dataset_generator_reproduces_the_committed_file():
    import evaluation.study_r2.build_dataset as build
    text = json.dumps(build.build(), ensure_ascii=False, indent=1)
    assert text == Path(ev.DATASET).read_text(encoding="utf-8")


def test_faithful_simulation_passes_and_adversarial_fails_every_gate(tmp_path):
    dataset, _ = ev.load_dataset()
    good = ev.run(dataset, lambda item: ev.FaithfulStub(item), "faithful")
    bad = ev.run(dataset, lambda item: ev.AdversarialStub(item), "adversarial")
    assert all(good["gates"].values())
    assert not any(bad["gates"].values())
    assert bad["metrics"]["Q3_fabricated_quote_rate"] == 0.5 and bad["metrics"]["Q1_format_ok_rate"] == 0.9


def test_unsupported_numbers_and_superscripts_are_scored_language_independently():
    item = {"id": "X", "mode": "explain", "trap": "t", "paragraphs": ["a", "Films at 10² Pa were thinner.", "c"],
            "key_conditions": ["10² Pa"], "selection": "Films at 10² Pa were thinner."}
    structured = {"interpretation": "在 102 Pa 下較薄", "points": [], "rejected_quotes": [],
                  "uncertainty": {"level": "low"}, "system_check": {"format_ok": True}}
    row = ev.score_item(item, structured)
    assert row["unsupported_numbers"] == ["102"] and row["numeric_conditions_retained"] is False


def test_cli_refuses_live_without_spend_confirmation(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit):
        ev.main(["--live", "--out", str(tmp_path / "x.json")])
    assert not (tmp_path / "x.json").exists()


def test_simulation_output_is_labelled_and_writes_blind_rating_sheet(tmp_path):
    out = tmp_path / "sim.json"
    ev.main(["--simulate", "faithful", "--out", str(out)])
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["mode"].startswith("SIMULATION") and result["dataset_sha256"] == DATASET_SHA256
    sheet = out.with_suffix(".rating.csv").read_text(encoding="utf-8").splitlines()
    assert sheet[0].startswith("blind_id,item,point,quote,support") and len(sheet) == 31


# ---------------- 協議 v2（STUDY_EVAL_PROTOCOL_V2_2026-10-26）----------------

def test_v2_negative_controls_each_break_exactly_their_gate():
    dataset, _ = ev.load_dataset()
    controls = ev.negative_controls(dataset)
    assert set(controls) == set(ev.NEGATIVE_CONTROLS)
    assert all(c["as_expected"] for c in controls.values()), controls


def test_v2_faithful_simulation_passes_every_v2_gate():
    dataset, _ = ev.load_dataset()
    result = ev.run(dataset, lambda item: ev.FaithfulStub(item), "faithful")
    assert all(result["metrics"]["v2_gates"].values())


def test_evidence_labels_and_list_numbers_are_not_counted_as_added_numbers():
    item = {"id": "X", "mode": "explain", "trap": "t", "paragraphs": ["a", "Yield was 78 percent.", "c"],
            "key_conditions": ["78 percent"], "selection": "Yield was 78 percent."}
    structured = {"interpretation": "依 E1 與 E2，產率 78%。", "points": [{"text": "1. 範圍有限", "evidence": []}],
                  "rejected_quotes": [], "uncertainty": {"level": "low"}, "system_check": {"format_ok": True}}
    row = ev.score_item(item, structured)
    assert row["unsupported_numbers_v2"] == [] and row["unsupported_numbers"] != [], "v1 會把 E1／E2／列表編號誤算"


def test_overclaim_heuristic_respects_negation():
    assert ev.overclaim_hits("這項研究證實了對所有樣本有效") == ["證實", "所有"]
    assert ev.overclaim_hits("本研究無法證實，也不能證明因果") == []
    assert ev.overclaim_hits("結果未顯著") == []


def test_offline_rescore_reproduces_metrics_and_detects_tampering(tmp_path):
    out = tmp_path / "sim.json"
    ev.main(["--simulate", "adversarial", "--out", str(out)])
    original = json.loads(out.read_text(encoding="utf-8"))
    rescored = ev.rescore(str(out))
    assert rescored["metrics"]["Q3_fabricated_quote_rate"] == original["metrics"]["Q3_fabricated_quote_rate"]
    assert original["provenance"]["dataset_sha256"] == DATASET_SHA256 and len(original["provenance"]["code_sha"]) == 40
    original["outputs"][0]["raw"] += " 竄改"
    out.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="雜湊不符"):
        ev.rescore(str(out))


def test_cohen_kappa_on_rating_sheets(tmp_path):
    header = "blind_id,item,point,quote,support(supports|partial|no),unsupported_assertions(count),overclaim(y|n),scope_conditions_preserved(y|n),uncertainty_appropriate(1-3),usefulness(1-5),notes\n"
    rows_a = ["b1,S01,p,q1,supports,,,,,,", "b2,S02,p,q2,no,,,,,,", "b3,S03,p,q3,partial,,,,,,", "b4,S04,p,q4,supports,,,,,,"]
    rows_b = ["b1,S01,p,q1,supports,,,,,,", "b2,S02,p,q2,no,,,,,,", "b3,S03,p,q3,supports,,,,,,", "b4,S04,p,q4,supports,,,,,,"]
    (tmp_path / "a.csv").write_text(header + "\n".join(rows_a) + "\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text(header + "\n".join(rows_b) + "\n", encoding="utf-8")
    k = ev.cohen_kappa(str(tmp_path / "a.csv"), str(tmp_path / "b.csv"))
    assert k["pairs"] == 4 and k["observed_agreement"] == 0.75 and round(k["kappa"], 3) == 0.556
