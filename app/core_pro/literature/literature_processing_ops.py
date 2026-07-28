# 檔案路徑: app/core_pro/literature/literature_processing_ops.py
# 產生時間: 2026-07-04 19:30 +08:00
# 版本: v0.3
#功能概要:
#1. 承載 status/bridge/correction/full_json 等重邏輯實作。
#2. 供 literature_routes.py 薄 wrapper 呼叫，降低主檔體積。
#3. 維持既有資料流程與狀態機行為一致。
# 維護提醒:
# - 本輪補 artifact legacy fallback 與刪除清理，不改 Flow A/B 狀態機。

import json
import os
import re
import time
from datetime import datetime, timezone

from flask import jsonify, request, send_file
from app.core_pro.storage_layout import list_literature_papers, resolve_literature_paper_dir

# ---------------------------------------------------------------------------
# Section-title retagger  (v1.0 — deterministic, zero LLM quota)
# ---------------------------------------------------------------------------
# Mirrors the frontend rule in app/static/js/study_view.js _looksLikeSectionTitle
# so backend data and UI rendering stay consistent (belt-and-suspenders).
#
# Rules:
#   - Only retags blocks currently typed "Body" or "Unknown".
#   - Trimmed content length ≤ 64 AND ≤ 9 words.
#   - Matches a numbered heading  (e.g. "1. Introduction", "2.1 Experiments")
#     OR a known section keyword.
#   - Figure / Table / Equation blocks are NEVER retagged.
# ---------------------------------------------------------------------------
_SECTION_TITLE_NUMBERED_RE = re.compile(r'^\d+(?:\.\d+)*\.?\s+[A-Z]')
_SECTION_TITLE_KEYWORD_RE = re.compile(
    r'^(?:Abstract|Introduction|Related Work|Background|Motivation|Preliminaries'
    r'|Methods?|Materials and Methods|Approach|Model|Experiments?|Experimental Setup'
    r'|Results?|Evaluation|Discussion|Conclusions?|Future Work|References|Bibliography'
    r'|Acknowledge?ments?|Appendix|Data)\b',
    re.IGNORECASE,
)
_RETAG_ELIGIBLE_TYPES = {'Body', 'Unknown'}


def retag_section_titles(blocks):
    """Mutate block dicts in-place: set type='Title' for section-heading blocks.

    Operates on a flat list of block dicts (as loaded from text_{n}_fixed.json
    or assembled into a fusion page's 'blocks' list).  Returns the same list
    for convenience.

    Heuristic matches the frontend _looksLikeSectionTitle rule exactly:
      - type in {'Body', 'Unknown'} (Figure/Table/Equation are untouched)
      - trimmed content length ≤ 64 AND ≤ 9 whitespace-separated tokens
      - numbered heading OR known section-keyword prefix
    """
    if not isinstance(blocks, list):
        return blocks
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get('type') not in _RETAG_ELIGIBLE_TYPES:
            continue
        s = str(block.get('content', '') or '').strip()
        if not s or len(s) > 64:
            continue
        if len(s.split()) > 9:
            continue
        if _SECTION_TITLE_NUMBERED_RE.match(s) or _SECTION_TITLE_KEYWORD_RE.match(s):
            block['type'] = 'Title'
    return blocks
# ---------------------------------------------------------------------------


