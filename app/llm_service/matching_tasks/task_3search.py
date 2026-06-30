#路徑(./app/llm_service/matching_tasks/task_3search.py) #版本 v0.6 #更版時間 20260209-0030
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
from urllib.parse import urlparse

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

        output = {
            "apa_citations": [],
            "keywords": [],
            "reasoning": "",
            "papers": [],
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

    def _search_crossref(self, topic: str, limit: int = 20) -> List[Dict[str, Any]]:
        data = self._http_get_json(
            "https://api.crossref.org/works",
            {"query": topic, "rows": limit, "sort": "relevance"},
        )
        items = data.get("message", {}).get("items", []) if data else []
        out: List[Dict[str, Any]] = []
        for it in items:
            title = (it.get("title") or [""])[0].strip()
            if not title:
                continue
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
            out.append(
                {
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
            )
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
