# 檔案路徑: app/core_pro/manuscript/context_inject.py
# 產生時間: 2026-07-04 19:05 +08:00
# 版本: v0.2
# 模組定位:
#   Manuscript 段落級 context retrieval / packing。
# 主要責任:
#   1. 優先使用 Evidence Index 檢索。
#   2. Evidence Index 不可用時 fallback 到 Context Chain L1/L2/L3 keyword baseline。
#   3. 保留 provenance、fingerprint、token estimate 與 readable context block。
# 維護提醒:
#   - 本輪是 deterministic baseline；未來 embedding/vector DB 只能替換 retrieval layer。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.errors import AppError
from app.services.context_chain_service import ContextChainService
from app.services.text_tokenize import tokenize


def estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def context_pack_fingerprint(context_items: list[dict]) -> str:
    compact = [
        {
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "paper_id": item.get("paper_id"),
            "segment_id": item.get("segment_id"),
            "fingerprint": item.get("fingerprint"),
            "snippet": item.get("snippet"),
        }
        for item in context_items
    ]
    return _fingerprint(compact)


def build_context_pack_metadata(context_items: list[dict]) -> dict:
    return {
        "total_estimated_tokens": sum(int(item.get("estimated_tokens") or 0) for item in context_items),
        "context_pack_fingerprint": context_pack_fingerprint(context_items),
    }


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _score_text(query_tokens: list[str], text: str, *, source_priority: float = 0.5) -> float:
    tokens = tokenize(text)
    if not query_tokens or not tokens:
        return 0.0
    qset = set(query_tokens)
    tset = set(tokens)
    overlap = len(qset & tset)
    if overlap <= 0:
        return 0.0
    coverage = overlap / max(1, len(qset))
    density = overlap / max(1, len(tset))
    return round((0.72 * coverage + 0.28 * density) * source_priority, 6)


def _item_from_context_chain_packet(packet: dict, score: float, mode: str) -> dict:
    trace = packet.get("trace") if isinstance(packet.get("trace"), dict) else {}
    snippet = str(packet.get("text") or packet.get("project_brief") or "")[:1200]
    item = {
        "source_type": "context_chain_item",
        "source_id": str(packet.get("packet_id") or packet.get("claim_id") or packet.get("triple_id") or "context_chain"),
        "paper_id": trace.get("paper_id") or packet.get("paper_id"),
        "segment_id": packet.get("segment_id"),
        "title": str(packet.get("title") or packet.get("kind") or packet.get("type") or "Context Chain"),
        "snippet": snippet,
        "score": score,
        "fingerprint": _fingerprint(packet),
        "estimated_tokens": estimate_tokens(snippet),
        "retrieval_mode": mode,
        "degraded": mode == "fallback_context_chain",
    }
    return item


def _collect_context_chain_candidates(chain: dict) -> list[dict]:
    layers = chain.get("layers", {}) if isinstance(chain, dict) else {}
    out: list[dict] = []

    for packet in (layers.get("L2", {}) or {}).get("summary_packets", []) or []:
        if isinstance(packet, dict):
            out.append(packet)

    for claim in (layers.get("L1", {}) or {}).get("claims", []) or []:
        if isinstance(claim, dict):
            text = " ".join(
                str(claim.get(k) or "")
                for k in ("title", "source", "topic", "doi", "url")
            )
            packet = dict(claim)
            packet.setdefault("text", text)
            out.append(packet)

    l3 = layers.get("L3", {}) or {}
    if isinstance(l3, dict) and str(l3.get("project_brief") or "").strip():
        out.append(
            {
                "packet_id": "l3-project-brief",
                "kind": "project_brief",
                "text": str(l3.get("project_brief") or ""),
                "project_id": chain.get("project_id"),
            }
        )
    return out


