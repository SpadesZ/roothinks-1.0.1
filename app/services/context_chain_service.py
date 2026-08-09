# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/services/context_chain_service.py
# 產生時間: 2026-07-04 19:00 +08:00
# 版本: v0.2
# 模組定位:
#   Context Chain Orchestrator，負責 L1/L2/L3/KG persistence 與檢索。
# 主要責任: 升級並讀寫 project-scoped context chain schema，維持節點 identity、順序與來源 metadata。
#   1. 保存 context_chain.json 並維持 schema_version。
#   2. 以 env config 控制 L2 packet 上限。
#   3. 腐敗 context 檔案回傳明確 failed 狀態，不假裝成功。
# 維護提醒:
#   - L1/L2/L3 結構需向後相容；schema upgrade 只能補欄位，不移除舊資料。
# -----------------------------------------------------------------------------

import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from filelock import FileLock
from app.errors import AppError, ErrorCode, ErrorSeverity
from app.security import build_lock_path


CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION = 1


def _env_int(key: str, default: int, *, minimum: int = 1, maximum: int = 1000) -> int:
    raw = str(os.environ.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def upgrade_context_chain_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise AppError(
            ErrorCode.CONTEXT_CHAIN_INVALID,
            "context_chain payload must be a JSON object",
            ErrorSeverity.RECOVERABLE,
        )
    upgraded = dict(data)
    raw_version = upgraded.get("schema_version", 0)
    try:
        version = int(raw_version or 0)
    except Exception:
        version = 0
    if version < 1:
        upgraded["schema_version"] = CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION
        upgraded.setdefault("layers", {})
        upgraded.setdefault("provenance_ledger", [])
    elif version > CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION:
        raise AppError(
            ErrorCode.CONTEXT_CHAIN_INVALID,
            f"Unsupported context_chain schema_version={version}",
            ErrorSeverity.USER_ACTION_REQUIRED,
        )
    else:
        upgraded["schema_version"] = CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION
    return upgraded


class ContextChainService:
    """
    Context Chain Orchestrator (minimal-intrusive edition)

    Goals:
    1) Persist cross-task context in a layered structure (L1/L2/L3).
    2) Keep provenance logs for override and regeneration traceability.
    3) Provide fast token budget estimates for prompt packing decisions.
    """

    def __init__(self, data_root: Optional[str] = None):
        if data_root:
            self.data_root = data_root
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.data_root = os.path.join(base_dir, "data")
        self._MAX_LEDGER_ENTRIES = 500
        self._MAX_L2_PACKETS = _env_int("CONTEXT_CHAIN_L2_MAX_PACKETS", 8, minimum=1, maximum=100)
        self._ACCESS_LEVELS = {
            "public": 0,
            "internal": 1,
            "confidential": 2,
            "secret": 3,
        }

    def _project_dir(self, pid: str) -> str:
        p_dir = os.path.join(self.data_root, pid)
        os.makedirs(p_dir, exist_ok=True)
        return p_dir

    def _chain_path(self, pid: str) -> str:
        return os.path.join(self._project_dir(pid), "context_chain.json")

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _lock_path(self, path: str) -> str:
        return build_lock_path(path)

    def _safe_load_json(self, path: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
        if not os.path.exists(path):
            return fallback
        try:
            with FileLock(self._lock_path(path), timeout=5):
                if not os.path.exists(path):
                    return fallback
                with open(path, "r", encoding="utf-8") as f:
                    return upgrade_context_chain_schema(json.load(f))
        except AppError:
            raise
        except Exception as exc:
            # Try one-step backup recovery when the primary file is corrupted.
            backup_path = f"{path}.bak"
            if os.path.exists(backup_path):
                try:
                    with FileLock(self._lock_path(backup_path), timeout=5):
                        with open(backup_path, "r", encoding="utf-8") as f:
                            return upgrade_context_chain_schema(json.load(f))
                except Exception:
                    pass
            raise AppError(
                ErrorCode.CONTEXT_CHAIN_INVALID,
                f"Failed to load context_chain JSON from {os.path.basename(path)}",
                ErrorSeverity.RECOVERABLE,
                exc,
            ) from exc

    def _safe_write_json(self, path: str, obj: Dict[str, Any]) -> None:
        tmp_path = f"{path}.tmp"
        backup_path = f"{path}.bak"
        with FileLock(self._lock_path(path), timeout=10):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as src:
                        prev = src.read()
                    with open(backup_path, "w", encoding="utf-8") as bak:
                        bak.write(prev)
                except Exception:
                    # Backup failure should not block core writes.
                    pass
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)

    def _fingerprint(self, obj: Any) -> str:
        payload = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _normalize_access_level(self, level: Optional[str]) -> str:
        if not level:
            return "internal"
        v = str(level).strip().lower()
        if v in self._ACCESS_LEVELS:
            return v
        return "internal"

    def _can_access(self, record_level: Optional[str], requester_level: Optional[str]) -> bool:
        rec = self._ACCESS_LEVELS.get(self._normalize_access_level(record_level), 1)
        req = self._ACCESS_LEVELS.get(self._normalize_access_level(requester_level), 0)
        return req >= rec

    def _tenant_allowed(self, record_tenant: Optional[str], requester_tenant: Optional[str]) -> bool:
        # If record has no tenant tag, treat as globally visible.
        if not record_tenant:
            return True
        if not requester_tenant:
            return False
        return str(record_tenant).strip() == str(requester_tenant).strip()

    def _coerce_year(self, value: Any) -> Optional[int]:
        if isinstance(value, int):
            return value
        if value is None:
            return None
        try:
            parsed = int(str(value).strip())
            if 1800 <= parsed <= 3000:
                return parsed
        except Exception:
            return None
        return None

    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        import re
        return [
            t for t in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", text.lower())
            if t and len(t) >= 2
        ]

    def _text_to_sparse_vector(self, text: str) -> Dict[str, float]:
        vec: Dict[str, float] = {}
        for tok in self._tokenize(text):
            vec[tok] = vec.get(tok, 0.0) + 1.0
        return vec

    def _cosine_sparse(self, a: Dict[str, float], b: Dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = 0.0
        for k, av in a.items():
            bv = b.get(k)
            if bv is not None:
                dot += av * bv
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def estimate_tokens_fast(self, text: str) -> int:
        """
        Fast-pass token estimator:
        - CJK chars are roughly 1.5 tokens.
        - Non-CJK chars are roughly 0.35 tokens.
        """
        if not text:
            return 0
        cjk = 0
        non_cjk = 0
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                cjk += 1
            else:
                non_cjk += 1
        return int(cjk * 1.5 + non_cjk * 0.35)

    def plan_budget(self, instruction: str, query: str, context_blob: str, total_budget: int = 4000) -> Dict[str, Any]:
        """
        20/20/50/10 split with fast-pass estimates.
        """
        plan = {
            "total_budget": total_budget,
            "allocation": {
                "instruction": int(total_budget * 0.2),
                "query": int(total_budget * 0.2),
                "context": int(total_budget * 0.5),
                "guard": int(total_budget * 0.1),
            },
        }
        usage = {
            "instruction": self.estimate_tokens_fast(instruction),
            "query": self.estimate_tokens_fast(query),
            "context": self.estimate_tokens_fast(context_blob),
        }
        usage["total_estimated"] = usage["instruction"] + usage["query"] + usage["context"]
        plan["estimated_usage"] = usage
        plan["near_limit"] = usage["total_estimated"] >= int(total_budget * 0.8)
        return plan

    def _empty_chain(self, pid: str) -> Dict[str, Any]:
        return {
            "schema_version": CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION,
            "project_id": pid,
            "version": 0,
            "updated_at": self._now_iso(),
            "layers": {
                "L1": {"version": 0, "stale": False, "claims": []},
                "L2": {"version": 0, "stale": False, "summary_packets": []},
                "L3": {"version": 0, "stale": False, "project_brief": ""},
                "KG": {"version": 0, "stale": False, "triples": []},
            },
            "provenance_ledger": [],
        }

    def _extract_lightweight_kg_triples(
        self,
        claims: List[Dict[str, Any]],
        topic: str,
        tenant_id: Optional[str],
        access_level: str,
    ) -> List[Dict[str, Any]]:
        triples: List[Dict[str, Any]] = []
        ordered = sorted(
            claims,
            key=lambda x: float(x.get("confidence") or 0.0),
            reverse=True,
        )

        for i, c in enumerate(ordered):
            title = c.get("title", "")
            year = self._coerce_year(c.get("year"))
            source = c.get("source", "")
            cid = c.get("claim_id")

            if title:
                triples.append(
                    {
                        "triple_id": f"kg-topic-{cid}",
                        "subject": title,
                        "predicate": "targets_topic",
                        "object": topic,
                        "confidence": c.get("confidence", 0.0),
                        "trace": {"claim_id": cid, "doi": c.get("doi", ""), "url": c.get("url", "")},
                        "tenant_id": tenant_id,
                        "project_id": c.get("project_id"),
                        "access_level": access_level,
                    }
                )

            if source:
                triples.append(
                    {
                        "triple_id": f"kg-source-{cid}",
                        "subject": title,
                        "predicate": "published_in",
                        "object": source,
                        "confidence": c.get("confidence", 0.0),
                        "trace": {"claim_id": cid, "doi": c.get("doi", ""), "url": c.get("url", "")},
                        "tenant_id": tenant_id,
                        "project_id": c.get("project_id"),
                        "access_level": access_level,
                    }
                )

            # Lightweight chronology relation
            if i + 1 < len(ordered):
                nxt = ordered[i + 1]
                y1 = year if isinstance(year, int) else None
                y2 = self._coerce_year(nxt.get("year"))
                if title and nxt.get("title") and y1 and y2:
                    predicate = "improves_upon" if y1 >= y2 else "precursor_of"
                    triples.append(
                        {
                            "triple_id": f"kg-rel-{cid}-{nxt.get('claim_id')}",
                            "subject": title,
                            "predicate": predicate,
                            "object": nxt.get("title"),
                            "confidence": round((float(c.get("confidence") or 0.0) + float(nxt.get("confidence") or 0.0)) / 2, 4),
                            "trace": {"claim_id": cid, "peer_claim_id": nxt.get("claim_id")},
                            "tenant_id": tenant_id,
                            "project_id": c.get("project_id"),
                            "access_level": access_level,
                        }
                    )

        return triples[:80]

    def _triple_text(self, triple: Dict[str, Any]) -> str:
        return f"{triple.get('subject', '')} {triple.get('predicate', '')} {triple.get('object', '')}".strip()

    def _score_triple(self, query_vec: Dict[str, float], triple: Dict[str, Any]) -> float:
        tv = self._text_to_sparse_vector(self._triple_text(triple))
        sim = self._cosine_sparse(query_vec, tv)
        conf = float(triple.get("confidence") or 0.0)
        return round(0.7 * sim + 0.3 * conf, 6)

    def load_chain(self, pid: str) -> Dict[str, Any]:
        path = self._chain_path(pid)
        try:
            chain = self._safe_load_json(path, self._empty_chain(pid))
            return upgrade_context_chain_schema(chain)
        except AppError as exc:
            chain = self._empty_chain(pid)
            chain["load_status"] = "failed"
            chain["error_code"] = exc.code.value
            chain["error_message"] = exc.message
            return chain

    def save_chain(self, pid: str, chain: Dict[str, Any]) -> None:
        chain = upgrade_context_chain_schema(chain)
        ledger = chain.get("provenance_ledger")
        if isinstance(ledger, list) and len(ledger) > self._MAX_LEDGER_ENTRIES:
            chain["provenance_ledger"] = ledger[-self._MAX_LEDGER_ENTRIES :]
        chain["updated_at"] = self._now_iso()
        path = self._chain_path(pid)
        self._safe_write_json(path, chain)

    def update_from_task3_search(
        self,
        pid: str,
        topic: str,
        task3_output: Dict[str, Any],
        include_reasoning: bool = True,
        tenant_id: Optional[str] = None,
        access_level: str = "internal",
    ) -> Dict[str, Any]:
        """
        Materialize task3 output into layered context packets.
        """
        chain = self.load_chain(pid)
        now = self._now_iso()

        papers = task3_output.get("papers") or []
        normalized_access_level = self._normalize_access_level(access_level)
        claims: List[Dict[str, Any]] = []
        for idx, p in enumerate(papers, start=1):
            claim = {
                "claim_id": f"lit-{int(time.time())}-{idx}",
                "type": "paper_recommendation",
                "title": p.get("title", ""),
                "year": p.get("year"),
                "source": p.get("source", ""),
                "doi": p.get("doi", ""),
                "url": p.get("url", ""),
                "confidence": p.get("confidence", 0),
                "is_verified": bool(p.get("is_verified", False)),
                "score_breakdown": p.get("score_breakdown", {}),
                "topic": topic,
                "tenant_id": tenant_id,
                "project_id": pid,
                "access_level": normalized_access_level,
                "created_at": now,
            }
            claims.append(claim)

        max_packets = max(1, int(self._MAX_L2_PACKETS))
        truncated_count = max(0, len(claims) - max_packets)
        l2_packets = []
        for c in claims[:max_packets]:
            l2_packets.append(
                {
                    "packet_id": f"l2-{int(time.time())}-{c.get('claim_id')}",
                    "kind": "literature_fact",
                    "text": f"{c.get('title', '')} ({c.get('year', 'n.d.')}) [{c.get('source', '')}] conf={c.get('confidence', 0)}",
                    "tenant_id": tenant_id,
                    "project_id": pid,
                    "access_level": normalized_access_level,
                    "trace": {
                        "claim_id": c.get("claim_id"),
                        "doi": c.get("doi", ""),
                        "url": c.get("url", ""),
                    },
                    "vector": self._text_to_sparse_vector(
                        f"{c.get('title', '')} {topic} {c.get('source', '')}"
                    ),
                }
            )

        reasoning = task3_output.get("reasoning", "") if include_reasoning else ""
        if reasoning:
            project_brief = reasoning.strip()
        else:
            top_titles = [c.get("title", "") for c in claims[:3] if c.get("title")]
            if top_titles:
                project_brief = "; ".join(top_titles)
            else:
                project_brief = "No high-confidence literature summary available."

        chain["version"] = int(chain.get("version", 0)) + 1
        chain["layers"]["L1"] = {
            "version": chain["version"],
            "stale": False,
            "claims": claims,
            "topic": topic,
            "keywords": task3_output.get("keywords", []),
        }
        chain["layers"]["L2"] = {
            "version": chain["version"],
            "stale": False,
            "summary_packets": l2_packets,
            "truncated_count": truncated_count,
        }
        chain["layers"]["L3"] = {
            "version": chain["version"],
            "stale": False,
            "project_brief": project_brief,
            "tenant_id": tenant_id,
            "project_id": pid,
            "access_level": normalized_access_level,
        }
        chain["layers"]["KG"] = {
            "version": chain["version"],
            "stale": False,
            "triples": self._extract_lightweight_kg_triples(
                claims=claims,
                topic=topic,
                tenant_id=tenant_id,
                access_level=normalized_access_level,
            ),
        }

        chain["provenance_ledger"].append(
            {
                "event": "task3_context_refresh",
                "timestamp": now,
                "version": chain["version"],
                "topic": topic,
                "claims_count": len(claims),
                "truncated_count": truncated_count,
                "output_fingerprint": self._fingerprint(task3_output),
                "tenant_id": tenant_id,
                "access_level": normalized_access_level,
            }
        )

        self.save_chain(pid, chain)
        return chain

    def apply_manual_override(
        self,
        pid: str,
        claim_id: str,
        patch: Dict[str, Any],
        editor: str = "user",
        tenant_id: Optional[str] = None,
        requester_access_level: str = "internal",
    ) -> Dict[str, Any]:
        """
        Apply user override on L1 claim and mark L2/L3 as stale.
        """
        chain = self.load_chain(pid)
        now = self._now_iso()
        l1 = chain.get("layers", {}).get("L1", {})
        claims = l1.get("claims", [])

        target = None
        for c in claims:
            if c.get("claim_id") == claim_id:
                target = c
                break

        if target is None:
            raise ValueError(f"Claim not found: {claim_id}")

        if not self._tenant_allowed(target.get("tenant_id"), tenant_id):
            raise PermissionError("Tenant mismatch: not allowed to override this claim")
        if not self._can_access(target.get("access_level"), requester_access_level):
            raise PermissionError("Access level insufficient for claim override")

        before = dict(target)
        for k, v in patch.items():
            target[k] = v

        chain["version"] = int(chain.get("version", 0)) + 1
        chain["layers"]["L1"]["version"] = chain["version"]
        chain["layers"]["L1"]["stale"] = False
        chain["layers"]["L2"]["stale"] = True
        chain["layers"]["L3"]["stale"] = True
        chain["layers"]["KG"]["stale"] = True

        chain["provenance_ledger"].append(
            {
                "event": "manual_override",
                "timestamp": now,
                "version": chain["version"],
                "editor": editor,
                "claim_id": claim_id,
                "before_fingerprint": self._fingerprint(before),
                "after_fingerprint": self._fingerprint(target),
                "delta": patch,
                "tenant_id": tenant_id,
                "requester_access_level": self._normalize_access_level(requester_access_level),
            }
        )

        self.save_chain(pid, chain)
        return chain

    def get_project_brief(
        self,
        pid: str,
        tenant_id: Optional[str] = None,
        requester_access_level: str = "internal",
    ) -> Dict[str, Any]:
        chain = self.load_chain(pid)
        l3 = chain.get("layers", {}).get("L3", {})
        if not self._tenant_allowed(l3.get("tenant_id"), tenant_id):
            return {
                "project_id": pid,
                "version": chain.get("version", 0),
                "l3_stale": True,
                "project_brief": "",
                "updated_at": chain.get("updated_at"),
                "access_denied": True,
            }
        if not self._can_access(l3.get("access_level"), requester_access_level):
            return {
                "project_id": pid,
                "version": chain.get("version", 0),
                "l3_stale": True,
                "project_brief": "",
                "updated_at": chain.get("updated_at"),
                "access_denied": True,
            }
        return {
            "project_id": pid,
            "version": chain.get("version", 0),
            "l3_stale": bool(l3.get("stale", False)),
            "project_brief": l3.get("project_brief", ""),
            "updated_at": chain.get("updated_at"),
            "access_denied": False,
            "tenant_id": l3.get("tenant_id"),
            "access_level": l3.get("access_level"),
        }

    def query_topk_packets(
        self,
        pid: str,
        query: str,
        k: int = 5,
        tenant_id: Optional[str] = None,
        requester_access_level: str = "internal",
    ) -> Dict[str, Any]:
        chain = self.load_chain(pid)
        l2 = chain.get("layers", {}).get("L2", {})
        packets = l2.get("summary_packets", []) or []

        qvec = self._text_to_sparse_vector(query or "")
        scored = []
        for p in packets:
            if not self._tenant_allowed(p.get("tenant_id"), tenant_id):
                continue
            if not self._can_access(p.get("access_level"), requester_access_level):
                continue

            vec = p.get("vector") if isinstance(p.get("vector"), dict) else self._text_to_sparse_vector(p.get("text", ""))
            sim = self._cosine_sparse(qvec, vec)
            row = dict(p)
            row["similarity"] = round(sim, 6)
            row.pop("vector", None)
            scored.append(row)

        scored.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        topk = scored[: max(1, min(int(k or 5), 20))]

        return {
            "project_id": pid,
            "query": query,
            "k": len(topk),
            "version": chain.get("version", 0),
            "l2_stale": bool(l2.get("stale", False)),
            "results": topk,
        }

    def query_hybrid_kg_rag(
        self,
        pid: str,
        query: str,
        k_vector: int = 5,
        k_triple: int = 5,
        tenant_id: Optional[str] = None,
        requester_access_level: str = "internal",
    ) -> Dict[str, Any]:
        chain = self.load_chain(pid)
        l2 = chain.get("layers", {}).get("L2", {})
        kg = chain.get("layers", {}).get("KG", {})

        vector_part = self.query_topk_packets(
            pid=pid,
            query=query,
            k=k_vector,
            tenant_id=tenant_id,
            requester_access_level=requester_access_level,
        )

        triples = kg.get("triples", []) or []
        qvec = self._text_to_sparse_vector(query or "")
        scored_triples: List[Dict[str, Any]] = []
        for t in triples:
            if not self._tenant_allowed(t.get("tenant_id"), tenant_id):
                continue
            if not self._can_access(t.get("access_level"), requester_access_level):
                continue
            row = dict(t)
            row["hybrid_score"] = self._score_triple(qvec, row)
            scored_triples.append(row)

        scored_triples.sort(key=lambda x: x.get("hybrid_score", 0), reverse=True)
        top_triples = scored_triples[: max(1, min(int(k_triple or 5), 20))]

        compact_context = {
            "brief": (chain.get("layers", {}).get("L3", {}) or {}).get("project_brief", ""),
            "vector_facts": [r.get("text", "") for r in vector_part.get("results", [])],
            "kg_relations": [
                {
                    "s": t.get("subject", ""),
                    "p": t.get("predicate", ""),
                    "o": t.get("object", ""),
                    "score": t.get("hybrid_score", 0),
                }
                for t in top_triples
            ],
        }

        return {
            "project_id": pid,
            "version": chain.get("version", 0),
            "query": query,
            "l2_stale": bool(l2.get("stale", False)),
            "kg_stale": bool(kg.get("stale", False)),
            "vector_results": vector_part.get("results", []),
            "kg_results": top_triples,
            "compact_context": compact_context,
        }
