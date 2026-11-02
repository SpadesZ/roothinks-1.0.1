# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/task_6comp.py
# 模組定位: LLM task 業務層；組合特定任務 prompt，經 dispatcher 呼叫已綁定模型。
# 主要責任: 比較兩篇論文在研究者所選維度上的描述，要求逐字引文、明確狀態與可比性，並解析成結構化結果。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
#         呼叫端 study_matrix.MatrixEngine 先依章節挑段落（帶 p{page}-b{n} 標籤），回來後由它逐字核對引文。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
#           本層不截斷模型文字、不補缺的維度、不判斷優劣；引文是否存在由 study 層核對。
# 驗證: python -m pytest test/unit/test_study_matrix_r7.py test/unit/test_study_failure_states.py -q
#路徑(app/llm_service/matching_tasks/task_6comp.py) #版本 v3.0 #更版時間 20261102
import hashlib
import json
import re
import logging
from logging.handlers import RotatingFileHandler
import app.llm_service.llm_bus as llm_bus
from flask import has_app_context, current_app

# 設定 Log
logger = logging.getLogger("Task6_Comp")

# 添加檔案日誌以追蹤執行
import os
log_file = os.path.join(os.path.dirname(__file__), "task_6_debug.log")
_file_logger = logging.getLogger("Task6_Comp_File")
if not _file_logger.handlers:
    _handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _file_logger.addHandler(_handler)
    _file_logger.setLevel(logging.INFO)
    _file_logger.propagate = False

DEFAULT_DIMENSIONS = [
    "研究問題與目標",
    "方法與系統設計",
    "資料與實驗設定",
    "結果與證據強度",
    "限制與風險",
]
PROMPT_MARKER = "ROOTHINKS_MATRIX_PAIR/2"
# 直接呼叫 compare_pair、沒經過 MatrixEngine 挑段落時的單篇上限（照實記在 prompt_meta，不靜默）。
DIRECT_TEXT_LIMIT = 9000


def _is_debug_enabled():
    if has_app_context():
        return bool(current_app.debug)
    return os.environ.get("FLASK_DEBUG", "0") == "1" or os.environ.get("FLASK_ENV", "").lower() == "development"


def _debug_log(line):
    if not _is_debug_enabled():
        return
    _file_logger.info("%s", line)


def dimension_id(label):
    """維度 id 由文字決定（同一維度在不同記錄中 id 相同）；study_matrix 共用。"""
    norm = re.sub(r"\s+", " ", str(label or "")).strip().lower()
    return "dim-" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def _spec(criteria, defaults):
    """criteria 可為舊式字串（焦點，用預設維度）或 {label, dimensions:[{id,label}]}。"""
    if isinstance(criteria, dict) and isinstance(criteria.get("dimensions"), list) and criteria["dimensions"]:
        dims = []
        for d in criteria["dimensions"]:
            label = str((d.get("label") if isinstance(d, dict) else d) or "").strip()
            if label:
                dims.append({"id": (d.get("id") if isinstance(d, dict) and d.get("id") else dimension_id(label)), "label": label})
        return str(criteria.get("label") or "").strip() or "、".join(d["label"] for d in dims), dims
    return str(criteria or "General"), [{"id": dimension_id(label), "label": label} for label in defaults]


def _norm_label(text):
    return re.sub(r"[\s:：、，,.。()（）\[\]【】0-9]+", "", str(text or "")).lower()