def _task4_safe_int(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


def _task4_block_sort_key(block):
    if not isinstance(block, dict):
        return (10 ** 9, 10 ** 9, 10 ** 9, "")
    ro = _task4_safe_int(block.get("reading_order", 0), 0)
    if ro <= 0:
        ro = 10 ** 9
    bbox = block.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        x = _task4_safe_int(bbox[0], 10 ** 9)
        y = _task4_safe_int(bbox[1], 10 ** 9)
    else:
        x = 10 ** 9
        y = 10 ** 9
    seq = str(block.get("seq_id", "") or block.get("id", "") or "").strip()
    return (ro, y, x, seq)


def _task4_sort_blocks(blocks):
    if not isinstance(blocks, list):
        return blocks
    dict_blocks = [b for b in blocks if isinstance(b, dict)]
    sorted_blocks = sorted(dict_blocks, key=_task4_block_sort_key)
    if len(dict_blocks) == len(blocks):
        return sorted_blocks
    others = [b for b in blocks if not isinstance(b, dict)]
    return sorted_blocks + others


def _task4_seq_ids(blocks):
    if not isinstance(blocks, list):
        return []
    seq_ids = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        sid = str(b.get("seq_id", "") or "").strip()
        if sid:
            seq_ids.append(sid)
    return seq_ids


def _task4_fixed_compatible(raw_blocks, fixed_blocks):
    if not isinstance(fixed_blocks, list) or not fixed_blocks:
        return False
    if not isinstance(raw_blocks, list) or not raw_blocks:
        return True
    raw_seq = _task4_seq_ids(raw_blocks)
    fixed_seq = _task4_seq_ids(fixed_blocks)
    if raw_seq and fixed_seq:
        return len(raw_seq) == len(fixed_seq) and set(raw_seq) == set(fixed_seq)
    return len(fixed_blocks) >= len(raw_blocks)


def _paper_dir(deps, pid: str, paper_id: str, for_write: bool = False) -> str:
    return resolve_literature_paper_dir(
        deps.DATA_ROOT,
        pid,
        paper_id,
        for_write=for_write,
        migrate_legacy=True,
    )


def _task4_resolve_block_source(base_dir, block_base, deps):
    raw_path = deps.safe_join_under(base_dir, f"{block_base}_raw.json")
    fixed_path = deps.safe_join_under(base_dir, f"{block_base}_fixed.json")

    raw_content = None
    fixed_content = None
    if os.path.exists(raw_path):
        try:
            raw_content = deps.load_json_locked(raw_path, {})
        except Exception:
            raw_content = None
    if deps._is_nonempty_json_file(fixed_path):
        try:
            fixed_content = deps.load_json_locked(fixed_path, {})
        except Exception:
            fixed_content = None

    use_fixed = _task4_fixed_compatible(raw_content, fixed_content)
    if use_fixed:
        return "fixed", fixed_path, fixed_content, raw_content
    if isinstance(raw_content, list):
        return "raw", raw_path, raw_content, raw_content
    if isinstance(fixed_content, list):
        return "fixed", fixed_path, fixed_content, raw_content
    if os.path.exists(raw_path):
        return "raw", raw_path, raw_content, raw_content
    return "fixed", fixed_path, fixed_content, raw_content


def update_paper_status_impl(deps, pid, paper_id, status, log_msg=""):
    """
    更新 Paper 的 interpretation_status 和 process_log
    如果 Paper 不存在則創建
    """
    try:
        paper = deps.Paper.query.filter_by(paper_id=paper_id, pid=pid).first()

        if not paper:
            paper = deps.Paper(
                paper_id=paper_id,
                pid=pid,
                interpretation_status=status,
                process_log=log_msg,
            )
            deps.db.session.add(paper)
            deps.logger.info(f"[DB] Created new Paper record: {paper_id}")
        else:
            paper.interpretation_status = status
            paper.process_log = log_msg
            deps.logger.info(f"[DB] Updated Paper status: {paper_id} -> {status}")

        deps.db.session.commit()

    except Exception as e:
        deps.logger.error(f"[DB] Failed to update Paper status: {e}")
        deps.db.session.rollback()


def update_paper_metadata_impl(deps, pid, paper_id, meta):
    """
    更新 Paper 的 metadata (title, authors, etc.)
    """
    try:
        paper = deps.Paper.query.filter_by(paper_id=paper_id, pid=pid).first()

        if not paper:
            paper = deps.Paper(
                paper_id=paper_id,
                pid=pid,
                interpretation_status=deps.Paper.STATUS_ANALYZING,
            )
            deps.db.session.add(paper)

        if meta.get('title'):
            paper.title = meta['title'][:500]
        if meta.get('authors'):
            paper.authors = meta['authors'][:200]
        if meta.get('journal'):
            paper.journal = meta['journal'][:200]
        if meta.get('publish_date'):
            paper.publish_date = str(meta['publish_date'])
        elif meta.get('year'):
            paper.publish_date = str(meta['year'])

        deps.db.session.commit()
        deps.logger.info(f"[DB] Updated Paper metadata: {paper_id}")

    except Exception as e:
        deps.logger.error(f"[DB] Failed to update Paper metadata: {e}")
        deps.db.session.rollback()


def trigger_gold_bridge_impl(deps, pid, paper_id):
    """
    [Core Logic] 黃金數據橋接器
    職責: 將 Raw OCR 數據轉化為 Gold Level 結構化數據 (Summary/FullText)
    """
    try:
        deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_T5_RUNNING, "Task 5 running")
        data_root = deps.DATA_ROOT

        sc = deps.get_semantic_corrector()
        intp = deps.get_interpreter()

        if sc is None or intp is None:
            deps.logger.error(f"[{paper_id}] Required modules not available")
            deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_FAILED, "Required modules not available")
            return

        deps.logger.info(f"[{paper_id}] Bridge A: Generating Metadata & Context Guide...")
        meta = sc.extract_metadata(pid, paper_id, data_root)
        if meta:
            deps.logger.info(f"[{paper_id}] Metadata found: {meta.get('title')}")
            deps._update_paper_metadata(pid, paper_id, meta)

        guide_path = sc.generate_context_guide(pid, paper_id, data_root)

        deps.logger.info(f"[{paper_id}] Bridge B: Fixing Semantic Errors in batches...")
        paper_dir = _paper_dir(deps, pid, paper_id, for_write=True)
        raw_dir = deps.safe_join_under(paper_dir, "03_recognizes")
        if os.path.exists(raw_dir):
            page_pairs = []

            import re as _re

            def _page_num(fname):
                m = _re.search(r'(\d+)', fname)
                return int(m.group(1)) if m else 0

            for fname in sorted(os.listdir(raw_dir), key=_page_num):
                if fname.endswith("_raw.json"):
                    src = os.path.join(raw_dir, fname)
                    dst = os.path.join(raw_dir, fname.replace("_raw.json", "_fixed.json"))
                    page_pairs.append((src, dst))

            if page_pairs:
                batch_size = 5
                total_batches = (len(page_pairs) + batch_size - 1) // batch_size
                for i in range(0, len(page_pairs), batch_size):
                    batch = page_pairs[i:i + batch_size]
                    deps.logger.info(
                        "[%s] Fix batch %d/%d (%d pages)",
                        paper_id,
                        (i // batch_size) + 1,
                        total_batches,
                        len(batch),
                    )
                    try:
                        sc.fix_batch(batch, guide_path=guide_path)
                    except Exception as _batch_err:
                        deps.logger.warning(
                            "[%s] Fix batch %d/%d failed (%s); writing raw as fallback",
                            paper_id,
                            (i // batch_size) + 1,
                            total_batches,
                            _batch_err,
                        )
                        for src_path, dst_path in batch:
                            try:
                                if os.path.exists(src_path) and not os.path.exists(dst_path):
                                    import shutil as _shutil

                                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                                    _shutil.copy2(src_path, dst_path)
                            except Exception:
                                pass

        try:
            temp_dir = os.path.join(raw_dir, "temp")
            if os.path.isdir(temp_dir):
                import shutil

                shutil.rmtree(temp_dir)
        except Exception:
            deps.logger.warning("[%s] Failed to cleanup Task4 temp dir", paper_id)

        deps.logger.info(f"[{paper_id}] Bridge C: Interpreting & Summarizing...")
        fusion_path = intp.run_fusion(pid, paper_id, data_root)

        if fusion_path and os.path.exists(fusion_path):
            intp.run_summary(fusion_path)

        inline_translation = os.environ.get("LITERATURE_FLOWA_INLINE_TRANSLATION", "0").strip() == "1"
        if inline_translation:
            deps.logger.info(f"[{paper_id}] Bridge D: Translating Full Text (Hybrid Mode)...")
            translator = deps.get_nllb_translator()
            if translator and fusion_path and os.path.exists(fusion_path):
                try:
                    trans_output = fusion_path.replace("full_text.json", "full_text_trans.json")
                    from app.core_pro.literature.literature_translator import TranslationContext

                    translator.translate_file(fusion_path, trans_output, TranslationContext.LITERATURE_BATCH)
                    deps.logger.info(f"[{paper_id}] Translation completed: {trans_output}")
                except Exception as trans_err:
                    deps.logger.warning(f"[{paper_id}] Translation failed (non-critical): {trans_err}")
            else:
                deps.logger.info(f"[{paper_id}] Translation skipped (translator not available or fusion missing)")
        else:
            deps.logger.info(f"[{paper_id}] Flow B translation deferred (use /api/literature/run_translation)")

        summary_ok = False
        summary_path = os.path.join(paper_dir, "05_interprets", "summary.json")
        if os.path.exists(summary_path):
            try:
                with open(summary_path, 'r', encoding='utf-8') as f:
                    sj = json.load(f)
                summary_str = json.dumps(sj, ensure_ascii=False).lower()
                mode = str(sj.get("mode", "") or "").strip().lower() if isinstance(sj, dict) else ""

                if mode in {
                    "fallback_synthetic",
                    "fallback_empty",
                    "fallback_partial",
                    "fallback_partial_parse",
                    "fallback_llm_parse_error",
                    "degraded",
                    "normalized_extract",
                }:
                    summary_ok = True
                elif mode == "error_fallback":
                    summary_ok = bool(isinstance(sj, dict) and sj.get("abstract_zh"))
                elif "429" in summary_str or "quota" in summary_str or "resource_exhausted" in summary_str:
                    summary_ok = False
                elif isinstance(sj, dict) and "error" in sj and not sj.get("abstract_zh"):
                    summary_ok = False
                elif "failed" in summary_str:
                    summary_ok = False
                else:
                    summary_ok = True
            except Exception:
                summary_ok = False

        # Flow A 粗索引：fusion 產出即可索引（頁級 segments），失敗不影響 pipeline。
        # Flow B reflow 成功後會原子取代為 section 級精索引。
        try:
            from app.services.paper_evidence_sync import sync_paper_evidence

            sync_result = sync_paper_evidence(pid, paper_id, paper_dir, prefer="fusion")
            deps.logger.info(f"[{paper_id}] Evidence coarse index: {sync_result}")
        except Exception as evidence_err:
            deps.logger.warning(f"[{paper_id}] Evidence coarse index failed (non-critical): {evidence_err}")

        if summary_ok:
            deps.logger.info(f"[{paper_id}] Gold Data Bridge Completed Successfully.")
            deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_GOLD_READY, "Gold data ready for Study")
        else:
            deps.logger.warning(f"[{paper_id}] Summary not ready or failed; marking need retry.")
            deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_FAILED, "Summary not ready or failed, please retry")

    except Exception as e:
        deps.logger.error(f"[{paper_id}] Gold Bridge Failed: {e}")
        deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_FAILED, f"Error: {str(e)}")


