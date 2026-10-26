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
# v1 預先登記的機器門檻（協議 v1 §4）；保持原樣以便重現 v1。
GATES = {
    "Q1_format_ok_rate": (">=", 0.95),
    "Q2_quote_text_match_rate": (">=", 0.90),
    "Q3_fabricated_quote_rate": ("<=", 0.05),
    "Q4_items_with_unsupported_numbers_rate": ("<=", 0.10),
    "Q5_uncertainty_provided_rate": (">=", 0.90),
    "Q6_numeric_condition_retention_rate": (">=", 0.80),
}
# v2（STUDY_EVAL_PROTOCOL_V2）：在任何真模型執行前登記。Q4 改用去除證據標籤後的數字；新增 Q7 中文過度宣稱。
GATES_V2 = {**GATES, "Q7_items_with_overclaim_rate": ("<=", 0.10)}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_EVIDENCE_LABEL = re.compile(r"\bE\d+\b|^\s*\d+[.、)]\s*", re.M)
# 確定性／普遍化用語；前面緊接否定詞時不算（「不能證明」「無法證實」）。這是啟發式代理，不取代人類評分。
OVERCLAIM_TERMS = ("證實", "證明", "確定", "必然", "一定", "所有", "普遍", "全部", "完全", "毫無疑問", "因果關係",
                   "已被重現", "已重現", "已驗證", "臨床上有效", "適用於所有", "顯著")
_NEGATION = ("不", "無", "未", "非", "沒", "尚未", "並不")
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


def _model_numbers(text):
    """模型文字中的數字；先移除 E1／E2 證據標籤與列表編號，避免誤報。"""
    return _numbers(_EVIDENCE_LABEL.sub(" ", text or ""))


def overclaim_hits(text):
    """回傳未被否定的確定性／普遍化用語。"""
    hits = []
    for term in OVERCLAIM_TERMS:
        start = (text or "").find(term)
        while start >= 0:
            before = text[max(0, start - 3):start]
            if not any(neg in before for neg in _NEGATION):
                hits.append(term)
                break
            start = text.find(term, start + 1)
    return hits


def score_item(item, structured):
    """只做客觀、與語言無關的機器檢查；語意支持、過度宣稱與有用性留給人類評分（協議 §5）。"""
    passage = " ".join(item["paragraphs"])
    pieces = [structured.get("interpretation") or ""] + [p["text"] for p in structured.get("points") or []]
    model_text = " ".join(pieces)
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
        "unsupported_numbers_v2": sorted(set().union(*(_model_numbers(piece) for piece in pieces)) - _numbers(passage)),
        "overclaim_terms": overclaim_hits(model_text),
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
    if all("overclaim_terms" in r for r in rows):
        v2 = dict(metrics)
        v2["Q4_items_with_unsupported_numbers_rate"] = sum(bool(r["unsupported_numbers_v2"]) for r in rows) / n
        v2["Q7_items_with_overclaim_rate"] = sum(bool(r["overclaim_terms"]) for r in rows) / n
        metrics["v2"] = v2
    gates = {}
    for name, (op, threshold) in GATES.items():
        value = metrics[name]
        gates[name] = None if value is None else (value >= threshold if op == ">=" else value <= threshold)
    if "v2" in metrics:
        gates_v2 = {}
        for name, (op, threshold) in GATES_V2.items():
            value = metrics["v2"][name]
            gates_v2[name] = None if value is None else (value >= threshold if op == ">=" else value <= threshold)
        metrics["v2_gates"] = gates_v2
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


class _SingleFault(FaithfulStub):
    """負向控制：每個替身只製造一種錯誤，用來確認每個門檻只抓到它該抓的問題（敏感度與特異度）。"""
    fault = ""

    def __call__(self, prompt):
        base = json.loads(super().__call__(prompt))
        if self.fault == "fabricated_quote":
            base["points"].append({"text": "另有支持", "evidence": [{"ref": "E1",
                                   "quote": "This effect was confirmed in an independent replication cohort."}]})
        elif self.fault == "added_number":
            base["interpretation"] += " 改善幅度約為 987654。"
        elif self.fault == "overclaim":
            base["interpretation"] += " 這已證實對所有樣本普遍有效。"
        elif self.fault == "no_uncertainty":
            base.pop("uncertainty")
        elif self.fault == "dropped_conditions":
            base = {"interpretation": "結果有改善。", "points": [], "uncertainty": {"level": "low", "reasons": []}}
        elif self.fault == "format_break":
            return "這不是 JSON。"
        return json.dumps(base, ensure_ascii=False)


NEGATIVE_CONTROLS = {
    "fabricated_quote": {"Q2_quote_text_match_rate", "Q3_fabricated_quote_rate"},
    "added_number": {"Q4_items_with_unsupported_numbers_rate"},
    "overclaim": {"Q7_items_with_overclaim_rate"},
    "no_uncertainty": {"Q5_uncertainty_provided_rate"},
    "dropped_conditions": {"Q6_numeric_condition_retention_rate"},
    "format_break": {"Q1_format_ok_rate", "Q5_uncertainty_provided_rate", "Q6_numeric_condition_retention_rate"},
}


def negative_controls(dataset):
    """每種單一錯誤跑一次；回報哪些 v2 門檻被打破。預期集合在 NEGATIVE_CONTROLS（執行前寫定）。"""
    out = {}
    for fault in NEGATIVE_CONTROLS:
        stub = type(f"Fault_{fault}", (_SingleFault,), {"fault": fault, "name": f"NEGATIVE-CONTROL-{fault}"})
        result = run(dataset, lambda item, stub=stub: stub(item), stub.name)
        failed = {k for k, v in result["metrics"]["v2_gates"].items() if v is False}
        out[fault] = {"failed_gates": sorted(failed), "expected": sorted(NEGATIVE_CONTROLS[fault]),
                      "as_expected": failed == NEGATIVE_CONTROLS[fault]}
    return out


