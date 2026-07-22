# 檔案路徑: app/services/paper_evidence_sync.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   Literature artifacts → EvidenceSegment 的同步層。
# 主要責任:
#   1. Flow A 粗索引：full_text.json 以「頁」為單位切段（含 block id 供回溯）。
#   2. Flow B 精索引：semantic_sections.json 以 section 為單位
#      （雙語內文 + source_block_refs 可回溯 PDF 區塊）。
#   3. 每次同步走 replace_paper_segments 原子取代，stale segments 必被清除。
# 維護提醒:
#   - 呼叫端（pipeline hooks）必須把這裡的失敗當非致命：索引壞了不能拖垮處理流程。
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
from typing import Any

from app.security import load_json_locked
from app.services.evidence_index_service import replace_paper_segments

STAGE_FLOW_A = "flow_a_fusion"
STAGE_FLOW_B = "flow_b_reflow"

_MIN_SEGMENT_CHARS = 20
_SKIP_BLOCK_TYPES = {"header", "footer", "pagenum", "page_number"}


def build_segments_from_fusion(fusion_payload: dict) -> list[dict]:
    """Flow A 粗索引：每頁一段，metadata 帶 block ids。"""
    out: list[dict] = []
    content = (fusion_payload or {}).get("content")
    if not isinstance(content, list):
        return out
    for page_obj in content:
        if not isinstance(page_obj, dict):
            continue
        page_no = page_obj.get("page")
        blocks = page_obj.get("blocks") if isinstance(page_obj.get("blocks"), list) else []
        texts: list[str] = []
        block_ids: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() in _SKIP_BLOCK_TYPES:
                continue
            text = str(block.get("content") or "").strip()
            if not text:
                continue
            texts.append(text)
            block_id = str(block.get("id") or block.get("seq_id") or "").strip()
            if block_id:
                block_ids.append(block_id)
        joined = "\n".join(texts).strip()
        if len(joined) < _MIN_SEGMENT_CHARS:
            continue
        out.append(
            {
                "segment_id": f"page-{page_no}",
                "title": f"Page {page_no}",
                "text": joined,
                "metadata": {"page": page_no, "block_ids": block_ids},
            }
        )
    return out


def build_segments_from_reflow(reflow_payload: dict) -> list[dict]:
    """Flow B 精索引：每 semantic section 一段，雙語並存，帶 source_block_refs。"""
    out: list[dict] = []
    sections = (reflow_payload or {}).get("sections")
    if not isinstance(sections, list):
        return out
    for idx, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        label = str(section.get("section_label") or "").strip() or f"Section {idx}"
        content_en = str(section.get("content_en") or "").strip()
        content_zh = str(section.get("content_zh") or "").strip()
        text = "\n\n".join(x for x in (content_en, content_zh) if x)
        if len(text) < _MIN_SEGMENT_CHARS:
            continue
        refs = section.get("source_block_refs")
        out.append(
            {
                "segment_id": f"sec-{idx}",
                "title": label,
                "text": text,
                "metadata": {
                    "section_label": label,
                    "source_block_refs": refs if isinstance(refs, list) else [],
                    "confidence": section.get("confidence"),
                    "inferred_label": bool(section.get("inferred_label", False)),
                },
            }
        )
    return out


def build_segments_from_summary(summary_payload: dict) -> list[dict]:
    """summary.json → 一個彙總段（中文摘要可直接支援中文篩選查詢）。"""
    if not isinstance(summary_payload, dict):
        return []
    parts: list[str] = []
    abstract = str(summary_payload.get("abstract_zh") or "").strip()
    if abstract:
        parts.append(abstract)
    findings = summary_payload.get("key_findings")
    if isinstance(findings, list):
        parts.extend(str(x).strip() for x in findings if str(x).strip())
    conclusion = str(summary_payload.get("conclusion") or "").strip()
    if conclusion:
        parts.append(conclusion)
    text = "\n".join(parts).strip()
    if len(text) < _MIN_SEGMENT_CHARS:
        return []
    return [
        {
            "segment_id": "summary",
            "title": "Summary",
            "text": text,
            "metadata": {"artifact": "summary.json"},
        }
    ]