def delete_paper_impl(deps):
    """
    刪除文件及其所有相關數據
    """
    data = request.get_json(silent=True) or {}
    pid = deps._normalize_literature_pid(data.get('pid'))
    paper_id = data.get('paper_id')

    if not pid:
        return jsonify({"status": "error", "message": "Invalid pid"}), 404
    if not paper_id:
        return jsonify({"status": "error", "message": "Missing paper_id"}), 400
    try:
        paper_id = deps._safe_paper_id(paper_id)
    except deps.BadRequest:
        return jsonify({"status": "error", "message": "Invalid paper_id"}), 400

    try:
        project_dir = deps._get_project_dir(pid)
        canonical_paper_dir = deps.safe_join_under(deps._get_literature_dir(pid), "papers", paper_id)
        legacy_paper_dir = deps.safe_join_under(project_dir, paper_id)
        project_dir_real = os.path.realpath(project_dir)

        deleted_dirs = []
        for paper_dir in [canonical_paper_dir, legacy_paper_dir]:
            paper_dir_real = os.path.realpath(paper_dir)
            if os.path.commonpath([project_dir_real, paper_dir_real]) != project_dir_real:
                return jsonify({"status": "error", "message": "Invalid paper path"}), 400
            if os.path.islink(paper_dir):
                return jsonify({"status": "error", "message": "Refusing to delete symlink path"}), 400
            if os.path.exists(paper_dir):
                import shutil

                shutil.rmtree(paper_dir)
                deleted_dirs.append(paper_dir)
                deps.logger.info(f"Deleted paper directory: {paper_dir}")

        paper = deps.Paper.query.filter_by(pid=pid, paper_id=paper_id).first()
        if paper:
            deps.db.session.delete(paper)
            deps.db.session.commit()
            deps.logger.info(f"Deleted paper record: {paper_id}")

        return jsonify({"status": "success", "message": "Paper deleted successfully", "deleted_dirs": deleted_dirs})
    except Exception as e:
        return deps._internal_error("delete_paper", e)


# ---------------------------------------------------------------------------
# [progress] 對外的中性進度表示
#
# 為什麼要這一層：畫面上把「OCR-A / 規則仲裁 / 校對」換成百分比之後，
# /api/literature/status/<pid> 的 JSON 回應仍然原封不動吐出 stages.s2、
# flow_status="gold_ready" 這些值。任何人開開發者工具或直接打這個 API
# 就能還原整條流程結構——等於前面的遮蔽只擋住不看原始碼的人。
#
# 里程碑對應（與前端顯示的百分比一致，改動時兩邊要一起改）：
#   PDF(s1) → 15%(s2) → 30%(s3) → 40%(s4) → 60%(flow_a)
#   → 70%(s10) → 80%(s11) → 100%(flow_b)
# ---------------------------------------------------------------------------

_MILESTONE_SPEC = [
    ("s1", 0, "PDF"),
    ("s2", 15, "15%"),
    ("s3", 30, "30%"),
    ("s4", 40, "40%"),
    (None, 60, "60%"),        # flow_a_ready
    ("s10", 70, "70%"),
    ("s11", 80, "80%"),
    (None, 100, "100% 完成"),  # flow_b_ready
]

# 對外 API 只允許這些產品層欄位。設 LITERATURE_EXPOSE_INTERNALS=1 可保留
# 完整 row 供本機除錯；正式部署不該開。
_PUBLIC_FIELDS = frozenset({
    "paper_id", "filename", "progress_pct", "progress_state",
    "milestones", "llm_usage", "error_type",
})

# error_type 的原始值可能是 flowb_reflow_heuristic_fallback 這類字串
# （見 flowb_reflow_issue 的組法），直接吐出去等於把 Flow B 的內部
# 生成模式名稱交出去。對外只保留「哪一類錯誤」，細節留在伺服器日誌。
_ERROR_CLASS = {
    "quota_error": "quota",
    "degraded_summary": "degraded",
    "general_error": "failed",
}


def classify_error(raw):
    if not raw:
        return None
    key = str(raw)
    if key in _ERROR_CLASS:
        return _ERROR_CLASS[key]
    if key.startswith("flowb_"):
        return "translation_incomplete"
    return "failed"


def _expose_internals() -> bool:
    return str(os.environ.get("LITERATURE_EXPOSE_INTERNALS", "")).strip().lower() \
        in {"1", "true", "yes", "on"}


def build_milestones(stages, flow_a_ready, flow_b_ready):
    """把內部階段旗標轉成只有百分比與狀態的里程碑清單。"""
    stages = stages or {}
    out = []
    for idx, (key, pct, label) in enumerate(_MILESTONE_SPEC):
        if key is None:
            done = bool(flow_a_ready) if pct == 60 else bool(flow_b_ready)
            state = "done" if done else "pending"
        else:
            state = str(stages.get(key) or "pending")
            if state not in {"done", "processing", "pending"}:
                state = "pending"
        out.append({"pct": pct, "label": label, "state": state})
    return out


def progress_pct_of(milestones):
    """已達成的最高百分比。"""
    done = [m["pct"] for m in (milestones or []) if m.get("state") == "done"]
    return max(done) if done else 0


def progress_state_of(flow_status):
    """內部流程代號 → 對外的產品層狀態。

    只保留「使用者需要知道的事」：在等、在跑、跑完了、失敗了。
    gold_ready / rules_ready / bilingual_ready 這些名稱本身就洩漏做法。
    """
    st = str(flow_status or "pending")
    if st in {"gold_ready", "rules_ready", "ready_A"}:
        return "analyzed"
    if st in {"analyzing", "processing_A"}:
        return "analyzing"
    if st in {"bilingual_ready", "ready_B"}:
        return "completed"
    if st == "processing_B":
        return "translating"
    if st in {"need_retry", "failed"}:
        return "failed"
    return "uploaded"


