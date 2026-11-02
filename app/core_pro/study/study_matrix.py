# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_matrix.py
# 模組定位: Study 核心層；協調論文閱讀、筆記/矩陣與 AI tutor 的專案內狀態。
# 主要責任: 建立多篇比較矩陣（roothinks.study.matrix/2）：保留研究者選的論文順序與基準、研究者選的比較維度、
#           依章節挑選送給比較器的段落並記錄涵蓋範圍、逐字核對模型引文、給每格明確狀態。
# 上下游: Study routes 讀全文（與閱讀器相同的 _read_fulltext_source）後呼叫本層；本層呼叫 task_6comp.Comparator；
#         記錄由 route 寫入 data/<pid>/study/matrix_history；前端 study_matrix_view.js 渲染。
# 維護邊界: 引文只判定「原文中找得到」（study_anchors，逐字、不做 NFKC），不判定是否支持描述；缺資料不得排名或補值；
#           條件不同必須標「不可比較」並附理由；每篇對照論文仍只呼叫模型一次；不寫任何檔案（舊版在原始碼目錄寫除錯檔）。
# 驗證: python -m pytest test/unit/test_study_matrix_r7.py test/unit/test_study_failure_states.py -q
#路徑(./app/core_proc/study/study_matrix.py) #版本 v2.0 #更版時間 20261102
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

from app.core_pro.study import study_anchors
# [Import] 使用重構後的 Comparator
# dimension_id：維度 id 由文字決定（同一維度在不同記錄中 id 相同），不依位置。
from app.llm_service.matching_tasks.task_6comp import Comparator, DEFAULT_DIMENSIONS, dimension_id

logger = logging.getLogger("StudyMatrix")

MATRIX_SCHEMA = "roothinks.study.matrix/2"
CELL_STATUSES = ("reported", "not_reported", "uncertain", "not_comparable", "failed")
MAX_DIMENSIONS = 8
MAX_DIMENSION_CHARS = 40
# 每篇論文送給比較器的字數預算（舊版是 2,500 字，而且永遠是開頭）。可用環境變數調整，限制在 2,000–30,000。
DEFAULT_PAPER_CHAR_BUDGET = 9000
BLOCK_CHAR_CAP = 1800
# 單一文字欄位的保存上限；超過才截，而且一定標 truncated（舊版靜默截在 220／260 字）。
TEXT_SAFETY_CAP = 4000
MAX_QUOTES_PER_CELL = 3
MAX_MODEL_QUOTE_CHARS = 600
_REF_RE = re.compile(r"p\d{1,5}-b\d{1,6}")
# AI 的差異描述若含優劣字眼，畫面提示研究者自行判斷（不刪改模型文字）。
_JUDGEMENT_RE = re.compile(r"優於|劣於|較優|較差|較佳|較好|更好|更差|勝過|不如|outperform|superior|inferior|better than|worse than", re.I)