def _artifact_paths(paper_dir: str) -> dict[str, str]:
    return {
        "reflow": os.path.join(paper_dir, "06_translates", "reflow", "semantic_sections.json"),
        "fusion": os.path.join(paper_dir, "05_interprets", "fusion", "full_text.json"),
        "summary": os.path.join(paper_dir, "05_interprets", "summary.json"),
    }


def sync_paper_evidence(project_id: str, paper_id: str, paper_dir: str, *, prefer: str = "auto") -> dict:
    """
    讀取論文現有 artifacts 並原子取代其 evidence segments。
    prefer="fusion"：Flow A hook 用，fusion 為當下真相（Flow A 重跑後舊 reflow 視為 stale）。
    prefer="auto"：rebuild 用，semantic_sections 存在就用精索引。
    """
    paths = _artifact_paths(paper_dir)
    summary_payload = load_json_locked(paths["summary"], {}) if os.path.exists(paths["summary"]) else {}

    segments: list[dict] = []
    stage = ""
    if prefer != "fusion" and os.path.exists(paths["reflow"]):
        reflow_payload = load_json_locked(paths["reflow"], {})
        segments = build_segments_from_reflow(reflow_payload)
        stage = STAGE_FLOW_B
    if not segments and os.path.exists(paths["fusion"]):
        fusion_payload = load_json_locked(paths["fusion"], {})
        segments = build_segments_from_fusion(fusion_payload)
        stage = STAGE_FLOW_A
    if not segments:
        return {"skipped": True, "reason": "no indexable artifacts", "paper_id": paper_id}

    segments.extend(build_segments_from_summary(summary_payload))
    result = replace_paper_segments(project_id=project_id, paper_id=paper_id, segments=segments, stage=stage)
    result["paper_id"] = paper_id
    result["skipped"] = False
    return result


def sync_reflow_evidence(project_id: str, paper_id: str, reflow_payload: dict, summary_payload: dict | None = None) -> dict:
    """Flow B hook：拿記憶體中的 reflow payload 直接精索引（原子取代 Flow A 粗索引）。"""
    segments = build_segments_from_reflow(reflow_payload)
    if not segments:
        return {"skipped": True, "reason": "no reflow sections", "paper_id": paper_id}
    segments.extend(build_segments_from_summary(summary_payload or {}))
    result = replace_paper_segments(project_id=project_id, paper_id=paper_id, segments=segments, stage=STAGE_FLOW_B)
    result["paper_id"] = paper_id
    result["skipped"] = False
    return result


def rebuild_project_evidence(project_id: str, data_root: str, paper_ids: list[str] | None = None) -> dict:
    """Backfill / 修復：掃描專案內論文並逐篇重建索引（冪等）。"""
    from app.core_pro.storage_layout import list_literature_papers, resolve_literature_paper_dir

    wanted = {str(x) for x in paper_ids} if paper_ids else None
    results: list[dict] = []
    errors: list[dict] = []
    for paper_id, _paper_dir, _kind in list_literature_papers(data_root, project_id):
        if wanted is not None and paper_id not in wanted:
            continue
        try:
            paper_dir = resolve_literature_paper_dir(
                data_root, project_id, paper_id, for_write=False, migrate_legacy=False
            )
            results.append(sync_paper_evidence(project_id, paper_id, paper_dir, prefer="auto"))
        except Exception as exc:  # 單篇失敗不拖垮整批
            errors.append({"paper_id": paper_id, "error": str(exc)})
    return {
        "project_id": project_id,
        "papers_indexed": sum(1 for r in results if not r.get("skipped")),
        "papers_skipped": sum(1 for r in results if r.get("skipped")),
        "results": results,
        "errors": errors,
    }
