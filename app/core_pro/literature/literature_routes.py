# 檔案路徑: roothinks/app/core_pro/literature/literature_routes.py
# 產生時間: 2026-07-05 03:30 +08:00
# 版本: v3.6
# 模組定位:
#   Literature 主藍圖入口與共用依賴註冊(ACL、lazy loader、執行器);
#   統一註冊 context/batch/context-chain/debug 子路由;
#   Flow B reflow artifact 生成(task_5b -> heuristic fallback)。
# 主要責任:
#   1. 保留相容 wrapper(供測試 monkeypatch)並轉發至拆分模組。
#   2. _generate_flowb_reflow_artifact():semantic_sections.json 生成。
# 維護提醒:
#   - v3.6 新增 reflow 內容零損失防線:
#     (1) _validate_reflow_coverage():assigned_ratio(全 ref 追蹤)與
#         content_ratio(字數覆蓋)寫入 meta.coverage;
#         assigned<1.0 或 content<0.90 記 coverage_warning,
#         任一 <0.75 時 generation_mode 加 _low_coverage 後綴。
#     (2) _ensure_abstract_section():第一頁有 >=300 字長文但輸出無
#         Abstract section 時,規則保底注入(inferred, conf=0.5)。
#     驗證失敗「不會」讓 reflow 整體失敗(可用性優先),但警告必須可見。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_reflow_coverage.py test -q
# ------------------------------------------------------------------------------

# [MVP+Prototype Handoff Header]
# 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
# 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
# 2026-04-20~2026-04-21 Study/FlowB 變更已集中於本檔案，供後續人類團隊接手 hardening。
from flask import Blueprint, request, jsonify, send_file, current_app, render_template
import os
import json
import logging
import time
import threading
import atexit
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from werkzeug.utils import secure_filename
from werkzeug.exceptions import BadRequest, Forbidden
from app.models import db, Project, Paper
from app.system_runtime import cap_worker_count, get_cpu_limit_info
from app.services.context_chain_service import ContextChainService
from app.services.formal_project_sync import ensure_formal_project_records
from app.security import (
    enforce_project_ownership,
    get_acl_context,
    load_json_locked,
    safe_join_under,
    validate_id,
    write_json_locked,
)
from app.core_pro.storage_layout import (
    ensure_project_module_roots,
    literature_paper_dir,
    literature_papers_root,
    literature_root,
    list_literature_papers,
    project_dir,
    resolve_literature_paper_dir,
)
from PIL import Image, ImageOps
from app.core_pro.literature.literature_bflow import (
    FlowBRouteDeps,
    FlowBRuntimeDeps,
    append_flow_event as _bflow_append_flow_event,
    build_job_config as _bflow_build_job_config,
    flowb_debug_snapshot as _bflow_debug_snapshot,
    run_flowb_subprocess as _bflow_run_flowb_subprocess,
    run_translation_impl,
    write_job_config as _bflow_write_job_config,
)
from app.core_pro.literature.literature_batch_routes import register_batch_routes
from app.core_pro.literature.literature_context_chain_routes import register_context_chain_routes
from app.core_pro.literature.literature_context_routes import register_context_routes
from app.core_pro.literature.literature_debug_routes import register_debug_routes
from app.core_pro.literature.literature_processing_ops import (
    delete_paper_impl,
    get_block_json_impl,
    get_block_manifest_impl,
    get_full_json_impl,
    get_region_image_impl,
    get_status_impl,
    save_correction_impl,
    trigger_gold_bridge_impl,
    update_paper_metadata_impl,
    update_paper_status_impl,
)
from app.core_pro.literature.literature_flowb_helpers import (
    _flowb_append_uncovered_appendix,
    _flowb_build_ref_text_index,
    _flowb_build_reflow_prompt,
    _flowb_build_reflow_prompt_from_rows,
    _flowb_clean_section_text,
    _flowb_clean_section_text_by_mode,
    _flowb_clean_section_text_extractive,
    _flowb_clean_section_text_relaxed,
    _flowb_clip,
    _flowb_collect_all_block_refs,
    _flowb_collect_lang_from_refs,
    _flowb_collect_reflow_rows,
    _flowb_collect_section_refs,
    _flowb_is_section_anchor,
    _flowb_compute_fusion_quality_metrics,
    _flowb_extract_first_json_blob,
    _flowb_extract_first_json_object,
    _flowb_guess_abstract_en_from_trans_payload,
    _flowb_infer_label_by_position,
    _flowb_is_heading_like,
    _flowb_is_low_information_text,
    _flowb_is_meta_noise_line,
    _flowb_is_ready_generation_mode,
    _flowb_is_retryable_reflow_error,
    _flowb_match_section_label,
    _flowb_merge_llm_sections,
    _flowb_normalize_heading,
    _flowb_normalize_llm_sections,
    _flowb_normalize_refs,
    _flowb_parse_json_reply,
    _flowb_parse_reflow_sections_reply,
    _flowb_pick_best_block_text,
    _flowb_read_bool_env,
    _flowb_read_float_env,
    _flowb_read_positive_int_env,
    _flowb_read_ready_generation_modes,
    _flowb_read_reflow_mode_env,
    _flowb_repair_section_bilingual_fields,
    _flowb_rehome_appendix_special_refs,
    _flowb_squash_text,
    _flowb_strip_markdown_fence,
    _flowb_to_bool,
)

# 定義 Blueprint
literature_bp = Blueprint('literature_bp', __name__)
logger = logging.getLogger("LiteratureRoutes")

# 使用絕對路徑，確保在任何工作目錄下都能正確找到 data 資料夾
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_ROOT = os.path.join(BASE_DIR, "data")
logger.info(f"[Init] DATA_ROOT set to: {DATA_ROOT}")

# [Fix] 延遲初始化所有重型模組 (避免啟動時失敗)
cv_pipeline_engine = None
semantic_corrector = None
interpreter = None
semantic_reflow_task = None
task_3search_module = None
nllb_translator = None
context_chain_service = None
_BATCH_EXECUTOR_MAX_WORKERS = max(1, int(os.environ.get("LITERATURE_MAX_WORKERS", "1")))
_BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=_BATCH_EXECUTOR_MAX_WORKERS)
_FLOWB_EXECUTOR_MAX_WORKERS = max(1, int(os.environ.get("LITERATURE_MAX_WORKERS", "1")))
_FLOWB_EXECUTOR = ThreadPoolExecutor(max_workers=_FLOWB_EXECUTOR_MAX_WORKERS)
_ACTIVE_PIDS = set()
_ACTIVE_PIDS_LOCK = threading.Lock()
# ponytail: CPU NLLB full-paper translation can exceed 15 minutes; keep it bounded,
# but make the bound match the real local-model path instead of the old quick API path.
FLOWB_TIMEOUT_SEC = max(60, int(os.environ.get("LITERATURE_FLOWB_TIMEOUT_SEC", "7200")))
FLOWB_SUBPROCESS_ENABLED = os.environ.get("LITERATURE_FLOWB_USE_SUBPROCESS", "1").strip() != "0"