_HEADING_TYPES = {"title", "maintitle", "subtitle", "subsubtitle", "section_title", "section", "heading"}
_SKIP_TYPES = {"figure", "equation", "header"}
_NOT_EVIDENCE_SECTIONS = {"references", "appendix", "acknowledgements"}
# 章節標題 → 類別；順序有意義（"Results and Discussion" 歸結果，"Experimental setup" 歸資料）。
_SECTION_RULES = [
    ("references", r"^references?$|bibliography|參考文獻|引用文獻"),
    ("acknowledgements", r"acknowledg|致謝|誌謝"),
    ("appendix", r"appendix|supplementa|附錄"),
    ("abstract", r"^abstract|摘要"),
    ("limitations", r"limitation|threats? to validity|caveat|限制|侷限"),
    ("results", r"result|finding|evaluation|performance|結果|發現|評估|成效"),
    ("discussion", r"discussion|討論"),
    ("conclusion", r"conclusion|concluding|summary|future work|結論|總結|未來"),
    ("data", r"data|dataset|experiment|setup|setting|participant|sample|corpus|資料|實驗|樣本|受試|設定"),
    ("methods", r"method|approach|material|model|design|implementation|procedure|system|framework|algorithm|recommendation|方法|設計|系統|架構|實作|材料|建議"),
    ("related", r"related work|literature review|prior work|文獻回顧|相關研究"),
    ("introduction", r"introduction|background|motivation|緒論|前言|背景|引言"),
]
_SECTION_RE = [(name, re.compile(pattern, re.I)) for name, pattern in _SECTION_RULES]
_NUMBERING_RE = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*\.?|[IVXLC]+\.|[A-Z]\.|第[一二三四五六七八九十]+[章節])\s*")
# 維度關鍵字 → 優先章節（前面的優先）。自訂維度另以關鍵字重疊挑段落。
_DIMENSION_SECTIONS = [
    (re.compile(r"問題|目標|目的|動機|貢獻|question|aim|goal|objective|motivation|contribution", re.I),
     ["abstract", "front", "introduction", "conclusion"]),
    (re.compile(r"方法|設計|系統|模型|架構|演算|method|design|model|approach|architecture|algorithm", re.I),
     ["methods", "data", "front", "abstract"]),
    (re.compile(r"資料|樣本|實驗|設定|受試|條件|data|sample|dataset|experiment|setup|participant|condition", re.I),
     ["data", "methods", "results"]),
    (re.compile(r"結果|證據|效能|表現|發現|效果|result|evidence|performance|finding|effect|outcome", re.I),
     ["results", "discussion", "abstract", "front", "conclusion"]),
    (re.compile(r"限制|風險|威脅|侷限|偏誤|limitation|risk|threat|caveat|bias", re.I),
     ["limitations", "discussion", "conclusion", "results"]),
]
_FALLBACK_SECTIONS = ["results", "methods", "data", "discussion", "abstract", "front"]


def matrix_outcome(comparisons):
    rows = comparisons if isinstance(comparisons, list) else []
    success = sum(isinstance(row, dict) and not row.get('error')
                  and row.get('status') not in {'failed', 'degraded'}
                  and bool(row.get('analysis') or row.get('comparison_points') or row.get('content')) for row in rows)
    failed = len(rows) - success
    return {"status": "failed" if not success else ("partial" if failed else "completed"),
            "success_count": success, "failure_count": failed}


def paper_char_budget():
    try:
        value = int(os.environ.get("STUDY_MATRIX_PAPER_CHAR_BUDGET", DEFAULT_PAPER_CHAR_BUDGET))
    except (TypeError, ValueError):
        value = DEFAULT_PAPER_CHAR_BUDGET
    return max(2000, min(30000, value))


def normalize_dimensions(raw=None):
    """研究者選或自填的維度：去空白、去重、限長；沒有就用預設五維。回傳 [{id, label}]。"""
    items = raw if isinstance(raw, list) else []
    out, seen = [], set()
    for item in items:
        label = item.get("label") if isinstance(item, dict) else item
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:MAX_DIMENSION_CHARS]
        if not label:
            continue
        did = dimension_id(label)
        if did in seen:
            continue
        seen.add(did)
        out.append({"id": did, "label": label})
        if len(out) >= MAX_DIMENSIONS:
            break
    if not out:
        out = [{"id": dimension_id(label), "label": label} for label in DEFAULT_DIMENSIONS]
    return out


def parse_criteria(criteria):
    """generate_matrix 的第三個參數：舊呼叫是字串（焦點，用預設五維）；新呼叫是 {label, dimensions}。"""
    if isinstance(criteria, dict):
        dims = normalize_dimensions(criteria.get("dimensions"))
        label = str(criteria.get("label") or "").strip()[:80] or "、".join(d["label"] for d in dims)
        return label, dims
    if isinstance(criteria, list):
        dims = normalize_dimensions(criteria)
        return "、".join(d["label"] for d in dims), dims
    return (str(criteria or "General").strip()[:80] or "General"), normalize_dimensions(None)


