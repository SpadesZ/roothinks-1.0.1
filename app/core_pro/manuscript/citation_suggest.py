# 檔案路徑: app/core_pro/manuscript/citation_suggest.py
# 產生時間: 2026-07-04 19:18 +08:00
# 版本: v0.1
# 模組定位:
#   Manuscript paragraph citation suggestion baseline。
# 主要責任:
#   1. 從 Evidence Index 查找可引用 paper evidence。
#   2. 合併同一 paper 的多個 segment。
#   3. 偵測可能需要 citation 的段落。
# 維護提醒:
#   - 不捏造 citation；沒有 evidence 或 paper_id 時回空 list。
# -----------------------------------------------------------------------------

from __future__ import annotations

import re
from collections import defaultdict


CITATION_NEED_PATTERNS = [
    r"研究指出",
    r"文獻顯示",
    r"已被證實",
    r"previous studies",
    r"has been reported",
    r"\b\d+(\.\d+)?\s?%",
    r"\b(compared with|higher than|lower than|more than|less than)\b",
    r"\b(mechanism|pathway|mediated by|associated with)\b",
]


def detect_citation_needed(paragraph_text: str) -> bool:
    text = str(paragraph_text or "")
    if not text.strip():
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in CITATION_NEED_PATTERNS)


def suggest_citations_for_paragraph(
    project_id: str,
    paragraph_text: str,
    section_title: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    query = " ".join(x for x in [section_title or "", paragraph_text or ""] if x.strip())
    try:
        from app.services.evidence_index_service import search_evidence

        results = search_evidence(project_id=project_id, query=query, top_k=max(10, int(top_k or 5) * 3))
    except Exception:
        return []

    grouped: dict[str, dict] = {}
    snippets: dict[str, list[str]] = defaultdict(list)
    segments: dict[str, list[str]] = defaultdict(list)
    for item in results:
        paper_id = str(item.get("paper_id") or "").strip()
        if not paper_id:
            continue
        if paper_id not in grouped:
            grouped[paper_id] = {
                "paper_id": paper_id,
                "title": item.get("title") or "",
                "score": 0.0,
            }
        grouped[paper_id]["score"] += float(item.get("score") or 0.0)
        snippet = str(item.get("snippet") or "").strip()
        if snippet and snippet not in snippets[paper_id]:
            snippets[paper_id].append(snippet)
        segment_id = str(item.get("segment_id") or "").strip()
        if segment_id and segment_id not in segments[paper_id]:
            segments[paper_id].append(segment_id)

    out = []
    for paper_id, row in grouped.items():
        reason = "Matched paragraph keywords against indexed evidence snippets."
        out.append(
            {
                "paper_id": paper_id,
                "title": row["title"],
                "relevant_snippets": snippets[paper_id][:3],
                "score": round(row["score"], 6),
                "reason": reason,
                "segment_ids": segments[paper_id],
            }
        )
    out.sort(key=lambda item: (-float(item.get("score") or 0.0), item.get("paper_id") or ""))
    return out[: max(1, int(top_k or 5))]