@atexit.register
def _shutdown_literature_executor():
    try:
        _BATCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    try:
        _FLOWB_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


def _get_batch_executor():
    global _BATCH_EXECUTOR, _BATCH_EXECUTOR_MAX_WORKERS
    requested = os.environ.get("LITERATURE_MAX_WORKERS", str(_BATCH_EXECUTOR_MAX_WORKERS))
    try:
        target = max(1, int(requested))
    except Exception:
        target = _BATCH_EXECUTOR_MAX_WORKERS
    target = cap_worker_count(target)

    if target != _BATCH_EXECUTOR_MAX_WORKERS:
        old = _BATCH_EXECUTOR
        _BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=target)
        _BATCH_EXECUTOR_MAX_WORKERS = target
        try:
            old.shutdown(wait=False, cancel_futures=False)
        except Exception:
            pass
        logger.info("[Runtime] resized literature batch executor to max_workers=%s", target)
    return _BATCH_EXECUTOR


def _get_flowb_executor():
    global _FLOWB_EXECUTOR, _FLOWB_EXECUTOR_MAX_WORKERS
    requested = os.environ.get("LITERATURE_MAX_WORKERS", str(_FLOWB_EXECUTOR_MAX_WORKERS))
    try:
        target = max(1, int(requested))
    except Exception:
        target = _FLOWB_EXECUTOR_MAX_WORKERS
    target = cap_worker_count(target)

    if target != _FLOWB_EXECUTOR_MAX_WORKERS:
        old = _FLOWB_EXECUTOR
        _FLOWB_EXECUTOR = ThreadPoolExecutor(max_workers=target)
        _FLOWB_EXECUTOR_MAX_WORKERS = target
        try:
            old.shutdown(wait=False, cancel_futures=False)
        except Exception:
            pass
        logger.info("[Runtime] resized literature flowb executor to max_workers=%s", target)
    return _FLOWB_EXECUTOR


@literature_bp.before_request
def _enforce_literature_project_acl():
    if not request.path.startswith("/api/literature/"):
        return None

    raw_pid = None
    if isinstance(request.view_args, dict):
        raw_pid = request.view_args.get("pid")
    if not raw_pid:
        raw_pid = (
            request.args.get("pid")
            or request.form.get("pid")
            or ((request.get_json(silent=True) or {}).get("pid") if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None)
        )
    if not raw_pid:
        m = re.match(r"^/api/literature/status/([^/]+)$", request.path)
        raw_pid = m.group(1) if m else ""
    if not raw_pid:
        return None
    pid = _normalize_literature_pid(raw_pid)
    if not pid:
        return jsonify({"status": "error", "message": "Invalid pid"}), 404
    try:
        enforce_project_ownership(pid)
    except Forbidden:
        return jsonify({"status": "error", "message": "Forbidden"}), 403
    except Exception:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return None


def _to_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return default

def get_cv_pipeline():
    """Lazy-load CV Pipeline"""
    global cv_pipeline_engine
    if cv_pipeline_engine is None:
        try:
            from app.core_pro.literature.literature_cvpipeline import PaperCVPipeline
            cv_pipeline_engine = PaperCVPipeline(DATA_ROOT)
        except Exception as e:
            logger.warning(f"[System] CV Pipeline init failed: {e}")
            return None
    return cv_pipeline_engine

def get_semantic_corrector():
    """Lazy-load Semantic Corrector"""
    global semantic_corrector
    if semantic_corrector is None:
        try:
            from app.llm_service.matching_tasks.task_4cv import SemanticCorrector
            semantic_corrector = SemanticCorrector()
        except Exception as e:
            logger.warning(f"[System] SemanticCorrector init failed: {e}")
            return None
    return semantic_corrector

def get_interpreter():
    """Lazy-load Interpreter"""
    global interpreter
    if interpreter is None:
        try:
            from app.llm_service.matching_tasks.task_5interpret import Interpreter
            interpreter = Interpreter()
        except Exception as e:
            logger.warning(f"[System] Interpreter init failed: {e}")
            return None
    return interpreter

def get_semantic_reflow_task():
    """Lazy-load Flow B Semantic Reflow Task (Task 5B)"""
    global semantic_reflow_task
    if semantic_reflow_task is None:
        try:
            from app.llm_service.matching_tasks.task_5b_reflow import SemanticReflowTask
            semantic_reflow_task = SemanticReflowTask()
        except Exception as e:
            logger.warning(f"[System] SemanticReflowTask init failed: {e}")
            return None
    return semantic_reflow_task

def get_task_3search():
    """Lazy-load Task 3 Search"""
    global task_3search_module
    if task_3search_module is None:
        try:
            from app.llm_service.matching_tasks import task_3search
            task_3search_module = task_3search
        except Exception as e:
            logger.warning(f"[System] Task 3 Search init failed: {e}")
            return None
    return task_3search_module

def get_nllb_translator():
    """Lazy-load Hybrid Translator (NLLB + Gemini fallback)"""
    global nllb_translator
    if nllb_translator is None:
        try:
            from app.core_pro.literature.literature_translator import get_translator
            nllb_translator = get_translator()
            logger.info(f"[System] Hybrid Translator ready: {nllb_translator.status}")
        except Exception as e:
            logger.warning(f"[System] Hybrid Translator init failed: {e}")
            return None
    return nllb_translator


def get_context_chain_service():
    """Lazy-load Context Chain Service"""
    global context_chain_service
    if context_chain_service is None:
        context_chain_service = ContextChainService(DATA_ROOT)
    return context_chain_service

def _get_project_dir(pid):
    safe_pid = validate_id(pid, "project_id")
    ensure_project_module_roots(DATA_ROOT, safe_pid)
    return project_dir(DATA_ROOT, safe_pid)


def _get_literature_dir(pid):
    safe_pid = validate_id(pid, "project_id")
    return literature_root(DATA_ROOT, safe_pid)


def _get_literature_papers_dir(pid):
    safe_pid = validate_id(pid, "project_id")
    return literature_papers_root(DATA_ROOT, safe_pid)


def _get_literature_paper_dir(pid, paper_id, for_write: bool = False, migrate_legacy: bool = True):
    safe_pid = validate_id(pid, "project_id")
    safe_paper = _safe_paper_id(paper_id)
    return resolve_literature_paper_dir(
        DATA_ROOT,
        safe_pid,
        safe_paper,
        for_write=for_write,
        migrate_legacy=migrate_legacy,
    )


