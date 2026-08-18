# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/task_3bc_scout.py
# 模組定位: LLM task 業務層；Literature 對話式找文獻的 B/C 兩隻搜尋腳與互檢。
# 主要責任: 以 Google Search grounding 找候選文獻、B↔C 交叉查核、再回查真實學術來源剔除幻覺。
# 上下游: literature_chat_routes -> task_3a_litchat -> 本檔 -> dispatcher(grounding) / ContextSearcher。
# 維護邊界:
#   - 不得記錄 API key 或完整 prompt；provider error 不可靜默吞掉，一律進 meta.stage_errors。
#   - NOTE(NOTE-039)：**機械驗證（resolve_reference）是主防線，互檢是輔助**。兩個模型可以一起編出
#     同一篇不存在的論文而互檢一致通過，但編出來的東西在 Crossref/OpenAlex/PubMed/
#     arXiv 一定查不到。任何「因為模型很有把握所以保留」的例外都不准加。
#   - 對外顯示連結只走 doi.org / PubMed / arXiv / Google Scholar 四種；
#     查不到 canonical 來源時退回 Scholar 查詢連結，不得直接貼出版社 landing page。
# 驗證: python -m pytest test/unit/test_literature_chat_pipeline.py -q
#路徑(./app/llm_service/matching_tasks/task_3bc_scout.py) #版本 v0.1 #更版時間 20260817-1200
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import quote_plus

from app.llm_service.llm_dispatcher import dispatch_task
from app.llm_service.matching_tasks.task_3search import ContextSearcher

logger = logging.getLogger("app.llm_service.matching_tasks.task_3bc_scout")

SCOUT_TASKS = {"B": "task_3b_scout", "C": "task_3c_scout"}

# B/C 綁不同 Gemini 型號還不夠 —— 給不同檢索角度，兩隻腳才會踩到不同的文獻，
# 互檢也才有東西可檢。同角度同模型的「互檢」只是把同一個答案抄兩遍。
SCOUT_ANGLES = {
    "B": "偏重機制、神經科學實證與原始研究（primary studies）。",
    "C": "偏重方法學、系統性綜述、跨領域應用與近三年的新進展。",
}

# 搜尋限定範圍。Gemini grounding 沒有「限制網域」的 API 參數，只能寫進查詢字串引導；
# 真正的把關在 _verify_one() 的回查，這裡只是提高命中率。
SITE_SCOPE = (
    "site:pubmed.ncbi.nlm.nih.gov OR site:arxiv.org OR site:scholar.google.com"
)

_ALLOWED_LINK_HOSTS = {
    "doi.org",
    "pubmed.ncbi.nlm.nih.gov",
    "arxiv.org",
    "www.arxiv.org",
    "scholar.google.com",
}


def _read_int_env(key: str, default_val: int) -> int:
    try:
        val = int(str(os.environ.get(key, default_val)).strip())
        return val if val > 0 else default_val
    except Exception:
        return default_val


def scout_limit() -> int:
    return _read_int_env("LIT_CHAT_SCOUT_LIMIT", 10)


def max_papers() -> int:
    return _read_int_env("LIT_CHAT_MAX_PAPERS", 20)


def stage_timeout_sec() -> int:
    """互檢與回查的時限。兩者都不開 grounding，本來就快。"""
    return _read_int_env("LIT_CHAT_STAGE_TIMEOUT_SEC", 60)


def search_timeout_sec() -> int:
    """
    grounded 檢索的時限，另外開一個而不是共用 stage timeout。

    實測（2026-08-17）：gemini-flash-latest 跑完 9 條搜尋查詢約 90 秒，
    gemini-3.1-pro-preview 在 75 秒內跑不完直接被砍。grounded 檢索是唯一
    要等外部搜尋往返的階段，跟後面兩個純文字階段用同一個數字只會兩邊都不對。
    """
    return _read_int_env("LIT_CHAT_SEARCH_TIMEOUT_SEC", 150)


