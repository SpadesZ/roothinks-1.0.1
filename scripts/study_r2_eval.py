# Roothinks source maintenance contract
# 檔案路徑: scripts/study_r2_eval.py
# 目的: Study R2 真模型評估（協議 docs/engineering/STUDY_R2_EVAL_PROTOCOL_2026-10-26.md，預先登記）。
#       走產品同一條路：study_anchors.resolve → evidence_window → TutorEngine.process_anchored_query → verify_structured。
# 模式:
#   --dry-run              只組提示詞、估 token，不呼叫任何模型。
#   --simulate faithful|adversarial  用固定替身驗證工具本身；結果一律標 SIMULATION，不代表任何模型品質。
#   --live --confirm-spend --max-calls N   用 RootThinks 已設定的 task_7_qachat 綁定呼叫真模型；需要 Owner 授權的憑證與預算。
# 邊界: 只讀 evaluation/study_r2/dataset.json（合成文本）；不讀正式資料、sealed holdout 或私人文件；結果寫到 --out。
# 驗證: python -m pytest test/unit/test_study_r2_eval.py -q
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core_pro.study import study_anchors  # noqa: E402

DATASET = ROOT / "evaluation" / "study_r2" / "dataset.json"
# 預先登記的機器門檻（協議 §4）；看到結果後不得修改。
GATES = {
    "Q1_format_ok_rate": (">=", 0.95),
    "Q2_quote_text_match_rate": (">=", 0.90),
    "Q3_fabricated_quote_rate": ("<=", 0.05),
    "Q4_items_with_unsupported_numbers_rate": ("<=", 0.10),
    "Q5_uncertainty_provided_rate": (">=", 0.90),
    "Q6_numeric_condition_retention_rate": (">=", 0.80),
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_QUESTION = {"explain": "請解釋這段：它的意思、適用範圍與條件。",
             "critique": "請批判這段：它的假設、限制、替代解釋，以及還需要什麼證據？"}


def load_dataset(path=DATASET):
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def pages_for(item):
    return [{"page": 1, "blocks": [{"type": "body", "seq_id": f"p1_body_{n}", "content": text}
                                    for n, text in enumerate(item["paragraphs"], start=1)]}]


def prepare(item):
    """與產品相同：伺服器驗證選段、取證據視窗。選段必須是 exact，否則資料集本身有錯。"""
    meta = {"pid": "EVAL-p", "paper_id": item["id"], "source": "flowa_fusion", "source_sha256": "synthetic"}
    pages = pages_for(item)
    anchor = study_anchors.resolve(pages, {"quote": item["selection"], "ref": "p1-b2"}, meta)
    if anchor["status"] != "exact":
        raise ValueError(f"{item['id']}: selection did not resolve exactly ({anchor['status']})")
    return meta, anchor, study_anchors.evidence_window(pages, anchor)


def build_prompt(item, anchor, window):
    from app.llm_service.matching_tasks.task_7qachat import TutorEngine
    return TutorEngine._construct_anchored_prompt(None, _QUESTION[item["mode"]], item["mode"], window, anchor)


def estimate_tokens(text):
    # 粗估：英文約 4 字元／token、中文約 1.5 字元／token；兩者取較大者，避免低估花費。
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return max(len(text) / 4, (len(text) - cjk) / 4 + cjk / 1.5)


def _numbers(text):
    return set(_NUMBER.findall(text or ""))


def score_item(item, structured):
    """只做客觀、與語言無關的機器檢查；語意支持、過度宣稱與有用性留給人類評分（協議 §5）。"""
    passage = " ".join(item["paragraphs"])
    model_text = " ".join([structured.get("interpretation") or ""] + [p["text"] for p in structured.get("points") or []])
    given = sum(len(p["evidence"]) for p in structured.get("points") or []) + len(structured.get("rejected_quotes") or [])
    found = sum(len(p["evidence"]) for p in structured.get("points") or [])
    not_found = sum(1 for r in structured.get("rejected_quotes") or [] if r.get("reason") == "原文中找不到這段文字")
    key_numbers = set().union(*(_numbers(k) for k in item["key_conditions"]))
    return {
        "id": item["id"], "mode": item["mode"], "trap": item["trap"],
        "format_ok": bool(structured["system_check"].get("format_ok")),
        "quotes_given": given, "quotes_found": found, "quotes_not_in_source": not_found,
        "quotes_rejected_other": len(structured.get("rejected_quotes") or []) - not_found,
        "unsupported_numbers": sorted(_numbers(model_text) - _numbers(passage)),
        "uncertainty_provided": (structured.get("uncertainty") or {}).get("level") in ("low", "medium", "high"),
        "numeric_conditions": sorted(key_numbers),
        "numeric_conditions_retained": bool(key_numbers) and key_numbers <= _numbers(model_text),
    }


def aggregate(rows):
    n = len(rows)
    given = sum(r["quotes_given"] for r in rows)
    numeric = [r for r in rows if r["numeric_conditions"]]
    metrics = {
        "items": n,
        "Q1_format_ok_rate": sum(r["format_ok"] for r in rows) / n,
        "Q2_quote_text_match_rate": (sum(r["quotes_found"] for r in rows) / given) if given else None,
        "Q3_fabricated_quote_rate": (sum(r["quotes_not_in_source"] for r in rows) / given) if given else None,
        "Q4_items_with_unsupported_numbers_rate": sum(bool(r["unsupported_numbers"]) for r in rows) / n,
        "Q5_uncertainty_provided_rate": sum(r["uncertainty_provided"] for r in rows) / n,
        "Q6_numeric_condition_retention_rate": (sum(r["numeric_conditions_retained"] for r in numeric) / len(numeric)) if numeric else None,
        "quotes_given": given,
    }
    gates = {}
    for name, (op, threshold) in GATES.items():
        value = metrics[name]
        gates[name] = None if value is None else (value >= threshold if op == ">=" else value <= threshold)
    return metrics, gates


class FaithfulStub:
    """替身：逐字引用選段、只用原文數字。用來證明工具能算出「全通過」的情況。"""
    name = "SIMULATION-faithful"

    def __init__(self, item):
        self.item = item

    def __call__(self, prompt):
        sel = self.item["selection"]
        nums = " ".join(sorted(set().union(*(_numbers(k) for k in self.item["key_conditions"]))))
        return json.dumps({"interpretation": f"只在原文條件下成立（{nums}）。", "points": [
            {"text": f"範圍：{' / '.join(self.item['key_conditions'])}", "evidence": [{"ref": "E1", "quote": sel}]}],
            "uncertainty": {"level": "medium", "reasons": ["單一研究"]}, "open_questions": []}, ensure_ascii=False)


class AdversarialStub(FaithfulStub):
    """替身：捏造引文、加入原文沒有的數字、偶爾不回 JSON。用來證明工具抓得到這些問題。"""
    name = "SIMULATION-adversarial"

    def __call__(self, prompt):
        if self.item["id"] in ("S05", "S15", "S25"):
            return "這是一段沒有結構的回答，數字 99 也不在原文。"
        return json.dumps({"interpretation": "結果在 7 個實驗室都重現。", "points": [
            {"text": "已被證實", "evidence": [{"ref": "E1", "quote": "This result was replicated in seven independent laboratories."}]},
            {"text": "原文", "evidence": [{"ref": "E1", "quote": self.item["selection"]}]}],
            "uncertainty": {"level": "certain"}}, ensure_ascii=False)


def run(dataset, caller_for, label):
    rows, outputs = [], []
    for item in dataset["items"]:
        meta, anchor, window = prepare(item)
        prompt = build_prompt(item, anchor, window)
        raw = caller_for(item)(prompt)
        structured = study_anchors.verify_structured(raw, window, anchor, meta)
        rows.append(score_item(item, structured))
        outputs.append({"id": item["id"], "raw": raw, "structured": structured})
    metrics, gates = aggregate(rows)
    return {"label": label, "metrics": metrics, "gates": gates, "rows": rows, "outputs": outputs}


def dry_run(dataset):
    per = []
    for item in dataset["items"]:
        _, anchor, window = prepare(item)
        prompt = build_prompt(item, anchor, window)
        per.append({"id": item["id"], "prompt_chars": len(prompt), "prompt_tokens_est": round(estimate_tokens(prompt))})
    total_in = sum(p["prompt_tokens_est"] for p in per)
    # 輸出上限假設：每次最多 800 tokens（提示詞要求精簡 JSON）；花費 = token × 已設定供應商的單價（由 Owner 填）。
    return {"calls": len(per), "input_tokens_est": total_in, "output_tokens_cap_est": 800 * len(per), "per_item": per}


def live_caller(max_calls):
    """用 RootThinks 已設定的 task_7_qachat 綁定；沒有綁定或憑證時，TutorEngine 會丟錯，本工具不另外找憑證。"""
    from app.llm_service.matching_tasks.task_7qachat import TutorEngine
    engine = TutorEngine()
    budget = {"left": max_calls}

    def caller_for(item):
        def call(prompt):
            if budget["left"] <= 0:
                raise RuntimeError("已達 --max-calls 上限，停止呼叫")
            budget["left"] -= 1
            ok, text = engine.bus.dispatch_task("task_7_qachat", prompt, max_retries=0)
            if not ok:
                raise RuntimeError("provider request failed")
            return text
        return call
    return caller_for


def human_rating_template(result, path):
    """人類評分表（協議 §5）：評分者看不到是哪一次執行；每個找到的引文都要判斷是否支持論點。"""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["blind_id", "item", "point", "quote", "support(supports|partial|no)", "unsupported_assertions(count)",
                    "overclaim(y|n)", "scope_conditions_preserved(y|n)", "uncertainty_appropriate(1-3)", "usefulness(1-5)", "notes"])
        for n, out in enumerate(result["outputs"]):
            blind = hashlib.sha256(f"{result['label']}:{out['id']}".encode()).hexdigest()[:10]
            points = out["structured"].get("points") or [{"text": "", "evidence": []}]
            for p in points:
                quotes = p["evidence"] or [{"quote": ""}]
                for q in quotes:
                    w.writerow([blind, out["id"], p["text"], q["quote"], "", "", "", "", "", "", ""])


def main(argv=None):
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--simulate", choices=["faithful", "adversarial"])
    mode.add_argument("--live", action="store_true")
    ap.add_argument("--confirm-spend", action="store_true")
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    dataset, digest = load_dataset()
    header = {"protocol": "STUDY_R2_EVAL_PROTOCOL_2026-10-26", "dataset_sha256": digest, "gates": GATES}
    if args.dry_run:
        result = {**header, "mode": "dry-run", **dry_run(dataset)}
    elif args.simulate:
        stub = FaithfulStub if args.simulate == "faithful" else AdversarialStub
        result = {**header, "mode": "SIMULATION (not a model measurement)",
                  **run(dataset, lambda item: stub(item), stub.name)}
    else:
        if not args.confirm_spend:
            ap.error("--live 需要 --confirm-spend（Owner 已授權憑證與預算）")
        from app import create_app
        app = create_app()
        with app.app_context():
            result = {**header, "mode": "live", **run(dataset, live_caller(args.max_calls), "live")}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if "outputs" in result:
        human_rating_template(result, str(Path(args.out).with_suffix(".rating.csv")))
    print(json.dumps({k: result.get(k) for k in ("mode", "metrics", "gates", "input_tokens_est", "calls")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