def _list_literature_papers(pid):
    safe_pid = validate_id(pid, "project_id")
    return list_literature_papers(DATA_ROOT, safe_pid)


def _safe_paper_id(paper_id: str) -> str:
    return validate_id(paper_id, "paper_id")


def _internal_error(scope: str, exc: Exception):
    logger.error("[%s] %s", scope, exc, exc_info=True)
    return jsonify({"status": "error", "message": "Internal server error"}), 500

def _normalize_literature_pid(pid):
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


def _count_task4_assets(paper_dir):
    origin_pages = _collect_origin_pages(paper_dir)
    recog_dir = os.path.join(paper_dir, "03_recognizes")
    # Split View 只需頁面檔案存在即可編輯，不在此處做內容品質判定
    raw_pages = _collect_stage_pages(recog_dir, "_raw.json", min_size=0)

    if origin_pages:
        covered = len(origin_pages & raw_pages)
        return len(origin_pages), covered
    return 0, len(raw_pages)


def _collect_origin_pages(paper_dir: str):
    origin_dir = os.path.join(paper_dir, "00_origins")
    pages = set()
    if not os.path.exists(origin_dir):
        return pages

    for name in os.listdir(origin_dir):
        m = re.match(r"^page_(\d+)\.(?:jpg|jpeg|png)$", name, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            pages.add(int(m.group(1)))
        except Exception:
            continue
    return pages


def _collect_stage_pages(directory: str, suffix: str, min_size: int = 100):
    pages = set()
    if not os.path.exists(directory):
        return pages

    low_suffix = suffix.lower()
    for fname in os.listdir(directory):
        low = fname.lower()
        if not low.startswith("text_") or not low.endswith(low_suffix):
            continue
        fpath = os.path.join(directory, fname)
        try:
            if os.path.getsize(fpath) <= min_size:
                continue
        except Exception:
            continue
        m = re.match(r"^text_(\d+)_", low)
        if not m:
            continue
        try:
            pages.add(int(m.group(1)))
        except Exception:
            continue
    return pages


def _has_valid_stage_files(
    directory: str,
    suffix: str,
    min_size: int = 100,
    min_ratio: float = 0.5,
    expected_pages=None,
) -> bool:
    if not os.path.exists(directory):
        return False

    valid_pages = _collect_stage_pages(directory, suffix, min_size=min_size)
    if not valid_pages:
        return False

    if expected_pages:
        expected = set(expected_pages)
        covered = len(expected & valid_pages)
        required = max(1, int(len(expected) * float(min_ratio) + 0.9999))
        return covered >= required

    files = [f for f in os.listdir(directory) if f.endswith(suffix)]
    if not files:
        return False
    return len(valid_pages) >= len(files) * min_ratio


def _is_nonempty_json_file(path: str, min_size: int = 5) -> bool:
    if not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) <= min_size:
            return False
        data = load_json_locked(path, None)
    except Exception:
        return False

    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        return len(data) > 0
    return data not in (None, "", [])


def _task4_autoheal_state_path(paper_dir: str) -> str:
    rec_dir = os.path.join(paper_dir, "03_recognizes")
    os.makedirs(rec_dir, exist_ok=True)
    return os.path.join(rec_dir, ".task4_autoheal_state.json")


def _load_task4_autoheal_state(state_path: str) -> dict:
    default_state = {"attempts": 0, "last_attempt_ts": 0}
    if not os.path.exists(state_path):
        return default_state
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return {
            "attempts": int(data.get("attempts") or 0),
            "last_attempt_ts": float(data.get("last_attempt_ts") or 0),
        }
    except Exception:
        return default_state


def _save_task4_autoheal_state(state_path: str, state: dict) -> None:
    try:
        tmp_path = f"{state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "attempts": int(state.get("attempts") or 0),
                    "last_attempt_ts": float(state.get("last_attempt_ts") or 0),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp_path, state_path)
    except Exception:
        pass


def _clear_task4_autoheal_state(state_path: str) -> None:
    try:
        if os.path.exists(state_path):
            os.remove(state_path)
    except Exception:
        pass


def _ensure_task4_assets(pid, paper_id):
    """
    Ensure Split View dependencies exist:
    - 00_origins/page_*.jpg|jpeg|png
    - 03_recognizes/text_*_raw.json
    If missing, rerun CV pipeline for the specific paper.
    """
    if not pid or not paper_id:
        return False, "Missing pid or paper_id"
    try:
        pid = validate_id(pid, "project_id")
        paper_id = _safe_paper_id(paper_id)
    except Exception:
        return False, "Invalid pid or paper_id"

    paper_dir = _get_literature_paper_dir(pid, paper_id, for_write=False)
    source_pdf = safe_join_under(paper_dir, "00_origins", "source.pdf")

    if not os.path.exists(source_pdf):
        return False, f"Source PDF not found: {source_pdf}"

    img_count, raw_count = _count_task4_assets(paper_dir)
    if img_count > 0 and raw_count >= img_count:
        return True, "Task4 assets ready"
    
    return False, "Not Ready"

    pipeline = get_cv_pipeline()
    if pipeline is None:
        return False, "CV Pipeline unavailable"

    max_retries = max(0, int(os.environ.get("TASK4_AUTOHEAL_MAX_RETRY", "1")))
    cooldown_sec = max(0, int(os.environ.get("TASK4_AUTOHEAL_COOLDOWN_SEC", "900")))
    state_path = _task4_autoheal_state_path(paper_dir)
    state = _load_task4_autoheal_state(state_path)
    attempts = int(state.get("attempts") or 0)
    last_attempt_ts = float(state.get("last_attempt_ts") or 0)
    now_ts = time.time()

    if attempts >= max_retries:
        if cooldown_sec > 0 and (now_ts - last_attempt_ts) < cooldown_sec:
            remain = max(0, int(cooldown_sec - (now_ts - last_attempt_ts)))
            return False, (
                f"Task4 auto-heal cooldown active "
                f"(attempts={attempts}, retry_in={remain}s)"
            )
        # cooldown passed: reopen one retry window
        attempts = 0

    try:
        attempts += 1
        _save_task4_autoheal_state(
            state_path,
            {"attempts": attempts, "last_attempt_ts": now_ts},
        )
        logger.warning(
            f"[Task4 Auto-Heal] Missing assets for {pid}/{paper_id} "
            f"(images={img_count}, raw_covered={raw_count}, attempt={attempts}/{max_retries}). "
            "Re-running CV pipeline."
        )
        pipeline.run_pipeline(pid, paper_id, source_pdf)
    except Exception as e:
        logger.error(f"[Task4 Auto-Heal] CV pipeline rerun failed: {e}")
        return False, f"CV pipeline rerun failed: {e}"

    img_count, raw_count = _count_task4_assets(paper_dir)
    if img_count > 0 and raw_count >= img_count:
        _clear_task4_autoheal_state(state_path)
        return True, "Task4 assets repaired"

    return False, (
        f"Task4 assets still missing after rerun "
        f"(images={img_count}, raw_covered={raw_count})"
    )


