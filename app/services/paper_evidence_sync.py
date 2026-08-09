# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/services/paper_evidence_sync.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   Literature artifacts → EvidenceSegment 的同步層。
# 主要責任: 把 fusion/reflow/summary artifacts 轉成穩定 EvidenceSegment records，再以 paper identity 同步索引。
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
# 由 fusion 的 Title 區塊切出的章節索引。品質介於 flow_a_fusion（頁）與
# flow_b_reflow（語意章節）之間，用來讓 Flow B 跑不動的論文也有章節級索引。
STAGE_FLOW_A_SECTIONS = "flow_a_fusion_sections"
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


def build_segments_from_fusion_sections(fusion_payload: dict) -> list[dict]:
    """Flow A 章節索引：以 fusion 的 Title 區塊為界切段。

    為什麼需要這條路：章節級索引原本只有 Flow B 的 semantic_sections.json 提供，
    但 Flow B 在正式站的 e2-medium（2 vCPU）跑本機 NLLB 翻譯必定 timeout，
    多數論文永遠拿不到 semantic_sections，索引就一路退回「一頁一段」。
    頁的邊界會把一個論點攔腰切斷，是目前檢索品質的主要損失來源。

    fusion 的 block 自帶 type，實測 SEBASR_IJMIR 有 18 個 Title 區塊
    （例如 '1 Introduction'），足以切章節，不必等 Flow B。

    回傳空 list 代表這份 fusion 沒有可用的 Title 區塊，呼叫端應退回頁分段 ——
    寧可粗一點也不要沒有索引。
    """
    out: list[dict] = []
    content = (fusion_payload or {}).get("content")
    if not isinstance(content, list):
        return out

    current: dict[str, Any] | None = None
    saw_title = False

    def _flush(sec: dict[str, Any] | None) -> None:
        if not sec:
            return
        joined = "\n".join(sec["texts"]).strip()
        if len(joined) < _MIN_SEGMENT_CHARS:
            return
        idx = len(out) + 1
        out.append(
            {
                "segment_id": f"fsec-{idx}",
                "title": sec["label"],
                "text": joined,
                "metadata": {
                    "section_label": sec["label"],
                    # 保留頁碼區間與 block ids，才回溯得到 PDF 原始位置。
                    "page_start": sec["page_start"],
                    "page_end": sec["page_end"],
                    "block_ids": sec["block_ids"],
                    "derived_from": "fusion_titles",
                },
            }
        )

    for page_obj in content:
        if not isinstance(page_obj, dict):
            continue
        page_no = page_obj.get("page")
        raw_blocks = page_obj.get("blocks") if isinstance(page_obj.get("blocks"), list) else []
        # reading_order 才是版面上的閱讀順序；照陣列順序讀會把雙欄排版讀錯。
        blocks = sorted(
            (b for b in raw_blocks if isinstance(b, dict)),
            key=lambda b: (b.get("reading_order") if isinstance(b.get("reading_order"), int) else 10**6),
        )
        for block in blocks:
            btype = str(block.get("type") or "").strip().lower()
            if btype in _SKIP_BLOCK_TYPES or block.get("is_page_noise"):
                continue
            text = str(block.get("content") or "").strip()
            if not text:
                continue

            if btype == "title":
                saw_title = True
                _flush(current)
                current = {
                    "label": text[:120],
                    "texts": [],
                    "block_ids": [],
                    "page_start": page_no,
                    "page_end": page_no,
                }
                continue

            if current is None:
                # 第一個 Title 之前的內容（標題頁、作者、Abstract）不能丟掉 ——
                # Abstract 往往正是寫摘要時最該被檢索到的東西。
                current = {
                    "label": "Front Matter",
                    "texts": [],
                    "block_ids": [],
                    "page_start": page_no,
                    "page_end": page_no,
                }
            current["texts"].append(text)
            current["page_end"] = page_no
            block_id = str(block.get("id") or block.get("seq_id") or "").strip()
            if block_id:
                current["block_ids"].append(block_id)

    _flush(current)
    # 沒有任何 Title 就不算章節索引，交還給頁分段處理。
    return out if saw_title else []


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
        # 章節優先、頁墊底。Flow B 跑不動的論文（正式站多數）過去只能拿到
        # 頁分段，一段就是一整頁 4000～6500 字元，檢索預算根本塞不下一段。
        segments = build_segments_from_fusion_sections(fusion_payload)
        stage = STAGE_FLOW_A_SECTIONS
        if not segments:
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