def strip_internal_fields(row):
    """只輸出明確允許的產品層欄位（除非明確開啟本機除錯）。"""
    if _expose_internals():
        return row
    return {k: v for k, v in row.items() if k in _PUBLIC_FIELDS}


def get_status_impl(deps, pid):
    """
    掃描檔案結構回傳詳細狀態 (Traffic Lights)
    """
    pid = deps._normalize_literature_pid(pid)
    if not pid:
        return jsonify({"status": "error", "message": "Invalid pid", "papers": []}), 404

    p_dir = deps._get_project_dir(pid)
    papers = []

    paper_db_map = {}
    try:
        paper_rows = deps.Paper.query.filter_by(pid=pid).all()
        paper_db_map = {p.paper_id: p for p in paper_rows}
    except Exception as e:
        deps.logger.warning(f"[Status] Failed to load Paper DB map for pid={pid}: {e}")

    # [usage] 整個專案的用量一次查完，迴圈裡只做查表。
    usage_by_paper = {}
    try:
        from app.llm_service.llm_usage import summarize_project
        usage_by_paper = summarize_project(pid) or {}
    except Exception:
        deps.logger.warning("[status] 用量彙總失敗 pid=%s", pid, exc_info=True)

    if os.path.exists(p_dir):
        paper_rows = list_literature_papers(deps.DATA_ROOT, pid)
        for paper_id, paper_path, _storage_kind in paper_rows:

            meta_path = os.path.join(paper_path, "00_origins", "metadata.json")
            filename = paper_id
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        filename = json.load(f).get("original_filename", paper_id)
                except Exception:
                    deps.logger.warning("[get_status] failed to load metadata.json for %s/%s", pid, paper_id)

            rec_dir = os.path.join(paper_path, "03_recognizes")
            origin_pages = deps._collect_origin_pages(paper_path)
            try:
                stage_ready_ratio = float(os.environ.get("LITERATURE_STAGE_READY_RATIO", "1.0"))
            except Exception:
                stage_ready_ratio = 1.0
            stage_ready_ratio = min(1.0, max(0.1, stage_ready_ratio))
            expected_pages = origin_pages if origin_pages else None
            has_raw = deps._has_valid_stage_files(
                rec_dir,
                "_raw.json",
                min_size=100,
                min_ratio=stage_ready_ratio,
                expected_pages=expected_pages,
            )

            has_fixed = deps._has_valid_stage_files(
                rec_dir,
                "_fixed.json",
                min_size=100,
                min_ratio=stage_ready_ratio,
                expected_pages=expected_pages,
            )

            interp_dir = os.path.join(paper_path, "05_interprets")
            legacy_paper_path = os.path.join(deps.DATA_ROOT, pid, paper_id)
            legacy_interp_dir = os.path.join(legacy_paper_path, "05_interprets")
            summary_candidates = [
                os.path.join(interp_dir, "summary.json"),
                os.path.join(legacy_interp_dir, "summary.json"),
            ]
            summary_path = next((p for p in summary_candidates if os.path.exists(p)), summary_candidates[0])
            has_summary = False
            summary_error = None

            if os.path.exists(summary_path):
                try:
                    with open(summary_path, 'r', encoding='utf-8') as f:
                        summary_data = json.load(f)
                        summary_str = json.dumps(summary_data, ensure_ascii=False).lower()
                        mode = str(summary_data.get("mode", "") or "").strip().lower() if isinstance(summary_data, dict) else ""

                        if mode in {
                            "fallback_synthetic",
                            "fallback_empty",
                            "fallback_partial",
                            "fallback_partial_parse",
                            "fallback_llm_parse_error",
                            "degraded",
                            "normalized_extract",
                        }:
                            summary_error = "degraded_summary"
                            has_summary = True
                        elif mode == "error_fallback":
                            if isinstance(summary_data, dict) and summary_data.get("abstract_zh"):
                                summary_error = "degraded_summary"
                                has_summary = True
                            else:
                                summary_error = "general_error"
                        elif "429" in summary_str or "quota" in summary_str or "resource_exhausted" in summary_str:
                            summary_error = "quota_error"
                        elif isinstance(summary_data, dict) and "error" in summary_data and not summary_data.get("abstract_zh"):
                            summary_error = "general_error"
                        elif "failed" in summary_str:
                            summary_error = "general_error"
                        else:
                            has_summary = True
                except Exception as e:
                    deps.logger.warning("[get_status] summary parse failed for %s/%s: %s", pid, paper_id, e)
                    summary_error = "parse_error"

            origins_dir = os.path.join(paper_path, "00_origins")
            stk_a_dir = os.path.join(paper_path, "01_intermediate", "stack_a")
            stk_b_dir = os.path.join(paper_path, "01_intermediate", "stack_b")
            fusion_dir = os.path.join(interp_dir, "fusion")
            legacy_fusion_dir = os.path.join(legacy_interp_dir, "fusion")
            trans_base_dir = os.path.join(paper_path, "06_translates")
            nllb_dir = os.path.join(trans_base_dir, "nllb")
            judge_dir = os.path.join(trans_base_dir, "judge")
            trans_fusion_dir = os.path.join(trans_base_dir, "fusion")
            full_text_candidates = [
                os.path.join(fusion_dir, "full_text.json"),
                os.path.join(legacy_fusion_dir, "full_text.json"),
            ]
            full_text_trans_candidates = [
                os.path.join(trans_fusion_dir, "full_text_trans.json"),
                os.path.join(legacy_interp_dir, "full_text_trans.json"),
                os.path.join(legacy_fusion_dir, "full_text_trans.json"),
            ]
            full_text_path = next((p for p in full_text_candidates if os.path.exists(p)), full_text_candidates[0])
            full_text_trans_path = next((p for p in full_text_trans_candidates if os.path.exists(p)), full_text_trans_candidates[0])
            reflow_semantic_path = os.path.join(trans_base_dir, "reflow", "semantic_sections.json")
            guide_path = os.path.join(rec_dir, "temp", "full_text_context_guide.json")

            has_origin = bool(origin_pages)
            has_stack_a = (
                os.path.exists(stk_a_dir) and any(f.endswith('.json') for f in os.listdir(stk_a_dir))
                if os.path.exists(stk_a_dir)
                else False
            )
            has_stack_b = (
                os.path.exists(stk_b_dir) and any(f.endswith('.json') for f in os.listdir(stk_b_dir))
                if os.path.exists(stk_b_dir)
                else False
            )
            has_fusion = os.path.exists(full_text_path)
            has_nllb = (
                os.path.exists(nllb_dir) and any(f.endswith('_nllb.json') for f in os.listdir(nllb_dir))
                if os.path.exists(nllb_dir)
                else False
            )
            has_judge = (
                os.path.exists(judge_dir) and any(f.endswith('_judged.json') for f in os.listdir(judge_dir))
                if os.path.exists(judge_dir)
                else False
            )
            has_trans_b = os.path.exists(full_text_trans_path)
            has_reflow_sections = False
            reflow_generation_mode = ""
            has_reflow_llm_ready = False
            if os.path.exists(reflow_semantic_path):
                try:
                    reflow_payload = deps.load_json_locked(reflow_semantic_path, {})
                    if isinstance(reflow_payload, dict):
                        reflow_sections = reflow_payload.get("sections", [])
                        has_reflow_sections = isinstance(reflow_sections, list) and len(reflow_sections) > 0
                        meta = reflow_payload.get("meta", {})
                        if isinstance(meta, dict):
                            reflow_generation_mode = str(meta.get("generation_mode") or "").strip().lower()
                            if not reflow_generation_mode:
                                llm_note = str(meta.get("llm_error") or "").strip().lower()
                                if llm_note.startswith("used=task_5interpret"):
                                    reflow_generation_mode = "task_5interpret"
                                elif "used=task_5b_reflow" in llm_note:
                                    reflow_generation_mode = "task_5b_reflow"
                        has_reflow_llm_ready = bool(
                            has_reflow_sections and deps._flowb_is_ready_generation_mode(reflow_generation_mode)
                        )
                except Exception as reflow_meta_err:
                    deps.logger.warning(
                        "[get_status] reflow meta parse failed for %s/%s: %s",
                        pid,
                        paper_id,
                        reflow_meta_err,
                    )

            has_trans_b_ready = bool(has_trans_b and has_reflow_llm_ready)
            flowb_reflow_issue = ""
            if has_trans_b and not has_trans_b_ready:
                if not os.path.exists(reflow_semantic_path):
                    flowb_reflow_issue = "flowb_reflow_missing"
                elif not has_reflow_sections:
                    flowb_reflow_issue = "flowb_reflow_empty"
                elif reflow_generation_mode == "heuristic_fallback":
                    flowb_reflow_issue = "flowb_reflow_fallback"
                elif reflow_generation_mode:
                    flowb_reflow_issue = f"flowb_reflow_{reflow_generation_mode}"
                else:
                    flowb_reflow_issue = "flowb_reflow_unknown_mode"

            if has_trans_b and not has_nllb:
                has_nllb = True
            if has_trans_b and not has_judge:
                has_judge = True

            status_cv = 'done' if has_raw else 'pending'
            status_fix = 'done' if has_fixed else ('processing' if has_raw else 'pending')

            db_row = paper_db_map.get(paper_id)
            db_interp = (db_row.interpretation_status or '').lower() if db_row else ''
            db_t5_busy = db_interp in {deps.Paper.STATUS_T5_QUEUED, deps.Paper.STATUS_T5_RUNNING}
            db_flowb_busy = db_interp in {'processing_b', 'translating_local', 'judging_llm'}

            hard_summary_error = summary_error in {"quota_error", "general_error", "parse_error"}

            if has_summary:
                status_trans = 'done'
            elif db_t5_busy:
                status_trans = 'processing'
            elif hard_summary_error:
                status_trans = 'error'
            elif has_fixed:
                status_trans = 'processing'
            else:
                status_trans = 'pending'

            artifacts_ready = has_summary

            if has_trans_b_ready:
                db_status = 'ready_B'
                if db_row and db_interp in {'translating_local', 'judging_llm', 'processing_b'}:
                    try:
                        deps._update_paper_status(
                            pid,
                            paper_id,
                            'ready_B',
                            'Auto-healed: bilingual + LLM reflow artifacts detected',
                        )
                        deps.logger.info(
                            "[status] Auto-healed %s/%s: %s -> ready_B (strict gate)",
                            pid,
                            paper_id,
                            db_interp,
                        )
                    except Exception as _heal_err:
                        deps.logger.warning("[status] Auto-heal failed for %s/%s: %s", pid, paper_id, _heal_err)
            elif has_trans_b and not has_trans_b_ready and db_flowb_busy:
                stale_sec = max(60, int(os.environ.get("LITERATURE_FLOWB_STALE_SEC", "7500")))
                updated = db_row.updated_at if db_row else None
                is_stale = True
                if isinstance(updated, datetime):
                    if updated.tzinfo is not None:
                        updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
                    age_sec = (datetime.utcnow() - updated).total_seconds()
                    is_stale = age_sec >= stale_sec

                if db_flowb_busy or not is_stale:
                    db_status = 'processing_B'
                else:
                    db_status = 'need_retry'
                    if not summary_error:
                        summary_error = flowb_reflow_issue or 'flowb_reflow_not_ready'
            elif db_interp in {'ready_b', 'bilingual_ready'}:
                db_status = 'need_retry'
                if not summary_error:
                    summary_error = flowb_reflow_issue or 'flowb_artifact_missing'
            elif db_flowb_busy:
                stale_sec = max(60, int(os.environ.get("LITERATURE_FLOWB_STALE_SEC", "7500")))
                updated = db_row.updated_at if db_row else None
                is_stale = True
                if isinstance(updated, datetime):
                    if updated.tzinfo is not None:
                        updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
                    age_sec = (datetime.utcnow() - updated).total_seconds()
                    is_stale = age_sec >= stale_sec

                if is_stale and not has_trans_b:
                    db_status = 'need_retry'
                    if not summary_error:
                        summary_error = 'stale_flowb'
                else:
                    db_status = 'processing_B'
            elif db_interp == deps.Paper.STATUS_T5_QUEUED:
                db_status = 'gold_ready' if artifacts_ready else deps.Paper.STATUS_T5_QUEUED
            elif db_interp == deps.Paper.STATUS_T5_RUNNING:
                db_status = 'gold_ready' if artifacts_ready else deps.Paper.STATUS_T5_RUNNING
            elif db_interp == deps.Paper.STATUS_GOLD_READY and artifacts_ready:
                db_status = 'gold_ready'
            elif db_interp == deps.Paper.STATUS_GOLD_READY and not artifacts_ready:
                db_status = 'need_retry'
            elif db_interp == deps.Paper.STATUS_ANALYZING and artifacts_ready:
                db_status = 'gold_ready'
            elif db_interp in {'ready_a', 'rules_ready', 'ocr_ready'}:
                db_status = 'gold_ready'
            elif db_interp == deps.Paper.STATUS_FAILED:
                db_status = 'need_retry'
            elif db_interp == deps.Paper.STATUS_ANALYZING:
                stale_sec = max(60, int(os.environ.get("LITERATURE_ANALYZING_STALE_SEC", "600")))
                updated = db_row.updated_at if db_row else None
                is_stale = True
                if isinstance(updated, datetime):
                    if updated.tzinfo is not None:
                        updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
                    age_sec = (datetime.utcnow() - updated).total_seconds()
                    is_stale = age_sec >= stale_sec
                lock_path = os.path.join(os.environ.get("LOCK_ROOT", "/tmp/roothinks-locks"), f"literature_active_{pid}.lock")
                pid_active = os.path.exists(lock_path)
                if pid_active:
                    try:
                        mtime = os.path.getmtime(lock_path)
                        lock_timeout = float(os.environ.get("LITERATURE_LOCK_TIMEOUT_SEC", "3600"))
                        if time.time() - mtime > lock_timeout:
                            pid_active = False
                    except Exception:
                        pass
                if is_stale and not pid_active:
                    db_status = 'need_retry'
                    if not summary_error:
                        summary_error = 'stale_analyzing'
                else:
                    db_status = 'analyzing'
            elif db_interp == deps.Paper.STATUS_PENDING:
                db_status = 'pending'
            else:
                if has_summary:
                    db_status = 'gold_ready'
                elif hard_summary_error:
                    db_status = 'need_retry'
                elif has_fixed and not has_summary and not hard_summary_error:
                    db_status = 'analyzing'
                elif has_raw:
                    db_status = 'analyzing'
                else:
                    db_status = 'pending'

            if db_status in {deps.Paper.STATUS_T5_QUEUED, deps.Paper.STATUS_T5_RUNNING} and status_trans != 'done':
                status_trans = 'processing'

            if db_status == 'need_retry' and status_trans != 'done':
                status_trans = 'error'
                if not summary_error:
                    summary_error = 'missing_summary'

            flow_a_ready = bool(has_summary)
            flow_b_ready = bool(has_trans_b_ready)
            if db_status == 'ready_B':
                flow_b_ready = True

            if db_status == 'ready_B':
                flow_status = 'ready_B'
            elif db_status == 'processing_B':
                flow_status = 'processing_B'
            elif db_status in {'gold_ready'}:
                flow_status = 'ready_A'
            elif db_status in {'analyzing', deps.Paper.STATUS_T5_QUEUED, deps.Paper.STATUS_T5_RUNNING}:
                flow_status = 'processing_A'
            elif db_status == 'need_retry':
                flow_status = 'failed'
            else:
                flow_status = 'pending'

            s_processing_a = flow_status == 'processing_A'
            s_processing_b = flow_status == 'processing_B'

            stages = {
                "s1": 'done' if has_origin else 'pending',
                "s2": 'done' if has_stack_a else ('processing' if s_processing_a and has_origin else 'pending'),
                "s3": 'done' if has_stack_b else ('processing' if s_processing_a and has_origin else 'pending'),
                "s4": 'done' if has_raw else ('processing' if s_processing_a and (has_stack_a or has_stack_b) else 'pending'),
                "s5": 'done'
                if (db_row and db_row.title not in [None, '', paper_id + '.pdf', paper_id])
                else ('processing' if s_processing_a and has_raw else 'pending'),
                "s6": 'done' if os.path.exists(guide_path) else ('processing' if s_processing_a and has_raw else 'pending'),
                "s7": 'done' if has_fixed else ('processing' if s_processing_a and has_raw else 'pending'),
                "s8": 'done' if has_fusion else ('processing' if s_processing_a and has_fixed else 'pending'),
                "s9": 'done'
                if has_summary
                else ('error' if hard_summary_error else ('processing' if s_processing_a and has_fixed else 'pending')),
                "s10": 'done' if has_nllb else ('processing' if s_processing_b else 'pending'),
                "s11": 'done' if has_judge else ('processing' if s_processing_b else 'pending'),
                "s12": 'done'
                if has_trans_b_ready
                else (
                    'error'
                    if has_trans_b and not has_trans_b_ready and not s_processing_b
                    else ('processing' if s_processing_b else 'pending')
                ),
            }

            row = {
                "paper_id": paper_id,
                "filename": filename,
                "status_cv": status_cv,
                "status_fix": status_fix,
                "status_trans": status_trans,
                "db_status": db_status,
                "flow_status": flow_status,
                "flow_a_ready": flow_a_ready,
                "flow_b_ready": flow_b_ready,
                "stages": stages,
                "has_fixed": has_fixed,
                "has_trans": has_summary,
                "flowb_generation_mode": reflow_generation_mode or None,
                "flowb_llm_ready": bool(has_trans_b_ready),
                "error_type": classify_error(summary_error) if db_status in {'need_retry', 'failed'} else None,
            }

            # [progress] 對外的中性進度表示。前端顯示一律吃這組欄位，
            # 不再讀 stages / flow_status —— 那些欄位會把內部流程
            # （雙軌 OCR、規則仲裁、語意重組…）攤給任何看得到這個回應的人。
            row["milestones"] = build_milestones(stages, flow_a_ready, flow_b_ready)
            row["progress_pct"] = progress_pct_of(row["milestones"])
            row["progress_state"] = progress_state_of(flow_status)

            # [usage] 該篇累計的 LLM token 與費用，供解析後決定是否續跑翻譯。
            # 用量在迴圈外一次查完（usage_by_paper），不要每篇各開一條連線：
            # 這支 API 每 3 秒被輪詢一次且會列出全部文獻，逐篇查等於
            # 180 篇的專案每 3 秒建 180 條 SQLite 連線。
            row["llm_usage"] = usage_by_paper.get(paper_id)

            papers.append(strip_internal_fields(row))

    return jsonify({"papers": papers})