def _load_seed_manual_context(pid):
    records_dir = os.path.join(_get_project_dir(pid), 'literature', 'paq_records')
    seed_file = os.path.join(records_dir, 'manual_context.json')
    if os.path.exists(seed_file):
        try:
            with open(seed_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _dedupe_keep_order(values):
    seen = set()
    out = []
    for v in values or []:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_run_id(flow: str, pid: str, paper_id: str) -> str:
    flow_key = (flow or "flow").strip().lower().replace(" ", "_")
    ts = int(time.time() * 1000)
    return f"{flow_key}_{pid}_{paper_id}_{ts}_{uuid.uuid4().hex[:8]}"


def _flowb_build_json_repair_prompt(bad_reply: str) -> str:
    return f"""
你剛才輸出的內容不是合法 JSON。
請你只做一件事：把下面這段內容修復為「合法 JSON」。

修復規則:
1) 僅修復 JSON 格式錯誤（例如缺逗號、引號未跳脫、尾逗號、包住在 markdown fence）。
2) 不可改寫語意，不可新增任何原本不存在的章節內容。
3) 最終輸出只能是 JSON 本體，禁止任何解釋文字。
4) JSON 結構必須是:
{{
  "sections": [
    {{
      "section_label": "...",
      "confidence": 0.0,
      "inferred_label": false,
      "source_block_refs": ["p1-b1"],
      "dropped_refs": [],
      "content_en": "...",
      "content_zh": "..."
    }}
  ]
}}

待修復內容:
{_flowb_clip(str(bad_reply or ""), max_chars=26000)}
""".strip()


def _flowb_write_debug_reply(pid: str, paper_id: str, tag: str, content: str):
    try:
        p = str(pid or "").strip()
        paper = str(paper_id or "").strip()
        if not p or not paper:
            return
        paper_dir = _get_literature_paper_dir(p, paper, for_write=False)
        debug_dir = safe_join_under(paper_dir, "06_translates", "reflow", "_debug")
        os.makedirs(debug_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tag or "debug"))[:80]
        out_path = safe_join_under(debug_dir, f"{ts}_{fname}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(str(content or ""))
    except Exception:
        return



def _build_flowb_semantic_sections_task5b(
    trans_payload: dict,
    summary_payload: dict | None = None,
    pid: str = "",
    paper_id: str = "",
) -> tuple[list[dict], str]:
    if not isinstance(trans_payload, dict):
        return [], "invalid trans payload"
    try:
        reflow_mode = _flowb_read_reflow_mode_env()
        reflow_task = get_semantic_reflow_task()
        max_rows = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_MAX_ROWS", 220)
        chunk_rows = max(16, _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_CHUNK_ROWS", 48))
        compact_rows = _flowb_collect_reflow_rows(trans_payload, max_rows=max_rows)
        if not compact_rows:
            return [], "task_5b_reflow: no usable rows"

        prompt = _flowb_build_reflow_prompt_from_rows(compact_rows[:96], summary_payload, reflow_mode=reflow_mode)
        all_errors = []

        if reflow_task is not None:
            total_rows = len(compact_rows)
            if total_rows > chunk_rows:
                batch_sections = []
                chunk_notes = []
                chunk_errors = []
                boundary_notes = []
                split_depth_limit = _flowb_read_positive_int_env(
                    "LITERATURE_FLOWB_REFLOW_CHUNK_SPLIT_DEPTH",
                    2,
                )
                min_split_rows = max(
                    8,
                    _flowb_read_positive_int_env(
                        "LITERATURE_FLOWB_REFLOW_MIN_SPLIT_ROWS",
                        12,
                    ),
                )
                affinity_shift_max = _flowb_read_positive_int_env(
                    "LITERATURE_FLOWB_REFLOW_AFFINITY_BOUNDARY_SHIFT",
                    4,
                )

                def _row_affinity_key(row: dict) -> str:
                    if not isinstance(row, dict):
                        return ""
                    return str(row.get("chunk_affinity_key") or "").strip()

                def _adjust_chunk_end(start: int, end: int) -> int:
                    if affinity_shift_max <= 0:
                        return end
                    if end <= start or end >= total_rows:
                        return end
                    left_key = _row_affinity_key(compact_rows[end - 1])
                    right_key = _row_affinity_key(compact_rows[end])
                    if not left_key or left_key != right_key:
                        return end

                    extended = end
                    moved = 0
                    while extended < total_rows and moved < affinity_shift_max:
                        if _row_affinity_key(compact_rows[extended]) != left_key:
                            break
                        extended += 1
                        moved += 1
                    if extended > end:
                        boundary_notes.append(
                            f"boundary_shift:+{extended - end}@{start + 1}-{extended}({left_key[:36]})"
                        )
                        return extended

                    shrink = end
                    moved = 0
                    min_rows_per_chunk = max(8, min_split_rows)
                    while shrink > start + min_rows_per_chunk and moved < affinity_shift_max:
                        if _row_affinity_key(compact_rows[shrink - 1]) != left_key:
                            break
                        shrink -= 1
                        moved += 1
                    if shrink < end:
                        boundary_notes.append(
                            f"boundary_shift:-{end - shrink}@{start + 1}-{shrink}({left_key[:36]})"
                        )
                        return shrink
                    return end

                chunk_ranges = []
                cursor = 0
                while cursor < total_rows:
                    raw_end = min(cursor + chunk_rows, total_rows)
                    end = _adjust_chunk_end(cursor, raw_end)
                    if end <= cursor:
                        end = raw_end
                    if end <= cursor:
                        end = min(cursor + 1, total_rows)
                    chunk_ranges.append((cursor, end))
                    cursor = end

                total_chunks = len(chunk_ranges)

                def _run_chunk_with_split(rows_chunk: list[dict], hint: str, depth: int = 0):
                    chunk_prompt = _flowb_build_reflow_prompt_from_rows(
                        rows_chunk,
                        summary_payload,
                        chunk_hint=hint,
                        reflow_mode=reflow_mode,
                    )
                    ok, reply_text, err = reflow_task.generate(chunk_prompt)
                    if ok:
                        sections, parse_note = _flowb_parse_reflow_sections_reply(
                            "task_5b_reflow",
                            reply_text,
                            reflow_task,
                            pid,
                            paper_id,
                            reflow_mode=reflow_mode,
                        )
                        if sections:
                            local_notes = [f"{hint}:{parse_note}"] if parse_note else []
                            return True, [sections], local_notes, ""
                        err_msg = str(parse_note or "invalid reply")
                    else:
                        err_msg = str(err or "dispatch failed")

                    can_split = (
                        depth < split_depth_limit
                        and len(rows_chunk) >= max(min_split_rows * 2, 16)
                        and _flowb_is_retryable_reflow_error(err_msg)
                    )
                    if not can_split:
                        return False, [], [], f"{hint}: {err_msg}"

                    mid = len(rows_chunk) // 2
                    left_rows = rows_chunk[:mid]
                    right_rows = rows_chunk[mid:]
                    if not left_rows or not right_rows:
                        return False, [], [], f"{hint}: {err_msg}"

                    left_hint = f"{hint}.1"
                    right_hint = f"{hint}.2"
                    ok_left, left_batches, left_notes, left_err = _run_chunk_with_split(
                        left_rows,
                        left_hint,
                        depth + 1,
                    )
                    if not ok_left:
                        return False, [], [], left_err
                    ok_right, right_batches, right_notes, right_err = _run_chunk_with_split(
                        right_rows,
                        right_hint,
                        depth + 1,
                    )
                    if not ok_right:
                        return False, [], [], right_err

                    split_note = (
                        f"{hint}:split depth={depth + 1}; rows={len(rows_chunk)}"
                        f" -> {len(left_rows)}+{len(right_rows)}"
                    )
                    merged_batches = list(left_batches) + list(right_batches)
                    merged_notes = [split_note] + list(left_notes) + list(right_notes)
                    return True, merged_batches, merged_notes, ""

                for chunk_idx, (st, ed) in enumerate(chunk_ranges):
                    rows_chunk = compact_rows[st:ed]
                    chunk_hint = f"chunk{chunk_idx + 1}/{total_chunks}"
                    ok_chunk, chunk_batches, split_notes, chunk_err = _run_chunk_with_split(
                        rows_chunk,
                        chunk_hint,
                        depth=0,
                    )
                    if not ok_chunk:
                        chunk_errors.append(str(chunk_err or f"{chunk_hint}: unknown error"))
                        break
                    batch_sections.extend(chunk_batches)
                    chunk_notes.extend(split_notes)

                if not chunk_errors:
                    merged_sections = _flowb_merge_llm_sections(batch_sections, reflow_mode=reflow_mode)
                    if merged_sections:
                        note = (
                            f"used=task_5b_reflow_chunked; chunks={total_chunks}; "
                            f"rows={total_rows}; chunk_rows={chunk_rows}; affinity_shift={affinity_shift_max}"
                        )
                        if boundary_notes:
                            note += "; " + " | ".join(boundary_notes)[:220]
                        if chunk_notes:
                            note += "; " + " | ".join(chunk_notes)[:280]
                        return merged_sections, note
                    all_errors.append("task_5b_reflow: chunked merge produced no usable sections")
                else:
                    all_errors.append("task_5b_reflow: " + " | ".join(chunk_errors)[:420])
            else:
                full_prompt = _flowb_build_reflow_prompt_from_rows(compact_rows, summary_payload, reflow_mode=reflow_mode)
                ok, reply_text, err = reflow_task.generate(full_prompt)
                if ok:
                    sections, parse_note = _flowb_parse_reflow_sections_reply(
                        "task_5b_reflow",
                        reply_text,
                        reflow_task,
                        pid,
                        paper_id,
                        reflow_mode=reflow_mode,
                    )
                    if sections:
                        note = "used=task_5b_reflow"
                        if parse_note:
                            note += f"; {parse_note}"
                        return sections, note
                    all_errors.append(parse_note or "task_5b_reflow: invalid reply")
                else:
                    all_errors.append(f"task_5b_reflow: {str(err or 'dispatch failed')}")

        allow_task5interpret_fallback = str(
            os.environ.get("LITERATURE_FLOWB_ALLOW_TASK5INTERPRET_FALLBACK", "0")
        ).strip() == "1"

        def _run_via_dispatcher(task_id: str):
            from app.llm_service.llm_dispatcher import dispatcher
            ok, res, msg = dispatcher.execute(task_id, prompt)
            if ok:
                return True, str((res or {}).get("text") or ""), ""
            return False, "", str(msg or "dispatcher failed")

        if allow_task5interpret_fallback:
            ok, reply_text, err = _run_via_dispatcher("task_5interpret")
            if not ok:
                all_errors.append(f"task_5interpret: {str(err or 'dispatch failed')}")
            else:
                sections, parse_note = _flowb_parse_reflow_sections_reply(
                    "task_5interpret",
                    reply_text,
                    None,
                    pid,
                    paper_id,
                    reflow_mode=reflow_mode,
                )
                if sections:
                    note = "used=task_5interpret"
                    if parse_note:
                        note += f"; {parse_note}"
                    if all_errors:
                        note += "; previous_failures=" + " | ".join(all_errors)[:320]
                    return sections, note
                all_errors.append(parse_note or "task_5interpret: invalid reply")

        if reflow_task is None and not allow_task5interpret_fallback:
            return [], "task_5b_reflow module unavailable"
        return [], " | ".join(all_errors)[:500]
    except Exception as e:
        return [], f"task_5b_reflow exception: {e}"


def _build_flowb_semantic_sections(trans_payload: dict, summary_payload: dict | None = None) -> list[dict]:
    if not isinstance(trans_payload, dict):
        return []

    pages = trans_payload.get("content", [])
    if not isinstance(pages, list):
        return []

    total_pages = max(1, len(pages))
    buckets = {}
    ordered_labels = []

    def _ensure_bucket(label: str):
        key = label or "未分類段落"
        if key not in buckets:
            buckets[key] = {
                "label": key,
                "refs": [],
                "content_en": [],
                "content_zh": [],
                "confidence_samples": [],
                "explicit_hits": 0,
            }
            ordered_labels.append(key)
        return buckets[key]

    current_label = ""
    current_inferred = True

    for page_idx, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page") or page_idx)
        block_list = page.get("blocks", [])
        if not isinstance(block_list, list):
            continue

        for block_idx, block in enumerate(block_list, start=1):
            if not isinstance(block, dict):
                continue

            text_en = _flowb_squash_text(block.get("content"))
            text_zh = _flowb_squash_text(block.get("content_zh"))
            if not text_en and not text_zh:
                continue

            best_text, _ = _flowb_pick_best_block_text(block)
            if _flowb_is_low_information_text(best_text):
                continue

            block_type = str(block.get("type") or "").strip()
            heading_source = best_text or text_en or text_zh
            heading_like = _flowb_is_heading_like(heading_source, block_type)
            explicit_label = _flowb_match_section_label(heading_source) if heading_like else ""

            if explicit_label:
                current_label = explicit_label
                current_inferred = False
                # Skip pure heading rows to keep output focused on semantic content.
                if len(heading_source) <= 100:
                    continue

            if current_label:
                label = current_label
                confidence = 0.86 if not current_inferred else 0.72
                inferred_label = current_inferred
            else:
                label = _flowb_infer_label_by_position(page_no, total_pages)
                confidence = 0.62
                inferred_label = True

            bucket = _ensure_bucket(label)
            ref = f"p{page_no}-b{block_idx}"
            bucket["refs"].append(ref)
            if text_en and not _flowb_is_low_information_text(text_en):
                bucket["content_en"].append(text_en)
            if text_zh and not _flowb_is_low_information_text(text_zh):
                bucket["content_zh"].append(text_zh)
            bucket["confidence_samples"].append(confidence)
            if not inferred_label:
                bucket["explicit_hits"] += 1

    if isinstance(summary_payload, dict):
        summary_zh = str(summary_payload.get("abstract_zh") or "").strip()
        if not summary_zh:
            findings = summary_payload.get("key_findings")
            if isinstance(findings, list):
                summary_zh = "\n".join(str(x or "").strip() for x in findings[:3] if str(x or "").strip()).strip()
        if summary_zh:
            abstract_bucket = _ensure_bucket("Abstract")
            if not abstract_bucket["content_zh"]:
                abstract_bucket["content_zh"].append(summary_zh)
                abstract_bucket["refs"].append("summary.json")
                abstract_bucket["confidence_samples"].append(0.58)

    sections = []
    reflow_mode = _flowb_read_reflow_mode_env()
    section_max_chars = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_CHARS", 12000)
    section_max_lines = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_SECTION_MAX_LINES", 40)
    for label in ordered_labels:
        bucket = buckets.get(label) or {}
        merged_en = _flowb_clip("\n\n".join(bucket.get("content_en") or []), max_chars=section_max_chars)
        merged_zh = _flowb_clip("\n\n".join(bucket.get("content_zh") or []), max_chars=section_max_chars)
        cleaned_en = _flowb_clean_section_text_by_mode(
            merged_en,
            reflow_mode=reflow_mode,
            max_lines=section_max_lines,
            max_chars=section_max_chars,
        )
        cleaned_zh = _flowb_clean_section_text_by_mode(
            merged_zh,
            reflow_mode=reflow_mode,
            max_lines=section_max_lines,
            max_chars=section_max_chars,
        )
        if not cleaned_en and not cleaned_zh:
            continue

        max_refs = _flowb_read_positive_int_env("LITERATURE_FLOWB_REFLOW_MAX_REFS_PER_SECTION", 128)
        refs = list(dict.fromkeys([str(r) for r in (bucket.get("refs") or []) if str(r).strip()]))[:max_refs]
        conf_samples = [float(x) for x in (bucket.get("confidence_samples") or []) if isinstance(x, (int, float))]
        confidence = round(sum(conf_samples) / len(conf_samples), 3) if conf_samples else 0.6
        explicit_hits = int(bucket.get("explicit_hits") or 0)

        sections.append(
            {
                "section_label": label or "未分類段落",
                "confidence": confidence,
                "inferred_label": explicit_hits == 0,
                "source_block_refs": refs,
                "dropped_refs": [],
                "content_en": cleaned_en,
                "content_zh": cleaned_zh,
            }
        )

    return sections


def _validate_reflow_coverage(rows: list, sections: list) -> dict:
    """
    後驗覆蓋率驗證(v0.2 內容零損失防線):
    - assigned_ratio:輸入 ref 出現在任一 section 的 source_block_refs
      或 dropped_refs 的比例(目標 1.0)。
    - content_ratio:各 section 內容總字數 / 輸入 rows 文字總字數(粗估)。
    回傳 {"assigned_ratio", "content_ratio", "unassigned_refs"}。
    """
    input_refs = []
    input_chars = 0
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        ref = str(r.get("ref", "") or "").strip()
        if ref:
            input_refs.append(ref)
        input_chars += len(str(r.get("text", "") or ""))

    covered = set()
    section_chars = 0
    for s in sections if isinstance(sections, list) else []:
        if not isinstance(s, dict):
            continue
        for key in ("source_block_refs", "dropped_refs"):
            refs = s.get(key)
            if isinstance(refs, list):
                covered.update(str(x or "").strip() for x in refs)
        section_chars += len(str(s.get("content_en", "") or "")) + len(str(s.get("content_zh", "") or ""))

    unassigned = [ref for ref in input_refs if ref not in covered]
    assigned_ratio = 1.0 if not input_refs else (len(input_refs) - len(unassigned)) / len(input_refs)
    # content_zh 與 content_en 合計可能超過輸入(雙語),content_ratio 只做下限警戒。
    content_ratio = 1.0 if input_chars <= 0 else min(2.0, section_chars / float(input_chars))
    return {
        "assigned_ratio": round(assigned_ratio, 4),
        "content_ratio": round(content_ratio, 4),
        "unassigned_refs": unassigned[:80],
    }


def _ensure_abstract_section(rows: list, sections: list) -> tuple[list, bool]:
    """
    Abstract 保底(v0.2):第一頁若有 >=300 字元的長文字 row,
    但輸出沒有任何 Abstract section,用規則直接補一個(inferred, conf=0.5)。
    """
    has_abstract = any(
        "abstract" in str(s.get("section_label", "") or "").strip().lower()
        for s in sections
        if isinstance(s, dict)
    )
    if has_abstract:
        return sections, False

    page1_rows = [
        r for r in rows
        if isinstance(r, dict) and str(r.get("ref", "") or "").startswith("p1-")
    ]
    if not page1_rows:
        return sections, False

    best = max(page1_rows, key=lambda r: len(str(r.get("text_en", "") or r.get("text", "") or "")), default=None)
    best_en = str(best.get("text_en", "") or best.get("text", "") or "") if best else ""
    if len(best_en) < 300:
        return sections, False

    fallback_section = {
        "section_label": "Abstract",
        "confidence": 0.5,
        "inferred_label": True,
        "source_block_refs": [str(best.get("ref", "") or "")],
        "dropped_refs": [],
        "content_en": best_en,
        "content_zh": str(best.get("text_zh", "") or ""),
    }
    logger.warning(
        "[reflow] Abstract missing from sections; rule-based fallback injected from %s",
        best.get("ref"),
    )
    return [fallback_section] + list(sections), True


def _generate_flowb_reflow_artifact(
    pid: str,
    paper_id: str,
    fusion_input: str,
    trans_output: str,
    summary_input: str,
) -> tuple[bool, str, str, str]:
    if not os.path.exists(trans_output):
        return False, "trans output not found", "", ""

    try:
        trans_payload = load_json_locked(trans_output, {})
    except Exception as e:
        return False, f"failed to load bilingual output: {e}", "", ""

    if not isinstance(trans_payload, dict):
        return False, "invalid bilingual payload format", "", ""
    reflow_mode = _flowb_read_reflow_mode_env()

    summary_payload = {}
    if os.path.exists(summary_input):
        try:
            loaded_summary = load_json_locked(summary_input, {})
            if isinstance(loaded_summary, dict):
                summary_payload = loaded_summary
        except Exception:
            summary_payload = {}

    llm_sections, llm_err = _build_flowb_semantic_sections_task5b(
        trans_payload,
        summary_payload,
        pid=pid,
        paper_id=paper_id,
    )
    if llm_sections:
        sections = llm_sections
        llm_note = str(llm_err or "").strip().lower()
        if llm_note.startswith("used=task_5interpret"):
            generation_mode = "task_5interpret"
        else:
            generation_mode = "task_5b_reflow"
    else:
        sections = _build_flowb_semantic_sections(trans_payload, summary_payload)
        generation_mode = "heuristic_fallback"

    sections, repaired_fields = _flowb_repair_section_bilingual_fields(
        sections,
        trans_payload,
        summary_payload,
    )
    if repaired_fields > 0:
        repair_note = f"bilingual_repair={repaired_fields}"
        llm_err = (f"{llm_err}; {repair_note}" if str(llm_err or "").strip() else repair_note)

    sections, uncovered_ref_count = _flowb_append_uncovered_appendix(
        sections,
        trans_payload,
        reflow_mode=reflow_mode,
    )
    if uncovered_ref_count > 0:
        appendix_note = f"appendix_uncovered_refs={uncovered_ref_count}"
        llm_err = (f"{llm_err}; {appendix_note}" if str(llm_err or "").strip() else appendix_note)

    sections, special_refs_rehomed = _flowb_rehome_appendix_special_refs(sections, trans_payload)
    if special_refs_rehomed > 0:
        special_note = f"special_refs_rehomed={special_refs_rehomed}"
        llm_err = (f"{llm_err}; {special_note}" if str(llm_err or "").strip() else special_note)

    if not sections:
        return False, "no semantic sections generated", "", generation_mode

    # v0.2: 內容零損失防線——覆蓋率驗證 + Abstract 保底。
    coverage_rows = _flowb_collect_reflow_rows(trans_payload)
    sections, abstract_injected = _ensure_abstract_section(coverage_rows, sections)
    coverage = _validate_reflow_coverage(coverage_rows, sections)
    coverage_warning = None
    if coverage["assigned_ratio"] < 1.0 or coverage["content_ratio"] < 0.90:
        coverage_warning = coverage
        logger.warning(
            "[reflow] coverage warning for %s/%s: assigned=%.2f content=%.2f unassigned=%s",
            pid, paper_id,
            coverage["assigned_ratio"], coverage["content_ratio"],
            coverage["unassigned_refs"][:10],
        )
        if coverage["assigned_ratio"] < 0.75 or coverage["content_ratio"] < 0.75:
            generation_mode = f"{generation_mode}_low_coverage"

    paper_dir = _get_literature_paper_dir(pid, paper_id, for_write=False)
    reflow_dir = safe_join_under(paper_dir, "06_translates", "reflow")
    os.makedirs(reflow_dir, exist_ok=True)
    out_path = safe_join_under(reflow_dir, "semantic_sections.json")

    payload = {
        "meta": {
            "pid": pid,
            "paper_id": paper_id,
            "version": "flowb_reflow_v1",
            "generation_mode": generation_mode,
            "reflow_mode": reflow_mode,
            "llm_error": str(llm_err or "")[:400],
            "created_at": _now_str(),
            "source_files": {
                "full_text": fusion_input,
                "full_text_trans": trans_output,
                "summary": summary_input if os.path.exists(summary_input) else "",
            },
            "section_count": len(sections),
            "appendix_uncovered_refs": int(uncovered_ref_count or 0),
            "coverage": coverage,
            "coverage_warning": coverage_warning,
            "abstract_fallback_injected": bool(abstract_injected),
        },
        "sections": sections,
    }
    write_json_locked(out_path, payload)
    return True, str(llm_err or ""), out_path, generation_mode


def _runtime_log_file_path() -> str:
    configured = str(os.environ.get("LECTURE_SYSTEM_LOG_FILE", "")).strip()
    if configured:
        if os.path.isabs(configured):
            return configured
        return os.path.abspath(os.path.join(BASE_DIR, configured))
    return os.path.join(DATA_ROOT, "_logs", "system", "runtime.log")


def _tail_text_file(path: str, lines: int = 120, max_bytes: int = 262144):
    safe_lines = max(1, min(int(lines or 120), 1000))
    safe_bytes = max(4096, min(int(max_bytes or 262144), 2 * 1024 * 1024))
    if not os.path.exists(path):
        return []

    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size <= 0:
                return []
            read_size = min(size, safe_bytes)
            if read_size < size:
                f.seek(-read_size, os.SEEK_END)
            else:
                f.seek(0)
            chunk = f.read().decode("utf-8", errors="replace")
        rows = chunk.splitlines()
        return rows[-safe_lines:]
    except Exception:
        return []


def _get_flowb_runtime_deps() -> FlowBRuntimeDeps:
    return FlowBRuntimeDeps(
        DATA_ROOT=DATA_ROOT,
        BASE_DIR=BASE_DIR,
        FLOWB_TIMEOUT_SEC=FLOWB_TIMEOUT_SEC,
        FLOWB_SUBPROCESS_ENABLED=FLOWB_SUBPROCESS_ENABLED,
        Paper=Paper,
        cap_worker_count=cap_worker_count,
        get_cpu_limit_info=get_cpu_limit_info,
        _now_str=_now_str,
        safe_join_under=safe_join_under,
        _runtime_log_file_path=_runtime_log_file_path,
        _tail_text_file=_tail_text_file,
        logger=logger,
    )


def _build_job_config(flow: str, pid: str, paper_id: str, run_id: str = "") -> dict:
    return _bflow_build_job_config(
        deps=_get_flowb_runtime_deps(),
        flow=flow,
        pid=pid,
        paper_id=paper_id,
        run_id=run_id,
    )


def _write_job_config(pid: str, paper_id: str, flow: str, run_id: str = "") -> str:
    return _bflow_write_job_config(
        deps=_get_flowb_runtime_deps(),
        pid=pid,
        paper_id=paper_id,
        flow=flow,
        run_id=run_id,
    )


def _append_flow_event(
    pid: str,
    paper_id: str,
    flow: str,
    event: str,
    run_id: str = "",
    status: str = "",
    message: str = "",
    extra: dict | None = None,
) -> None:
    return _bflow_append_flow_event(
        deps=_get_flowb_runtime_deps(),
        pid=pid,
        paper_id=paper_id,
        flow=flow,
        event=event,
        run_id=run_id,
        status=status,
        message=message,
        extra=extra,
    )


def _run_flowb_subprocess(
    pid: str,
    paper_id: str,
    fusion_input: str,
    trans_output: str,
    result_path: str,
) -> tuple[bool, str, dict]:
    return _bflow_run_flowb_subprocess(
        deps=_get_flowb_runtime_deps(),
        pid=pid,
        paper_id=paper_id,
        fusion_input=fusion_input,
        trans_output=trans_output,
        result_path=result_path,
    )


def _flowb_debug_snapshot(pid: str, paper_id: str, log_lines: int = 160) -> dict:
    return _bflow_debug_snapshot(
        deps=_get_flowb_runtime_deps(),
        pid=pid,
        paper_id=paper_id,
        log_lines=log_lines,
    )


def run_translation():
    """
    Compatibility wrapper kept for smoke tests and monkeypatch hooks.
    Route registration now lives in literature_batch_routes.py.
    """
    deps = FlowBRouteDeps(
        request=request,
        jsonify=jsonify,
        current_app=current_app,
        logger=logger,
        db=db,
        Paper=Paper,
        BadRequest=BadRequest,
        DATA_ROOT=DATA_ROOT,
        FLOWB_SUBPROCESS_ENABLED=FLOWB_SUBPROCESS_ENABLED,
        FLOWB_TIMEOUT_SEC=FLOWB_TIMEOUT_SEC,
        cap_worker_count=cap_worker_count,
        safe_join_under=safe_join_under,
        load_json_locked=load_json_locked,
        _normalize_literature_pid=_normalize_literature_pid,
        _safe_paper_id=_safe_paper_id,
        _dedupe_keep_order=_dedupe_keep_order,
        _new_run_id=_new_run_id,
        _write_job_config=_write_job_config,
        _append_flow_event=_append_flow_event,
        _update_paper_status=_update_paper_status,
        _flowb_read_bool_env=_flowb_read_bool_env,
        _flowb_compute_fusion_quality_metrics=_flowb_compute_fusion_quality_metrics,
        _generate_flowb_reflow_artifact=_generate_flowb_reflow_artifact,
        _flowb_is_ready_generation_mode=_flowb_is_ready_generation_mode,
        _flowb_read_ready_generation_modes=_flowb_read_ready_generation_modes,
        _run_flowb_subprocess=_run_flowb_subprocess,
        _to_bool=_to_bool,
        _get_flowb_executor=_get_flowb_executor,
        get_nllb_translator=get_nllb_translator,
    )
    return run_translation_impl(deps)


# --- HTML Routes ---

@literature_bp.route('/literature/', methods=['GET'])
@literature_bp.route('/literature', methods=['GET'])
def literature_page():
    """
    返回 Literature 頁面
    """
    try:
        pid = _normalize_literature_pid(request.args.get('pid'))
        if pid:
            proj = Project.query.filter_by(project_id=pid).first()
            if proj and proj.status == 'formal':
                ensure_formal_project_records(proj)
        return render_template('literature.html', pid=pid)
    except Exception as e:
        logger.error(f"Literature page error: {e}")
        return f"<h1>Error loading Literature page</h1><p>{e}</p>", 500


@literature_bp.route('/api/literature/system_profile', methods=['GET'])
def system_profile():
    requested_workers = max(1, int(os.environ.get("LITERATURE_MAX_WORKERS", "1")))
    effective_workers = cap_worker_count(requested_workers)
    return jsonify(
        {
            "status": "success",
            "profile": {
                "demo_strict_mode": os.environ.get("DEMO_STRICT_MODE", "0").strip().lower() in {"1", "true", "yes", "on"},
                "nllb_mode": os.environ.get("LECTURE_NLLB_MODE", "hybrid"),
                "judge_mode": os.environ.get("LECTURE_LLM_JUDGE_MODE", "bus"),
                "page_workers": effective_workers,
                "page_workers_requested": requested_workers,
                "cpu_limit": get_cpu_limit_info(),
                "flowa_subprocess_enabled": False,
                "flowa_global_limit": effective_workers,
                "flowb_global_limit": effective_workers,
            },
        }
    )


@literature_bp.route('/api/literature/preload_translation_models', methods=['GET', 'POST'])
def preload_translation_models():
    translator = get_nllb_translator()
    if translator is None:
        return jsonify({"status": "error", "message": "Translator is not available"}), 503
    return jsonify({"status": "success", "message": "Translator is ready"})


# -----------------------------------------------------------------------------
# 輔助函數: 數據庫更新
# -----------------------------------------------------------------------------

def _update_paper_status(pid, paper_id, status, log_msg=""):
    return update_paper_status_impl(sys.modules[__name__], pid, paper_id, status, log_msg)


def _update_paper_metadata(pid, paper_id, meta):
    return update_paper_metadata_impl(sys.modules[__name__], pid, paper_id, meta)


def _trigger_gold_bridge(pid, paper_id):
    return trigger_gold_bridge_impl(sys.modules[__name__], pid, paper_id)

@literature_bp.route('/api/literature/delete_paper', methods=['POST'])
def delete_paper():
    return delete_paper_impl(sys.modules[__name__])

@literature_bp.route('/api/literature/status/<pid>', methods=['GET'])
def get_status(pid):
    return get_status_impl(sys.modules[__name__], pid)

# --- 4. Correction & Split View APIs ---

@literature_bp.route('/api/literature/get_region_image')
def get_region_image():
    return get_region_image_impl(sys.modules[__name__])

@literature_bp.route('/api/literature/get_block_json')
def get_block_json():
    return get_block_json_impl(sys.modules[__name__])

@literature_bp.route('/api/literature/get_block_manifest')
def get_block_manifest():
    return get_block_manifest_impl(sys.modules[__name__])

@literature_bp.route('/api/literature/save_correction', methods=['POST'])
def save_correction():
    return save_correction_impl(sys.modules[__name__])

@literature_bp.route('/api/literature/get_full_json')
def get_full_json():
    return get_full_json_impl(sys.modules[__name__])


register_context_routes(literature_bp, sys.modules[__name__])
register_batch_routes(literature_bp, sys.modules[__name__])
register_context_chain_routes(literature_bp, sys.modules[__name__])
register_debug_routes(literature_bp, sys.modules[__name__])