def classify_section(heading):
    text = _NUMBERING_RE.sub("", str(heading or "")).strip()
    if not text:
        return None
    for name, pattern in _SECTION_RE:
        if pattern.search(text):
            return name
    return None


def _section_heading(text, paper_title=""):
    """章節標題才分類；論文標題、長句不算（"...Synthetic Sample Set" 不能被當成「資料」章節）。"""
    stripped = str(text or "").strip()
    if not stripped or _norm(stripped) == _norm(paper_title):
        return None
    words = len(_NUMBERING_RE.sub("", stripped).split())
    if words > 6 or (words <= 1 and len(stripped) > 24) or stripped.endswith((".", "。", ":", "：")):
        return None
    return classify_section(stripped)


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def paper_blocks(paper):
    """論文的正文段落，帶閱讀器相同的 ref（p{page}-b{index}）與所屬章節。

    來源優先序：pages（與閱讀器同一份全文）→ 舊 loader 的 content list → 純文字（以空行分段，沒有頁碼）。
    """
    blocks = []
    section, heading = "front", ""
    title = (paper.get("metadata") or {}).get("title", "") if isinstance(paper.get("metadata"), dict) else ""
    pages = paper.get("pages") if isinstance(paper.get("pages"), list) else None
    if pages:
        source = study_anchors.iter_blocks(pages)
        paged = True
    elif isinstance(paper.get("content"), list):
        source = ((None, idx, b) for idx, b in enumerate(paper["content"], start=1))
        paged = False
    else:
        raw = str(paper.get("content") or paper.get("summary") or "")
        source = ((None, idx, part) for idx, part in enumerate([p for p in re.split(r"\n\s*\n", raw) if p.strip()], start=1))
        paged = False
    for page, block_idx, block in source:
        kind = str(block.get("type") or "body").lower() if isinstance(block, dict) else "body"
        if isinstance(block, dict) and block.get("is_page_noise"):
            continue
        text = study_anchors.side_text(block, "original").strip()
        if not text or kind in _SKIP_TYPES:
            continue
        # 正文段落只有「編號開頭」或三個字以內才可能是漏標的章節標題（避免把短句當標題吃掉）。
        body_heading = len(text) <= 80 and (bool(_NUMBERING_RE.match(text)) or len(text.split()) <= 3)
        found = _section_heading(text, title) if (kind in _HEADING_TYPES or body_heading) else None
        if kind in _HEADING_TYPES or found:
            if found:
                section, heading = found, text[:120]
            continue
        blocks.append({"ref": f"p{page}-b{block_idx}" if paged else f"t{block_idx}", "page": page if paged else None,
                       "block_index": block_idx, "type": kind, "section": section, "heading": heading, "text": text})
    return blocks


def _tokens(label):
    text = str(label or "").lower()
    words = {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text)}
    cjk = re.sub(r"[^一-鿿]", "", text)
    words.update(cjk[i:i + 2] for i in range(max(0, len(cjk) - 1)))
    return words


def _section_prefs(label):
    for pattern, prefs in _DIMENSION_SECTIONS:
        if pattern.search(str(label or "")):
            return prefs
    return _FALLBACK_SECTIONS


