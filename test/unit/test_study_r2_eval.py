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