def provenance(label, dataset_digest):
    import datetime
    import subprocess
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:
        sha, dirty = "unknown", None
    return {"label": label, "code_sha": sha, "worktree_dirty": dirty, "dataset_sha256": dataset_digest,
            "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "anchors_sha256": hashlib.sha256((ROOT / "app/core_pro/study/study_anchors.py").read_bytes()).hexdigest(),
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}


def run(dataset, caller_for, label):
    rows, outputs = [], []
    for item in dataset["items"]:
        meta, anchor, window = prepare(item)
        prompt = build_prompt(item, anchor, window)
        raw = caller_for(item)(prompt)
        structured = study_anchors.verify_structured(raw, window, anchor, meta)
        rows.append(score_item(item, structured))
        outputs.append({"id": item["id"], "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "raw_sha256": hashlib.sha256(str(raw).encode("utf-8")).hexdigest(), "raw": raw, "structured": structured})
    metrics, gates = aggregate(rows)
    return {"label": label, "metrics": metrics, "gates": gates, "rows": rows, "outputs": outputs}


def rescore(path):
    """離線重評：只用已存的模型原文重算，不呼叫任何模型；核對原文雜湊沒被改過。"""
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    dataset, digest = load_dataset()
    if saved.get("dataset_sha256") != digest:
        raise ValueError("結果檔的資料集雜湊與目前資料集不同，不能重評")
    raws = {o["id"]: o["raw"] for o in saved["outputs"]}
    for o in saved["outputs"]:
        if hashlib.sha256(str(o["raw"]).encode("utf-8")).hexdigest() != o.get("raw_sha256"):
            raise ValueError(f"{o['id']}: 模型原文與記錄的雜湊不符")
    return run(dataset, lambda item: (lambda prompt: raws[item["id"]]), f"rescore:{saved.get('label')}")


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


def _binding_description():
    """記錄 task_7_qachat 綁定的供應商與模型名稱（不記錄金鑰）。查不到時寫 unknown，不猜。"""
    try:
        from app.llm_service.llm_model import LLMModel
        binding = LLMModel().get_binding("task_7_qachat") if hasattr(LLMModel, "get_binding") else None
        if isinstance(binding, dict):
            return {k: binding.get(k) for k in ("provider", "model", "connection_name") if k in binding}
    except Exception:
        pass
    return "unknown"


def cohen_kappa(path_a, path_b, column="support(supports|partial|no)"):
    """兩位評分者在「引文是否支持論點」欄的 Cohen's kappa；只比對兩人都有填的列。"""
    import csv

    def load(path):
        with open(path, encoding="utf-8") as fh:
            return {(r["blind_id"], r["point"], r["quote"]): r[column].strip().lower() for r in csv.DictReader(fh) if r[column].strip()}
    a, b = load(path_a), load(path_b)
    keys = sorted(set(a) & set(b))
    if not keys:
        return {"pairs": 0, "kappa": None}
    labels = sorted({a[k] for k in keys} | {b[k] for k in keys})
    po = sum(a[k] == b[k] for k in keys) / len(keys)
    pe = sum((sum(a[k] == lab for k in keys) / len(keys)) * (sum(b[k] == lab for k in keys) / len(keys)) for lab in labels)
    return {"pairs": len(keys), "observed_agreement": po, "kappa": None if pe == 1 else (po - pe) / (1 - pe)}


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
    mode.add_argument("--negative-controls", action="store_true")
    mode.add_argument("--rescore", metavar="RESULT_JSON")
    mode.add_argument("--kappa", nargs=2, metavar=("RATER1_CSV", "RATER2_CSV"))
    ap.add_argument("--confirm-spend", action="store_true")
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    dataset, digest = load_dataset()
    header = {"protocol": "STUDY_EVAL_PROTOCOL_V2_2026-10-26 (v1 gates kept for comparison)", "dataset_sha256": digest,
              "gates": GATES, "gates_v2": GATES_V2}
    if args.kappa:
        result = {**header, "mode": "human-agreement", **cohen_kappa(*args.kappa)}
    elif args.negative_controls:
        result = {**header, "mode": "NEGATIVE CONTROLS (simulation)", "controls": negative_controls(dataset),
                  "provenance": provenance("negative-controls", digest)}
    elif args.rescore:
        result = {**header, "mode": "offline rescore", **rescore(args.rescore), "provenance": provenance("rescore", digest)}
    elif args.dry_run:
        result = {**header, "mode": "dry-run", **dry_run(dataset)}
    elif args.simulate:
        stub = FaithfulStub if args.simulate == "faithful" else AdversarialStub
        result = {**header, "mode": "SIMULATION (not a model measurement)",
                  **run(dataset, lambda item: stub(item), stub.name), "provenance": provenance(stub.name, digest)}
    else:
        if not args.confirm_spend:
            ap.error("--live 需要 --confirm-spend（Owner 已授權憑證與預算）")
        from app import create_app
        app = create_app()
        with app.app_context():
            prov = provenance("live", digest)
            prov["model_binding"] = _binding_description()
            result = {**header, "mode": "live", **run(dataset, live_caller(args.max_calls), "live"), "provenance": prov}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if "outputs" in result:
        human_rating_template(result, str(Path(args.out).with_suffix(".rating.csv")))
    print(json.dumps({k: result.get(k) for k in ("mode", "metrics", "gates", "input_tokens_est", "calls")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