def select_blocks(blocks, dimensions, budget=None):
    """依所選維度在預算內挑段落：每個維度輪流取「章節最相關、再看關鍵字」的下一段，剩餘預算依原文順序補滿。

    回傳 (送出的段落（原文順序）, 涵蓋紀錄)。涵蓋紀錄只描述「給了比較器什麼」，不代表論文其他部分沒有內容。
    """
    budget = budget or paper_char_budget()
    usable = [b for b in blocks if b["section"] not in _NOT_EVIDENCE_SECTIONS]
    chosen, used = {}, 0

    def take(block):
        nonlocal used
        if block["ref"] in chosen or used >= budget:
            return False
        room = budget - used
        size = min(len(block["text"]), BLOCK_CHAR_CAP)
        if size > room:
            if room < 400:
                return False
            size = room
        chosen[block["ref"]] = {"size": size, "partial": size < len(block["text"])}
        used += size
        return True

    ranked = []
    for dim in dimensions:
        prefs = _section_prefs(dim["label"])
        words = _tokens(dim["label"])
        scored = []
        for order, block in enumerate(usable):
            hit = sum(1 for w in words if w in block["text"].lower())
            rank = prefs.index(block["section"]) if block["section"] in prefs else len(prefs)
            if rank < len(prefs) or hit:
                scored.append((rank, -hit, order, block))
        ranked.append([item[-1] for item in sorted(scored, key=lambda x: x[:3])])
    progress = True
    while progress and used < budget:
        progress = False
        for queue in ranked:
            while queue:
                block = queue.pop(0)
                if take(block):
                    progress = True
                    break
    for block in usable:
        if used >= budget:
            break
        take(block)

    given = [dict(b, size=chosen[b["ref"]]["size"], partial=chosen[b["ref"]]["partial"]) for b in usable if b["ref"] in chosen]
    pages = sorted({b["page"] for b in given if b["page"] is not None})
    all_sections = [s for s in dict.fromkeys(b["section"] for b in usable)]
    given_sections = [s for s in dict.fromkeys(b["section"] for b in given)]
    coverage = {
        "strategy": "section_priority/1",
        "budget_chars": budget,
        "chars_total": sum(len(b["text"]) for b in usable),
        "chars_given": sum(b["size"] for b in given),
        "text_blocks_total": len(usable),
        "blocks_given": len(given),
        "blocks_partial": sum(1 for b in given if b["partial"]),
        "complete": len(given) == len(usable) and not any(b["partial"] for b in given),
        "pages": pages,
        "page_span": [pages[0], pages[-1]] if pages else None,
        "paged": any(b["page"] is not None for b in usable),
        "sections_given": given_sections,
        "sections_not_given": [s for s in all_sections if s not in given_sections],
        "blocks": [{"ref": b["ref"], "page": b["page"], "section": b["section"], "chars": b["size"],
                    "partial": b["partial"]} for b in given],
    }
    return given, coverage


def excerpt_text(given):
    """給比較器的文字：每段前面標位置與章節，讓模型引文時帶 ref。"""
    lines = []
    for b in given:
        text = b["text"][:b["size"]]
        if b["partial"]:
            text += " …[本段後文未提供]"
        lines.append(f"[{b['ref']} | {b['section']}] {text}")
    return "\n".join(lines)


def _cap_text(value):
    text = str(value or "").strip()
    if len(text) <= TEXT_SAFETY_CAP:
        return text, False, len(text)
    return text[:TEXT_SAFETY_CAP], True, len(text)


def _side_status(value):
    status = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if status in ("reported", "not_reported", "uncertain"):
        return status
    if status in ("n/a", "na", "missing", "none", "not_stated", "not_found"):
        return "not_reported"
    return "uncertain"


class QuoteChecker:
    """以閱讀器同一份全文逐字核對模型引文（study_anchors 規則）；找不到、太短或無法歸屬的一律排除。"""

    def __init__(self, paper, blocks, given_refs):
        self.paper = paper
        self.pages = paper.get("pages") if isinstance(paper.get("pages"), list) else None
        self.meta = paper.get("anchor_meta") if isinstance(paper.get("anchor_meta"), dict) else None
        self.blocks = blocks
        self.given_refs = set(given_refs)

    def check(self, ref, quote):
        quote = str(quote or "").strip()
        ref = str(ref or "").strip()
        if not quote:
            return None, "空白引文"
        if len(quote) > MAX_MODEL_QUOTE_CHARS:
            return None, "引文過長，無法歸屬到特定位置"
        if study_anchors.too_short_to_attribute(quote):
            return None, "太短，無法歸屬到特定位置"
        if self.pages is not None and self.meta:
            try:
                anchor = study_anchors.resolve(self.pages, {"quote": quote, "side": "original",
                                                            "ref": ref if _REF_RE.fullmatch(ref) else ""}, self.meta)
            except study_anchors.AnchorError as exc:
                return None, str(exc)
            if anchor.get("status") not in study_anchors.TEXT_FOUND:
                return None, ("多個段落都有這段文字，無法歸屬" if anchor.get("status") == "ambiguous" else "原文中找不到這段文字")
        else:
            hits = [b for b in self.blocks if study_anchors.locate(b["text"], quote)]
            if len(hits) != 1:
                return None, ("多個段落都有這段文字，無法歸屬" if hits else "原文中找不到這段文字")
            anchor = {"schema": study_anchors.ANCHOR_SCHEMA, "paper_id": self.paper.get("paper_id"), "side": "original",
                      "quote": quote, "ref": None, "page": None, "status": "exact", "match_kind": "quote",
                      "location": "unpaged"}
        if ref and anchor.get("ref") and anchor.get("ref") != ref:
            anchor["ref_corrected_from"] = ref[:16]
        if anchor.get("ref") and anchor["ref"] not in self.given_refs:
            # 原文確實有這段，但不在送給比較器的段落裡：照實標示。
            anchor["outside_given_blocks"] = True
        anchor["quote_match"] = "found"
        anchor["claim_support"] = "not_assessed"
        return anchor, None