def get_region_image_impl(deps):
    """
    獲取完整頁面圖片，用於 Split View 對照
    """
    pid = deps._normalize_literature_pid(request.args.get('pid'))
    paper_id = request.args.get('paper_id')
    page_raw = request.args.get('page', '1')
    if not pid or not paper_id:
        return "Missing/invalid pid or paper_id", 400
    try:
        page = max(int(page_raw), 1)
    except Exception:
        return "Invalid page", 400
    try:
        paper_id = deps._safe_paper_id(paper_id)
    except deps.BadRequest:
        return "Invalid paper_id", 400

    paper_root = _paper_dir(deps, pid, paper_id, for_write=False)
    paper_dir = deps.safe_join_under(paper_root, "00_origins")
    legacy_paper_root = deps.safe_join_under(deps.DATA_ROOT, pid, paper_id)
    legacy_paper_dir = deps.safe_join_under(legacy_paper_root, "00_origins")
    search_dirs = [paper_dir]
    if legacy_paper_dir != paper_dir:
        search_dirs.append(legacy_paper_dir)

    deps.logger.info(f"[get_region_image] Looking for image: pid={pid}, paper_id={paper_id}, page={page}")
    deps.logger.info(f"[get_region_image] Searching in directory: {paper_dir}")
    deps.logger.info(f"[get_region_image] Directory exists: {os.path.exists(paper_dir)}")

    ok, msg = deps._ensure_task4_assets(pid, paper_id)
    if not ok:
        deps.logger.warning(f"[get_region_image] Asset check failed: {msg}")

    try:
        for candidate_dir in search_dirs:
            for ext in ['jpg', 'jpeg', 'png']:
                img_path = deps.safe_join_under(candidate_dir, f"page_{page}.{ext}")
                deps.logger.info(f"[get_region_image] Trying: {img_path} - Exists: {os.path.exists(img_path)}")
                if os.path.exists(img_path):
                    mimetype = f'image/{"jpeg" if ext in ["jpg", "jpeg"] else ext}'
                    deps.logger.info(f"[get_region_image] Found! Returning: {img_path}")
                    return send_file(img_path, mimetype=mimetype, as_attachment=False)

        for candidate_dir in search_dirs:
            if os.path.exists(candidate_dir):
                files = os.listdir(candidate_dir)
                deps.logger.warning(f"[get_region_image] Image not found. Files in directory: {files}")
            else:
                deps.logger.warning(f"[get_region_image] Directory does not exist: {candidate_dir}")

        return "Image not found", 404
    except Exception as e:
        deps.logger.error(f"[get_region_image] Error: {e}", exc_info=True)
        return "Internal server error", 500