def total_budget_sec() -> int:
    """
    整輪搜尋的總預算。各階段的時限相加會超過 gunicorn 的 --timeout（300s），
    所以要有一個總閘門：前面的階段跑久了，後面的階段就只能拿剩下的時間。
    留約 90 秒給 A 組稿（adapter 自己的 timeout 是 90s）。
    """
    return _read_int_env("LIT_CHAT_TOTAL_BUDGET_SEC", 210)


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    """
    從模型輸出中取出第一個 JSON 物件。

    grounding 與 JSON mode 在 Gemini API 互斥，所以這條路徑的回應一定是自由文字，
    常見包在 ```json 圍籬裡或前後帶說明句 —— 必須容忍，但解不出來就回空，
    不要用正則硬拼出一個看起來像 JSON 的東西。
    """
    text = str(raw_text or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    blob = text[start : end + 1]
    blob = re.sub(r",\s*([\]}])", r"\1", blob)  # 去掉結尾多餘逗號
    try:
        parsed = json.loads(blob)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_jobs(
    jobs: Dict[str, Callable[[], Any]], timeout_sec: int, max_workers: int = 0
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    並行跑 jobs，共用一個 deadline。逾時或拋錯的 job 進 errors 而不是消失。
    max_workers=0 表示不限（每個 job 一條）；打外部 API 時要給上限，別壓垮對方。
    回傳 (results, errors)。
    """
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    if not jobs:
        return results, errors

    deadline = time.time() + max(1, timeout_sec)
    workers = min(max_workers, len(jobs)) if max_workers > 0 else len(jobs)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, fut in futures.items():
            try:
                results[name] = fut.result(timeout=max(0.1, deadline - time.time()))
            except FutureTimeoutError:
                errors[name] = f"timeout after {timeout_sec}s"
            except Exception as e:
                errors[name] = str(e)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results, errors


# ---------------------------------------------------------------------------
# Stage 1: 各自上網搜尋
# ---------------------------------------------------------------------------
def _build_scout_prompt(query: str, role: str, limit: int) -> str:
    """
    NOTE(NOTE-039): 這段**刻意是開放式散文提問，不要求 JSON**。
    在 grounded 請求裡要求「只輸出 JSON」會讓 Gemini 整個跳過搜尋（實測見 run_scout）。
    結構化交給第二段 _build_extract_prompt() 處理。
    """
    angle = SCOUT_ANGLES.get(role, "")
    return (
        "You are an academic literature scout. Use Google Search to find the most "
        "relevant peer-reviewed papers for the research topic below, then write a "
        "short survey of what you actually found.\n\n"
        f"Research topic: {query}\n"
        f"Search angle: {angle}\n"
        f"Restrict your queries to: {SITE_SCOPE}\n\n"
        "Requirements:\n"
        f"- Cover at most {limit} papers. Quality over quantity: if the search returns "
        "little of value, report fewer papers rather than padding the list.\n"
        "- For every paper give: full title, first authors, publication year, venue, "
        "and the DOI **only if it appears in the search results** (never guess or "
        "reconstruct a DOI).\n"
        "- Only report papers you actually saw in the search results. Do not add "
        "papers from memory.\n"
        "- For each paper add one sentence on why it matters for this topic.\n"
        "- Write it as normal prose or a numbered list. Do not output JSON."
    )


def _normalize_scout_item(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    title = str(raw.get("title") or "").strip()
    if not title:
        return {}
    authors = raw.get("authors")
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r";|,", authors) if a.strip()]
    elif not isinstance(authors, list):
        authors = []
    return {
        "title": title,
        "authors": [str(a).strip() for a in authors if str(a).strip()][:10],
        "year": raw.get("year"),
        "venue": str(raw.get("venue") or "").strip(),
        "doi": str(raw.get("doi") or "").strip(),
        "url": str(raw.get("url") or "").strip(),
        "why": str(raw.get("why") or "").strip()[:120],
    }


def _build_extract_prompt(survey_text: str, limit: int) -> str:
    return (
        "把下面這份文獻檢索回顧整理成結構化清單。\n\n"
        f"=== 回顧全文 ===\n{survey_text}\n=== 全文結束 ===\n\n"
        "規則：\n"
        "1. **只能抽取回顧中實際提到的論文**，不得補充任何回顧沒寫到的文獻。\n"
        f"2. 最多 {limit} 篇。\n"
        "3. 回顧沒有明講的 DOI 一律填空字串，年份不確定填 null。**不要猜**。\n"
        "4. why 用繁體中文摘出回顧給的理由，40 字以內。\n\n"
        "只輸出 JSON，不要任何說明文字：\n"
        '{"papers":[{"title":"","authors":[""],"year":2020,"venue":"",'
        '"doi":"","url":"","why":""}]}'
    )


def run_scout(role: str, query: str, limit: int = 0) -> Dict[str, Any]:
    """
    單隻搜尋腳。回 {"items": [...], "grounding": {...}}。

    NOTE(NOTE-039): **兩段式，而且不能合併成一次呼叫。**
    實測（gemini-flash-latest / gemini-3.1-pro-preview，2026-08-17）：
    同一個 grounded 請求只要在 prompt 裡要求「只輸出 JSON」，Gemini 就**完全跳過搜尋** ——
    回應裡連 `groundingMetadata` 都不存在，直接憑記憶編出 title/DOI，而且 HTTP 200、
    沒有任何錯誤訊息。改成開放式提問則穩定拿到 12 個 groundingChunks 與 5 條 webSearchQueries。
    所以：第一段開放式提問負責「真的上網」，第二段不帶 tools 負責「轉成 JSON」。

    provider 失敗或**沒有實際搜尋**一律拋例外，交由 _run_jobs 記進 stage_errors。
    回空清單假裝「這次剛好沒找到」會讓設定錯誤看起來像正常結果。
    """
    task_id = SCOUT_TASKS.get(role)
    if not task_id:
        raise ValueError(f"Unknown scout role: {role}")

    limit = limit or scout_limit()

    # 第一段：grounded，開放式提問，讓模型真的去搜。
    survey = dispatch_task(
        task_id, _build_scout_prompt(query, role, limit), grounding=True
    )
    if not survey.get("ok"):
        raise RuntimeError(survey.get("msg") or "scout survey dispatch failed")

    grounding = survey.get("grounding") or {}
    chunk_count = int(grounding.get("chunk_count") or 0)
    if chunk_count <= 0:
        # 唯一能證明「這批文獻來自搜尋而非記憶」的硬證據就是 groundingChunks。
        # 沒有就是沒搜尋，不得當成「剛好沒找到」而放行。
        raise RuntimeError(
            "grounding 未生效：回應沒有任何 groundingChunks，代表模型是憑記憶作答"
        )

    survey_text = str(survey.get("text") or "").strip()
    if not survey_text:
        raise RuntimeError("grounded 回應為空")

    # 第二段：不帶 tools，純抽取。這裡才可以要求 JSON。
    extracted = dispatch_task(task_id, _build_extract_prompt(survey_text, limit))
    if not extracted.get("ok"):
        raise RuntimeError(extracted.get("msg") or "scout extract dispatch failed")

    parsed = _extract_json_object(extracted.get("text", ""))
    items: List[Dict[str, Any]] = []
    for raw in (parsed.get("papers") or [])[:limit]:
        item = _normalize_scout_item(raw)
        if item:
            item["found_by"] = role
            items.append(item)

    logger.info(
        "[scout %s] %d items from %d grounding chunks", role, len(items), chunk_count
    )
    return {"items": items, "grounding": grounding, "survey": survey_text}


# ---------------------------------------------------------------------------
# Stage 2: B 檢查 C、C 檢查 B
# ---------------------------------------------------------------------------
def _build_review_prompt(query: str, peer_items: List[Dict[str, Any]]) -> str:
    compact = [
        {
            "index": idx,
            "title": it.get("title", ""),
            "authors": (it.get("authors") or [])[:3],
            "year": it.get("year"),
            "venue": it.get("venue", ""),
            "doi": it.get("doi", ""),
        }
        for idx, it in enumerate(peer_items)
    ]
    return (
        "你是文獻查核員。以下是另一個搜尋代理提出的論文清單，請逐筆判斷是否可疑。\n\n"
        f"研究主題：{query}\n"
        f"待查核清單：{json.dumps(compact, ensure_ascii=False)}\n\n"
        "判定為 suspect 的情形：\n"
        "- 標題、作者、年份、期刊的組合不合理，像是拼湊出來的\n"
        "- 與研究主題明顯無關\n"
        "- DOI 格式錯誤，或明顯與標題／期刊不相符\n"
        "其餘一律 ok。不確定就給 ok，並在 reason 說明疑慮——寧可交給後續回查驗證。\n\n"
        "只輸出 JSON，不要任何說明文字：\n"
        '{"reviews":[{"index":0,"verdict":"ok","reason":""}]}'
    )


def cross_review(
    reviewer_role: str, query: str, peer_items: List[Dict[str, Any]]
) -> Dict[int, Dict[str, str]]:
    """回 {index: {"verdict": "ok"|"suspect", "reason": str}}。"""
    if not peer_items:
        return {}
    task_id = SCOUT_TASKS.get(reviewer_role)
    if not task_id:
        raise ValueError(f"Unknown reviewer role: {reviewer_role}")

    result = dispatch_task(task_id, _build_review_prompt(query, peer_items))
    if not result.get("ok"):
        raise RuntimeError(result.get("msg") or "cross_review dispatch failed")

    parsed = _extract_json_object(result.get("text", ""))
    out: Dict[int, Dict[str, str]] = {}
    for row in parsed.get("reviews") or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except Exception:
            continue
        if not 0 <= idx < len(peer_items):
            continue
        verdict = str(row.get("verdict") or "ok").strip().lower()
        out[idx] = {
            "verdict": "suspect" if verdict == "suspect" else "ok",
            "reason": str(row.get("reason") or "").strip()[:160],
        }
    return out


# ---------------------------------------------------------------------------
# Stage 3: 回查真實來源
# ---------------------------------------------------------------------------
def _dedupe_key(item: Dict[str, Any]) -> str:
    doi = ContextSearcher.normalize_doi(item.get("doi") or "")
    if doi:
        return f"doi:{doi.lower()}"
    norm = re.sub(r"[^a-z0-9]+", "", str(item.get("title") or "").lower())
    return f"title:{norm[:120]}"


def _scholar_url(title: str) -> str:
    return f"https://scholar.google.com/scholar?q={quote_plus(str(title or '')[:300])}"


def _display_link(resolved: Dict[str, Any], title: str) -> Tuple[str, str]:
    """
    決定要顯示哪個連結。回 (url, kind)。

    只允許 doi.org / PubMed / arXiv；其餘（例如 OpenAlex 給的出版社 landing page）
    一律退回 Google Scholar 查詢連結。理由：這個功能的承諾是「連結只到
    Scholar/PubMed/arXiv」，貼一個沒被驗證過的出版社網址等於默默違約。
    """
    canonical = str(resolved.get("canonical_url") or "").strip()
    if canonical:
        host = re.sub(r"^https?://", "", canonical).split("/")[0].lower()
        if host in _ALLOWED_LINK_HOSTS:
            if host == "doi.org":
                return canonical, "doi"
            if "pubmed" in host:
                return canonical, "pubmed"
            if "arxiv" in host:
                return canonical, "arxiv"
            return canonical, "scholar"
    return _scholar_url(title), "scholar"


def _verify_one(searcher: ContextSearcher, item: Dict[str, Any]) -> Dict[str, Any]:
    resolved = searcher.resolve_reference(
        title=item.get("title", ""),
        authors=item.get("authors") or [],
        year=item.get("year"),
        doi=item.get("doi", ""),
    )
    if not resolved:
        return {}

    url, kind = _display_link(resolved, resolved.get("title") or item.get("title", ""))
    return {
        "title": resolved.get("title", ""),
        "authors": resolved.get("authors") or [],
        "year": resolved.get("year"),
        "venue": resolved.get("venue", ""),
        "doi": resolved.get("doi", ""),
        "url": url,
        "link_kind": kind,
        "scholar_url": _scholar_url(resolved.get("title") or item.get("title", "")),
        "abstract": str(resolved.get("abstract") or "")[:4000],
        "citations": int(resolved.get("citations") or 0),
        "source": resolved.get("source", ""),
        "match": resolved.get("match", ""),
        "match_score": resolved.get("match_score", 0.0),
        "apa": resolved.get("apa", ""),
        "why": item.get("why", ""),
        "found_by": item.get("found_by", ""),
        "consensus": item.get("consensus", ""),
        "peer_verdict": item.get("peer_verdict", "ok"),
        "peer_reason": item.get("peer_reason", ""),
    }


def verify_candidates(
    items: List[Dict[str, Any]], timeout_sec: int = 0
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    對每筆候選回查 Crossref/OpenAlex/PubMed/arXiv。
    回 (verified, dropped)；dropped 保留標題與原因，讓使用者看得到剔除了什麼。
    """
    if not items:
        return [], []

    searcher = ContextSearcher()
    timeout_sec = timeout_sec or stage_timeout_sec()
    jobs = {
        str(idx): (lambda it=item: _verify_one(searcher, it))
        for idx, item in enumerate(items)
    }
    # 對外部 API 併發：每個 finder 內部已有 6s timeout 與退避，6 條夠用且不會壓垮對方。
    results, errors = _run_jobs(jobs, timeout_sec, max_workers=6)

    verified: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    for idx, item in enumerate(items):
        key = str(idx)
        if key in errors:
            dropped.append(
                {"title": item.get("title", ""), "reason": f"驗證失敗：{errors[key]}"}
            )
            continue
        hit = results.get(key) or {}
        if hit:
            verified.append(hit)
        else:
            dropped.append(
                {
                    "title": item.get("title", ""),
                    "reason": "Crossref/OpenAlex/PubMed/arXiv 均查無此文，視為幻覺剔除",
                }
            )
    return verified, dropped


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def _merge_and_mark(
    items_b: List[Dict[str, Any]],
    items_c: List[Dict[str, Any]],
    review_of_b: Dict[int, Dict[str, str]],
    review_of_c: Dict[int, Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    合併去重、標共識，並套用互檢剔除規則。

    剔除規則：**只有一隻腳找到、而且對方判定 suspect** 才丟。
    兩隻腳都獨立找到的論文即使被標 suspect 也留著送去回查 —— 互檢的長處是抓
    「真實但不相干」，判斷相關性本來就會有分歧，讓機械驗證與使用者自己決定。
    """
    merged: Dict[str, Dict[str, Any]] = {}
    dropped: List[Dict[str, str]] = []

    for idx, item in enumerate(items_b):
        entry = dict(item)
        verdict = review_of_b.get(idx) or {}
        entry["peer_verdict"] = verdict.get("verdict", "ok")
        entry["peer_reason"] = verdict.get("reason", "")
        merged[_dedupe_key(entry)] = entry

    for idx, item in enumerate(items_c):
        entry = dict(item)
        verdict = review_of_c.get(idx) or {}
        entry["peer_verdict"] = verdict.get("verdict", "ok")
        entry["peer_reason"] = verdict.get("reason", "")
        key = _dedupe_key(entry)
        if key in merged:
            existing = merged[key]
            existing["consensus"] = "both"
            # 兩邊都找到時，補上任一邊有而另一邊缺的欄位（DOI 常常只有一邊給）。
            for field in ("doi", "venue", "year", "url"):
                if not existing.get(field) and entry.get(field):
                    existing[field] = entry[field]
            if entry.get("why") and not existing.get("why"):
                existing["why"] = entry["why"]
            # 只要有一邊的互檢說 ok，就不算被標記。
            if existing.get("peer_verdict") == "suspect" and entry.get("peer_verdict") == "ok":
                existing["peer_verdict"] = "ok"
        else:
            merged[key] = entry

    out: List[Dict[str, Any]] = []
    for entry in merged.values():
        entry.setdefault("consensus", "")
        if not entry["consensus"]:
            entry["consensus"] = "b_only" if entry.get("found_by") == "B" else "c_only"
        if entry["consensus"] != "both" and entry.get("peer_verdict") == "suspect":
            dropped.append(
                {
                    "title": entry.get("title", ""),
                    "reason": f"互檢標記可疑且無第二方佐證：{entry.get('peer_reason', '')}".strip("："),
                }
            )
            continue
        out.append(entry)
    return out, dropped


def _rank(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def sort_key(p: Dict[str, Any]):
        consensus_rank = 0 if p.get("consensus") == "both" else 1
        doi_rank = 0 if p.get("doi") else 1
        return (
            consensus_rank,
            doi_rank,
            -float(p.get("match_score") or 0.0),
            -int(p.get("citations") or 0),
        )

    return sorted(papers, key=sort_key)


def run_debate_search(query: str, limit: int = 0) -> Dict[str, Any]:
    """
    B/C 搜尋 -> 互檢 -> 回查驗證的完整流程。

    :return: {"papers": [...], "dropped": [...], "meta": {...}}
             meta.stage_errors 為空才代表四個階段都完整跑過；有值時前端必須顯示，
             不得讓使用者以為看到的是完整結果。
    """
    query = str(query or "").strip()
    if not query:
        return {"papers": [], "dropped": [], "meta": {"stage_errors": {"input": "empty query"}}}

    cap = limit or max_papers()
    per_scout = scout_limit()
    stage_errors: Dict[str, str] = {}

    # 總閘門：檢索跑久了，後面兩個階段就只能拿剩下的時間，
    # 否則各階段時限相加會衝破 gunicorn 的 --timeout。
    deadline = time.time() + total_budget_sec()

    def _budget(cap_sec: int) -> int:
        return max(5, min(cap_sec, int(deadline - time.time())))

    # Stage 1: 兩隻腳並行上網
    scout_results, scout_errors = _run_jobs(
        {
            "B": lambda: run_scout("B", query, per_scout),
            "C": lambda: run_scout("C", query, per_scout),
        },
        _budget(search_timeout_sec()),
    )
    for role, err in scout_errors.items():
        stage_errors[f"scout_{role}"] = err

    items_b = (scout_results.get("B") or {}).get("items") or []
    items_c = (scout_results.get("C") or {}).get("items") or []
    grounding = {
        role: (scout_results.get(role) or {}).get("grounding") or {}
        for role in ("B", "C")
        if role in scout_results
    }

    if not items_b and not items_c:
        return {
            "papers": [],
            "dropped": [],
            "meta": {
                "stage_errors": stage_errors,
                "searched_by": [],
                "grounding": grounding,
                "candidate_count": 0,
            },
        }

    # Stage 2: 互檢（B 檢 C 的、C 檢 B 的）
    review_jobs: Dict[str, Callable[[], Any]] = {}
    if items_b:
        review_jobs["review_of_b"] = lambda: cross_review("C", query, items_b)
    if items_c:
        review_jobs["review_of_c"] = lambda: cross_review("B", query, items_c)
    review_results, review_errors = _run_jobs(review_jobs, _budget(stage_timeout_sec()))
    stage_errors.update(review_errors)

    merged, debate_dropped = _merge_and_mark(
        items_b,
        items_c,
        review_results.get("review_of_b") or {},
        review_results.get("review_of_c") or {},
    )

    # Stage 3: 機械驗證（主防線）
    verified, verify_dropped = verify_candidates(merged, _budget(stage_timeout_sec()))

    papers = _rank(verified)[:cap]
    return {
        "papers": papers,
        "dropped": debate_dropped + verify_dropped,
        "meta": {
            "stage_errors": stage_errors,
            "searched_by": sorted(scout_results.keys()),
            "grounding": grounding,
            "candidate_count": len(items_b) + len(items_c),
            "merged_count": len(merged),
            "verified_count": len(verified),
            "returned_count": len(papers),
        },
    }