def _build_side(raw, checker, failed_reason=None):
    """一篇論文在一個維度的格子。reported 必須至少有一段原文中找得到的引文，否則降為 uncertain。"""
    if failed_reason:
        return {"status": "failed", "text": "", "reason": failed_reason, "evidence": [], "rejected_quotes": [],
                "claim_support": "not_assessed"}
    raw = raw if isinstance(raw, dict) else {}
    model_status = _side_status(raw.get("status"))
    text, truncated, full_chars = _cap_text(raw.get("text"))
    if text.upper() in ("N/A", "NA") and model_status == "reported":
        model_status, text = "not_reported", ""
    evidence, rejected = [], []
    quotes = raw.get("quotes") if isinstance(raw.get("quotes"), list) else []
    for item in quotes[:MAX_QUOTES_PER_CELL + 3]:
        if not isinstance(item, dict):
            continue
        anchor, reason = checker.check(item.get("ref"), item.get("quote"))
        if anchor is not None and len(evidence) < MAX_QUOTES_PER_CELL:
            evidence.append(anchor)
        elif anchor is None:
            rejected.append({"ref": str(item.get("ref") or "")[:16], "quote": str(item.get("quote") or "")[:MAX_MODEL_QUOTE_CHARS],
                             "reason": reason})
    cell = {"status": model_status, "text": text, "reason": "", "evidence": evidence, "rejected_quotes": rejected,
            "claim_support": "not_assessed"}
    if truncated:
        cell.update(truncated=True, full_chars=full_chars)
    if model_status == "not_reported":
        # 缺資料不保留任何描述或引文，避免被讀成「有資料」。
        cell.update(text="", evidence=[], reason="AI 在提供的段落中沒有找到")
    elif model_status == "uncertain":
        cell["reason"] = str(raw.get("uncertain_reason") or "").strip()[:500] or "AI 標示為不確定"
    elif not evidence:
        cell.update(status="uncertain", status_from_model="reported",
                    reason="AI 說有報告，但沒有提供原文中找得到的引文")
    return cell


