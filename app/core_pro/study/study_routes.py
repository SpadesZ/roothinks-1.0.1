# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/study_routes.py
# 模組定位: Study 核心層；協調論文閱讀、筆記/矩陣與 AI tutor 的專案內狀態。
# 主要責任: 提供 Study 工作台、資料就緒、閱讀/媒體、筆記與 tutor API，協調 loader、index 和 matrix。
# 上下游: Study routes/static JS 呼叫本層，讀取 Literature 素材並把筆記、對話或矩陣保存到 data/<pid>/study。
# 維護邊界: 所有讀寫保留 PID、user 與 section scope；草稿/版本/快取不得跨使用者、跨章或以舊非同步回應覆蓋新狀態。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/core_pro/study/study_routes.py) #版本 v2.7-MediaGalleryVnsGuard #更版時間 20260430-1930
# [MVP+Prototype Handoff Header]
# 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
# 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 與 Study Matrix/Fulltext/Task7 互動相關的階段性變更，請由人類團隊後續統一 hardening。
import hashlib
import json
import time
import os
import logging
import re
from flask import Blueprint, request, jsonify, current_app, render_template, redirect, send_file
from urllib.parse import quote
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound
from app.models import db, Paper, Project
from app.services.context_chain_service import ContextChainService
from app.services.formal_project_sync import resolve_data_root
from app.security import (
    enforce_project_ownership,
    is_review_scoped,
    check_and_save_json,
    mutate_versioned_json,
    update_json_locked,
    get_acl_context,
    load_json_locked,
    safe_join_under,
    validate_id,
    write_json_locked,
)
from app.core_pro.storage_layout import list_literature_papers, resolve_literature_paper_dir
from app.core_pro.study import study_anchors, study_evidence_notes

# [Upgrade] 引入新版 AI 引擎 (v2.0)
from app.core_pro.study.study_matrix import MatrixEngine, matrix_outcome
from app.llm_service.matching_tasks.task_7qachat import TutorEngine

# 定義 Blueprint
study_bp = Blueprint('study_bp', __name__)
logger = logging.getLogger("StudyRoutes")

# 初始化引擎
matrix_engine = MatrixEngine()
tutor_engine = TutorEngine()
context_chain_service = None


@study_bp.before_request
def _enforce_study_project_acl():
    if not request.path.startswith("/api/study/"):
        return None
    body = request.get_json(silent=True) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else {}
    if body is not None and not isinstance(body, dict):
        return jsonify({"ok": False, "msg": "Invalid request"}), 400
    supplied = [request.view_args.get("pid") if request.view_args else None,
                request.args.get("pid"), (body or {}).get("pid")]
    pids = {_normalize_study_pid(value) for value in supplied if value}
    if len(pids) > 1:
        return jsonify({"ok": False, "msg": "Conflicting project identifiers"}), 400
    raw_pid = next((value for value in supplied if value), None)
    if not raw_pid:
        return None
    pid = _normalize_study_pid(raw_pid)
    if not pid:
        return jsonify({"ok": False, "msg": "Invalid PID"}), 404
    try:
        enforce_project_ownership(pid)
    except Forbidden:
        return jsonify({"ok": False, "msg": "Forbidden"}), 403
    except Exception:
        return jsonify({"ok": False, "msg": "Unauthorized"}), 401
    if _review_scope(pid) and request.endpoint not in _REVIEW_SCOPE_ENDPOINTS:
        return jsonify({"ok": False, "review_scope": True,
                        "msg": "教授審閱權限只包含研究者分享的審閱資料與論文本身"}), 403
    return None


# 教授審閱身分（security.is_review_scoped）在 Study 只能用這些端點：論文本身與「已請求審閱」的證據筆記。
# 自由筆記、對話、矩陣是研究者的私人工作，不在審閱範圍。白名單：新增端點預設不開放給審閱身分。
_REVIEW_SCOPE_ENDPOINTS = frozenset({
    'study_bp.check_data_readiness', 'study_bp.get_paper_list', 'study_bp.get_fulltext',
    'study_bp.get_media_gallery', 'study_bp.get_media_asset',
    'study_bp.list_evidence_notes', 'study_bp.export_evidence_notes',
})


def _review_scope(pid):
    """目前登入者在此專案是否為教授審閱身分；未登入（token／開發模式）不適用。"""
    from flask_login import current_user
    try:
        return bool(current_user.is_authenticated and is_review_scoped(current_user.id, pid))
    except AttributeError:
        return False


def get_context_chain_service():
    global context_chain_service
    data_root = resolve_data_root()
    if context_chain_service is None or context_chain_service.data_root != data_root:
        context_chain_service = ContextChainService(data_root=data_root)
    return context_chain_service


def _normalize_study_pid(pid: str) -> str:
    """Resolve incoming pid to an existing project id (prefer formal -p when available)."""
    p = str(pid or "").strip()
    if not p:
        return ""
    try:
        if p.endswith("-p"):
            validate_id(p[:-2], "project_id")
        else:
            validate_id(p, "project_id")
    except Exception:
        return ""

    candidates = [p]
    if p.endswith("-p"):
        base = p[:-2]
        if base:
            candidates.append(base)
    else:
        candidates.append(f"{p}-p")
    candidates = list(dict.fromkeys([c for c in candidates if c]))

    rows = Project.query.filter(Project.project_id.in_(candidates)).all()
    found = {r.project_id: r for r in rows}

    if p.endswith("-p"):
        base = p[:-2]
        if p in found:
            return p
        if base and base in found:
            return base
        return ""

    formal = f"{p}-p"
    if formal in found:
        return formal
    if p in found:
        return p
    return ""


def _safe_conv_id(conv_id: str) -> str:
    return validate_id(conv_id, "conv_id")


def _safe_matrix_id(matrix_id: str) -> str:
    return validate_id(matrix_id, "matrix_id")


def _internal_error(scope: str, exc: Exception):
    logger.error("[%s] %s", scope, exc, exc_info=True)
    return jsonify({"ok": False, "msg": "Internal server error"}), 500


def _get_matrix_history_dir(pid):
    """Get matrix history directory for a project."""
    pid = _normalize_study_pid(pid)
    if not pid:
        raise ValueError("Invalid PID")
    data_root = resolve_data_root()
    base_dir = safe_join_under(data_root, pid, "study", "matrix_history")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _get_legacy_papers_comp_dir(pid):
    """Legacy matrix records generated by older pipeline versions."""
    pid = _normalize_study_pid(pid)
    if not pid:
        raise ValueError("Invalid PID")
    data_root = resolve_data_root()
    return safe_join_under(data_root, pid, "study", "papers_comp")


def _get_study_note_path(pid):
    """Primary note path: data/<pid>/study/<pid>_note.json"""
    pid = _normalize_study_pid(pid)
    if not pid:
        raise ValueError("Invalid PID")
    pid_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(pid))
    data_root = resolve_data_root()
    study_dir = safe_join_under(data_root, pid, "study")
    os.makedirs(study_dir, exist_ok=True)
    return safe_join_under(study_dir, f"{pid_safe}_note.json")


def _get_latest_matrix_snapshot_path(pid):
    """Stable snapshot for refresh restore: data/<pid>/study/latest_matrix.json"""
    pid = _normalize_study_pid(pid)
    if not pid:
        raise ValueError("Invalid PID")
    data_root = resolve_data_root()
    study_dir = safe_join_under(data_root, pid, "study")
    os.makedirs(study_dir, exist_ok=True)
    return safe_join_under(study_dir, "latest_matrix.json")


def _load_study_note_text(pid):
    """缺檔可無筆記；讀取失敗不假裝空白，legacy 原文只讀不搬移。"""
    note_path = _get_study_note_path(pid)
    old_path = os.path.join(resolve_data_root(), "note", f"{pid}_note.json")
    for path in (note_path, old_path):
        if os.path.exists(path):
            data = load_json_locked(path, {}, strict=True)
            return str(data.get("notes") or "").strip() if isinstance(data, dict) else str(data or "").strip()
    legacy = os.path.join(resolve_data_root(), pid, "study_notes.txt")
    if os.path.exists(legacy):
        with open(legacy, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _write_latest_snapshot(pid, record_obj):
    """Persist a stable latest snapshot without breaking request flow."""
    try:
        if not isinstance(record_obj, dict) or not isinstance(record_obj.get('data'), dict):
            return
        snap_path = _get_latest_matrix_snapshot_path(pid)
        write_json_locked(snap_path, record_obj)
    except Exception as e:
        logger.warning(f"[latest_snapshot] write failed: {e}")


def _remove_file_if_exists(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"[matrix_history] remove file failed ({path}): {e}")


def _build_legacy_matrix_record(pid, legacy_name, legacy_path, obj):
    return {
        'matrix_id': f"legacy::{legacy_name}",
        'pid': pid,
        'criteria': obj.get('criteria', 'General') if isinstance(obj, dict) else 'General',
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(legacy_path))),
        'display_name': legacy_name,
        'is_legacy': True,
        'data': obj if isinstance(obj, dict) else {},
    }


def _collect_latest_record_without_snapshot(pid):
    """
    Resolve the latest matrix record by storage order:
    1) matrix_history/*.json (except latest.json)
    2) legacy papers_comp/*.json
    """
    hist_dir = _get_matrix_history_dir(pid)
    files = [f for f in os.listdir(hist_dir) if f.endswith('.json') and f != 'latest.json']
    if files:
        newest = max(files, key=lambda n: os.path.getmtime(os.path.join(hist_dir, n)))
        obj = load_json_locked(os.path.join(hist_dir, newest), {})
        if isinstance(obj, dict):
            if not obj.get('matrix_id'):
                obj['matrix_id'] = newest[:-5]
            obj.setdefault('display_name', obj.get('matrix_id') or newest[:-5])
            obj.setdefault('is_legacy', False)
            return obj, 'normal'

    legacy_dir = _get_legacy_papers_comp_dir(pid)
    if os.path.isdir(legacy_dir):
        legacy_files = [f for f in os.listdir(legacy_dir) if f.endswith('.json')]
        if legacy_files:
            newest_legacy = max(
                legacy_files,
                key=lambda n: os.path.getmtime(os.path.join(legacy_dir, n)),
            )
            legacy_name = newest_legacy[:-5]
            legacy_path = os.path.join(legacy_dir, newest_legacy)
            obj = load_json_locked(legacy_path, {})
            return _build_legacy_matrix_record(pid, legacy_name, legacy_path, obj), 'legacy'

    return None, None


def _refresh_matrix_latest_pointers(pid):
    """
    Recompute latest pointers after mutation (rename/delete), ensuring:
    - study/matrix_history/latest.json
    - study/latest_matrix.json
    stay consistent.
    """
    hist_dir = _get_matrix_history_dir(pid)
    latest_path = os.path.join(hist_dir, 'latest.json')
    snap_path = _get_latest_matrix_snapshot_path(pid)

    record, source = _collect_latest_record_without_snapshot(pid)
    if not record:
        _remove_file_if_exists(latest_path)
        _remove_file_if_exists(snap_path)
        return None

    if source == 'normal':
        write_json_locked(latest_path, record)
    else:
        _remove_file_if_exists(latest_path)
    _write_latest_snapshot(pid, record)
    return record