def _fallback_context_chain_search(
    *,
    project_id: str,
    query: str,
    top_k: int,
    max_tokens: int,
    data_root: str | None = None,
    context_chain_service: ContextChainService | None = None,
) -> list[dict]:
    service = context_chain_service or ContextChainService(data_root=data_root)
    chain = service.load_chain(project_id)
    query_tokens = tokenize(query)
    scored = []
    for packet in _collect_context_chain_candidates(chain):
        text = str(packet.get("text") or packet.get("title") or "")
        score = _score_text(query_tokens, text, source_priority=0.9)
        if score <= 0:
            continue
        scored.append(_item_from_context_chain_packet(packet, score, "fallback_context_chain"))

    scored.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("source_id") or "")))
    return _apply_limits(scored, top_k=top_k, max_tokens=max_tokens)


def _apply_limits(items: list[dict], *, top_k: int, max_tokens: int) -> list[dict]:
    out = []
    budget = max(1, int(max_tokens or 1200))
    used = 0
    for item in items[: max(1, int(top_k or 8))]:
        snippet = str(item.get("snippet") or "")
        est = estimate_tokens(snippet)
        if used + est > budget:
            remaining = budget - used
            if remaining <= 0:
                break
            max_chars = max(1, remaining * 4)
            item = dict(item)
            item["snippet"] = snippet[:max_chars].rstrip()
            item["estimated_tokens"] = estimate_tokens(item["snippet"])
            item["truncated"] = True
            est = int(item["estimated_tokens"])
        out.append(item)
        used += est
    pack = build_context_pack_metadata(out)
    for item in out:
        item["total_estimated_tokens"] = pack["total_estimated_tokens"]
        item["context_pack_fingerprint"] = pack["context_pack_fingerprint"]
    return out


def retrieve_paragraph_context(
    project_id: str,
    section_title: str,
    paragraph_goal: str | None = None,
    draft_text: str | None = None,
    max_tokens: int = 1200,
    top_k: int = 8,
    *,
    data_root: str | None = None,
    context_chain_service: ContextChainService | None = None,
) -> list[dict]:
    query = " ".join(x for x in [section_title, paragraph_goal or "", draft_text or ""] if str(x).strip())

    try:
        from app.services.evidence_index_service import search_evidence

        evidence_items = search_evidence(project_id=project_id, query=query, top_k=top_k)
        if evidence_items:
            normalized = []
            for item in evidence_items:
                row = dict(item)
                row.setdefault("estimated_tokens", estimate_tokens(row.get("snippet", "")))
                row["retrieval_mode"] = "evidence_index"
                row["degraded"] = False
                normalized.append(row)
            return _apply_limits(normalized, top_k=top_k, max_tokens=max_tokens)
    except Exception:
        # ponytail: fallback is intentionally broad here because DB app-context
        # availability varies in CLI/tests; upgrade path is a service health probe.
        pass

    return _fallback_context_chain_search(
        project_id=project_id,
        query=query,
        top_k=top_k,
        max_tokens=max_tokens,
        data_root=data_root or os.path.join(_project_root(), "data"),
        context_chain_service=context_chain_service,
    )


def build_injected_context_block(context_items: list[dict]) -> str:
    if not context_items:
        return ""
    meta = build_context_pack_metadata(context_items)
    lines = [
        "[Injected Context Pack]",
        f"total_estimated_tokens: {meta['total_estimated_tokens']}",
        f"context_pack_fingerprint: {meta['context_pack_fingerprint']}",
        "",
    ]
    for idx, item in enumerate(context_items, start=1):
        lines.extend(
            [
                f"[Context {idx}]",
                f"source_type: {item.get('source_type', '')}",
                f"source_id: {item.get('source_id', '')}",
                f"paper_id: {item.get('paper_id', '') or ''}",
                f"segment_id: {item.get('segment_id', '') or ''}",
                f"title: {item.get('title', '')}",
                f"score: {item.get('score', 0)}",
                f"estimated_tokens: {item.get('estimated_tokens', 0)}",
                f"fingerprint: {item.get('fingerprint', '')}",
                f"snippet: {item.get('snippet', '')}",
                "",
            ]
        )
    return "\n".join(lines).strip()


class ContextInjector:
    def __init__(self, pid: str):
        self.pid = pid

    def get_context_for_section(self, section_key: str) -> str:
        items = retrieve_paragraph_context(
            project_id=self.pid,
            section_title=section_key,
            paragraph_goal=section_key,
        )
        return build_injected_context_block(items)
