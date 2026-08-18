# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/task_3search.py
# 模組定位: LLM task 業務層；組合特定任務 prompt，經 dispatcher 呼叫已綁定模型。
# 主要責任: 以研究查詢搜尋外部學術來源並正規化候選 metadata；只負責 seed discovery。
#   v0.7 另提供 resolve_reference()：把單筆書目回查成真實 DOI/URL，供 Literature
#   對話式找文獻（task_3bc_scout）做幻覺剔除。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
#   resolve_reference() 查無資料時必須回空，不得為了「有東西可回」而放寬比對門檻。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/llm_service/matching_tasks/task_3search.py) #版本 v0.7 #更版時間 20260817-1200
import difflib
import json
import math
import hashlib
import os
import re
import time
import socket
import ipaddress
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Any
from urllib.parse import quote, urlparse

from filelock import FileLock

import requests
from app.llm_service.llm_dispatcher import dispatch_task
from app.security import build_lock_path, validate_id

class ContextSearcher:
    """
    Task 3: 搜尋建議生成器 (Search Agent) v0.6
    職責: 
    1. 接收模糊的研究主題 (Topic)。
    2. 利用 LLM 知識庫生成 3-5 篇高相關度的經典文獻。
    3. 強制產出標準 APA 格式引用、搜尋關鍵字。
    4. [Feature v0.6] 必須提供「推薦理由 (Reasoning)」，解釋為何選擇這些文獻或策略。
    """
    TASK_ID = "task_3search"
    _UA = "roothinks-task3/1.0 (academic-retrieval)"
    _CACHE_TTL_SEC = 6 * 60 * 60
    _HTTP_MAX_RETRIES = 2
    _ALLOWED_HTTP_HOSTS = {
        "api.crossref.org",
        "api.openalex.org",
        "export.arxiv.org",
        "eutils.ncbi.nlm.nih.gov",
        "pubmed.ncbi.nlm.nih.gov",
        "doi.org",
    }

    def generate_suggestions(self, pid: str, topic: str, include_reasoning: bool = True) -> Dict[str, Any]:
        """
        生成文獻建議與搜尋關鍵字
        :param pid: 專案 ID
        :param topic: 使用者輸入的研究主題
        :return: Dict 包含 apa_citations, keywords, reasoning
        """
        if not topic or not topic.strip():
            return {"error": "Topic is empty."}

        # 1. 先做真實檢索與排序，避免直接由 LLM 幻覺生成文獻
        keywords = self._build_search_keywords(topic)
        topic_key = self._topic_key(topic)
        candidates = self._load_cached_candidates(pid, topic_key)
        cache_used = bool(candidates)

        if not candidates:
            candidates = []
            candidates.extend(self._search_crossref(topic, limit=30))
            candidates.extend(self._search_openalex(topic, limit=30))
            candidates.extend(self._search_arxiv(topic, limit=20))
            candidates.extend(self._search_pubmed(topic, limit=30))
            self._save_cached_candidates(pid, topic_key, keywords, candidates)

        deduped = self._dedupe_candidates(candidates)
        filtered = self._keyword_filter(deduped, keywords)
        ranked = self._rank_candidates(filtered, keywords)
        verified_ranked = self._apply_hard_validation(ranked, check_limit=20)
        top_papers = verified_ranked[:5]
        # library_pool: 全量排序候選（完整 metadata），供持久文獻庫累積；
        # route 端 merge 後會從回應中移除，不影響前端契約。
        library_pool = [
            {
                "title": p.get("title", ""),
                "authors": p.get("authors") or [],
                "year": p.get("year"),
                "venue": p.get("venue", ""),
                "doi": p.get("doi", ""),
                "url": p.get("url", ""),
                "abstract": str(p.get("abstract") or "")[:4000],
                "citations": int(p.get("citations") or 0),
                "source": p.get("source", ""),
                "confidence": round(float(p.get("confidence", 0.0)), 3),
                "is_verified": bool(p.get("is_verified", False)),
            }
            for p in verified_ranked[:50]
        ]

        output = {
            "apa_citations": [],
            "keywords": [],
            "reasoning": "",
            "papers": [],
            "library_pool": library_pool,
            "meta": {
                "cache_used": cache_used,
                "total_candidates": len(candidates),
                "deduped": len(deduped),
                "filtered": len(filtered),
                "verified_ranked": len(verified_ranked),
            }
        }

        if not top_papers:
            output["apa_citations"] = ["System: No validated papers found from current sources."]
            output["keywords"] = keywords[:5]
            output["reasoning"] = "目前未找到欄位完整且符合關鍵字的文獻，建議縮小主題或增加同義關鍵詞。"
            return output

        output["apa_citations"] = [self._to_apa_citation(p) for p in top_papers]
        output["keywords"] = keywords[:5]
        output["papers"] = [
            {
                "title": p.get("title", ""),
                "year": p.get("year"),
                "source": p.get("source", ""),
                "doi": p.get("doi", ""),
                "url": p.get("url", ""),
                "is_verified": bool(p.get("is_verified", False)),
                "confidence": round(float(p.get("confidence", 0.0)), 3),
                "score_breakdown": p.get("score_breakdown", {}),
                "reason": self._build_paper_reason(p, output["keywords"]),
            }
            for p in top_papers
        ]
        if include_reasoning:
            output["reasoning"] = self._generate_reasoning_low_token(topic, top_papers, output["keywords"])
        else:
            output["reasoning"] = ""
        return output

    def _data_root(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(base_dir, "data")

    def _cache_file_path(self, pid: str) -> str:
        safe_pid = validate_id(pid, "project_id")
        p_dir = os.path.join(self._data_root(), safe_pid)
        p_dir_real = os.path.realpath(p_dir)
        root_real = os.path.realpath(self._data_root())
        if os.path.commonpath([root_real, p_dir_real]) != root_real:
            raise ValueError("Invalid cache path")
        os.makedirs(p_dir, exist_ok=True)
        return os.path.join(p_dir, "task3_index_cache.json")

    def _topic_key(self, topic: str) -> str:
        normalized = re.sub(r"\s+", " ", (topic or "").strip().lower())
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def _load_cached_candidates(self, pid: str, topic_key: str) -> List[Dict[str, Any]]:
        path = self._cache_file_path(pid)
        if not os.path.exists(path):
            return []
        try:
            with FileLock(build_lock_path(path), timeout=5):
                if not os.path.exists(path):
                    return []
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            entry = (data.get("entries") or {}).get(topic_key)
            if not entry:
                return []
            ts = float(entry.get("timestamp") or 0)
            if (time.time() - ts) > self._CACHE_TTL_SEC:
                return []
            return entry.get("candidates") or []
        except Exception:
            return []

    def _save_cached_candidates(self, pid: str, topic_key: str, keywords: List[str], candidates: List[Dict[str, Any]]) -> None:
        path = self._cache_file_path(pid)
        tmp_path = f"{path}.tmp"
        try:
            existing = {}
            now_ts = time.time()
            with FileLock(build_lock_path(path), timeout=10):
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        existing = json.load(f)

                entries = existing.get("entries") or {}
                # 清理過期 cache entry
                entries = {
                    k: v for k, v in entries.items()
                    if now_ts - float((v or {}).get("timestamp") or 0) <= self._CACHE_TTL_SEC
                }
                entries[topic_key] = {
                    "timestamp": now_ts,
                    "keywords": keywords[:8],
                    "count": len(candidates),
                    "candidates": candidates,
                }
                existing["entries"] = entries
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, path)
        except Exception:
            return

    def _is_private_host(self, host: str) -> bool:
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                ip_str = info[4][0]
                ip_obj = ipaddress.ip_address(ip_str)
                if (
                    ip_obj.is_private
                    or ip_obj.is_loopback
                    or ip_obj.is_link_local
                    or ip_obj.is_reserved
                    or ip_obj.is_multicast
                ):
                    return True
        except Exception:
            return True
        return False

    def _is_allowed_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False
        if host not in self._ALLOWED_HTTP_HOSTS:
            return False
        if self._is_private_host(host):
            return False
        return True

    def _http_get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_allowed_url(url):
            return {}
        for attempt in range(self._HTTP_MAX_RETRIES + 1):
            try:
                resp = requests.get(
                    url,
                    params=params,
                    headers={"User-Agent": self._UA},
                    timeout=6,
                )
                if resp.status_code == 200:
                    return resp.json()
                # Retry transient statuses only.
                if resp.status_code not in (408, 429, 500, 502, 503, 504):
                    return {}
            except Exception:
                pass
            if attempt < self._HTTP_MAX_RETRIES:
                time.sleep(0.25 * (2 ** attempt))
        return {}

    def _crossref_item_to_paper(self, it: Dict[str, Any]) -> Dict[str, Any]:
        """Crossref work -> 內部 paper dict。搜尋清單與單筆 DOI 查詢共用同一份對應。"""
        title = (it.get("title") or [""])[0].strip()
        if not title:
            return {}

        authors = []
        for a in it.get("author", [])[:8]:
            family = (a.get("family") or "").strip()
            given = (a.get("given") or "").strip()
            name = f"{family}, {given}".strip(", ")
            if name:
                authors.append(name)

        year = None
        issued = it.get("issued", {}).get("date-parts", [])
        if issued and issued[0]:
            year = issued[0][0]

        doi = (it.get("DOI") or "").strip()
        return {
            "title": title,
            "authors": authors,
            "year": year,
            "venue": (it.get("container-title") or [""])[0],
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else (it.get("URL") or ""),
            "abstract": it.get("abstract") or "",
            "citations": int(it.get("is-referenced-by-count") or 0),
            "source": "crossref",
        }

    def _search_crossref(self, topic: str, limit: int = 20) -> List[Dict[str, Any]]:
        data = self._http_get_json(
            "https://api.crossref.org/works",
            {"query": topic, "rows": limit, "sort": "relevance"},
        )
        items = data.get("message", {}).get("items", []) if data else []
        out: List[Dict[str, Any]] = []
        for it in items:
            paper = self._crossref_item_to_paper(it)
            if paper:
                out.append(paper)
        return out

    def _search_openalex(self, topic: str, limit: int = 20) -> List[Dict[str, Any]]:
        data = self._http_get_json(
            "https://api.openalex.org/works",
            {"search": topic, "per-page": limit, "sort": "relevance_score:desc"},
        )
        items = data.get("results", []) if data else []
        out: List[Dict[str, Any]] = []
        for it in items:
            title = (it.get("display_name") or "").strip()
            if not title:
                continue
            abstract = self._openalex_abstract_to_text(it.get("abstract_inverted_index") or {})
            doi = (it.get("doi") or "").replace("https://doi.org/", "").strip()
            location = it.get("primary_location") or {}
            source_obj = location.get("source") or {}
            out.append(
                {
                    "title": title,
                    "authors": [
                        (a.get("author") or {}).get("display_name", "")
                        for a in (it.get("authorships") or [])[:8]
                        if (a.get("author") or {}).get("display_name")
                    ],
                    "year": it.get("publication_year"),
                    "venue": source_obj.get("display_name") or "",
                    "doi": doi,
                    "url": location.get("landing_page_url") or it.get("id") or (f"https://doi.org/{doi}" if doi else ""),
                    "abstract": abstract,
                    "citations": int(it.get("cited_by_count") or 0),
                    "source": "openalex",
                }
            )
        return out

    def _search_arxiv(self, topic: str, limit: int = 12) -> List[Dict[str, Any]]:
        query = f"all:{topic}"
        text = ""
        arxiv_url = "https://export.arxiv.org/api/query"
        if not self._is_allowed_url(arxiv_url):
            return []
        for attempt in range(self._HTTP_MAX_RETRIES + 1):
            try:
                resp = requests.get(
                    arxiv_url,
                    params={"search_query": query, "start": 0, "max_results": limit},
                    headers={"User-Agent": self._UA},
                    timeout=6,
                )
                if resp.status_code == 200:
                    text = resp.text
                    break
                if resp.status_code not in (408, 429, 500, 502, 503, 504):
                    return []
            except Exception:
                pass
            if attempt < self._HTTP_MAX_RETRIES:
                time.sleep(0.25 * (2 ** attempt))

        if not text:
            return []
        try:
            root = ET.fromstring(text)
        except Exception:
            return []

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        out: List[Dict[str, Any]] = []
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            if not title:
                continue
            abs_text = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
            published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
            year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
            arxiv_url = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
            out.append(
                {
                    "title": title,
                    "authors": [
                        (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
                        for a in entry.findall("atom:author", ns)
                        if (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
                    ][:8],
                    "year": year,
                    "venue": "arXiv",
                    "doi": "",
                    "url": arxiv_url,
                    "abstract": abs_text,
                    "citations": 0,
                    "source": "arxiv",
                }
            )
        return out

    def _search_pubmed(self, topic: str, limit: int = 30) -> List[Dict[str, Any]]:
        try:
            search_resp = self._http_get_json(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                {
                    "db": "pubmed",
                    "retmode": "json",
                    "retmax": limit,
                    "sort": "relevance",
                    "term": topic,
                },
            )
            id_list = (search_resp.get("esearchresult") or {}).get("idlist") or []
            if not id_list:
                return []

            summary_resp = self._http_get_json(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                {
                    "db": "pubmed",
                    "retmode": "json",
                    "id": ",".join(id_list),
                },
            )
            result_obj = summary_resp.get("result") or {}
            out: List[Dict[str, Any]] = []
            for pid in id_list:
                item = result_obj.get(str(pid)) or {}
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                pubdate = item.get("pubdate") or ""
                year_match = re.search(r"(19|20)\d{2}", pubdate)
                year = int(year_match.group(0)) if year_match else None
                authors = [a.get("name", "") for a in (item.get("authors") or []) if a.get("name")]
                article_ids = item.get("articleids") or []
                doi = ""
                for aid in article_ids:
                    if (aid.get("idtype") or "").lower() == "doi":
                        doi = (aid.get("value") or "").strip()
                        break
                out.append(
                    {
                        "title": title,
                        "authors": authors[:8],
                        "year": year,
                        "venue": (item.get("fulljournalname") or "").strip(),
                        "doi": doi,
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                        "abstract": "",
                        "citations": 0,
                        "source": "pubmed",
                    }
                )
            return out
        except Exception:
            return []

    # ---------------------------------------------------------------------
    # Reference resolution：把「LLM 說有這篇」變成「這篇真的存在，而且在這裡」
    # ---------------------------------------------------------------------
    # 0.82 是「標題只差冠詞、副標題或標點」還能過、但同領域的不同論文會被擋下來的位置。
    # 調低會開始放行同主題的不同篇；調高則連合法的標題變體（連字號、大小寫）都擋掉。
    _RESOLVE_TITLE_THRESHOLD = 0.82
    # DOI 對得上時標題門檻放寬到 0.55：出版社登錄的標題常與作者寫法有出入，
    # 但完全不像（例如 DOI 被接到另一篇）仍必須擋下來。
    _RESOLVE_DOI_TITLE_FLOOR = 0.55

    @staticmethod
    def _norm_title(title: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()

    def _title_similarity(self, left: str, right: str) -> float:
        a = self._norm_title(left)
        b = self._norm_title(right)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        return difflib.SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def normalize_doi(raw: str) -> str:
        clean = str(raw or "").strip()
        clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean, flags=re.IGNORECASE)
        clean = clean.strip().rstrip(".,;)")
        return clean if re.match(r"^10\.\d{4,9}/\S+$", clean) else ""

    def _fetch_by_doi(self, doi: str) -> Dict[str, Any]:
        clean = self.normalize_doi(doi)
        if not clean:
            return {}
        data = self._http_get_json(
            f"https://api.crossref.org/works/{quote(clean, safe='/')}", {}
        )
        message = (data or {}).get("message") or {}
        if not isinstance(message, dict):
            return {}
        return self._crossref_item_to_paper(message)

    @staticmethod
    def canonical_reference_url(paper: Dict[str, Any]) -> str:
        """
        對外顯示用的連結，依可驗證程度排序：doi.org > PubMed/arXiv 原始頁 > 其他 landing page。
        一律 https —— arXiv 的 Atom id 是 http，直接放上去會被瀏覽器擋混合內容。
        """
        doi = str(paper.get("doi") or "").strip()
        if doi:
            return f"https://doi.org/{doi}"
        url = str(paper.get("url") or "").strip()
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        return url

    def resolve_reference(
        self,
        title: str,
        authors: List[str] = None,
        year: Any = None,
        doi: str = "",
    ) -> Dict[str, Any]:
        """
        以書目資訊回查真實學術來源，取得 canonical DOI/URL。

        NOTE(NOTE-039): 這是對抗 LLM 幻覺的**主要**防線，不是輔助。兩個模型可以一起編出同一篇
        不存在的論文（互檢會一致通過），但編出來的東西在 Crossref/OpenAlex/
        PubMed/arXiv 一定查不到。回 {} 代表「在我們認可的來源裡查無此文」，
        呼叫端應直接剔除，不得因為「模型很有把握」而保留。

        :return: 命中的 paper dict（含 match/match_score/canonical_url），查無則 {}。
        """
        title = str(title or "").strip()
        clean_doi = self.normalize_doi(doi)

        if clean_doi:
            hit = self._fetch_by_doi(clean_doi)
            if hit:
                sim = self._title_similarity(title, hit.get("title")) if title else 1.0
                if sim >= self._RESOLVE_DOI_TITLE_FLOOR:
                    hit["match"] = "doi"
                    hit["match_score"] = round(sim, 4)
                    hit["canonical_url"] = self.canonical_reference_url(hit)
                    hit["apa"] = self._to_apa_citation(hit)
                    return hit
                # DOI 存在但指向完全不同的論文 = 模型把 DOI 接錯了，
                # 這種「半真」比整篇捏造更危險，不能靜靜地採用 DOI 那一邊。

        if not title:
            return {}

        best: Dict[str, Any] = {}
        best_score = 0.0
        for finder in (
            self._search_crossref,
            self._search_openalex,
            self._search_pubmed,
            self._search_arxiv,
        ):
            try:
                candidates = finder(title, limit=5)
            except Exception:
                continue
            for cand in candidates or []:
                score = self._title_similarity(title, cand.get("title"))
                if score > best_score:
                    best, best_score = cand, score
            if best_score >= 0.95:
                break  # 逐字命中，沒必要再打其餘 API

        if not best or best_score < self._RESOLVE_TITLE_THRESHOLD:
            return {}

        want_year = self._normalize_year(year)
        got_year = self._normalize_year(best.get("year"))
        if isinstance(want_year, int) and isinstance(got_year, int) and abs(want_year - got_year) > 1:
            # 標題像但年份差兩年以上：多半是對到同名的另一篇（會議版 vs 期刊版差 1 年，容許）。
            return {}

        resolved = dict(best)
        resolved["match"] = "title"
        resolved["match_score"] = round(best_score, 4)
        resolved["canonical_url"] = self.canonical_reference_url(resolved)
        resolved["apa"] = self._to_apa_citation(resolved)
        return resolved

    def _build_search_keywords(self, topic: str) -> List[str]:
        cleaned = re.sub(r"\s+", " ", topic).strip()
        lowered = cleaned.lower()
        raw_tokens = re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", lowered)
        stopwords = {
            "the", "and", "for", "with", "from", "that", "this", "into", "using", "based",
            "study", "research", "analysis", "of", "in", "on", "to", "a", "an",
        }
        tokens = [t for t in raw_tokens if len(t) >= 2 and t not in stopwords]

        keywords: List[str] = []
        if cleaned:
            keywords.append(cleaned)
        for t in tokens:
            if t not in keywords:
                keywords.append(t)
            if len(keywords) >= 8:
                break
        return keywords or [cleaned]

    def _keyword_filter(self, papers: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
        if not keywords:
            return papers
        keep = []
        terms = [k.lower() for k in keywords if k]
        for p in papers:
            text = f"{p.get('title', '')} {p.get('abstract', '')}".lower()
            hit = sum(1 for t in terms if t in text)
            if hit > 0:
                keep.append(p)
        return keep if keep else papers

    def _dedupe_candidates(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique: Dict[str, Dict[str, Any]] = {}
        for p in papers:
            doi = (p.get("doi") or "").strip().lower()
            norm_title = re.sub(r"\W+", "", (p.get("title") or "").lower())
            key = f"doi:{doi}" if doi else f"title:{norm_title[:120]}"
            if not norm_title:
                continue
            if key not in unique:
                unique[key] = p
                continue
            # 保留欄位較完整者
            if self._completeness_score(p) > self._completeness_score(unique[key]):
                unique[key] = p
        return list(unique.values())

    def _rank_candidates(self, papers: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
        for p in papers:
            breakdown = self._score_breakdown(p, keywords)
            p["score_breakdown"] = breakdown
            p["confidence"] = breakdown.get("final", 0.0)
        return sorted(papers, key=lambda x: x.get("confidence", 0), reverse=True)

    def _score_breakdown(self, paper: Dict[str, Any], keywords: List[str]) -> Dict[str, float]:
        text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
        terms = [k.lower() for k in keywords if k]
        kw_hits = sum(1 for t in terms if t in text)
        kw_score = kw_hits / max(len(terms), 1)

        source_weight = {
            "crossref": 1.0,
            "openalex": 1.0,
            "arxiv": 0.9,
            "pubmed": 1.0,
        }.get((paper.get("source") or "").lower(), 0.7)

        c = int(paper.get("citations") or 0)
        citation_score = min(math.log1p(c) / 6.0, 1.0)

        year = self._normalize_year(paper.get("year"))
        if isinstance(year, int):
            age = max(0, datetime.now().year - year)
            recency = max(0.0, 1.0 - (age / 20.0))
        else:
            recency = 0.4

        complete = self._completeness_score(paper)
        score = (
            0.35 * kw_score
            + 0.25 * source_weight
            + 0.20 * citation_score
            + 0.10 * recency
            + 0.10 * complete
        )
        final = max(0.0, min(score, 1.0))
        return {
            "keyword": round(kw_score, 4),
            "source": round(source_weight, 4),
            "citation": round(citation_score, 4),
            "recency": round(recency, 4),
            "completeness": round(complete, 4),
            "final": round(final, 4),
        }

    def _apply_hard_validation(self, ranked: List[Dict[str, Any]], check_limit: int = 20) -> List[Dict[str, Any]]:
        verified_list: List[Dict[str, Any]] = []
        uncertain_list: List[Dict[str, Any]] = []

        for idx, p in enumerate(ranked):
            if idx < check_limit:
                verified = self._verify_paper_link(p)
                p["is_verified"] = verified
                if not verified:
                    p["confidence"] = max(0.0, round(float(p.get("confidence", 0.0)) - 0.15, 4))
                    if isinstance(p.get("score_breakdown"), dict):
                        p["score_breakdown"]["final"] = p["confidence"]
                if verified:
                    verified_list.append(p)
                else:
                    uncertain_list.append(p)
            else:
                p["is_verified"] = False
                uncertain_list.append(p)

        merged = verified_list + uncertain_list
        merged.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return merged

    def _verify_paper_link(self, paper: Dict[str, Any]) -> bool:
        doi = (paper.get("doi") or "").strip()
        url = (paper.get("url") or "").strip()

        targets = []
        if doi:
            targets.append(f"https://doi.org/{doi}")
        if url:
            targets.append(url)

        for target in targets:
            ok = self._check_url_alive(target)
            if ok:
                return True
        return False

    def _check_url_alive(self, url: str) -> bool:
        if not self._is_allowed_url(url):
            return False
        for attempt in range(self._HTTP_MAX_RETRIES + 1):
            try:
                head = requests.head(url, timeout=4, allow_redirects=True, headers={"User-Agent": self._UA})
                if 200 <= head.status_code < 400:
                    return True
                if head.status_code in (403, 405):
                    get = requests.get(url, timeout=5, allow_redirects=True, headers={"User-Agent": self._UA})
                    return 200 <= get.status_code < 400
                if head.status_code not in (408, 429, 500, 502, 503, 504):
                    return False
            except Exception:
                pass
            if attempt < self._HTTP_MAX_RETRIES:
                time.sleep(0.2 * (2 ** attempt))
        return False

    def _completeness_score(self, paper: Dict[str, Any]) -> float:
        fields = [
            bool(paper.get("title")),
            bool(paper.get("authors")),
            bool(paper.get("year")),
            bool(paper.get("doi") or paper.get("url")),
        ]
        return sum(1 for f in fields if f) / len(fields)

    def _normalize_year(self, value: Any):
        if isinstance(value, int):
            return value
        if value is None:
            return None
        try:
            y = int(str(value).strip())
            if 1800 <= y <= 3000:
                return y
        except Exception:
            return None
        return None

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            out = float(value)
            if not math.isfinite(out):
                return default
            return out
        except Exception:
            return default

    def _matched_keywords(self, paper: Dict[str, Any], keywords: List[str], limit: int = 2) -> List[str]:
        text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
        hits: List[str] = []
        for kw in keywords or []:
            token = str(kw or "").strip()
            if len(token) < 2:
                continue
            token_l = token.lower()
            # 英文詞用 word-boundary，避免 "is" 命中 "this" 這類片段誤判。
            if re.fullmatch(r"[a-z0-9_]+", token_l):
                if re.search(rf"\b{re.escape(token_l)}\b", text):
                    hits.append(token)
            elif token_l in text:
                hits.append(token)
            if len(hits) >= limit:
                break
        return hits

    def _build_paper_reason(self, paper: Dict[str, Any], keywords: List[str]) -> str:
        breakdown = paper.get("score_breakdown") or {}
        kw_score = self._safe_float(breakdown.get("keyword"), 0.0)
        source_score = self._safe_float(breakdown.get("source"), 0.0)
        citation_score = self._safe_float(breakdown.get("citation"), 0.0)
        recency_score = self._safe_float(breakdown.get("recency"), 0.0)

        title_raw = str(paper.get("title") or "").strip()
        has_real_title = bool(title_raw)
        short_title = title_raw if len(title_raw) <= 40 else f"{title_raw[:37]}..."
        prefix = f"《{short_title}》" if has_real_title else "此文獻"

        kw_hits = self._matched_keywords(paper, keywords, limit=2)
        if kw_hits:
            topic_part = f"命中關鍵詞「{'、'.join(kw_hits)}」"
        elif kw_score >= 0.6:
            topic_part = "與主題詞高度匹配"
        elif kw_score >= 0.3:
            topic_part = "與主題詞具中度關聯"
        elif keywords:
            topic_part = f"與主題詞「{keywords[0]}」具可用關聯"
        else:
            topic_part = "與當前主題具基本關聯"

        evidence_parts: List[str] = []
        year = self._normalize_year(paper.get("year"))
        if isinstance(year, int):
            evidence_parts.append(f"{year}年發表")

        source = str(paper.get("source") or "").strip()
        if source and source_score >= 0.95:
            evidence_parts.append(f"來源 {source} 可信度高")
        elif source and source_score >= 0.85:
            evidence_parts.append(f"來源 {source} 可信度良好")

        if citation_score >= 0.55:
            evidence_parts.append("引用訊號較強")
        elif citation_score >= 0.3:
            evidence_parts.append("具一定引用支持")

        if recency_score >= 0.75:
            evidence_parts.append("時效性較佳")

        if paper.get("is_verified"):
            evidence_parts.append("連結已驗證可達")
        else:
            evidence_parts.append("連結待人工覆核")

        return f"{prefix}{topic_part}，{'，'.join(evidence_parts[:2])}。"

    def _to_apa_citation(self, paper: Dict[str, Any]) -> str:
        authors = self._format_authors_apa(paper.get("authors") or [])
        year = paper.get("year") or "n.d."
        title = (paper.get("title") or "Untitled").rstrip(".") + "."
        venue = (paper.get("venue") or "").strip()
        tail = ""
        if paper.get("doi"):
            tail = f"https://doi.org/{paper.get('doi')}"
        elif paper.get("url"):
            tail = paper.get("url")

        parts = [f"{authors} ({year}).", title]
        if venue:
            parts.append(f"{venue}.")
        if tail:
            parts.append(tail)
        return " ".join(p for p in parts if p).strip()

    def _format_authors_apa(self, authors: List[str]) -> str:
        if not authors:
            return "Unknown Author"
        cleaned = [a.strip() for a in authors if a and a.strip()]
        if not cleaned:
            return "Unknown Author"
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]}, & {cleaned[1]}"
        return ", ".join(cleaned[:3]) + ", et al."

    def _generate_reasoning_low_token(self, topic: str, papers: List[Dict[str, Any]], keywords: List[str]) -> str:
        compact_list = [
            {
                "t": p.get("title", "")[:120],
                "y": p.get("year"),
                "s": p.get("source"),
                "c": round(float(p.get("confidence", 0.0)), 3),
            }
            for p in papers[:5]
        ]
        prompt = (
            "你是研究助理。根據以下已驗證文獻清單，"
            "以繁中寫80-120字推薦理由與檢索策略，不可新增清單外文獻。\n"
            f"Topic: {topic}\n"
            f"Keywords: {', '.join(keywords[:5])}\n"
            f"Papers: {json.dumps(compact_list, ensure_ascii=False)}\n"
            "只輸出純文字。"
        )
        result = dispatch_task(self.TASK_ID, prompt)
        if result.get("ok") and result.get("text"):
            return result.get("text", "").strip()

        return (
            "已優先選擇關鍵詞匹配高、來源可信（Crossref/OpenAlex/arXiv）、"
            "欄位完整且信心分數較高的文獻；建議先讀高分核心文獻，再用同義詞與方法詞擴展搜尋。"
        )

    def _openalex_abstract_to_text(self, inverted_index: Dict[str, List[int]]) -> str:
        if not inverted_index:
            return ""
        max_pos = -1
        for poses in inverted_index.values():
            if poses:
                max_pos = max(max_pos, max(poses))
        if max_pos < 0:
            return ""
        words = [""] * (max_pos + 1)
        for word, poses in inverted_index.items():
            for p in poses:
                if 0 <= p <= max_pos:
                    words[p] = word
        return " ".join(w for w in words if w).strip()

    def _sanitize_json(self, raw_text: str) -> str:
        """
        [Helper] 清洗 LLM 輸出的字串，移除 Markdown 與常見語法錯誤
        """
        # 移除 ```json ... ``` 包裹
        text = re.sub(r'^```json\s*', '', raw_text, flags=re.MULTILINE)
        text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
        
        # 移除可能的開頭非 JSON 文字
        text = text.strip()
        if not text.startswith('{'):
            # 嘗試尋找第一個 { 和最後一個 }
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1:
                text = text[start : end + 1]
        
        # 修復結尾多餘的逗號 (e.g., ["a", "b",] -> ["a", "b"])
        # 小心不要誤刪正常的逗號，這裡針對 list 結尾和 object 結尾
        text = re.sub(r',\s*([\]}])', r'\1', text)
        
        return text.strip()