def get_block_json_impl(deps):
    """
    獲取單一區塊的 JSON 數據 (優先回傳 Fixed)
    """
    pid = deps._normalize_literature_pid(request.args.get('pid'))
    paper_id = request.args.get('paper_id')
    block = request.args.get('block')
    if not pid or not paper_id:
        return jsonify({"status": "error", "message": "Missing/invalid pid or paper_id"}), 400
    try:
        paper_id = deps._safe_paper_id(paper_id)
    except deps.BadRequest:
        return jsonify({"status": "error", "message": "Invalid paper_id"}), 400

    paper_root = _paper_dir(deps, pid, paper_id, for_write=False)
    base_dir = deps.safe_join_under(paper_root, "03_recognizes")
    legacy_paper_root = deps.safe_join_under(deps.DATA_ROOT, pid, paper_id)
    legacy_base_dir = deps.safe_join_under(legacy_paper_root, "03_recognizes")
    if not os.path.exists(base_dir) and os.path.exists(legacy_base_dir):
        base_dir = legacy_base_dir

    ok, msg = deps._ensure_task4_assets(pid, paper_id)
    if not ok and not os.path.exists(base_dir):
        deps.logger.warning("[get_block_json] task4 assets unavailable for %s/%s: %s", pid, paper_id, msg)
        return jsonify({"status": "error", "message": "Task4 assets unavailable"}), 503

    safe_block = deps.re.sub(r"[^A-Za-z0-9_-]+", "", str(block or ""))
    fixed_path = deps.safe_join_under(base_dir, f"{safe_block}_fixed.json")
    raw_path = deps.safe_join_under(base_dir, f"{safe_block}_raw.json")

    source_kind, target_path, content, raw_content = _task4_resolve_block_source(
        base_dir,
        safe_block,
        deps,
    )
    raw_path = deps.safe_join_under(base_dir, f"{safe_block}_raw.json")
    fixed_path = deps.safe_join_under(base_dir, f"{safe_block}_fixed.json")

    if os.path.exists(target_path):
        try:
            if content is None:
                content = deps.load_json_locked(target_path, {})
            # Fixed 檔若缺 score，回填 raw score，避免前端顯示 N/A 造成誤判。
            if (
                source_kind == "fixed"
                and isinstance(content, list)
                and isinstance(raw_content, list)
            ):
                if raw_content:
                    raw_by_seq = {}
                    raw_by_id = {}
                    raw_by_bbox = {}
                    for rb in raw_content:
                        if not isinstance(rb, dict):
                            continue
                        sid = str(rb.get("seq_id", "") or "").strip()
                        bid = str(rb.get("id", "") or "").strip()
                        bb = rb.get("bbox")
                        if sid:
                            raw_by_seq[sid] = rb
                        if bid:
                            raw_by_id[bid] = rb
                        if isinstance(bb, list) and len(bb) >= 4:
                            raw_by_bbox[tuple(int(v) for v in bb[:4])] = rb

                    def _score_num(v):
                        try:
                            x = float(v)
                        except Exception:
                            return 0.0
                        return x if x > 0 else 0.0

                    for fb in content:
                        if not isinstance(fb, dict):
                            continue
                        current_score = _score_num(fb.get("score", 0.0))
                        current_arb = _score_num(fb.get("arbiter_score", 0.0))
                        if current_score > 0 and current_arb > 0:
                            continue

                        sid = str(fb.get("seq_id", "") or "").strip()
                        bid = str(fb.get("id", "") or "").strip()
                        bb = fb.get("bbox")
                        source_block = None
                        if sid and sid in raw_by_seq:
                            source_block = raw_by_seq[sid]
                        elif bid and bid in raw_by_id:
                            source_block = raw_by_id[bid]
                        elif isinstance(bb, list) and len(bb) >= 4:
                            source_block = raw_by_bbox.get(tuple(int(v) for v in bb[:4]))
                        if not isinstance(source_block, dict):
                            continue

                        raw_score = _score_num(source_block.get("score", 0.0))
                        raw_arb = _score_num(source_block.get("arbiter_score", 0.0))
                        if current_score <= 0 and raw_score > 0:
                            fb["score"] = round(raw_score, 4)
                        if current_arb <= 0 and raw_arb > 0:
                            fb["arbiter_score"] = round(raw_arb, 4)
                        if not str(fb.get("source", "") or "").strip():
                            src = str(source_block.get("source", "") or "").strip()
                            if src:
                                fb["source"] = src
            if isinstance(content, list):
                content = _task4_sort_blocks(content)
            return jsonify({"status": "success", "content": content})
        except Exception as e:
            return deps._internal_error("get_block_json", e)
    return jsonify({"status": "error", "message": "File not found"}), 404