class Comparator:
    """
    [Task 6] 多文獻比較引擎 (Multi-Paper Comparison Engine)
    職責:
    1. 接收兩篇論文的數據包 (Base vs Target)；text 是已挑選、帶位置標籤的段落。
    2. 組裝提示詞：研究者選的維度、逐字引文、明確狀態、可比性；不評優劣。
    3. 呼叫 LlmBus 執行推論（每對一次）。
    4. 解析成 dimension_results；不截斷文字、不補缺的維度（缺的列在 missing_dimensions）。
    """

    def __init__(self):
        self.bus = llm_bus.get_bus()
        self.default_dimensions = list(DEFAULT_DIMENSIONS)
        side = {"status": "reported | not_reported | uncertain",
                "text": "這篇論文在此維度的具體作法或數據（繁體中文；not_reported 時留空）",
                "quotes": [{"ref": "p2-b3", "quote": "從該篇段落逐字複製的原文句子"}],
                "uncertain_reason": "status 為 uncertain 時說明原因"}
        self.output_schema = {
            "dimensions": [{
                "dimension_id": "照抄 DIMENSIONS_JSON 的 id",
                "baseline": side,
                "target": side,
                "comparability": {"status": "comparable | not_comparable | unclear",
                                  "reason": "not_comparable 時必填：條件、母群、單位、指標或比較基線哪裡不同"},
                "relation": "中性描述兩者差異（哪裡不同、在什麼條件下）；不評優劣、不排名；任一方 not_reported 時留空",
            }],
            "overall_summary": "整體差異的中性摘要（100 字內，不評優劣）",
        }

    def compare_pair(self, base_pkg, target_pkg, criteria):
        """
        執行單一配對比較
        :param base_pkg: {id, title, text, excerpt_blocks?, total_blocks?}
        :param target_pkg: 同上
        :param criteria: 舊式焦點字串，或 {"label": str, "dimensions": [{"id","label"}]}
        :return: dict；成功時含 dimension_results（未核對引文），失敗時 status=failed
        """
        _debug_log(f"[compare_pair] START for {target_pkg['id']}")
        task_name = "task_6_comparison"
        label, dims = _spec(criteria, self.default_dimensions)
        prompt, meta = self._construct_prompt(base_pkg, target_pkg, label, dims)
        logger.info(f"[Task6.DEBUG] Starting comparison for {target_pkg['id']}")
        success, response_text = self.bus.dispatch_task(task_name, prompt, max_retries=2)
        _debug_log(f"[compare_pair] dispatch_task returned: success={success}")
        logger.info(f"[Task6.DEBUG] dispatch_task returned: success={success}, response_len={len(response_text) if isinstance(response_text, str) else 'N/A'}")

        if not success:
            logger.warning("[Task6] provider failed for %s", target_pkg['id'])
            # 字面統計只作診斷，不進維度正規化或聲稱可作科學比較。
            return {
                "error": "provider_failed", "status": "failed", "quality_mode": "heuristic",
                "target_paper_id": target_pkg['id'], "target_paper_title": target_pkg.get('title'),
                "comparison_points": [], "analysis": "模型比較失敗；僅有字元數診斷，不能作為研究結論。",
                "reason": "模型比較失敗",
                "diagnostics": {"baseline_chars": len(base_pkg.get('text', '')),
                                "target_chars": len(target_pkg.get('text', ''))},
                "prompt_meta": meta,
            }
        try:
            data = self._parse_json(response_text)
            results, missing = self._normalize_result(data, dims)
            if not results:
                raise ValueError("Missing comparison dimensions")
        except Exception:
            logger.exception("[Task6] invalid comparison response")
            return {"error": "invalid_model_response", "status": "failed", "quality_mode": "unavailable",
                    "target_paper_id": target_pkg['id'], "comparison_points": [], "reason": "模型回應無法解析",
                    "analysis": "模型回應無法解析，未完成科學比較。", "prompt_meta": meta}
        summary = str(data.get("overall_summary") or "").strip()
        points = [{"dimension": r["label"], "dimension_id": r["dimension_id"],
                   "baseline_view": r["baseline"].get("text") or "", "target_view": r["target"].get("text") or "",
                   "synthesis": r.get("relation") or ""} for r in results]
        return {
            "status": "completed", "quality_mode": "llm",
            "target_paper_id": target_pkg['id'], "target_paper_title": target_pkg.get('title'),
            "criteria": label, "dimension_results": results, "missing_dimensions": missing,
            "overall_summary": summary, "comparison_points": points,
            "analysis": "\n\n".join(f"【{p['dimension']}】\n基準: {p['baseline_view']}\n對照: {p['target_view']}\n差異: {p['synthesis']}"
                                    for p in points),
            "prompt_meta": meta,
        }

    def _excerpt(self, pkg):
        text = str(pkg.get('text') or '')
        if 'excerpt_blocks' in pkg:
            return text, None
        cut = text[:DIRECT_TEXT_LIMIT]
        return cut, (len(text) if len(text) > DIRECT_TEXT_LIMIT else None)

    def _construct_prompt(self, base, target, label, dims):
        """建構提示詞；回傳 (prompt, meta)。meta 只記長度與段落數，不記內容。"""
        base_text, base_cut = self._excerpt(base)
        target_text, target_cut = self._excerpt(target)

        def coverage_line(pkg):
            if 'excerpt_blocks' in pkg:
                return (f"These excerpts are the ONLY parts of this paper you can see "
                        f"({pkg.get('excerpt_blocks')} of {pkg.get('total_blocks')} text blocks). "
                        f"Text you were not given may still exist in the paper.")
            return "This is the only text of this paper you can see."

        dims_json = json.dumps([{"id": d["id"], "label": d["label"]} for d in dims], ensure_ascii=False)
        prompt = f"""{PROMPT_MARKER}
You assist a researcher who makes the final judgement. You compare and describe; you never decide which paper is better.
BASELINE_PAPER_ID: {base['id']}
TARGET_PAPER_ID: {target['id']}
FOCUS: {label}
DIMENSIONS_JSON: {dims_json}

[Paper A (Baseline)]:
Title: {base.get('title') or base['id']}
{coverage_line(base)} Each line starts with its location label [ref | section].
{base_text}

[Paper B (Target)]:
Title: {target.get('title') or target['id']}
{coverage_line(target)} Each line starts with its location label [ref | section].
{target_text}

[Rules]
1. Answer every dimension in DIMENSIONS_JSON, in that order, copying its "id" into "dimension_id".
2. For each paper and dimension choose a status:
   - "reported": the excerpts state it. Give 1-3 quotes copied EXACTLY, character for character, from THAT paper's excerpts (at least 5 words each), with the location label (e.g. "p2-b3") in "ref". Never paraphrase inside "quote".
   - "not_reported": the excerpts do not state it. Leave "text" empty. Do not guess or fill in from general knowledge.
   - "uncertain": something related is stated but incomplete or ambiguous; explain in "uncertain_reason".
3. "comparability": use "not_comparable" when conditions, populations, units, metrics or reference baselines differ, and give the concrete reason (e.g. "77 K vs 300 K", "% vs mW/K"). Use "unclear" if you cannot tell.
4. "relation": neutral description of what differs and under which conditions. Do not say which paper is better, do not rank, do not score. Leave it empty if either paper is "not_reported".
5. Language: Traditional Chinese (繁體中文) for text, reasons and relation; quotes stay in the original language.
6. Output STRICT JSON only, no markdown, following this schema:
{json.dumps(self.output_schema, ensure_ascii=False, indent=1)}
"""
        meta = {"prompt_version": PROMPT_MARKER, "prompt_chars": len(prompt), "dimensions": len(dims),
                "baseline_chars": len(base_text), "target_chars": len(target_text),
                "baseline_blocks": base.get('excerpt_blocks'), "target_blocks": target.get('excerpt_blocks')}
        if base_cut or target_cut:
            meta["direct_text_cut"] = {"limit": DIRECT_TEXT_LIMIT, "baseline_full_chars": base_cut, "target_full_chars": target_cut}
        return prompt, meta

    @staticmethod
    def _side(raw):
        raw = raw if isinstance(raw, dict) else {}
        quotes = []
        for q in raw.get("quotes") if isinstance(raw.get("quotes"), list) else []:
            if isinstance(q, dict) and str(q.get("quote") or "").strip():
                quotes.append({"ref": str(q.get("ref") or "").strip(), "quote": str(q.get("quote")).strip()})
            elif isinstance(q, str) and q.strip():
                quotes.append({"ref": "", "quote": q.strip()})
        return {"status": str(raw.get("status") or "").strip(), "text": str(raw.get("text") or "").strip(),
                "quotes": quotes, "uncertain_reason": str(raw.get("uncertain_reason") or "").strip()}

    def _normalize_result(self, data, dims):
        """把模型回覆對到研究者的維度；回傳 (results, 缺少的維度 id)。不截斷、不補值。"""
        if not isinstance(data, dict):
            raise ValueError("Model response is not an object")
        items = data.get("dimensions")
        if not isinstance(items, list) and isinstance(data.get("comparison_points"), list):
            # 舊格式回覆：只有文字、沒有引文與狀態；狀態交給 study 層依「有無原文引文」決定。
            items = []
            for p in data["comparison_points"]:
                if not isinstance(p, dict):
                    continue
                def old(view):
                    view = str(view or "").strip()
                    return {"status": "not_reported" if view.upper() in ("", "N/A", "NA") else "reported", "text": view}
                items.append({"dimension": p.get("dimension"), "baseline": old(p.get("baseline_view")),
                              "target": old(p.get("target_view")), "relation": p.get("synthesis")})
        if not isinstance(items, list):
            raise ValueError("Missing dimensions")
        by_id, by_label = {}, {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("dimension_id"):
                by_id.setdefault(str(item["dimension_id"]), item)
            for key in ("label", "dimension"):
                if item.get(key):
                    by_label.setdefault(_norm_label(item[key]), item)
        results, missing = [], []
        for dim in dims:
            src = by_id.get(dim["id"]) or by_label.get(_norm_label(dim["label"]))
            if src is None:
                # 只有在「唯一」包含關係時才對應，避免把別的維度錯配過來。
                cands = [v for k, v in by_label.items() if k and (k in _norm_label(dim["label"]) or _norm_label(dim["label"]) in k)]
                src = cands[0] if len(cands) == 1 else None
            if src is None:
                missing.append(dim["id"])
                continue
            comp = src.get("comparability") if isinstance(src.get("comparability"), dict) else {}
            results.append({"dimension_id": dim["id"], "label": dim["label"],
                            "baseline": self._side(src.get("baseline")), "target": self._side(src.get("target")),
                            "comparability": {"status": str(comp.get("status") or "unclear").strip(),
                                              "reason": str(comp.get("reason") or "").strip()},
                            "relation": str(src.get("relation") or "").strip()})
        return results, missing

    def _parse_json(self, text):
        """
        強健的 JSON 清洗器 (Anti-Hallucination / Anti-Markdown)
        舊版遇到不帶 json 標記的 ``` 圍欄會取到空字串；改為取圍欄內容。
        """
        text = str(text or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fence:
            text = fence.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
        return json.loads(text)

# 用於測試的 main block
if __name__ == "__main__":
    logger.info("Task 6 Comparator Module Loaded.")