def _build_target(raw_dim, checker, base_cell, failed_reason=None):
    cell = _build_side((raw_dim or {}).get("target"), checker, failed_reason)
    if failed_reason:
        return cell
    comp = (raw_dim or {}).get("comparability") if isinstance((raw_dim or {}).get("comparability"), dict) else {}
    comp_status = str(comp.get("status") or "unclear").strip().lower().replace("-", "_")
    comp_status = comp_status if comp_status in ("comparable", "not_comparable", "unclear") else "unclear"
    comp_reason = str(comp.get("reason") or "").strip()[:500]
    cell["comparability"] = {"status": comp_status, "reason": comp_reason}
    if cell["status"] in ("reported", "uncertain") and comp_status == "not_comparable":
        cell["status_before_comparability"] = cell["status"]
        cell["status"] = "not_comparable"
        cell["reason"] = comp_reason or "AI 標示條件不同、不可直接比較，但沒有說明理由"
    relation, rel_truncated, rel_chars = _cap_text((raw_dim or {}).get("relation"))
    withheld = None
    if cell["status"] in ("not_reported", "failed") or base_cell.get("status") in ("not_reported", "failed"):
        withheld = "missing"
    elif cell["status"] == "not_comparable":
        withheld = "not_comparable"
    if withheld:
        # 缺資料或不可比較：不顯示任何差異描述（AI 原文另存，供稽核，不給畫面）。
        cell["relation"] = ""
        cell["relation_withheld"] = withheld
        if relation:
            cell["relation_withheld_text"] = relation
    else:
        cell["relation"] = relation
        if rel_truncated:
            cell.update(relation_truncated=True, relation_full_chars=rel_chars)
        if relation and _JUDGEMENT_RE.search(relation):
            cell["relation_has_judgement"] = True
    return cell


def _legacy_points(dimensions, base_cells, target_cells):
    """保留舊欄位（comparison_points／analysis），讓舊畫面、歷史清單與 chat 快取照常讀。"""
    labels = {"not_reported": "未報告", "failed": "失敗"}
    points, parts = [], []
    for dim in dimensions:
        b, t = base_cells[dim["id"]], target_cells[dim["id"]]
        bv = b["text"] or labels.get(b["status"], "不確定")
        tv = t["text"] or labels.get(t["status"], "不確定")
        synthesis = t.get("relation") or ("（不可比較）" + t.get("reason", "") if t["status"] == "not_comparable" else "")
        points.append({"dimension": dim["label"], "dimension_id": dim["id"], "baseline_view": bv, "target_view": tv,
                       "synthesis": synthesis, "baseline_status": b["status"], "target_status": t["status"]})
        parts.append(f"【{dim['label']}】\n基準: {bv}\n對照: {tv}\n差異: {synthesis or '（不顯示）'}")
    return points, "\n\n".join(parts)