def get_block_manifest_impl(deps):
    """
    回傳可編輯區塊清單，供 Split View 切頁使用。
    規則：以 03_recognizes 內 text_{n}_fixed.json / text_{n}_raw.json 為來源。
    """
    pid = deps._normalize_literature_pid(request.args.get('pid'))
    paper_id = request.args.get('paper_id')

    if not pid or not paper_id:
        return jsonify({"status": "error", "message": "Missing/invalid pid or paper_id"}), 400
    try:
        paper_id = deps._safe_paper_id(paper_id)
    except deps.BadRequest:
        return jsonify({"status": "error", "message": "Invalid paper_id"}), 400

    paper_root = _paper_dir(deps, pid, paper_id, for_write=False)
    recog_dir = deps.safe_join_under(paper_root, "03_recognizes")
    legacy_paper_root = deps.safe_join_under(deps.DATA_ROOT, pid, paper_id)
    legacy_recog_dir = deps.safe_join_under(legacy_paper_root, "03_recognizes")
    if not os.path.exists(recog_dir) and os.path.exists(legacy_recog_dir):
        recog_dir = legacy_recog_dir

    ok, msg = deps._ensure_task4_assets(pid, paper_id)
    if not ok and not os.path.exists(recog_dir):
        deps.logger.warning("[get_block_manifest] task4 assets unavailable for %s/%s: %s", pid, paper_id, msg)
        return jsonify({"status": "error", "message": "Task4 assets unavailable"}), 503

    if not os.path.exists(recog_dir):
        return jsonify({"status": "success", "pages": []})

    pages_map = {}
    try:
        for fname in os.listdir(recog_dir):
            if not fname.startswith("text_") or not fname.endswith(".json"):
                continue

            stem = fname[:-5]
            fpath = os.path.join(recog_dir, fname)
            if stem.endswith("_fixed"):
                base = stem[:-6]
                source, _, _, _ = _task4_resolve_block_source(recog_dir, base, deps)
            elif stem.endswith("_raw"):
                base = stem[:-4]
                source = "raw"
            else:
                base = stem
                source = "raw"

            parts = base.split("_")
            if len(parts) < 2:
                continue

            try:
                page_num = int(parts[1])
            except ValueError:
                continue

            existing = pages_map.get(page_num)
            if existing is None:
                pages_map[page_num] = {
                    "page": page_num,
                    "block": base,
                    "has_fixed": source == "fixed",
                }
            elif source == "fixed":
                existing["block"] = base
                existing["has_fixed"] = True

        pages = [pages_map[k] for k in sorted(pages_map.keys())]
        return jsonify({"status": "success", "pages": pages})
    except Exception as e:
        return deps._internal_error("get_block_manifest", e)