def _sanitize_summary_lines(lines, max_items=3, max_line_chars=180, max_total_chars=520):
    """Normalize noisy fallback findings into short UI-safe lines."""
    if not isinstance(lines, list):
        return []

    cleaned = []
    total = 0
    for item in lines:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if len(text) < 10:
            continue
        if len(text) > max_line_chars:
            text = text[:max_line_chars].rstrip() + "..."
        if total + len(text) > max_total_chars:
            break
        cleaned.append(text)
        total += len(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _read_float_env(key: str, default: float) -> float:
    try:
        v = float(str(os.environ.get(key, default)).strip())
        return v
    except Exception:
        return float(default)


def _read_bool_env(key: str, default: bool) -> bool:
    raw = str(os.environ.get(key, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _compute_reflow_coverage(blocks_array, reflow_data):
    full_en = 0
    full_zh = 0
    reflow_en = 0
    reflow_zh = 0
    ref_total = 0
    refs_all = set()
    ref_char_en = {}
    ref_char_zh = {}
    canonical_labels = {
        "abstract",
        "introduction",
        "related work",
        "methods",
        "results",
        "discussion",
        "conclusion",
    }
    canonical_hits = set()
    refs_used = set()

    if isinstance(blocks_array, list):
        for page_idx, page in enumerate(blocks_array, start=1):
            if not isinstance(page, dict):
                continue
            page_no = int(page.get("page", page_idx) or page_idx)
            blocks = page.get("blocks", [])
            if not isinstance(blocks, list):
                continue
            for block_idx, blk in enumerate(blocks, start=1):
                if not isinstance(blk, dict):
                    continue
                txt_en = str(blk.get("content") or "")
                txt_zh = str(blk.get("translation") or blk.get("content_zh") or "")
                full_en += len(txt_en)
                full_zh += len(txt_zh)
                ref = f"p{page_no}-b{block_idx}"
                refs_all.add(ref)
                ref_char_en[ref] = len(txt_en)
                ref_char_zh[ref] = len(txt_zh)

    sections = []
    if isinstance(reflow_data, dict):
        raw_sections = reflow_data.get("sections", [])
        if isinstance(raw_sections, list):
            sections = raw_sections
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        reflow_en += len(str(sec.get("content_en") or ""))
        reflow_zh += len(str(sec.get("content_zh") or ""))
        label = str(sec.get("section_label") or "").strip().lower()
        if label in canonical_labels:
            canonical_hits.add(label)
        for ref in sec.get("source_block_refs", []) if isinstance(sec.get("source_block_refs"), list) else []:
            val = str(ref or "").strip()
            if val:
                refs_used.add(val)

    ratio_en = (reflow_en / full_en) if full_en > 0 else 0.0
    ratio_zh = (reflow_zh / full_zh) if full_zh > 0 else 0.0
    rendered_ratio = max(ratio_en, ratio_zh)

    ref_total = len(refs_all)
    ref_used = len([r for r in refs_used if r in refs_all])
    ref_coverage = (ref_used / ref_total) if ref_total > 0 else 0.0
    ref_used_en = sum(ref_char_en.get(r, 0) for r in refs_used if r in refs_all)
    ref_used_zh = sum(ref_char_zh.get(r, 0) for r in refs_used if r in refs_all)
    char_cov_en = (ref_used_en / full_en) if full_en > 0 else 0.0
    char_cov_zh = (ref_used_zh / full_zh) if full_zh > 0 else 0.0
    char_coverage = max(char_cov_en, char_cov_zh)
    section_coverage = (len(canonical_hits) / len(canonical_labels)) if canonical_labels else 0.0
    return {
        "ratio": round(char_coverage, 4),
        "ratio_en": round(ratio_en, 4),
        "ratio_zh": round(ratio_zh, 4),
        "rendered_ratio": round(rendered_ratio, 4),
        "char_coverage": round(char_coverage, 4),
        "char_coverage_en": round(char_cov_en, 4),
        "char_coverage_zh": round(char_cov_zh, 4),
        "ref_coverage": round(ref_coverage, 4),
        "ref_total": ref_total,
        "ref_used": ref_used,
        "section_coverage": round(section_coverage, 4),
        "canonical_section_hits": len(canonical_hits),
        "full_en": full_en,
        "full_zh": full_zh,
        "reflow_en": reflow_en,
        "reflow_zh": reflow_zh,
        "sections": len(sections),
    }


def _looks_like_noisy_summary(text: str) -> bool:
    """Detect OCR-noise-like pseudo-summary to avoid misleading users."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) < 60:
        return False

    zh_count = len(re.findall(r"[\u4e00-\u9fff]", t))
    en_count = len(re.findall(r"[A-Za-z]", t))
    citation_count = len(re.findall(r"\[[0-9,\s]{1,20}\]", t))
    weird_token = bool(re.search(r"[~_|]{2,}|[A-Za-z]{28,}", t))

    if zh_count <= 3 and en_count >= 80:
        return True
    if zh_count <= 5 and citation_count >= 2:
        return True
    if zh_count <= 5 and weird_token:
        return True
    return False


def _rebuild_summary_from_blocks(blocks_array, max_chars=520):
    """Build a readable extractive summary from existing paper blocks."""
    if not isinstance(blocks_array, list):
        return None

    candidates = []
    for page in blocks_array:
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page", 0) or 0)
        for blk in page.get("blocks", []):
            if not isinstance(blk, dict):
                continue
            txt = str(blk.get("translation") or blk.get("content_zh") or blk.get("content") or "")
            txt = re.sub(r"\s+", " ", txt).strip()
            if len(txt) < 40:
                continue

            # Filter obvious OCR/code/reference noise.
            if re.search(r"\b(import|numpy|pandas|yfinance|pip install|<TASK>|<ASSISTANT|<USER_)\b", txt, re.I):
                continue
            if re.search(r"\[[0-9,\s]{1,20}\]", txt) and len(re.findall(r"\[[0-9,\s]{1,20}\]", txt)) >= 3:
                continue
            if re.search(r"\b(et al\.|arXiv|Proceedings|ISBN|doi)\b", txt, re.I):
                continue

            zh_count = len(re.findall(r"[\u4e00-\u9fff]", txt))
            weird = len(re.findall(r"[~`|_{}<>\\]", txt))
            if zh_count <= 2 and weird >= 4:
                continue

            score = 0
            if page_no in (1, 2, 3):
                score += 3
            key_hits = re.findall(
                r"\b(abstract|introduction|we propose|contribution|method|results?|conclusion|framework|experiment|evaluation)\b|"
                r"(摘要|提出|方法|結果|結論|貢獻|實驗|評估)",
                txt,
                re.I,
            )
            score += len(key_hits) * 2
            score += min(len(txt) // 120, 4)
            candidates.append((score, page_no, txt))

    if not candidates:
        return None

    # Sort by score desc, then early page.
    candidates.sort(key=lambda x: (-x[0], x[1]))
    picked = []
    total = 0
    seen = set()
    for _, _, txt in candidates:
        key = txt[:80]
        if key in seen:
            continue
        seen.add(key)
        line = txt[:180].rstrip() + ("..." if len(txt) > 180 else "")
        if total + len(line) > max_chars:
            break
        picked.append(line)
        total += len(line)
        if len(picked) >= 3:
            break

    if not picked:
        return None

    abstract = "\n".join(picked)
    return {
        "abstract_zh": abstract,
        "key_findings": picked,
        "mode": "extractive_rebuild",
    }


def _is_quota_summary(summary_obj) -> bool:
    if not isinstance(summary_obj, dict):
        return False
    mode = str(summary_obj.get("mode", "") or "").lower()
    if mode == "quota_error":
        return True
    blob = json.dumps(summary_obj, ensure_ascii=False).lower()
    return ("429" in blob) or ("quota" in blob) or ("resource_exhausted" in blob)


def _is_plausible_paper_title(title: str) -> bool:
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if not t:
        return False
    if len(t) < 3 or len(t) > 260:
        return False

    lowered = t.lower()
    if lowered in {"untitled", "unknown", "n/a"}:
        return False
    if re.search(r"(研究背景與動機|context\s*&\s*background|project background)", lowered, re.I):
        return False

    # 排除看起來像段落內容而非標題的長句。
    if len(re.findall(r"[。！？!?]", t)) >= 2:
        return False
    if len(re.findall(r"[，,；;]", t)) >= 8 and len(t) > 120:
        return False
    return True


def _resolve_fulltext_display_title(pid: str, paper_id: str, payload: dict | None, project_dir: str) -> str:
    candidates = []

    try:
        db_paper = Paper.query.filter_by(paper_id=paper_id, pid=pid).first()
        if db_paper and db_paper.title:
            candidates.append(db_paper.title)
    except Exception:
        pass

    if isinstance(payload, dict):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            candidates.append(metadata.get("title"))
        candidates.append(payload.get("title"))

    try:
        paper_dir = resolve_literature_paper_dir(
            resolve_data_root(),
            pid,
            paper_id,
            for_write=False,
            migrate_legacy=True,
        )
        meta_path = safe_join_under(paper_dir, "00_origins", "metadata.json")
        if os.path.exists(meta_path):
            origin_meta = load_json_locked(meta_path, {})
            if isinstance(origin_meta, dict):
                original_filename = str(origin_meta.get("original_filename") or "").strip()
                if original_filename:
                    stem = re.sub(r"\.(pdf|docx?|txt)$", "", original_filename, flags=re.IGNORECASE).strip()
                    if stem:
                        candidates.append(stem)
    except Exception:
        pass

    candidates.append(paper_id)
    for item in candidates:
        candidate = re.sub(r"\s+", " ", str(item or "")).strip()
        if not candidate:
            continue
        if candidate == paper_id or _is_plausible_paper_title(candidate):
            return candidate

    return paper_id


def _resolve_active_pid(preferred_pid=None):
    pid = _normalize_study_pid(preferred_pid)
    if pid:
        return pid

    try:
        proj = Project.query.filter_by(status='formal').order_by(Project.created_at.desc()).first()
        if proj and proj.project_id:
            return proj.project_id
    except Exception:
        pass

    return ''


def _safe_seq_id(seq_id: str) -> str:
    s = str(seq_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", s):
        raise BadRequest("Invalid seq_id")
    return s


def _normalize_media_kind(kind_raw: str) -> tuple[str, str, str]:
    k = str(kind_raw or "").strip().lower()
    if k in {"figure", "fig", "image", "img"}:
        return "figure", "fig", "Figure"
    if k in {"table", "tbl"}:
        return "table", "table", "Table"
    if k in {"equation", "eq", "formula", "math"}:
        return "equation", "body", "Equation"
    raise BadRequest("Invalid kind")


def _to_local_data_path(data_root: str, path_raw: str) -> str:
    p = str(path_raw or "").strip()
    if not p:
        return ""

    if p.startswith("/app/data/"):
        rel = [x for x in p[len("/app/data/"):].split("/") if x and x not in {".", ".."}]
        try:
            return safe_join_under(data_root, *rel)
        except Exception:
            return ""

    if os.path.isabs(p):
        norm = os.path.abspath(p)
        try:
            if os.path.commonpath([data_root, norm]) == data_root:
                return norm
        except Exception:
            pass
        return ""

    rel = [x for x in p.replace("\\", "/").split("/") if x and x not in {".", ".."}]
    try:
        return safe_join_under(data_root, *rel)
    except Exception:
        return ""


def _pick_best_ocr_text(ocr_obj) -> tuple[str, str, float]:
    if not isinstance(ocr_obj, dict):
        return "", "", 0.0

    candidates = []
    for source in ("stack_b", "stack_a"):
        node = ocr_obj.get(source, {})
        if not isinstance(node, dict):
            continue
        text = str(node.get("content") or "").strip()
        if not text:
            continue
        try:
            score = float(node.get("score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        candidates.append((score, text, source))

    if not candidates:
        return "", "", 0.0
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    return best[1], best[2], float(best[0])


def _extract_caption_text(kind: str, text: str) -> str:
    src = str(text or "").strip()
    if not src:
        return ""

    if kind == "figure":
        m = re.search(r"(Figure\s*\d+[.:]\s*.+)", src, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:700]
    if kind == "table":
        m = re.search(r"(Table\s*\d+[.:]\s*.+)", src, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:700]

    return re.sub(r"\s+", " ", src).strip()[:420]


def _media_sort_key(item: dict) -> tuple:
    seq_id = str((item or {}).get("seq_id") or "")
    m = re.match(r"^p(\d+)_([a-z]+)_(\d+)$", seq_id)
    if m:
        return int(m.group(1)), int(m.group(3))
    try:
        page = int((item or {}).get("page", 0) or 0)
    except Exception:
        page = 0
    return page, 999999


def _seq_page_and_order(seq_id: str) -> tuple[int, int]:
    s = str(seq_id or "").strip()
    m = re.match(r"^p(\d+)_([a-z]+)_(\d+)$", s)
    if not m:
        return 0, 999999
    try:
        return int(m.group(1)), int(m.group(3))
    except Exception:
        return 0, 999999


def _study_block_sort_key(block: dict) -> tuple[int, int, str]:
    if not isinstance(block, dict):
        return 999999, 999999, ""

    try:
        reading_order = int(block.get("reading_order", 999999) or 999999)
    except Exception:
        reading_order = 999999

    seq_id = str(block.get("seq_id") or "").strip()
    _, seq_order = _seq_page_and_order(seq_id)
    return reading_order, seq_order, seq_id


def _pick_best_latex_ocr(latex_obj) -> tuple[str, float, str]:
    if not isinstance(latex_obj, dict):
        return "", 0.0, ""

    candidates = []
    for source in ("stack_b", "stack_a"):
        node = latex_obj.get(source, {})
        if not isinstance(node, dict):
            continue
        latex = str(node.get("latex") or "").strip()
        if not latex:
            continue
        try:
            confidence = float(node.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        latex_source = str(node.get("source") or source).strip()
        candidates.append((confidence, latex, latex_source))

    if not candidates:
        return "", 0.0, ""

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    return best[1], float(best[0]), best[2]


def _load_segmentation_equation_blocks(data_root: str, pid: str, paper_id: str) -> dict[int, list[dict]]:
    equations_by_page: dict[int, list[dict]] = {}

    try:
        seg_body_dir = safe_join_under(data_root, pid, "literature", "segmentation", "body")
    except Exception:
        return equations_by_page

    if not os.path.isdir(seg_body_dir):
        return equations_by_page

    for name in sorted(os.listdir(seg_body_dir)):
        if not name.lower().endswith(".json"):
            continue

        try:
            seg_path = safe_join_under(seg_body_dir, name)
        except Exception:
            continue

        rec = load_json_locked(seg_path, {})
        if not isinstance(rec, dict):
            continue

        if str(rec.get("paper_id") or "").strip() != str(paper_id or "").strip():
            continue

        if str(rec.get("type") or "").strip().lower() != "equation":
            continue

        seq_id = str(rec.get("seq_id") or "").strip()
        if not seq_id:
            continue

        try:
            page = int(rec.get("page") or 0)
        except Exception:
            page = 0
        if page <= 0:
            page, _ = _seq_page_and_order(seq_id)
        if page <= 0:
            continue

        _, inferred_order = _seq_page_and_order(seq_id)
        ocr_text, _, ocr_score = _pick_best_ocr_text(rec.get("ocr"))
        latex_text, latex_conf, latex_source = _pick_best_latex_ocr(rec.get("latex_ocr"))

        block = {
            "id": f"seg_eq_{seq_id}",
            "seq_id": seq_id,
            "page": page,
            "type": "Equation",
            "bbox": rec.get("bbox", []),
            "score": float(ocr_score or 0.0),
            "content": ocr_text,
            "reading_order": inferred_order,
            "method": "SegmentationFallback",
            "source": "segmentation_equation_fallback",
            "pair_id": "",
            "has_caption": False,
            "latex": latex_text,
            "latex_confidence": float(latex_conf or 0.0),
            "equation_source": latex_source,
            "is_equation": True,
            "pid": pid,
            "paper_id": paper_id,
            "equation_fallback": True,
        }

        equations_by_page.setdefault(page, []).append(block)

    for page_num, blocks in equations_by_page.items():
        blocks.sort(key=_study_block_sort_key)
        equations_by_page[page_num] = blocks

    return equations_by_page


def _inject_segmentation_equation_fallback(content_pages, data_root: str, pid: str, paper_id: str) -> tuple[list, int]:
    if not isinstance(content_pages, list):
        return [], 0

    existing_eq = 0
    for page in content_pages:
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks", []):
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() == "equation" or bool(block.get("is_equation")):
                existing_eq += 1

    if existing_eq > 0:
        return content_pages, 0

    eq_by_page = _load_segmentation_equation_blocks(data_root, pid, paper_id)
    if not eq_by_page:
        return content_pages, 0

    normalized_pages = []
    page_index = {}

    for page in content_pages:
        if isinstance(page, dict):
            copied = dict(page)
            copied["blocks"] = list(page.get("blocks") or [])
            normalized_pages.append(copied)
            try:
                page_num = int(copied.get("page") or 0)
            except Exception:
                page_num = 0
            if page_num > 0:
                page_index[page_num] = copied
        else:
            normalized_pages.append(page)

    injected = 0
    for page_num, eq_blocks in eq_by_page.items():
        page_obj = page_index.get(page_num)
        if page_obj is None:
            page_obj = {"page": page_num, "blocks": []}
            normalized_pages.append(page_obj)
            page_index[page_num] = page_obj

        blocks = page_obj.get("blocks")
        if not isinstance(blocks, list):
            blocks = []
            page_obj["blocks"] = blocks

        existing_seq_ids = set()
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            seq = str(blk.get("seq_id") or blk.get("id") or "").strip()
            if seq:
                existing_seq_ids.add(seq)

        for eq_block in eq_blocks:
            seq = str(eq_block.get("seq_id") or eq_block.get("id") or "").strip()
            if seq and seq in existing_seq_ids:
                continue
            blocks.append(dict(eq_block))
            if seq:
                existing_seq_ids.add(seq)
            injected += 1

        blocks.sort(key=_study_block_sort_key)

    normalized_pages.sort(key=lambda p: int((p or {}).get("page", 0) or 0) if isinstance(p, dict) else 999999)
    return normalized_pages, injected


def _study_media_cache_dir(pid: str) -> str:
    data_root = resolve_data_root()
    media_dir = safe_join_under(data_root, pid, "study", "media_analysis")
    os.makedirs(media_dir, exist_ok=True)
    return media_dir


def _study_media_cache_path(pid: str, paper_id: str, kind: str, seq_id: str) -> str:
    media_dir = _study_media_cache_dir(pid)
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{paper_id}__{kind}__{seq_id}")
    return safe_join_under(media_dir, f"{token}.json")


def _parse_llm_json_text(text: str):
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass

    m = re.search(r"\{[\s\S]+\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _build_fallback_media_analysis(kind: str, caption_text: str, ocr_text: str) -> dict:
    clean_caption = str(caption_text or "").strip()
    clean_ocr = re.sub(r"\s+", " ", str(ocr_text or "")).strip()
    if len(clean_ocr) > 500:
        clean_ocr = clean_ocr[:500].rstrip() + "..."

    if kind == "figure":
        summary = "此圖內容可辨識，但 LLM 解說暫時不可用；請先參考圖說與 OCR 文字。"
    else:
        summary = "此表內容可辨識，但 LLM 解說暫時不可用；建議搭配表說與正文一起判讀。"

    points = []
    if clean_caption:
        points.append(f"Caption: {clean_caption[:220]}")
    if clean_ocr:
        points.append(f"OCR 摘錄: {clean_ocr[:260]}")

    return {
        "analysis_zh": summary,
        "key_points": points,
        "source": "fallback",
    }


def _generate_media_analysis_with_llm(kind: str, page: int, seq_id: str, caption_text: str, ocr_text: str, image_abs_path: str) -> dict:
    prompt = f"""
你是學術論文圖表解說助手。請根據提供的資料，輸出給一般研究者可讀的繁中說明。

資料：
- 類型: {kind}
- 頁碼: {page}
- 區塊 ID: {seq_id}
- caption: {caption_text[:700]}
- OCR 文字: {ocr_text[:2200]}

請只輸出 JSON，格式必須為：
{{
  "analysis_zh": "2-4句重點解說，說明這張圖/表在論文中的作用與可解讀訊息",
  "key_points": ["重點1", "重點2", "重點3"],
  "data_notes": "若是表格，補充欄列/數值可讀性提醒；若是圖，補充趨勢/對比可讀性提醒"
}}

規則：
1) 不可捏造圖中不存在的數字。
2) 若 OCR 品質不足，需明確說明不確定性。
3) 內容必須使用繁體中文。
""".strip()

    try:
        try:
            from app.llm_service.llm_dispatcher import dispatch_task  # type: ignore
        except Exception:
            from llm_service.llm_dispatcher import dispatch_task  # type: ignore
    except Exception as e:
        return {
            "analysis_zh": f"LLM 模組不可用：{e}",
            "key_points": [],
            "source": "fallback",
        }

    images = [image_abs_path] if image_abs_path and os.path.exists(image_abs_path) else None
    res = dispatch_task("task_5interpret", prompt, priority=5, images=images, max_retries=1)
    ok = bool((res or {}).get("ok"))
    text = str((res or {}).get("text") or "")
    msg = str((res or {}).get("msg") or "")
    if not ok:
        fallback = _build_fallback_media_analysis(kind, caption_text, ocr_text)
        fallback["analysis_zh"] = f"{fallback['analysis_zh']}（原因：{msg[:120]}）"
        return fallback

    parsed = _parse_llm_json_text(text)
    if not isinstance(parsed, dict):
        fallback = _build_fallback_media_analysis(kind, caption_text, ocr_text)
        fallback["analysis_zh"] = f"{fallback['analysis_zh']}（原因：LLM 回覆非 JSON）"
        return fallback

    points = parsed.get("key_points", [])
    if not isinstance(points, list):
        points = []
    points = [str(x).strip() for x in points if str(x).strip()][:5]
    analysis_zh = str(parsed.get("analysis_zh") or "").strip()
    data_notes = str(parsed.get("data_notes") or "").strip()
    if not analysis_zh:
        fallback = _build_fallback_media_analysis(kind, caption_text, ocr_text)
        fallback["analysis_zh"] = f"{fallback['analysis_zh']}（原因：LLM 缺少 analysis_zh）"
        return fallback
    return {
        "analysis_zh": analysis_zh,
        "key_points": points,
        "data_notes": data_notes,
        "source": "llm_task_5interpret",
    }


def _collect_media_records(pid: str, paper_id: str, kind_raw: str) -> list[dict]:
    kind, folder, type_name = _normalize_media_kind(kind_raw)
    data_root = resolve_data_root()
    seg_dir = safe_join_under(data_root, pid, "literature", "segmentation", folder)

    def _resolve_png_local(seq_id: str, png_raw: str) -> str:
        png_local = _to_local_data_path(data_root, png_raw)
        if png_local and os.path.exists(png_local):
            return png_local
        if os.path.isdir(seg_dir):
            try:
                guessed = [n for n in os.listdir(seg_dir) if n.lower().endswith(".png") and seq_id in n]
            except Exception:
                guessed = []
            if guessed:
                guessed_path = safe_join_under(seg_dir, guessed[0])
                if os.path.exists(guessed_path):
                    return guessed_path
        return ""

    def _has_caption_lock(row: dict) -> bool:
        if not isinstance(row, dict):
            return False
        if bool(row.get("has_caption", False)):
            return True
        cap_bbox = row.get("caption_bbox")
        if isinstance(cap_bbox, list) and len(cap_bbox) >= 4:
            return True
        return False

    vns_records = []
    try:
        vns_path = safe_join_under(data_root, pid, "literature", "segmentation", f"{pid}_vns.json")
        vns_payload = load_json_locked(vns_path, {})
        records = (
            (vns_payload.get("papers", {}) or {}).get(paper_id, {}).get("records", [])
            if isinstance(vns_payload, dict)
            else []
        )
        if isinstance(records, list):
            vns_records = [r for r in records if isinstance(r, dict)]
    except Exception:
        vns_records = []

    # sidecar JSON 僅作 OCR 補強；不再作為清單來源，避免舊髒檔混入。
    sidecar_by_seq = {}
    if os.path.isdir(seg_dir):
        for name in os.listdir(seg_dir):
            if not name.lower().endswith(".json"):
                continue
            path = safe_join_under(seg_dir, name)
            try:
                row = load_json_locked(path, {})
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            seq_id = str(row.get("seq_id") or "").strip()
            if not seq_id:
                continue
            sidecar_by_seq[seq_id] = row

    items = []
    seen_seq_ids = set()
    for rec in vns_records:
        seq_id = str(rec.get("seq_id") or "").strip()
        rec_type = str(rec.get("type") or "").strip().lower()
        if not seq_id or seq_id in seen_seq_ids:
            continue
        if rec_type != type_name.lower():
            continue

        sidecar = sidecar_by_seq.get(seq_id, {}) if isinstance(sidecar_by_seq.get(seq_id), dict) else {}
        try:
            page_no = int(rec.get("page") or sidecar.get("page") or 0)
        except Exception:
            page_no = 0

        ocr_text, ocr_source, ocr_score = _pick_best_ocr_text(sidecar.get("ocr", {}))
        caption_seed = str(rec.get("caption_text") or rec.get("caption") or sidecar.get("caption_text") or "").strip()
        caption_text = _extract_caption_text(kind, caption_seed or ocr_text)
        png_raw = str(rec.get("png_path") or sidecar.get("png_path") or "").strip()
        png_local = _resolve_png_local(seq_id, png_raw)
        # 嚴格規則：Figure/Table 僅允許輸出已配對 caption 的區塊；Equation 不套用此限制。
        if kind in {"figure", "table"}:
            if not (_has_caption_lock(rec) or _has_caption_lock(sidecar)):
                continue
        if not png_local:
            continue

        seen_seq_ids.add(seq_id)

        items.append(
            {
                "seq_id": seq_id,
                "page": page_no,
                "type": type_name,
                "pair_id": str(rec.get("pair_id") or sidecar.get("pair_id") or "").strip(),
                "bbox": rec.get("bbox", []),
                "png_abs_path": png_local,
                "caption_text": caption_text,
                "ocr_text": ocr_text,
                "ocr_source": ocr_source,
                "ocr_score": round(float(ocr_score or 0.0), 4),
            }
        )

    # Fallback (strict): if current VNS has no major blocks for this paper/kind,
    # recover only from same-paper sidecars that already carry caption lock.
    # This avoids cross-paper stale pollution while unblocking historical runs that wrote
    # sidecars but failed to upsert major records into global vns.
    if not items and sidecar_by_seq:
        expected_type = type_name.lower()
        for seq_id, sidecar in sidecar_by_seq.items():
            if not isinstance(sidecar, dict):
                continue
            if not seq_id or seq_id in seen_seq_ids:
                continue
            if str(sidecar.get("paper_id") or "").strip() != paper_id:
                continue
            if str(sidecar.get("type") or "").strip().lower() != expected_type:
                continue
            if kind in {"figure", "table"} and not _has_caption_lock(sidecar):
                continue

            ocr_text, ocr_source, ocr_score = _pick_best_ocr_text(sidecar.get("ocr", {}))
            caption_text = _extract_caption_text(kind, ocr_text)

            try:
                page_no = int(sidecar.get("page") or 0)
            except Exception:
                page_no = 0

            png_raw = str(sidecar.get("png_path") or "").strip()
            png_local = _resolve_png_local(seq_id, png_raw)
            if not png_local:
                continue

            seen_seq_ids.add(seq_id)
            items.append(
                {
                    "seq_id": seq_id,
                    "page": page_no,
                    "type": type_name,
                    "pair_id": str(sidecar.get("pair_id") or "").strip(),
                    "bbox": sidecar.get("bbox", []),
                    "png_abs_path": png_local,
                    "caption_text": caption_text,
                    "ocr_text": ocr_text,
                    "ocr_source": ocr_source,
                    "ocr_score": round(float(ocr_score or 0.0), 4),
                }
            )

    items.sort(key=_media_sort_key)
    return items


def _build_media_gallery(pid: str, paper_id: str, kind_raw: str, refresh: bool = False) -> list[dict]:
    kind, _, _ = _normalize_media_kind(kind_raw)
    records = _collect_media_records(pid, paper_id, kind)
    out = []
    for item in records:
        seq_id = str(item.get("seq_id") or "").strip()
        if not seq_id:
            continue

        cache_path = _study_media_cache_path(pid, paper_id, kind, seq_id)
        analysis_payload = None
        if not refresh and os.path.exists(cache_path):
            try:
                cached = load_json_locked(cache_path, {})
                if isinstance(cached, dict) and str(cached.get("analysis_zh") or "").strip():
                    analysis_payload = cached
            except Exception:
                analysis_payload = None

        if analysis_payload is None:
            analysis_payload = _generate_media_analysis_with_llm(
                kind=kind,
                page=int(item.get("page") or 0),
                seq_id=seq_id,
                caption_text=str(item.get("caption_text") or ""),
                ocr_text=str(item.get("ocr_text") or ""),
                image_abs_path=str(item.get("png_abs_path") or ""),
            )
            try:
                payload_to_save = dict(analysis_payload)
                payload_to_save.update(
                    {
                        "pid": pid,
                        "paper_id": paper_id,
                        "kind": kind,
                        "seq_id": seq_id,
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                analysis_payload = payload_to_save
                write_json_locked(cache_path, payload_to_save)
            except Exception:
                pass

        out.append(
            {
                "seq_id": seq_id,
                "page": int(item.get("page") or 0),
                "type": item.get("type"),
                "pair_id": item.get("pair_id"),
                "bbox": item.get("bbox", []),
                "image_url": (
                    f"/api/study/media_asset/{quote(pid)}/{quote(paper_id)}/{quote(seq_id)}.png?kind={quote(kind)}"
                ),
                "caption_text": str(item.get("caption_text") or ""),
                "ocr_excerpt": str(item.get("ocr_text") or "")[:900],
                "ocr_source": item.get("ocr_source", ""),
                "ocr_score": float(item.get("ocr_score", 0.0) or 0.0),
                "analysis_zh": str((analysis_payload or {}).get("analysis_zh") or ""),
                "analysis_points": [
                    str(x).strip()
                    for x in ((analysis_payload or {}).get("key_points") or [])
                    if str(x).strip()
                ][:5],
                "analysis_notes": str((analysis_payload or {}).get("data_notes") or "").strip(),
                "analysis_source": str((analysis_payload or {}).get("source") or ""),
                "analysis_updated_at": str((analysis_payload or {}).get("updated_at") or "").strip(),
            }
        )
    return out

# -----------------------------------------------------------------------------
# HTML Routes
# -----------------------------------------------------------------------------

@study_bp.route('/study', methods=['GET'])
@study_bp.route('/study/', methods=['GET'])
def study_index():
    """
    Study 首頁重定向
    """
    pid = _resolve_active_pid(request.args.get('pid'))
    if not pid:
        return redirect('/')
    return redirect(f'/study/project/{pid}?pid={pid}')

@study_bp.route('/study/project/<pid>', methods=['GET'])
def study_page(pid):
    """
    返回 Study 頁面
    """
    try:
        resolved = _resolve_active_pid(pid)
        if not resolved:
            return redirect('/')
        from flask_login import current_user
        try:
            scoped = bool(current_user.is_authenticated and is_review_scoped(current_user.id, resolved))
        except AttributeError:
            scoped = False
        return render_template('study.html', pid=resolved, review_scope=scoped)
    except Exception as e:
        logger.error(f"Study page error: {e}")
        return f"<h1>Error loading Study page</h1><p>{e}</p>", 500

# -----------------------------------------------------------------------------
# 0. Gatekeeper & Status Check [New v2.4]
# -----------------------------------------------------------------------------

@study_bp.route('/api/study/check_gate/<pid>/<paper_id>', methods=['GET'])
def check_data_readiness(pid, paper_id):
    """
    [Gatekeeper] 檢查文獻是否已達 GOLD_READY 狀態
    供前端在進入 Study 頁面前呼叫。
    """
    pid = _normalize_study_pid(pid)
    if not pid:
        return jsonify({"status": "error", "message": "Invalid PID."}), 404
    try:
        paper_id = validate_id(paper_id, "paper_id")
    except BadRequest:
        return jsonify({"status": "error", "message": "Invalid paper_id"}), 400

    paper = Paper.query.filter_by(paper_id=paper_id, pid=pid).first()
    
    if not paper:
        # Fallback: 若 DB 無資料但檔案存在，視為 Legacy 模式 (Bronze)
        # 但建議嚴格閉鎖
        return jsonify({"status": "error", "message": "Paper not found in registry."}), 404

    if paper.interpretation_status in (Paper.STATUS_GOLD_READY, 'ready_B', 'ready_A',
                                         'translating_local', 'judging_llm', 'processing_b'):
        return jsonify({"status": "success", "message": "Ready"})
    elif paper.interpretation_status == Paper.STATUS_ANALYZING:
        return jsonify({"status": "locked", "message": "AI Analysis in progress...", "progress": paper.process_log}), 403
    elif paper.interpretation_status == Paper.STATUS_FAILED:
         return jsonify({"status": "error", "message": "Analysis failed"}), 500
    else:
        return jsonify({"status": "locked", "message": "Data not yet analyzed. Please trigger AI analysis in Literature."}), 403

# -----------------------------------------------------------------------------
# 1. 基礎資料獲取 (Sidebar Data)
# -----------------------------------------------------------------------------

@study_bp.route('/api/study/paper_list/<pid>', methods=['GET'])
def get_paper_list(pid):
    """
    獲取指定專案下的文獻列表
    [Fixed v2.4] 改進容錯能力，不依賴於数據庫完整性
    """
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404

        data_root = resolve_data_root()
        project_dir = safe_join_under(data_root, pid)
        if not os.path.exists(project_dir):
            return jsonify({"ok": True, "papers": []})

        valid_papers = []
        for paper_id, p_dir, _storage_kind in list_literature_papers(data_root, pid):
            try:
                paper_id = validate_id(paper_id, "paper_id")
            except BadRequest:
                continue
            
            # 優先檢查文件系統的完成狀態（最可靠）
            is_ready = False
            status_text = "pending"
            
            # 檢查各個 Pipeline 階段
            has_raw = False
            rec_dir = os.path.join(p_dir, "03_recognizes")
            if os.path.exists(rec_dir):
                has_raw = any(f.endswith("_raw.json") for f in os.listdir(rec_dir))
            
            has_fixed = False
            if os.path.exists(rec_dir):
                has_fixed = any(f.endswith("_fixed.json") for f in os.listdir(rec_dir))

            # Task 5 完成的重要標誌
            interp_dir = os.path.join(p_dir, "05_interprets")
            has_full_text = False
            fusion_dir = os.path.join(interp_dir, "fusion")
            if os.path.exists(fusion_dir):
                has_full_text = os.path.exists(os.path.join(fusion_dir, "full_text.json"))
            
            # 決定狀態
            if has_full_text:
                is_ready = True
                status_text = "ready"
            elif has_fixed:
                status_text = "processing"
            elif has_raw:
                status_text = "analyzing"
            else:
                status_text = "pending"
            
            # 嘗試從 DB 讀取，失敗則用預設值
            title = paper_id
            try:
                db_paper = Paper.query.filter_by(paper_id=paper_id, pid=pid).first()
                if db_paper and db_paper.title:
                    title = db_paper.title
            except Exception as db_err:
                logger.warning(f"[get_paper_list] DB query failed for {paper_id}: {db_err}")
                # 繼續使用文件系統推斷的狀態

            valid_papers.append({
                "paper_id": paper_id,
                "title": title,
                "status": status_text,
                "is_ready": is_ready,
                "has_raw": has_raw,
                "has_fixed": has_fixed,
                "has_full_text": has_full_text
            })
        
        return jsonify({"ok": True, "papers": valid_papers})
    
    except Exception as e:
        return _internal_error("get_paper_list", e)

# -----------------------------------------------------------------------------
# 2. Task 6: 水平比較矩陣
# -----------------------------------------------------------------------------

@study_bp.route('/api/study/compare', methods=['POST'])
def run_matrix_comparison():
    start_time = time.time()
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        target_ids = data.get('paper_ids', [])
        criteria = data.get('criteria', 'General')

        if not pid:
            return jsonify({"ok": False, "error": "Invalid PID"}), 404
        if not target_ids or len(target_ids) < 2:
            return jsonify({"ok": False, "error": "請至少選擇 2 篇文獻進行比較。"}), 400

        data_root = resolve_data_root()
        papers_payload = []
        
        for p_id_obj in target_ids:
            # Handle both string IDs and object IDs
            if isinstance(p_id_obj, dict):
                p_id = p_id_obj.get('paper_id') or p_id_obj.get('id') or str(p_id_obj)
            else:
                p_id = str(p_id_obj)
            try:
                p_id = validate_id(p_id, "paper_id")
            except BadRequest:
                continue
            
            paper_dir = resolve_literature_paper_dir(
                data_root,
                pid,
                p_id,
                for_write=False,
                migrate_legacy=True,
            )
            fusion_path = safe_join_under(paper_dir, "05_interprets", "fusion", "full_text.json")
            
            p_obj = {
                "paper_id": p_id,
                "metadata": {"title": p_id},
                "content": "",
                "summary": ""
            }

            if os.path.exists(fusion_path):
                try:
                    fusion_data = load_json_locked(fusion_path, {})

                    # 提取文獻標題
                    if fusion_data.get("title"):
                        p_obj["metadata"]["title"] = fusion_data["title"]
                    elif fusion_data.get("paper_id"):
                        p_obj["metadata"]["title"] = fusion_data["paper_id"]

                    # 提取內容（可能是嵌套結構）
                    content_raw = fusion_data.get("content", "")
                    if isinstance(content_raw, list):
                        # 結構: content = [{ page: N, blocks: [{ content: "..." }, ...] }, ...]
                        text_blocks = []
                        for page in content_raw:
                            if isinstance(page, dict):
                                blocks = page.get("blocks", [])
                                for block in blocks:
                                    if isinstance(block, dict) and block.get("content"):
                                        text_blocks.append(block["content"])

                        p_obj["content"] = "\n\n".join(text_blocks) if text_blocks else ""
                    elif isinstance(content_raw, dict):
                        p_obj["content"] = content_raw.get("text", json.dumps(content_raw, ensure_ascii=False))
                    else:
                        p_obj["content"] = str(content_raw)

                    # 提取摘要
                    if fusion_data.get("summary"):
                        p_obj["summary"] = fusion_data["summary"]
                except Exception as e:
                    logger.warning(f"[compare] Failed to load fusion data for {p_id}: {e}")
            
            papers_payload.append(p_obj)

        if len(papers_payload) < 2:
             return jsonify({"ok": False, "error": "Insufficient data loaded."}), 400

        matrix_result = matrix_engine.generate_matrix(pid, papers_payload, criteria)
        elapsed = round(time.time() - start_time, 2)
        
        if not isinstance(matrix_result, dict):
            return jsonify({"ok": False, "saved": False, "error": "Invalid comparison result"}), 502
        for comparison in matrix_result.get('comparisons', []):
            if isinstance(comparison, dict) and not comparison.get('error') and isinstance(comparison.get('content'), str):
                comparison.setdefault('analysis', comparison['content'])
        outcome = matrix_outcome(matrix_result.get('comparisons'))
        matrix_result.update(outcome)
        if outcome['status'] == 'failed':
            return jsonify({"ok": False, "saved": False, "result_status": "failed",
                            "error": "所有比較均未完成，原有結果保留。", "data": matrix_result}), 502

        # Persist matrix result for history playback.
        display_name = ""
        matrix_id = None
        try:
            hist_dir = _get_matrix_history_dir(pid)
            matrix_id = f"mx_{int(time.time())}_{os.urandom(4).hex()}"
            created_at = time.strftime('%Y-%m-%d %H:%M:%S')
            paper_titles = [p.get("metadata", {}).get("title", p.get("paper_id", "")) for p in papers_payload]

            record = {
                "matrix_id": matrix_id,
                "pid": pid,
                "criteria": criteria,
                "paper_ids": [p.get("paper_id", "") for p in papers_payload],
                "paper_titles": paper_titles,
                "created_at": created_at,
                "elapsed": elapsed,
                "display_name": f"{criteria} @ {created_at}",
                "is_legacy": False,
                "data": matrix_result,
            }
            display_name = record.get("display_name", "")

            write_json_locked(os.path.join(hist_dir, f"{matrix_id}.json"), record)

            # Keep an explicit latest pointer for other modules.
            write_json_locked(os.path.join(hist_dir, "latest.json"), record)

            # Stable snapshot used by Study refresh flow.
            snapshot = dict(record)
            write_json_locked(_get_latest_matrix_snapshot_path(pid), snapshot)
        except Exception as persist_err:
            logger.exception("[compare] Matrix persistence failed")
            return jsonify({"ok": False, "saved": False, "result_status": outcome['status'],
                            "error": "比較已完成，但保存未完成；請保留本次結果。",
                            "data": matrix_result, "matrix_id": matrix_id}), 503
             
        return jsonify(
            {
                "ok": outcome['status'] == 'completed',
                "saved": True,
                "result_status": outcome['status'],
                "data": matrix_result,
                "elapsed": elapsed,
                "matrix_id": matrix_id,
                "display_name": display_name,
            }
        ), (207 if outcome['status'] == 'partial' else 200)

    except Exception as e:
        return _internal_error("run_matrix_comparison", e)


@study_bp.route('/api/study/<pid>/matrix/history', methods=['GET'])
def list_matrix_history(pid):
    """List persisted matrix comparison records for a project."""
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404

        hist_dir = _get_matrix_history_dir(pid)
        items = []

        for filename in os.listdir(hist_dir):
            if not filename.endswith('.json') or filename == 'latest.json':
                continue

            fpath = os.path.join(hist_dir, filename)
            try:
                obj = load_json_locked(fpath, {})

                matrix_id = obj.get('matrix_id') or filename[:-5]
                paper_titles = obj.get('paper_titles') or []
                if not paper_titles and isinstance(obj.get('data'), dict):
                    b = obj['data'].get('baseline', {})
                    c = (obj['data'].get('comparisons') or [])
                    t = c[0] if c else {}
                    paper_titles = [
                        b.get('title') or b.get('id') or 'Paper A',
                        t.get('target_title') or t.get('target') or t.get('id') or 'Paper B',
                    ]

                items.append({
                    'matrix_id': matrix_id,
                    'created_at': obj.get('created_at', ''),
                    'criteria': obj.get('criteria', 'General'),
                    'elapsed': obj.get('elapsed', None),
                    'paper_titles': paper_titles,
                    'display_name': obj.get('display_name') or matrix_id,
                    'is_legacy': False,
                })
            except Exception as e:
                logger.warning(f"[matrix_history] failed to parse {filename}: {e}")

        # Backward compatibility: include legacy papers_comp records.
        legacy_dir = _get_legacy_papers_comp_dir(pid)
        if os.path.isdir(legacy_dir):
            for filename in os.listdir(legacy_dir):
                if not filename.endswith('.json'):
                    continue
                fpath = os.path.join(legacy_dir, filename)
                try:
                    obj = load_json_locked(fpath, {})

                    legacy_id = f"legacy::{filename[:-5]}"
                    b = obj.get('baseline', {}) if isinstance(obj, dict) else {}
                    c = (obj.get('comparisons') or []) if isinstance(obj, dict) else []
                    t = c[0] if c else {}
                    paper_titles = [
                        b.get('title') or b.get('id') or 'Paper A',
                        t.get('target_paper_title') or t.get('target_title') or t.get('target') or t.get('target_paper_id') or 'Paper B',
                    ]

                    created_ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(fpath)))
                    items.append({
                        'matrix_id': legacy_id,
                        'created_at': created_ts,
                        'criteria': obj.get('criteria', 'General') if isinstance(obj, dict) else 'General',
                        'elapsed': None,
                        'paper_titles': paper_titles,
                        'display_name': filename[:-5],
                        'is_legacy': True,
                    })
                except Exception as e:
                    logger.warning(f"[matrix_history] failed to parse legacy {filename}: {e}")

        items.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return jsonify({'ok': True, 'history': items})
    except Exception as e:
        return _internal_error("list_matrix_history", e)


@study_bp.route('/api/study/<pid>/matrix/<matrix_id>', methods=['GET'])
def get_matrix_history_item(pid, matrix_id):
    """Load one persisted matrix record."""
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({'ok': False, 'msg': 'Invalid PID'}), 404
        try:
            matrix_id = _safe_matrix_id(matrix_id)
        except BadRequest:
            return jsonify({'ok': False, 'msg': 'Invalid matrix_id'}), 400

        if str(matrix_id).startswith('legacy::'):
            legacy_name = str(matrix_id).split('legacy::', 1)[1]
            legacy_path = os.path.join(_get_legacy_papers_comp_dir(pid), f"{legacy_name}.json")
            if not os.path.exists(legacy_path):
                return jsonify({'ok': False, 'msg': 'Matrix record not found'}), 404
            obj = load_json_locked(legacy_path, {})
            record = _build_legacy_matrix_record(pid, legacy_name, legacy_path, obj)
            return jsonify({'ok': True, 'record': record})

        hist_dir = _get_matrix_history_dir(pid)
        fpath = os.path.join(hist_dir, f"{matrix_id}.json")
        if not os.path.exists(fpath):
            return jsonify({'ok': False, 'msg': 'Matrix record not found'}), 404

        obj = load_json_locked(fpath, {})
        if isinstance(obj, dict):
            obj.setdefault('matrix_id', matrix_id)
            obj.setdefault('display_name', obj.get('matrix_id') or matrix_id)
            obj.setdefault('is_legacy', False)

        return jsonify({'ok': True, 'record': obj})
    except Exception as e:
        return _internal_error("get_matrix_history_item", e)


@study_bp.route('/api/study/<pid>/matrix/<matrix_id>/rename', methods=['PATCH'])
def rename_matrix_history_item(pid, matrix_id):
    """Rename one matrix history record by updating display_name."""
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({'ok': False, 'msg': 'Invalid PID'}), 404
        try:
            matrix_id = _safe_matrix_id(matrix_id)
        except BadRequest:
            return jsonify({'ok': False, 'msg': 'Invalid matrix_id'}), 400

        if str(matrix_id).startswith('legacy::'):
            return jsonify({'ok': False, 'msg': 'Legacy matrix does not support rename'}), 400

        body = request.get_json(silent=True) or {}
        display_name = str(body.get('display_name') or '').strip()
        if not display_name:
            return jsonify({'ok': False, 'msg': 'display_name is required'}), 400
        if len(display_name) > 120:
            return jsonify({'ok': False, 'msg': 'display_name too long'}), 400

        hist_dir = _get_matrix_history_dir(pid)
        fpath = os.path.join(hist_dir, f"{matrix_id}.json")
        if not os.path.exists(fpath):
            return jsonify({'ok': False, 'msg': 'Matrix record not found'}), 404

        obj = load_json_locked(fpath, {})
        if not isinstance(obj, dict):
            obj = {}
        obj['matrix_id'] = matrix_id
        obj['display_name'] = display_name
        obj['is_legacy'] = False
        obj['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        write_json_locked(fpath, obj)

        latest_record = _refresh_matrix_latest_pointers(pid)
        return jsonify(
            {
                'ok': True,
                'record': obj,
                'latest_matrix_id': (latest_record or {}).get('matrix_id'),
            }
        )
    except Exception as e:
        return _internal_error("rename_matrix_history_item", e)


@study_bp.route('/api/study/<pid>/matrix/<matrix_id>', methods=['DELETE'])
def delete_matrix_history_item(pid, matrix_id):
    """Delete one matrix history record (normal or legacy)."""
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({'ok': False, 'msg': 'Invalid PID'}), 404
        try:
            matrix_id = _safe_matrix_id(matrix_id)
        except BadRequest:
            return jsonify({'ok': False, 'msg': 'Invalid matrix_id'}), 400

        deleted = False
        if str(matrix_id).startswith('legacy::'):
            legacy_name = str(matrix_id).split('legacy::', 1)[1]
            legacy_path = os.path.join(_get_legacy_papers_comp_dir(pid), f"{legacy_name}.json")
            if os.path.exists(legacy_path):
                os.remove(legacy_path)
                deleted = True
        else:
            hist_dir = _get_matrix_history_dir(pid)
            fpath = os.path.join(hist_dir, f"{matrix_id}.json")
            if os.path.exists(fpath):
                os.remove(fpath)
                deleted = True

        if not deleted:
            return jsonify({'ok': False, 'msg': 'Matrix record not found'}), 404

        latest_record = _refresh_matrix_latest_pointers(pid)
        return jsonify(
            {
                'ok': True,
                'deleted_matrix_id': matrix_id,
                'latest_matrix_id': (latest_record or {}).get('matrix_id'),
            }
        )
    except Exception as e:
        return _internal_error("delete_matrix_history_item", e)


@study_bp.route('/api/study/<pid>/matrix/latest', methods=['GET'])
def get_latest_matrix_history_item(pid):
    """Load latest persisted matrix record."""
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({'ok': False, 'msg': 'Invalid PID'}), 404

        # 1) Stable snapshot first (written on every compare success).
        snap_path = _get_latest_matrix_snapshot_path(pid)
        if os.path.exists(snap_path):
            obj = load_json_locked(snap_path, {})
            if isinstance(obj, dict):
                obj.setdefault('display_name', obj.get('matrix_id') or '')
                obj.setdefault('is_legacy', str(obj.get('matrix_id', '')).startswith('legacy::'))
            return jsonify({'ok': True, 'record': obj})

        # 2) Recompute pointer from storage.
        record = _refresh_matrix_latest_pointers(pid)
        if record:
            return jsonify({'ok': True, 'record': record})

        return jsonify({'ok': False, 'msg': 'No matrix record found'}), 404
    except Exception as e:
        return _internal_error("get_latest_matrix_history_item", e)

# -----------------------------------------------------------------------------
# 3. Task 7: AI 家教問答
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# 3A. 對話管理 (Conversation Management) - Persistent Chat Sessions
# -----------------------------------------------------------------------------

def _get_conversations_dir(pid):
    """獲取對話歷史目錄"""
    pid = _normalize_study_pid(pid)
    if not pid:
        raise ValueError("Invalid PID")
    data_root = resolve_data_root()
    base_dir = safe_join_under(data_root, pid, "study", "conversations")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

@study_bp.route('/api/study/<pid>/conversations', methods=['GET'])
def list_conversations(pid):
    """
    列出指定專案的所有對話
    返回: [{conv_id, title, created_at, updated_at, message_count}, ...]
    """
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        try:
            limit = min(max(int(request.args.get("limit", 50)), 1), 200)
            offset = max(int(request.args.get("offset", 0)), 0)
        except Exception:
            return jsonify({"ok": False, "msg": "Invalid pagination"}), 400

        conv_dir = _get_conversations_dir(pid)
        conversations = []
        
        for filename in os.listdir(conv_dir):
            if not filename.endswith('.json'):
                continue
            
            conv_id = filename.replace('.json', '')
            conv_path = os.path.join(conv_dir, filename)
            
            try:
                conv_id = _safe_conv_id(conv_id)
                conv_data = load_json_locked(conv_path, {})
                
                conversations.append({
                    "conv_id": conv_id,
                    "title": conv_data.get('title', '未命名對話'),
                    "created_at": conv_data.get('created_at', ''),
                    "updated_at": conv_data.get('updated_at', ''),
                    "message_count": len(conv_data.get('messages', []))
                })
            except Exception as e:
                logger.warning(f"Failed to load conversation {filename}: {e}")
        
        # 按更新時間排序
        conversations.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
        total = len(conversations)
        page = conversations[offset: offset + limit]
        return jsonify({"ok": True, "conversations": page, "total": total, "limit": limit, "offset": offset})
    
    except Exception as e:
        return _internal_error("list_conversations", e)

@study_bp.route('/api/study/conversations/create', methods=['POST'])
def create_conversation():
    """
    創建新對話
    Payload: {pid, title?}
    Returns: {conv_id, title, created_at}
    """
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        title = data.get('title', '新的對話')

        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        
        # 生成唯一 ID (timestamp + random)
        conv_id = f"conv_{int(time.time())}_{os.urandom(4).hex()}"
        conv_id = _safe_conv_id(conv_id)
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        
        actor = None
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                actor = current_user.id
        except Exception:
            actor = None
        conv_data = {
            "conv_id": conv_id,
            "pid": pid,
            "title": title,
            # 誰建立的：刪除只允許建立者或專案 owner（舊對話沒有這個欄位 → 只有 owner 能刪）。
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
            "messages": []  # [{role: 'user'/'assistant', content: '', timestamp: ''}]
        }
        
        conv_dir = _get_conversations_dir(pid)
        conv_path = os.path.join(conv_dir, f"{conv_id}.json")
        
        write_json_locked(conv_path, conv_data)
        
        return jsonify({
            "ok": True,
            "conv_id": conv_id,
            "title": title,
            "created_at": now
        })
    
    except Exception as e:
        return _internal_error("create_conversation", e)

@study_bp.route('/api/study/conversation/<conv_id>', methods=['GET'])
def get_conversation(conv_id):
    """
    獲取對話內容
    需要 Query: ?pid=xxx
    """
    try:
        try:
            conv_id = _safe_conv_id(conv_id)
        except BadRequest:
            return jsonify({"ok": False, "msg": "Invalid conversation id"}), 400
        pid = _normalize_study_pid(request.args.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        
        conv_dir = _get_conversations_dir(pid)
        conv_path = os.path.join(conv_dir, f"{conv_id}.json")
        
        if not os.path.exists(conv_path):
            return jsonify({"ok": False, "msg": "Conversation not found"}), 404
        
        conv_data = load_json_locked(conv_path, {})
        
        return jsonify({"ok": True, "conversation": conv_data})
    
    except Exception as e:
        return _internal_error("get_conversation", e)

@study_bp.route('/api/study/conversation/<conv_id>/delete', methods=['POST'])
def delete_conversation(conv_id):
    """
    刪除對話
    Payload: {pid}
    """
    try:
        try:
            conv_id = _safe_conv_id(conv_id)
        except BadRequest:
            return jsonify({"ok": False, "msg": "Invalid conversation id"}), 400
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))

        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        
        conv_dir = _get_conversations_dir(pid)
        conv_path = os.path.join(conv_dir, f"{conv_id}.json")
        
        if os.path.exists(conv_path):
            # 刪除無法復原：editor 只能刪自己建立的對話，其他人的（含沒有建立者紀錄的舊對話）只有 owner 能刪。
            from app.security import require_owner_or_creator
            try:
                with open(conv_path, encoding="utf-8") as fh:
                    created_by = (json.load(fh) or {}).get("created_by")
            except Exception:
                created_by = None
            denied = require_owner_or_creator(pid, created_by)
            if denied is not None:
                return denied
            os.remove(conv_path)
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "msg": "Conversation not found"}), 404
    
    except Exception as e:
        return _internal_error("delete_conversation", e)

@study_bp.route('/api/study/conversation/<conv_id>/rename', methods=['POST'])
def rename_conversation(conv_id):
    """
    重命名對話
    Payload: {pid, title}
    """
    try:
        try:
            conv_id = _safe_conv_id(conv_id)
        except BadRequest:
            return jsonify({"ok": False, "msg": "Invalid conversation id"}), 400
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        new_title = data.get('title')

        if not pid or not new_title:
            return jsonify({"ok": False, "msg": "Invalid PID or title required"}), 400
        
        conv_dir = _get_conversations_dir(pid)
        conv_path = os.path.join(conv_dir, f"{conv_id}.json")
        
        if not os.path.exists(conv_path):
            return jsonify({"ok": False, "msg": "Conversation not found"}), 404
        
        def rename_current(conv_data):
            if not isinstance(conv_data, dict) or conv_data.get('pid') != pid:
                raise ValueError("Invalid conversation")
            conv_data['title'] = new_title
            conv_data['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return conv_data
        update_json_locked(conv_path, rename_current)

        return jsonify({"ok": True})
    
    except Exception as e:
        return _internal_error("rename_conversation", e)

# -----------------------------------------------------------------------------
# 3B. AI 家教問答 (Enhanced with Conversation Support)
# -----------------------------------------------------------------------------

@study_bp.route('/api/study/chat', methods=['POST'])
def chat_with_tutor():
    """
    AI 家教對話 (支持對話持久化)
    Payload: {pid, question, context, conv_id?}
    """
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        query = data.get('query') or data.get('question')
        context_payload = data.get('context', {})
        conv_id = data.get('conv_id')  # 可選：指定對話 ID
        if conv_id:
            try:
                conv_id = _safe_conv_id(conv_id)
            except BadRequest:
                return jsonify({"ok": False, "msg": "Invalid conversation id"}), 400

        if not data.get('pid'):
            return jsonify({"ok": False, "msg": "Missing project id"}), 400
        if not query:
            return jsonify({"ok": False, "msg": "Question is empty"}), 400
        if data.get('pid') and not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        mode = data.get('mode')
        if mode is not None:
            if mode not in ('explain', 'critique'):
                return jsonify({"ok": False, "msg": "Invalid mode"}), 400
            return _anchored_tutor_reply(pid, str(query)[:2000], mode, data, conv_id)

        if not isinstance(context_payload, dict):
            context_payload = {}

        # [Context Upgrade] Normalize focus_pids for matrix-cell grounding.
        focus_pids_raw = context_payload.get('focus_pids', [])
        if not isinstance(focus_pids_raw, list):
            focus_pids_raw = [focus_pids_raw]
        focus_pids = []
        for v in focus_pids_raw:
            s = str(v or '').strip()
            if not s:
                continue
            if s.lower() in {'synthesis', 'paper_a', 'paper_b'}:
                continue
            if s not in focus_pids:
                focus_pids.append(s)
        pid_hint = str(context_payload.get('pid') or '').strip()
        if not focus_pids and pid_hint and pid_hint.lower() not in {'synthesis', 'paper_a', 'paper_b'}:
            focus_pids.append(pid_hint)
        context_payload['focus_pids'] = focus_pids

        # [Context Upgrade] Always attach current project pid for precise paper lookup.
        if pid:
            context_payload['project_pid'] = pid

        # [Context Upgrade] Attach matrix-cell text and note context with safe budget.
        cell_content = str(context_payload.get('content') or '').strip()
        if cell_content:
            context_payload['content'] = cell_content[:1400]

        user_notes = str(context_payload.get('user_notes') or '').strip()
        if not user_notes and pid:
            user_notes = _load_study_note_text(pid)
        context_payload['user_notes'] = user_notes[:4000] if user_notes else ""

        # [Context Upgrade] Pull compact hybrid context from Literature context chain.
        if pid:
            try:
                chain_query = " ".join([
                    str(query or "").strip(),
                    str(context_payload.get('dimension') or '').strip(),
                    str(context_payload.get('content') or '').strip()[:280],
                ]).strip()[:1200]
                if chain_query:
                    ccs = get_context_chain_service()
                    hybrid = ccs.query_hybrid_kg_rag(
                        pid=pid,
                        query=chain_query,
                        k_vector=4,
                        k_triple=4,
                        requester_access_level='internal',
                    )
                    context_payload['chain_context'] = hybrid.get('compact_context', {})
            except Exception as chain_err:
                logger.warning(f"[study.chat] context_chain hybrid query failed: {chain_err}")

        # AI 處理
        answer = tutor_engine.process_query(query, context_payload)
        
        return _reply_and_save(pid, conv_id, query, answer)

    except Exception as e:
        return _internal_error("chat_with_tutor", e)


def _reply_and_save(pid, conv_id, query, answer, structured=None):
    """回答與對話保存；保存失敗時仍把回答交給使用者（503），結構化結果一併保留。"""
    saved = False
    extra = {"structured": structured} if structured is not None else {}
    if conv_id and pid:
        try:
            conv_path = os.path.join(_get_conversations_dir(pid), f"{conv_id}.json")
            def append_messages(conv_data):
                if not isinstance(conv_data, dict) or conv_data.get('pid') != pid:
                    raise ValueError("Conversation missing or outside project")
                now = time.strftime('%Y-%m-%d %H:%M:%S')
                conv_data['messages'].extend([
                    {"role": "user", "content": query, "timestamp": now},
                    {"role": "assistant", "content": answer, "timestamp": now, **extra},
                ])
                if conv_data.get('title') == '新的對話' and len(conv_data['messages']) == 2:
                    conv_data['title'] = query[:30]
                conv_data['updated_at'] = now
                return conv_data
            update_json_locked(conv_path, append_messages)
            saved = True
        except Exception:
            logger.exception("Failed to save chat history")
            return jsonify({"ok": False, "saved": False, "answer": answer, **extra,
                            "msg": "回答已產生，但對話未儲存；請保留本次問答後重試。"}), 503
    return jsonify({"ok": True, "saved": saved, "answer": answer, **extra})


def _anchor_source(pid, paper_id, source_hint=None):
    """讀與閱讀器相同的全文來源；source_hint 是閱讀器回報的 fulltext_source。"""
    hint = str(source_hint or '')
    prefer = 'flowb' if hint.startswith('flowb') else ('flowa' if hint.startswith('flowa') else 'auto')
    data_root = resolve_data_root()
    paper_dir = resolve_literature_paper_dir(data_root, pid, paper_id, for_write=False, migrate_legacy=True)
    source = _read_fulltext_source(data_root, pid, paper_id, paper_dir, prefer)
    if source is None:
        raise FileNotFoundError(paper_id)
    meta = {"pid": pid, "paper_id": paper_id, "source": source["target_source"], "source_sha256": source["source_sha256"]}
    return source["blocks"], meta


def _selection_hint(selection):
    return selection.get('source') if isinstance(selection, dict) else None


def _anchored_tutor_reply(pid, query, mode, data, conv_id):
    """選段解釋／批判：先由伺服器驗證選段，再請模型回 JSON，最後逐字核對模型引文。"""
    try:
        paper_id = validate_id(data.get('paper_id'), "paper_id")
    except BadRequest:
        return jsonify({"ok": False, "msg": "Invalid paper_id"}), 400
    selection = data.get('selection')
    try:
        pages, meta = _anchor_source(pid, paper_id, _selection_hint(selection))
        anchor = study_anchors.resolve(pages, selection, meta)
    except study_anchors.AnchorError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "全文尚未就緒，無法定位選取文字"}), 404
    if anchor["status"] not in study_anchors.TEXT_FOUND:
        return jsonify({"ok": False, "anchor": anchor,
                        "msg": "選取的文字在目前原文中找不到或出現多次；請重新選取（沒有呼叫模型）"}), 409
    window = study_anchors.evidence_window(pages, anchor)
    raw = tutor_engine.process_anchored_query(query, mode, window, anchor)
    structured = study_anchors.verify_structured(raw, window, anchor, meta)
    structured["mode"] = mode
    if anchor["side"] == "translation":
        structured["selection_source"] = study_anchors.original_for(pages, anchor, meta)
    return _reply_and_save(pid, conv_id, query, study_anchors.render_text(structured, mode), structured)

# -----------------------------------------------------------------------------
# 4. 筆記存檔 (Notes)
# -----------------------------------------------------------------------------

@study_bp.route('/api/study/notes/save', methods=['POST'])
def save_user_notes():
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        notes = data.get('notes')
        if not isinstance(notes, str):
            return jsonify({'ok': False, 'msg': 'Invalid notes'}), 400
        
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404

        note_path = _get_study_note_path(pid)
        payload = {
            "pid": str(pid),
            "notes": str(notes or ""),
            "updated_at": time.strftime('%Y-%m-%d %H:%M:%S')
        }
        if 'base_rev' not in data or data['base_rev'] is None:
            return jsonify({"ok": False, "msg": "請重新載入筆記後再儲存"}), 428
        if type(data['base_rev']) is not int or data['base_rev'] < 0:
            return jsonify({"ok": False, "msg": "Invalid base_rev"}), 400
        from flask_login import current_user
        try:
            actor = str(current_user.id) if current_user.is_authenticated else None
        except AttributeError:
            actor = None
        result = check_and_save_json(note_path, payload, data['base_rev'], actor, history_dir=note_path + '.history')
        return jsonify(result), (200 if result['ok'] else 409)
    except Exception as e:
        return _internal_error("save_user_notes", e)

# -----------------------------------------------------------------------------
# 5. 筆記讀取 (Notes Load)
# -----------------------------------------------------------------------------

@study_bp.route('/api/study/notes/load/<pid>', methods=['GET'])
def load_user_notes(pid):
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        note_path = _get_study_note_path(pid)
        
        content = ""
        revision = 0
        if os.path.exists(note_path):
            data = load_json_locked(note_path, {}, strict=True)
            if isinstance(data, dict):
                content = str(data.get("notes") or "")
                revision = data.get("_rev", 0)
            else:
                content = str(data or "")
        else:
            # Backward compatibility: legacy shared note dir and txt file.
            old_note_path = os.path.join(resolve_data_root(), "note", f"{pid}_note.json")
            if os.path.exists(old_note_path):
                with open(old_note_path, 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
                    if isinstance(old_data, dict):
                        content = str(old_data.get("notes") or "")
                    else:
                        content = str(old_data or "")

            # Legacy plain-text notes under project dir.
            legacy_path = os.path.join(resolve_data_root(), pid, "study_notes.txt")
            if not content and os.path.exists(legacy_path):
                with open(legacy_path, 'r', encoding='utf-8') as f:
                    content = f.read()

        requested_rev = request.args.get("rev")
        if requested_rev is not None:
            if not requested_rev.isascii() or not requested_rev.isdigit():
                return jsonify({"ok": False, "msg": "Invalid revision"}), 400
            requested_rev = int(requested_rev)
            if requested_rev != revision:
                snapshot = os.path.join(note_path + ".history", f"{requested_rev}.json")
                if not os.path.isfile(snapshot):
                    return jsonify({"ok": False, "msg": "Revision not found"}), 404
                old = load_json_locked(snapshot, {}, strict=True)
                content, revision = old["notes"], old.get("_rev", 0)
        return jsonify({"ok": True, "notes": content, "rev": revision})
    except Exception as e:
        return _internal_error("load_user_notes", e)

# -----------------------------------------------------------------------------
# 5B. 證據筆記 (roothinks.study.evidence_notes/1)
#     權限沿用本檔 before_request：GET＝viewer、POST＝editor；限定編輯進不了 Study。
#     AI 草稿只有 accepted=True 才寫入；原文事實由伺服器重新驗證；留言與審閱事件只追加。
# -----------------------------------------------------------------------------

def _actor():
    from flask_login import current_user
    try:
        if current_user.is_authenticated:
            return {"user_id": current_user.id, "username": current_user.username}
    except AttributeError:
        pass
    return None


def _evidence_path(pid):
    return study_evidence_notes.notes_path(resolve_data_root(), pid)


def _evidence_write(pid, mutate, base_rev):
    path = _evidence_path(pid)
    actor = _actor()
    result = mutate_versioned_json(path, mutate, base_rev, str(actor["user_id"]) if actor else None,
                                   history_dir=path + '.history')
    return result


def _evidence_error(exc):
    status = 404 if isinstance(exc, NotFound) else 403 if isinstance(exc, Forbidden) else 409 if isinstance(exc, Conflict) else 400
    body = {"ok": False, "msg": exc.description}
    if isinstance(exc, Conflict):
        body["stale_suggestion"] = True
    return jsonify(body), status


def _resolve_note_anchors(pid, paper_id, selections):
    """把前端的選段與引文（順序不變）重新交給伺服器驗證；全文讀不到時標 unavailable，仍可存成無證據筆記。"""
    if not selections:
        return []
    if not isinstance(selections, list) or len(selections) > 1 + study_evidence_notes.MAX_EVIDENCE:
        raise BadRequest("證據數量不合法")
    try:
        pages, meta = _anchor_source(pid, paper_id, _selection_hint(selections[0]))
    except FileNotFoundError:
        pages, meta = None, None
    anchors = []
    for item in selections:
        try:
            sel = study_anchors.clean_selection(item)
        except study_anchors.AnchorError as exc:
            raise BadRequest(str(exc))
        if pages is None:
            anchors.append({"schema": study_anchors.ANCHOR_SCHEMA, "pid": pid, "paper_id": paper_id, "side": sel["side"],
                            "quote": sel["quote"], "ref": sel["ref"], "status": "unavailable"})
        else:
            anchors.append(study_anchors.resolve(pages, item, meta))
    return anchors


@study_bp.route('/api/study/evidence_notes/<pid>', methods=['GET'])
def list_evidence_notes(pid):
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        doc = study_evidence_notes.load(_evidence_path(pid), pid)
        scoped = _review_scope(pid)
        if scoped:
            doc["notes"] = study_evidence_notes.review_shared(doc["notes"])
        if request.args.get('verify') == '1':
            _attach_current_anchor_status(pid, doc["notes"])
        if not scoped:
            # 反向連結：這則筆記被用在稿件哪幾段（R4 段落出處）。讀不到出處檔時不影響筆記清單。
            try:
                from app.core_pro.manuscript import paragraph_provenance
                links = paragraph_provenance.load(resolve_data_root(), pid)["links"]
                for note in doc["notes"]:
                    note["used_in"] = paragraph_provenance.used_in(links, note.get("note_id"))
            except Exception:
                logger.warning("[evidence_notes] 讀取段落出處失敗 pid=%s", pid, exc_info=True)
        return jsonify({"ok": True, "rev": doc.get("_rev", 0), "notes": doc["notes"], "review_scope": scoped})
    except Exception as e:
        return _internal_error("list_evidence_notes", e)


def _attach_current_anchor_status(pid, notes):
    """只在回應中附上「現在」的錨點狀態（不寫回）；原文重跑後會顯示版本已變更或找不到。"""
    sources = {}
    for note in notes:
        for anchor in note.get("anchors") or []:
            paper_id = anchor.get("paper_id")
            if not paper_id or anchor.get("status") == "unavailable":
                continue
            key = (paper_id, str(anchor.get("source") or "")[:5])
            if key not in sources:
                try:
                    sources[key] = _anchor_source(pid, validate_id(paper_id, "paper_id"), anchor.get("source"))
                except Exception:
                    sources[key] = None
            if sources[key] is None:
                anchor["current"] = {"status": "unavailable"}
                continue
            current = study_anchors.resolve(sources[key][0], anchor, sources[key][1])
            anchor["current"] = {k: current.get(k) for k in ("status", "version_changed", "ref", "page")}


@study_bp.route('/api/study/evidence_notes/accept', methods=['POST'])
def accept_evidence_note():
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        if data.get('accepted') is not True:
            return jsonify({"ok": False, "msg": "AI 草稿必須由研究者明確接受才會存檔"}), 400
        if 'base_rev' not in data or data['base_rev'] is None:
            return jsonify({"ok": False, "msg": "請重新載入證據筆記後再儲存"}), 428
        note_in = data.get('note')
        if not isinstance(note_in, dict):
            return jsonify({"ok": False, "msg": "Invalid note"}), 400
        selections = note_in.get('anchors') or []
        paper_id = None
        if selections or note_in.get('paper_id'):
            paper_id = validate_id(note_in.get('paper_id'), "paper_id")
        anchors = _resolve_note_anchors(pid, paper_id, selections)
        note = study_evidence_notes.build_note(note_in, anchors, _actor())
        def mutate(current):
            doc = study_evidence_notes.document(current, pid)
            doc["notes"].append(note)
            return doc
        result = _evidence_write(pid, mutate, data['base_rev'])
        if result.get('ok'):
            result['note'] = note
        return jsonify(result), (200 if result.get('ok') else 409)
    except (BadRequest, NotFound, Forbidden, Conflict) as e:
        return _evidence_error(e)
    except Exception as e:
        return _internal_error("accept_evidence_note", e)


@study_bp.route('/api/study/evidence_notes/update', methods=['POST'])
def update_evidence_note():
    """作者改寫自己的陳述（非作者 403，請改用建議）；舊分頁送出得到 409，不覆蓋。錨點不可在此修改。"""
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        if 'base_rev' not in data or data['base_rev'] is None:
            return jsonify({"ok": False, "msg": "請重新載入證據筆記後再儲存"}), 428
        nid = study_evidence_notes.note_id(data.get('note_id'))
        actor = _actor()
        def mutate(current):
            doc = study_evidence_notes.document(current, pid)
            study_evidence_notes.edit(study_evidence_notes.find(doc, nid), data.get('statements'), actor,
                                      data.get('title') if 'title' in data else None)
            return doc
        result = _evidence_write(pid, mutate, data['base_rev'])
        return jsonify(result), (200 if result.get('ok') else 409)
    except (BadRequest, NotFound, Forbidden, Conflict) as e:
        return _evidence_error(e)
    except Exception as e:
        return _internal_error("update_evidence_note", e)


@study_bp.route('/api/study/evidence_notes/suggest', methods=['POST'])
def suggest_evidence_note():
    """非作者提出修改建議：只追加，作者的陳述不變。"""
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        nid = study_evidence_notes.note_id(data.get('note_id'))
        actor = _actor()
        made = {}
        def mutate(current):
            doc = study_evidence_notes.document(current, pid)
            made.update(study_evidence_notes.suggest(study_evidence_notes.find(doc, nid), data.get('statements'),
                                                     data.get('reason'), actor))
            return doc
        result = _evidence_write(pid, mutate, None)
        result['suggestion'] = made
        return jsonify(result), 200
    except (BadRequest, NotFound, Forbidden, Conflict) as e:
        return _evidence_error(e)
    except Exception as e:
        return _internal_error("suggest_evidence_note", e)


@study_bp.route('/api/study/evidence_notes/suggestion', methods=['POST'])
def resolve_evidence_suggestion():
    """作者接受或不採用一則建議；接受會改陳述，因此需要 base_rev，舊分頁得到 409。"""
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        if 'base_rev' not in data or data['base_rev'] is None:
            return jsonify({"ok": False, "msg": "請重新載入證據筆記後再處理建議"}), 428
        nid = study_evidence_notes.note_id(data.get('note_id'))
        actor = _actor()
        def mutate(current):
            doc = study_evidence_notes.document(current, pid)
            study_evidence_notes.resolve_suggestion(study_evidence_notes.find(doc, nid), data.get('suggestion_id'),
                                                    data.get('decision'), actor, data.get('reason'),
                                                    allow_stale=data.get('allow_stale') is True)
            return doc
        result = _evidence_write(pid, mutate, data['base_rev'])
        return jsonify(result), (200 if result.get('ok') else 409)
    except (BadRequest, NotFound, Forbidden, Conflict) as e:
        return _evidence_error(e)
    except Exception as e:
        return _internal_error("resolve_evidence_suggestion", e)


@study_bp.route('/api/study/evidence_notes/comment', methods=['POST'])
def comment_evidence_note():
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        nid = study_evidence_notes.note_id(data.get('note_id'))
        entry = study_evidence_notes.comment(data.get('body'), _actor(), 'member')
        def mutate(current):
            doc = study_evidence_notes.document(current, pid)
            study_evidence_notes.find(doc, nid)["comments"].append(entry)
            return doc
        result = _evidence_write(pid, mutate, None)
        result['comment'] = entry
        return jsonify(result), 200
    except (BadRequest, NotFound, Forbidden, Conflict) as e:
        return _evidence_error(e)
    except Exception as e:
        return _internal_error("comment_evidence_note", e)


@study_bp.route('/api/study/evidence_notes/review', methods=['POST'])
def review_evidence_note():
    """作者請求或撤回審閱；只改審閱狀態與事件紀錄，不改陳述。"""
    try:
        data = request.get_json(silent=True) or {}
        pid = _normalize_study_pid(data.get('pid'))
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        nid = study_evidence_notes.note_id(data.get('note_id'))
        actor = _actor()
        def mutate(current):
            doc = study_evidence_notes.document(current, pid)
            study_evidence_notes.review_event(study_evidence_notes.find(doc, nid), data.get('action'), actor, data.get('message'))
            return doc
        result = _evidence_write(pid, mutate, None)
        return jsonify(result), 200
    except (BadRequest, NotFound, Forbidden, Conflict) as e:
        return _evidence_error(e)
    except Exception as e:
        return _internal_error("review_evidence_note", e)


@study_bp.route('/api/study/evidence_notes/<pid>/export', methods=['GET'])
def export_evidence_notes(pid):
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        doc = study_evidence_notes.load(_evidence_path(pid), pid)
        if _review_scope(pid):
            doc["notes"] = study_evidence_notes.review_shared(doc["notes"])
        body = study_evidence_notes.to_markdown(doc, pid)
        response = current_app.response_class(body, mimetype='text/markdown; charset=utf-8')
        response.headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(pid)}_evidence_notes.md"
        return response
    except Exception as e:
        return _internal_error("export_evidence_notes", e)


def _read_fulltext_source(data_root, pid, paper_id, paper_dir, prefer_mode):
    """讀取 Study 全文來源；閱讀器與錨點驗證共用，確保 page／block 編號一致。缺檔回傳 None。"""
    # 優先查找 full_text_trans.json (含 NLLB 翻譯的版本)
    trans_path = safe_join_under(paper_dir, "05_interprets", "full_text_trans.json")
    # Backward-compatible legacy location
    trans_path_legacy = safe_join_under(paper_dir, "05_interprets", "fusion", "full_text_trans.json")
    # Flow B (Slim) output location
    trans_path_flowb = safe_join_under(paper_dir, "06_translates", "fusion", "full_text_trans.json")
    fusion_path = safe_join_under(paper_dir, "05_interprets", "fusion", "full_text.json")
    
    # 檢查翻譯檔是否真的包含翻譯欄位(content_zh)
    translation_ready = False
    if not os.path.exists(trans_path) and os.path.exists(trans_path_legacy):
        trans_path = trans_path_legacy
    if not os.path.exists(trans_path) and os.path.exists(trans_path_flowb):
        trans_path = trans_path_flowb

    if os.path.exists(trans_path):
        try:
            trans_data = load_json_locked(trans_path, {})
            trans_content = trans_data.get("content", []) if isinstance(trans_data, dict) else []
            for page in trans_content:
                if not isinstance(page, dict):
                    continue
                for block in page.get("blocks", []):
                    if isinstance(block, dict) and str(block.get("content_zh", "")).strip():
                        translation_ready = True
                        break
                if translation_ready:
                    break
        except Exception:
            translation_ready = False

    # source 選擇策略：
    # - prefer=flowa -> 強制讀 Flow A 融合檔，避免舊翻譯檔覆蓋最新切塊
    # - prefer=flowb -> 優先讀 trans（可用時）
    # - prefer=auto  -> 維持原行為（trans 可用時優先）
    target_source = "flowa_fusion"
    if prefer_mode in {"flowa", "a", "fusion", "raw"}:
        target_path = fusion_path
        target_source = "flowa_fusion"
    elif prefer_mode in {"flowb", "b", "trans", "translation"}:
        if os.path.exists(trans_path) and translation_ready:
            target_path = trans_path
            target_source = "flowb_trans"
        else:
            target_path = fusion_path
            target_source = "flowa_fusion"
    else:
        if os.path.exists(trans_path) and translation_ready:
            target_path = trans_path
            target_source = "flowb_trans"
        else:
            target_path = fusion_path
            target_source = "flowa_fusion"
    
    if not os.path.exists(target_path):
        return None
    data = load_json_locked(target_path, {})
    
    # 提取內容
    blocks_array = []  # Store blocks in original structure
    content_raw = data.get("content", [])
    equation_fallback_injected = 0

    if target_source == "flowa_fusion" and isinstance(content_raw, list):
        content_raw, equation_fallback_injected = _inject_segmentation_equation_fallback(
            content_raw,
            data_root,
            pid,
            paper_id,
        )
        if equation_fallback_injected > 0:
            logger.warning(
                "[get_fulltext] injected %s segmentation equation blocks for %s/%s",
                equation_fallback_injected,
                pid,
                paper_id,
            )
            target_source = "flowa_fusion+seg_eq_fallback"
    
    if isinstance(content_raw, list):
        # 數據結構: content = [{ page: 1, blocks: [{ content: "...", type: "...", content_zh: "..." }, ...] }, ...]
        text_blocks = []
        for page in content_raw:
            if isinstance(page, dict):
                page_blocks = page.get("blocks", [])
                
                # [Fix v2.8] 映射 content_zh -> translation
                processed_blocks = []
                for block in page_blocks:
                    if isinstance(block, dict):
                        processed_block = dict(block)  # Copy
                        # Map content_zh to translation for frontend
                        if "content_zh" in processed_block and "translation" not in processed_block:
                            processed_block["translation"] = processed_block["content_zh"]
                        processed_blocks.append(processed_block)
                        
                        if block.get("content"):
                            text_blocks.append(block["content"])
                    else:
                        processed_blocks.append(block)
                
                blocks_array.append({
                    "page": page.get("page", 0),
                    "blocks": processed_blocks  # Use processed blocks with translation
                })
        
        full_content = "\n\n".join(text_blocks) if text_blocks else ""
    elif isinstance(content_raw, dict):
        full_content = content_raw.get("text", json.dumps(content_raw, ensure_ascii=False))
    else:
        full_content = str(content_raw)
    
    return {
        "data": data,
        "target_path": target_path,
        "target_source": target_source,
        "translation_ready": translation_ready,
        "blocks": blocks_array,
        "full_content": full_content,
        "equation_fallback_injected": equation_fallback_injected,
        # 內容版本：以正規化 JSON 計算，與檔案排版無關；重跑解析就會改變。
        "source_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
    }


@study_bp.route('/api/study/get_fulltext/<pid>/<paper_id>', methods=['GET'])
def get_fulltext(pid, paper_id):
    """
    獲取文獻的全文（來自 Task 5 Interpret 輸出）
    [Fixed v2.8] 優先使用 full_text_trans.json (含翻譯), 並映射 content_zh -> translation
    """
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        try:
            paper_id = validate_id(paper_id, "paper_id")
        except BadRequest:
            return jsonify({"ok": False, "msg": "Invalid paper_id"}), 400

        data_root = resolve_data_root()
        project_dir = safe_join_under(data_root, pid)
        paper_dir = resolve_literature_paper_dir(
            data_root,
            pid,
            paper_id,
            for_write=False,
            migrate_legacy=True,
        )

        # Default to Flow A so even stale frontend bundles still render latest segmentation first.
        prefer_mode = str(request.args.get("prefer", "flowa") or "flowa").strip().lower()

        source = _read_fulltext_source(data_root, pid, paper_id, paper_dir, prefer_mode)
        if source is not None:
            data = source["data"]
            target_source = source["target_source"]
            translation_ready = source["translation_ready"]
            blocks_array = source["blocks"]
            full_content = source["full_content"]
            equation_fallback_injected = source["equation_fallback_injected"]
            display_title = _resolve_fulltext_display_title(pid, paper_id, data if isinstance(data, dict) else None, project_dir)

            # [Fix] 讀取 summary.json 並做欄位相容
            summary_data = ""
            summary_path = safe_join_under(paper_dir, "05_interprets", "summary.json")
            
            if os.path.exists(summary_path):
                try:
                    summary_json = load_json_locked(summary_path, {})
                    
                    # If summary contains error, generate fallback
                    if "error" in summary_json and not summary_json.get("abstract_zh"):
                        summary_data = {
                            "abstract_zh": "摘要生成失敗，但全文內容可供參閱。",
                            "abstract_en": "[Offline Mode] AI summarization unavailable. Full text accessible.",
                            "key_findings": [],
                            "mode": "error_fallback",
                            "error_msg": summary_json.get("error","Unknown error")[:150]
                        }
                    else:
                        # 相容非標準摘要格式 (如 background/methods/conclusion...)
                        if isinstance(summary_json, dict) and not summary_json.get("abstract_zh"):
                            fallback_parts = []
                            for key in ["background", "introduction_problem", "methods", "conclusion"]:
                                val = summary_json.get(key)
                                if isinstance(val, str) and val.strip():
                                    fallback_parts.append(val.strip())

                            proposed = summary_json.get("proposed_solution")
                            if isinstance(proposed, dict):
                                desc = proposed.get("description") or proposed.get("goal")
                                if isinstance(desc, str) and desc.strip():
                                    fallback_parts.append(desc.strip())

                            key_findings = summary_json.get("key_findings")
                            if isinstance(key_findings, list):
                                key_findings = [str(x).strip() for x in key_findings if str(x).strip()]
                            else:
                                key_findings = []

                            abstract_text = "\n\n".join(fallback_parts[:3]).strip() or "此文獻已完成解析，但摘要欄位尚未標準化。"
                            summary_data = {
                                "abstract_zh": abstract_text,
                                "key_findings": key_findings,
                                "mode": "normalized"
                            }
                        else:
                            summary_data = summary_json

                    # Generic fallback abstract is technically present but not informative.
                    if isinstance(summary_data, dict):
                        if _is_quota_summary(summary_data):
                            summary_data = {
                                "abstract_zh": "429 quota: LLM token/配額已達上限，請稍後重試。",
                                "key_findings": [],
                                "mode": "quota_error",
                            }

                        abstract_text = str(summary_data.get("abstract_zh", "") or "").strip()
                        if summary_data.get("mode") != "quota_error" and abstract_text in {
                            "根據論文內容自動提取，完整分析請參考全文。",
                            "此文獻已完成解析，但摘要欄位尚未標準化。"
                        }:
                            kf = summary_data.get("key_findings")
                            kf = _sanitize_summary_lines(kf, max_items=3, max_line_chars=180, max_total_chars=520)
                            conclusion = str(summary_data.get("conclusion", "") or "").strip()
                            rebuilt = "\n".join(kf[:3]).strip()
                            if not rebuilt and conclusion:
                                rebuilt = conclusion[:220].rstrip() + ("..." if len(conclusion) > 220 else "")
                            if rebuilt:
                                summary_data["abstract_zh"] = rebuilt

                        # Always keep key findings concise for UI safety.
                        summary_data["key_findings"] = _sanitize_summary_lines(
                            summary_data.get("key_findings"),
                            max_items=5,
                            max_line_chars=180,
                            max_total_chars=800,
                        )

                        if summary_data.get("mode") != "quota_error" and _looks_like_noisy_summary(summary_data.get("abstract_zh", "")):
                            rebuilt_summary = _rebuild_summary_from_blocks(blocks_array)
                            if rebuilt_summary:
                                summary_data = rebuilt_summary
                            else:
                                summary_data["abstract_zh"] = "目前尚無可讀摘要（OCR 噪音偏高），請先參考下方全文對照。"
                                summary_data["key_findings"] = []
                                summary_data["mode"] = "quality_guard"
                except Exception as e:
                    logger.warning(f"[get_fulltext] Failed to load summary: {e}")
                    summary_data = ""

            reflow_data = None
            reflow_meta = None
            document_flow_data = None
            document_flow_path = safe_join_under(
                paper_dir,
                "06_translates",
                "reflow",
                "document_flow.json",
            )
            if os.path.exists(document_flow_path):
                try:
                    loaded_document_flow = load_json_locked(document_flow_path, {})
                    if isinstance(loaded_document_flow, dict) and isinstance(loaded_document_flow.get("items"), list):
                        document_flow_data = loaded_document_flow
                except Exception as e:
                    logger.warning(f"[get_fulltext] Failed to load document flow payload: {e}")

            reflow_path = safe_join_under(
                paper_dir,
                "06_translates",
                "reflow",
                "semantic_sections.json",
            )
            if os.path.exists(reflow_path):
                try:
                    loaded_reflow = load_json_locked(reflow_path, {})
                    if isinstance(loaded_reflow, dict):
                        reflow_data = loaded_reflow
                        if document_flow_data is not None:
                            reflow_data["document_flow"] = document_flow_data
                        coverage = _compute_reflow_coverage(blocks_array, loaded_reflow)
                        min_ratio = max(0.0, min(1.0, _read_float_env("STUDY_REFLOW_MIN_COVERAGE_RATIO", 0.55)))
                        min_ref_ratio = max(0.0, min(1.0, _read_float_env("STUDY_REFLOW_MIN_REF_COVERAGE_RATIO", 0.45)))
                        min_char_ratio = max(0.0, min(1.0, _read_float_env("STUDY_REFLOW_MIN_CHAR_COVERAGE_RATIO", min_ratio)))
                        min_sections = max(1, int(_read_float_env("STUDY_REFLOW_MIN_SECTIONS", 2)))
                        warnings = []
                        if coverage.get("ref_coverage", 0.0) < min_ref_ratio:
                            warnings.append("low_ref_coverage")
                        if coverage.get("char_coverage", coverage.get("ratio", 0.0)) < min_char_ratio:
                            warnings.append("low_char_coverage")
                        if coverage["sections"] < min_sections:
                            warnings.append("low_sections")

                        allow_suppress = _read_bool_env("STUDY_REFLOW_ALLOW_SUPPRESS", False)
                        should_hide = bool(allow_suppress and warnings)
                        reflow_meta = {
                            "coverage": coverage,
                            "thresholds": {
                                "min_ratio": min_ratio,
                                "min_ref_ratio": min_ref_ratio,
                                "min_char_ratio": min_char_ratio,
                                "min_sections": min_sections,
                            },
                            "min_ratio": min_ratio,
                            "min_ref_ratio": min_ref_ratio,
                            "min_char_ratio": min_char_ratio,
                            "min_sections": min_sections,
                            "warnings": warnings,
                            "allow_suppress": allow_suppress,
                            "suppressed": bool(should_hide),
                        }
                        if should_hide:
                            logger.info(
                                "[get_fulltext] suppress reflow for %s/%s due to coverage warnings=%s",
                                pid,
                                paper_id,
                                ",".join(warnings),
                            )
                            reflow_data = None
                except Exception as e:
                    logger.warning(f"[get_fulltext] Failed to load reflow payload: {e}")
            elif document_flow_data is not None:
                reflow_data = {"document_flow": document_flow_data}
            
            return jsonify({
                "ok": True,
                "fulltext": full_content,
                "blocks": blocks_array,  # [New] Return blocks in original structure
                "title": display_title,
                "summary": summary_data,
                "reflow": reflow_data,
                "reflow_document_flow": document_flow_data,
                "reflow_meta": reflow_meta,
                "translation_ready": translation_ready,
                "fulltext_source": target_source,
                "equation_fallback_injected": equation_fallback_injected,
                "prefer_mode": prefer_mode,
                "source_sha256": source["source_sha256"],
                "paper_id": data.get("paper_id", paper_id)
            })
        
        # Fallback: 嘗試查找 legacy 路徑
        interpret_path = safe_join_under(paper_dir, "05_interprets", "full_content.json")
        
        if os.path.exists(interpret_path):
            data = load_json_locked(interpret_path, {})
            display_title = _resolve_fulltext_display_title(pid, paper_id, data if isinstance(data, dict) else None, project_dir)
            return jsonify({
                "ok": True,
                "fulltext": data.get("full_content", ""),
                "title": display_title,
                "summary": data.get("summary", ""),
                "reflow": None
            })
        
        # 都不存在 -> Task 5 還未完成
        return jsonify({"ok": False, "msg": "Task 5 not completed - Full text not available"}), 404
    
    except Exception as e:
        return _internal_error("get_fulltext", e)


@study_bp.route('/api/study/media_gallery/<pid>/<paper_id>', methods=['GET'])
def get_media_gallery(pid, paper_id):
    """
    回傳指定文獻的圖表清單與 LLM 解說。
    Query:
            - kind=figure|table|equation (default: figure)
      - refresh=1 強制重跑 LLM（忽略快取）
    """
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return jsonify({"ok": False, "msg": "Invalid PID"}), 404
        try:
            paper_id = validate_id(paper_id, "paper_id")
        except BadRequest:
            return jsonify({"ok": False, "msg": "Invalid paper_id"}), 400

        kind_raw = request.args.get("kind", "figure")
        try:
            kind, _, _ = _normalize_media_kind(kind_raw)
        except BadRequest:
            return jsonify({"ok": False, "msg": "Invalid kind. Use figure|table|equation"}), 400

        refresh_raw = str(request.args.get("refresh", "0")).strip().lower()
        refresh = refresh_raw in {"1", "true", "yes", "y", "on"}

        items = _build_media_gallery(pid, paper_id, kind, refresh=refresh)
        return jsonify(
            {
                "ok": True,
                "pid": pid,
                "paper_id": paper_id,
                "kind": kind,
                "count": len(items),
                "items": items,
            }
        )
    except Exception as e:
        return _internal_error("get_media_gallery", e)


@study_bp.route('/api/study/media_asset/<pid>/<paper_id>/<seq_id>.png', methods=['GET'])
def get_media_asset(pid, paper_id, seq_id):
    """
        回傳 Figure/Table/Equation 的切塊 PNG。
    Query:
            - kind=figure|table|equation
    """
    try:
        pid = _normalize_study_pid(pid)
        if not pid:
            return "Invalid PID", 404
        try:
            paper_id = validate_id(paper_id, "paper_id")
            seq_id = _safe_seq_id(seq_id)
        except BadRequest:
            return "Invalid parameters", 400

        kind_raw = request.args.get("kind", "figure")
        try:
            kind, _, type_name = _normalize_media_kind(kind_raw)
        except BadRequest:
            return "Invalid kind", 400

        data_root = resolve_data_root()
        try:
            vns_path = safe_join_under(data_root, pid, "literature", "segmentation", f"{pid}_vns.json")
            vns_payload = load_json_locked(vns_path, {})
            records = (
                (vns_payload.get("papers", {}) or {}).get(paper_id, {}).get("records", [])
                if isinstance(vns_payload, dict)
                else []
            )
            if isinstance(records, list):
                rec = next(
                    (
                        r for r in records
                        if isinstance(r, dict)
                        and str(r.get("seq_id") or "").strip() == seq_id
                        and str(r.get("type") or "").strip().lower() == type_name.lower()
                    ),
                    None,
                )
                if isinstance(rec, dict):
                    direct_path = _to_local_data_path(data_root, rec.get("png_path"))
                    if direct_path and os.path.exists(direct_path):
                        return send_file(direct_path, mimetype="image/png", as_attachment=False)
        except Exception as direct_err:
            logger.warning("[get_media_asset] direct VNS lookup failed: %s", direct_err)

        rows = _collect_media_records(pid, paper_id, kind)
        target = next((r for r in rows if str(r.get("seq_id") or "") == seq_id), None)
        if not target:
            return "Media not found", 404

        img_path = str(target.get("png_abs_path") or "")
        if not img_path or not os.path.exists(img_path):
            return "Image file missing", 404
        return send_file(img_path, mimetype="image/png", as_attachment=False)
    except Exception as e:
        logger.error("[get_media_asset] %s", e, exc_info=True)
        return "Internal server error", 500