class MatrixEngine:
    """
    Study Matrix Engine (v2.0)
    職責: 協調 Comparator 執行多文獻的「基準比較 (Baseline Strategy)」。
    流程:
    1. 接收 N 篇論文（順序＝研究者選的順序；第 0 篇是基準）。
    2. 每篇依所選維度挑段落並記錄涵蓋範圍。
    3. 並行呼叫 Comparator（每篇對照論文一次），結果依原順序放回（不依完成順序）。
    4. 逐字核對引文、給每格狀態，聚合為 roothinks.study.matrix/2；舊欄位保留。
    保存由 route 負責（不可變歷史記錄）。
    """
    def __init__(self, data_root="./data"):
        self.comparator = Comparator()
        self.data_root = data_root

    def _package(self, position, paper, dimensions):
        blocks = paper_blocks(paper)
        given, coverage = select_blocks(blocks, dimensions)
        meta = paper.get("anchor_meta") if isinstance(paper.get("anchor_meta"), dict) else {}
        coverage.update(source=meta.get("source"), source_sha256=meta.get("source_sha256"))
        pid = str(paper.get("paper_id") or f"paper_{position}")
        title = (paper.get("metadata") or {}).get("title") if isinstance(paper.get("metadata"), dict) else None
        return {
            "id": pid,
            "title": str(title or pid),
            "position": position,
            "text": excerpt_text(given),
            "excerpt_blocks": len(given),
            "total_blocks": coverage["text_blocks_total"],
            "coverage": coverage,
            "has_text": bool(given),
            "_checker": QuoteChecker(paper, blocks, [b["ref"] for b in given]),
        }

    def generate_matrix(self, pid, papers_data, criteria="Methodology"):
        """
        :param pid: Project ID
        :param papers_data: 論文清單（順序即欄位順序；第 0 篇是基準）
        :param criteria: 舊呼叫：焦點字串（預設五維）；新呼叫：{"label": str, "dimensions": [str]}
        """
        if len(papers_data) < 2:
            return {"error": "至少需要兩篇文獻 (Need > 1 papers)"}
        label, dimensions = parse_criteria(criteria)
        spec = {"label": label, "dimensions": dimensions}
        pkgs = [self._package(i, p, dimensions) for i, p in enumerate(papers_data)]
        base_pkg, target_pkgs = pkgs[0], pkgs[1:]
        logger.info("[matrix] pid=%s papers=%s dims=%s", pid, len(pkgs), len(dimensions))

        raw_results = [None] * len(target_pkgs)
        per_future_timeout_sec = int(os.environ.get("STUDY_MATRIX_FUTURE_TIMEOUT_SEC", "300"))
        total_wait_timeout_sec = int(os.environ.get("STUDY_MATRIX_TOTAL_TIMEOUT_SEC", "360"))
        model_calls = 0
        executor = ThreadPoolExecutor(max_workers=3)
        try:
            future_map = {}
            for idx, t_pkg in enumerate(target_pkgs):
                if not base_pkg["has_text"] or not t_pkg["has_text"]:
                    # 沒有全文就不呼叫模型：空白輸入只會換來猜測。
                    which = "基準論文" if not base_pkg["has_text"] else "這篇論文"
                    raw_results[idx] = {"error": "no_fulltext", "status": "failed", "target_paper_id": t_pkg["id"],
                                        "analysis": f"{which}沒有可用的全文，未進行比較。", "reason": f"{which}沒有可用的全文"}
                    continue
                future_map[executor.submit(self.comparator.compare_pair, _public(base_pkg), _public(t_pkg), spec)] = idx
                model_calls += 1
            completed = set()
            try:
                for future in as_completed(future_map, timeout=total_wait_timeout_sec):
                    completed.add(future)
                    idx = future_map[future]
                    try:
                        res = future.result(timeout=per_future_timeout_sec)
                        if not isinstance(res, dict):
                            res = {"error": f"Unexpected result type: {type(res).__name__}", "analysis": "無法生成比較結果"}
                    except Exception as exc:
                        logger.warning("[matrix] comparison thread failed for %s: %s", target_pkgs[idx]["id"], exc)
                        res = {"error": str(exc)[:300], "analysis": f"[系統錯誤] 無法生成比較: {str(exc)[:300]}"}
                    raw_results[idx] = res
            except FuturesTimeoutError:
                logger.warning("[matrix] timeout waiting futures after %ss", total_wait_timeout_sec)
            finally:
                for fut, idx in future_map.items():
                    if fut in completed:
                        continue
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    raw_results[idx] = {"error": f"timeout after {total_wait_timeout_sec}s",
                                        "analysis": "比較任務逾時，請稍後重試。"}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        comparisons = []
        for t_pkg, res in zip(target_pkgs, raw_results):
            comparisons.append(self._finish_pair(base_pkg, t_pkg, res or {"error": "missing_result"}, dimensions))
        baseline_cells = self._merge_baseline(comparisons, dimensions)
        for comp in comparisons:
            if comp.get("cells") and comp.get("status") != "failed" and not comp.get("legacy_result"):
                # 舊欄位以本對自己的基準描述組成（與舊記錄同義）；失敗的保留原本的失敗訊息。
                comp["comparison_points"], comp["analysis"] = _legacy_points(dimensions, comp["baseline_cells"], comp["cells"])
        found = sum(len(c.get("evidence") or []) for comp in comparisons for c in list((comp.get("cells") or {}).values())
                    + list((comp.get("baseline_cells") or {}).values()))
        rejected = sum(len(c.get("rejected_quotes") or []) for comp in comparisons for c in list((comp.get("cells") or {}).values())
                       + list((comp.get("baseline_cells") or {}).values()))
        final_matrix = {
            "schema": MATRIX_SCHEMA,
            "pid": pid,
            "criteria": label,
            "dimensions": dimensions,
            "paper_order": [p["id"] for p in pkgs],
            "baseline_id": base_pkg["id"],
            "papers": [{"paper_id": p["id"], "title": p["title"], "position": p["position"], "column_id": f"col-{p['position']}",
                        "role": "baseline" if p["position"] == 0 else "compared", "fulltext_available": p["has_text"],
                        "coverage": p["coverage"]} for p in pkgs],
            "baseline": {"id": base_pkg["id"], "title": base_pkg["title"]},
            "baseline_cells": baseline_cells,
            "comparisons": comparisons,
            "system_check": {"model_calls": model_calls, "quotes_found": found, "quotes_rejected": rejected,
                             "rule": "引文只核對是否逐字出現在原文（不判斷是否支持描述）；缺資料不排名；不可比較須附理由"},
        }
        final_matrix.update(matrix_outcome(comparisons))
        return final_matrix

    def _finish_pair(self, base_pkg, t_pkg, res, dimensions):
        failed_reason = None
        if res.get("error") or res.get("status") in ("failed", "degraded"):
            failed_reason = str(res.get("reason") or res.get("analysis") or res.get("error") or "比較失敗")[:300]
        out = {k: v for k, v in res.items() if k not in ("dimension_results", "missing_dimensions", "prompt_meta")}
        out.update(target_paper_id=t_pkg["id"], target_paper_title=t_pkg["title"], position=t_pkg["position"],
                   column_id=f"col-{t_pkg['position']}")
        if failed_reason:
            out["status"] = "failed"
        by_id = {}
        for item in res.get("dimension_results") or []:
            if isinstance(item, dict) and item.get("dimension_id"):
                by_id[item["dimension_id"]] = item
        if not failed_reason and not by_id:
            # 舊式比較器結果（只有 analysis／comparison_points）：沒有逐格資料，格子標不確定，不假裝有證據。
            out["legacy_result"] = True
        base_checker, t_checker = base_pkg["_checker"], t_pkg["_checker"]
        base_cells, cells = {}, {}
        for dim in dimensions:
            raw = by_id.get(dim["id"])
            reason = failed_reason
            if not reason and out.get("legacy_result"):
                raw = {"baseline": {"status": "uncertain", "uncertain_reason": "比較結果沒有逐維度資料"},
                       "target": {"status": "uncertain", "uncertain_reason": "比較結果沒有逐維度資料"}}
            elif not reason and raw is None:
                reason = "AI 回覆缺少這個維度"
            base_cells[dim["id"]] = _build_side((raw or {}).get("baseline"), base_checker, reason)
            cells[dim["id"]] = _build_target(raw, t_checker, base_cells[dim["id"]], reason)
        out["baseline_cells"], out["cells"] = base_cells, cells
        if res.get("prompt_meta"):
            out["input"] = res["prompt_meta"]
        return out

    @staticmethod
    def _merge_baseline(comparisons, dimensions):
        """基準欄：每個維度取順序最前、有原文引文的那一對的描述；各對判讀不一致時照實列出。"""
        merged = {}
        for dim in dimensions:
            options = [(c["target_paper_id"], (c.get("baseline_cells") or {}).get(dim["id"])) for c in comparisons]
            options = [(tid, cell) for tid, cell in options if isinstance(cell, dict)]
            pick = next(((tid, c) for tid, c in options if c["status"] == "reported"), None) \
                or next(((tid, c) for tid, c in options if c["status"] != "failed"), None) \
                or (options[0] if options else (None, {"status": "failed", "text": "", "reason": "沒有任何比較完成",
                                                       "evidence": [], "rejected_quotes": []}))
            cell = dict(pick[1])
            cell["source_pair"] = pick[0]
            statuses = {tid: c["status"] for tid, c in options}
            cell["pair_statuses"] = statuses
            if len({s for s in statuses.values() if s != "failed"}) > 1:
                cell["pair_disagreement"] = True
            merged[dim["id"]] = cell
        return merged


def _public(pkg):
    """送給 Comparator 的資料包（不含內部核對器）。"""
    return {k: v for k, v in pkg.items() if not k.startswith("_")}