def save_correction_impl(deps):
    """
    儲存使用者的修正 (Task 4 Intervention)
    """
    data = request.get_json(silent=True) or {}
    pid = deps._normalize_literature_pid(data.get('pid'))
    paper_id = data.get('paper_id')
    block_name = data.get('block_name')
    new_content = data.get('content')

    if not pid:
        return jsonify({"status": "error", "message": "Invalid pid"}), 404
    if not all([paper_id, block_name, new_content]):
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
    try:
        paper_id = deps._safe_paper_id(paper_id)
    except deps.BadRequest:
        return jsonify({"status": "error", "message": "Invalid paper_id"}), 400

    try:
        paper_root = _paper_dir(deps, pid, paper_id, for_write=True)
        save_dir = deps.safe_join_under(paper_root, "03_recognizes")
        os.makedirs(save_dir, exist_ok=True)
        safe_block_name = deps.re.sub(r"[^A-Za-z0-9_-]+", "", str(block_name))
        save_path = deps.safe_join_under(save_dir, f"{safe_block_name}_fixed.json")

        deps.write_json_locked(save_path, new_content)

        cleanup_targets = [
            deps.safe_join_under(paper_root, "06_translates", "fusion", "full_text_trans.json"),
            deps.safe_join_under(paper_root, "06_translates", "reflow", "semantic_sections.json"),
            deps.safe_join_under(paper_root, "06_translates", "reflow", "document_flow.json"),
            deps.safe_join_under(paper_root, "_jobs", "flow_b_result.json"),
        ]
        removed_count = 0
        for target in cleanup_targets:
            try:
                if os.path.exists(target):
                    os.remove(target)
                    removed_count += 1
            except Exception as rm_err:
                deps.logger.warning("save_correction cleanup failed for %s: %s", target, rm_err)

        deps._update_paper_status(pid, paper_id, 'ready_A', "Correction saved; Flow B cache cleared")

        return jsonify({"status": "success", "message": f"Correction saved. Flow B cache cleared ({removed_count})."})

    except Exception as e:
        return deps._internal_error("save_correction", e)


def get_full_json_impl(deps):
    """
    For Preview Modal (Summary / FullText / Raw)
    """
    pid = deps._normalize_literature_pid(request.args.get('pid'))
    paper_id = request.args.get('paper_id')
    type_ = request.args.get('type')
    if not pid or not paper_id:
        return jsonify({"error": "Missing/invalid pid or paper_id"}), 400
    try:
        paper_id = deps._safe_paper_id(paper_id)
    except deps.BadRequest:
        return jsonify({"error": "Invalid paper_id"}), 400

    p_dir = _paper_dir(deps, pid, paper_id, for_write=False)
    legacy_p_dir = deps.safe_join_under(deps.safe_join_under(deps.DATA_ROOT, pid), paper_id)

    try:
        if type_ == 'summary':
            summary_candidates = [
                os.path.join(p_dir, "05_interprets", "summary.json"),
                os.path.join(legacy_p_dir, "05_interprets", "summary.json"),
            ]
            path = next((p for p in summary_candidates if os.path.exists(p)), summary_candidates[0])
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    if isinstance(data, dict):
                        if data.get('mode') == 'error_fallback':
                            error_msg = data.get('error_msg', '摘要生成失敗')
                            return jsonify(
                                {
                                    "abstract_zh": f"[系統訊息] {error_msg}\n\n請稍後重試或聯繫管理員。",
                                    "key_findings": [],
                                    "mode": "error_fallback",
                                }
                            )
                        elif 'error' in data and 'abstract_zh' not in data:
                            error_msg = data.get('error', '摘要生成失敗')
                            if len(error_msg) > 200:
                                error_msg = "API 配額已用盡，摘要服務暫時不可用。"
                            return jsonify(
                                {
                                    "abstract_zh": f"[系統訊息] {error_msg}\n\n請稍後重試或檢查 LAVA 網路設定。",
                                    "key_findings": [],
                                }
                            )
                    return jsonify(data)
            return jsonify({"abstract_zh": "Summary not yet generated.", "key_findings": []})

        elif type_ == 'fulltext':
            fulltext_candidates = [
                os.path.join(p_dir, "06_translates", "fusion", "full_text_trans.json"),
                os.path.join(p_dir, "05_interprets", "fusion", "full_text.json"),
                os.path.join(legacy_p_dir, "05_interprets", "full_text_trans.json"),
                os.path.join(legacy_p_dir, "05_interprets", "fusion", "full_text_trans.json"),
                os.path.join(legacy_p_dir, "05_interprets", "fusion", "full_text.json"),
            ]
            path = next((p for p in fulltext_candidates if os.path.exists(p)), fulltext_candidates[0])

            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return jsonify(json.load(f))
            return jsonify({"message": "Full text not yet fused."})

        else:
            path = os.path.join(p_dir, "03_recognizes")
            if os.path.exists(path):
                files = sorted([f for f in os.listdir(path) if f.endswith(".json")])
                return jsonify(files)
            return jsonify([])

    except Exception as e:
        deps.logger.error("get_full_json failed: %s", e)
        return jsonify({"error": "Internal server error"}), 500
