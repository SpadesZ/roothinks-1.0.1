#路徑(app/core_pro/literature/literature_bflow.py) #版本 v0.2 #更版時間 20260429
#功能概要:
#1. 定義 Flow B 路由依賴資料結構與主執行入口。
#2. 處理翻譯流程、品質閘門、subprocess 與事件記錄。
#3. 提供 Flow B runtime helper 供 routes/worker 共同使用。

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.core_pro.storage_layout import resolve_literature_paper_dir


def _paper_dir(data_root: str, pid: str, paper_id: str, for_write: bool = False) -> str:
    return resolve_literature_paper_dir(
        data_root,
        pid,
        paper_id,
        for_write=for_write,
        migrate_legacy=True,
    )


@dataclass
class FlowBRouteDeps:
    request: Any
    jsonify: Callable[..., Any]
    current_app: Any
    logger: Any
    db: Any
    Paper: Any
    BadRequest: Any
    DATA_ROOT: str
    FLOWB_SUBPROCESS_ENABLED: bool
    FLOWB_TIMEOUT_SEC: int
    cap_worker_count: Callable[[int], int]
    safe_join_under: Callable[..., str]
    load_json_locked: Callable[..., Any]
    _normalize_literature_pid: Callable[[Any], str]
    _safe_paper_id: Callable[[Any], str]
    _dedupe_keep_order: Callable[[Any], list]
    _new_run_id: Callable[[str, str, str], str]
    _write_job_config: Callable[..., str]
    _append_flow_event: Callable[..., None]
    _update_paper_status: Callable[..., None]
    _flowb_read_bool_env: Callable[..., bool]
    _flowb_compute_fusion_quality_metrics: Callable[[dict], dict]
    _generate_flowb_reflow_artifact: Callable[..., tuple]
    _flowb_is_ready_generation_mode: Callable[[str], bool]
    _flowb_read_ready_generation_modes: Callable[[], set]
    _run_flowb_subprocess: Callable[..., tuple]
    _to_bool: Callable[..., bool]
    _get_flowb_executor: Callable[[], Any]
    get_nllb_translator: Callable[[], Any]


def run_translation_impl(deps: FlowBRouteDeps):
    """
    Flow B: slim translation lane (mapped to Task4/Task5 ecosystem).
    不直接綁定 LITE 的 task 編號，避免兩邊語意混用。
    """
    data = deps.request.get_json(silent=True) or {}
    pid = deps._normalize_literature_pid(data.get("pid"))
    paper_ids = data.get("paper_ids", [])

    if not pid:
        return deps.jsonify({"status": "error", "message": "Invalid pid"}), 404
    if not paper_ids:
        return deps.jsonify({"status": "error", "message": "No papers selected"}), 400
    try:
        paper_ids = [deps._safe_paper_id(p) for p in paper_ids]
        paper_ids = deps._dedupe_keep_order(paper_ids)
    except deps.BadRequest:
        return deps.jsonify({"status": "error", "message": "Invalid paper_id in request"}), 400

    if not deps.FLOWB_SUBPROCESS_ENABLED and deps.get_nllb_translator() is None:
        return deps.jsonify({"status": "error", "message": "Flow B translator unavailable"}), 503

    requested_workers = max(1, int(os.environ.get("LITERATURE_MAX_WORKERS", "1")))
    flowb_workers_effective = deps.cap_worker_count(requested_workers)

    queued_ids = []
    run_ids = {}
    flow_a_not_ready = []
    busy_ids = []

    for pid_paper in paper_ids:
        paper_row = deps.Paper.query.filter_by(paper_id=pid_paper, pid=pid).first()
        status = (paper_row.interpretation_status or "").lower() if paper_row else ""
        if status in {"processing_b", "translating_local", "judging_llm"}:
            updated = paper_row.updated_at if paper_row else None
            age_sec = None
            if isinstance(updated, datetime):
                if updated.tzinfo is not None:
                    updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
                age_sec = (datetime.utcnow() - updated).total_seconds()
            if age_sec is not None and age_sec < 30:
                busy_ids.append(pid_paper)
                continue

        paper_dir = _paper_dir(deps.DATA_ROOT, pid, pid_paper, for_write=False)
        fusion_input = deps.safe_join_under(paper_dir, "05_interprets", "fusion", "full_text.json")
        summary_input = deps.safe_join_under(paper_dir, "05_interprets", "summary.json")
        # summary.json is optional for Flow B; reflow can still proceed with bilingual content only.
        if not os.path.exists(fusion_input):
            flow_a_not_ready.append(pid_paper)
            continue

        queued_ids.append(pid_paper)
        run_id = deps._new_run_id("flow_b", pid, pid_paper)
        run_ids[pid_paper] = run_id
        deps._write_job_config(pid, pid_paper, flow="flow_b", run_id=run_id)
        deps._append_flow_event(
            pid,
            pid_paper,
            flow="flow_b",
            event="queued",
            run_id=run_id,
            status="processing_B",
            message="Flow B queued: translation pending",
            extra={
                "flowb_subprocess_enabled": deps.FLOWB_SUBPROCESS_ENABLED,
                "flowb_timeout_sec": deps.FLOWB_TIMEOUT_SEC,
                "flowb_workers_effective": flowb_workers_effective,
            },
        )
        deps._update_paper_status(pid, pid_paper, "processing_B", f"Flow B queued: translation pending [run_id={run_id}]")

    if not queued_ids:
        return deps.jsonify(
            {
                "status": "error",
                "message": "No papers queued for Flow B.",
                "details": {
                    "busy": busy_ids,
                    "flow_a_not_ready": flow_a_not_ready,
                },
            }
        ), 409

    app = deps.current_app._get_current_object()

    def _translation_worker(target_pid, target_paper_id, target_run_id, app_obj):
        with app_obj.app_context():
            run_id = str(target_run_id or "")
            pid_paper = str(target_paper_id or "")
            trans_output = ""
            try:
                started_ts = time.time()
                paper_dir = _paper_dir(deps.DATA_ROOT, target_pid, pid_paper, for_write=True)
                fusion_input = deps.safe_join_under(
                    paper_dir,
                    "05_interprets",
                    "fusion",
                    "full_text.json",
                )
                summary_input = deps.safe_join_under(paper_dir, "05_interprets", "summary.json")
                if not os.path.exists(fusion_input):
                    deps._append_flow_event(
                        target_pid,
                        pid_paper,
                        flow="flow_b",
                        event="failed",
                        run_id=run_id,
                        status=deps.Paper.STATUS_FAILED,
                        message="Flow B requires Flow A full_text.json",
                    )
                    deps._update_paper_status(
                        target_pid,
                        pid_paper,
                        deps.Paper.STATUS_FAILED,
                        f"Flow B requires Flow A full_text.json [run_id={run_id}]",
                    )
                    return

                quality_gate_enabled = deps._flowb_read_bool_env("LITERATURE_FLOWB_QUALITY_GATE_ENABLED", True)
                if quality_gate_enabled:
                    fusion_payload = deps.load_json_locked(fusion_input, {})
                    quality = deps._flowb_compute_fusion_quality_metrics(fusion_payload)
                    if quality.get("low_quality"):
                        reasons = quality.get("reasons") if isinstance(quality.get("reasons"), list) else []
                        reason_text = "; ".join(str(x) for x in reasons if str(x).strip())[:260]
                        reason_msg = (
                            "Flow B quality gate blocked: OCR block granularity too low; "
                            "please rerun Flow A OCR/Fix before Flow B."
                            + (f" ({reason_text})" if reason_text else "")
                        )
                        deps._append_flow_event(
                            target_pid,
                            pid_paper,
                            flow="flow_b",
                            event="quality_blocked",
                            run_id=run_id,
                            status=deps.Paper.STATUS_FAILED,
                            message=reason_msg,
                            extra={"quality_gate": quality},
                        )
                        deps._update_paper_status(
                            target_pid,
                            pid_paper,
                            deps.Paper.STATUS_FAILED,
                            f"{reason_msg} [run_id={run_id}]",
                        )
                        return

                trans_dir = deps.safe_join_under(paper_dir, "06_translates", "fusion")
                os.makedirs(trans_dir, exist_ok=True)
                trans_output = deps.safe_join_under(trans_dir, "full_text_trans.json")
                jobs_dir = deps.safe_join_under(paper_dir, "_jobs")
                os.makedirs(jobs_dir, exist_ok=True)
                result_path = deps.safe_join_under(jobs_dir, "flow_b_result.json")

                # Guard: 若已存在含 content_zh 的翻譯檔，跳過翻譯避免覆蓋好的結果
                if os.path.exists(trans_output):
                    try:
                        with open(trans_output, "r", encoding="utf-8") as _tf:
                            _existing = json.load(_tf)
                        _has_zh = any(
                            isinstance(b, dict) and str(b.get("content_zh", "")).strip()
                            for p in _existing.get("content", [])
                            for b in (p.get("blocks", []) if isinstance(p, dict) else [])
                        )
                        if _has_zh:
                            deps.logger.info(
                                "[FlowB] %s/%s: trans output already has content_zh, skipping re-translate",
                                target_pid,
                                pid_paper,
                            )
                            reflow_ok, reflow_err, reflow_path, reflow_generation_mode = deps._generate_flowb_reflow_artifact(
                                pid=target_pid,
                                paper_id=pid_paper,
                                fusion_input=fusion_input,
                                trans_output=trans_output,
                                summary_input=summary_input,
                            )
                            llm_ready = bool(reflow_ok and deps._flowb_is_ready_generation_mode(reflow_generation_mode))
                            deps._append_flow_event(
                                target_pid,
                                pid_paper,
                                flow="flow_b",
                                event="reflow_ready" if llm_ready else "reflow_warning",
                                run_id=run_id,
                                status="ready_B" if llm_ready else deps.Paper.STATUS_FAILED,
                                message=(
                                    (
                                        "Flow B reflow generated from existing bilingual output"
                                        + (f" (note: {reflow_err})" if reflow_err else "")
                                    )
                                    if llm_ready
                                    else (
                                        "Flow B existing bilingual output is not LLM-reflow-ready: "
                                        f"mode={reflow_generation_mode or 'unknown'}; {reflow_err}"
                                    )
                                ),
                                extra={
                                    "reflow_output": reflow_path,
                                    "generation_mode": reflow_generation_mode,
                                    "generation_note": reflow_err if reflow_ok else "",
                                },
                            )
                            if llm_ready:
                                deps._update_paper_status(
                                    target_pid,
                                    pid_paper,
                                    "ready_B",
                                    f"Flow B ready: bilingual output already exists [run_id={run_id}]",
                                )
                            else:
                                fail_reason = (
                                    "Flow B requires LLM reflow-ready output; got mode="
                                    f"{reflow_generation_mode or 'unknown'}"
                                )
                                deps._update_paper_status(
                                    target_pid,
                                    pid_paper,
                                    deps.Paper.STATUS_FAILED,
                                    f"{fail_reason} [run_id={run_id}]",
                                )
                            return
                    except Exception as trans_exist_err:
                        deps.logger.warning(
                            "[FlowB] %s/%s: existing-trans fast path failed: %s",
                            target_pid,
                            pid_paper,
                            trans_exist_err,
                        )
                        # [Fix] 讀取失敗但輸出檔案已存在：保守回寫 ready_B，避免重複翻譯覆蓋好的結果
                        if os.path.exists(trans_output):
                            deps._append_flow_event(
                                target_pid,
                                pid_paper,
                                flow="flow_b",
                                event="reflow_warning",
                                run_id=run_id,
                                status=deps.Paper.STATUS_FAILED,
                                message=f"Flow B fast-path skipped due to read/reflow error: {trans_exist_err}",
                            )
                            deps.logger.warning(
                                "[FlowB] %s/%s: trans file exists but unreadable, marking failed for strict-ready",
                                target_pid,
                                pid_paper,
                            )
                            deps._update_paper_status(
                                target_pid,
                                pid_paper,
                                deps.Paper.STATUS_FAILED,
                                f"Flow B strict-ready blocked: existing trans file unreadable [run_id={run_id}]",
                            )
                            return
                        # 讀取失敗且檔案不存在：繼續正常翻譯流程

                deps._append_flow_event(
                    target_pid,
                    pid_paper,
                    flow="flow_b",
                    event="start",
                    run_id=run_id,
                    status="translating_local",
                    message="Flow B running: slim translation engine",
                    extra={"flowb_workers_effective": flowb_workers_effective},
                )

                deps._update_paper_status(
                    target_pid,
                    pid_paper,
                    "translating_local",
                    f"Flow B running: slim translation engine [run_id={run_id}]",
                )

                if deps.FLOWB_SUBPROCESS_ENABLED:
                    ok, err_msg, payload = deps._run_flowb_subprocess(
                        pid=target_pid,
                        paper_id=pid_paper,
                        fusion_input=fusion_input,
                        trans_output=trans_output,
                        result_path=result_path,
                    )
                    deps._append_flow_event(
                        target_pid,
                        pid_paper,
                        flow="flow_b",
                        event="subprocess_result",
                        run_id=run_id,
                        status="translating_local",
                        message="Flow B subprocess completed",
                        extra={
                            "ok": ok,
                            "error": err_msg,
                            "payload": payload,
                        },
                    )
                else:
                    try:
                        from app.core_pro.literature.literature_translator import TranslationContext
                    except Exception as e:
                        deps._append_flow_event(
                            target_pid,
                            pid_paper,
                            flow="flow_b",
                            event="failed",
                            run_id=run_id,
                            status=deps.Paper.STATUS_FAILED,
                            message=f"Flow B translator import error: {e}",
                        )
                        deps._update_paper_status(
                            target_pid,
                            pid_paper,
                            deps.Paper.STATUS_FAILED,
                            f"Flow B translator import error: {e} [run_id={run_id}]",
                        )
                        return

                    local_translator = deps.get_nllb_translator()
                    if local_translator is None:
                        deps._append_flow_event(
                            target_pid,
                            pid_paper,
                            flow="flow_b",
                            event="failed",
                            run_id=run_id,
                            status=deps.Paper.STATUS_FAILED,
                            message="Flow B translator unavailable",
                        )
                        deps._update_paper_status(
                            target_pid,
                            pid_paper,
                            deps.Paper.STATUS_FAILED,
                            f"Flow B translator unavailable [run_id={run_id}]",
                        )
                        return

                    ok = bool(local_translator.translate_file(fusion_input, trans_output, TranslationContext.LITERATURE_BATCH))
                    err_msg = "" if ok else "translator returned false"
                    deps._append_flow_event(
                        target_pid,
                        pid_paper,
                        flow="flow_b",
                        event="direct_result",
                        run_id=run_id,
                        status="translating_local",
                        message="Flow B direct translator finished",
                        extra={"ok": ok, "error": err_msg},
                    )

                deps._update_paper_status(
                    target_pid,
                    pid_paper,
                    "judging_llm",
                    f"Flow B finalizing bilingual artifacts [run_id={run_id}]",
                )
                if ok and os.path.exists(trans_output):
                    reflow_ok, reflow_err, reflow_path, reflow_generation_mode = deps._generate_flowb_reflow_artifact(
                        pid=target_pid,
                        paper_id=pid_paper,
                        fusion_input=fusion_input,
                        trans_output=trans_output,
                        summary_input=summary_input,
                    )
                    llm_ready = bool(reflow_ok and deps._flowb_is_ready_generation_mode(reflow_generation_mode))
                    if llm_ready:
                        deps._append_flow_event(
                            target_pid,
                            pid_paper,
                            flow="flow_b",
                            event="reflow_ready",
                            run_id=run_id,
                            status="judging_llm",
                            message=(
                                "Flow B semantic reflow generated"
                                + (f" (note: {reflow_err})" if reflow_err else "")
                            ),
                            extra={
                                "reflow_output": reflow_path,
                                "generation_mode": reflow_generation_mode,
                                "generation_note": reflow_err if reflow_ok else "",
                            },
                        )
                    else:
                        deps._append_flow_event(
                            target_pid,
                            pid_paper,
                            flow="flow_b",
                            event="reflow_warning",
                            run_id=run_id,
                            status=deps.Paper.STATUS_FAILED,
                            message=(
                                "Flow B semantic reflow not LLM-ready: "
                                f"mode={reflow_generation_mode or 'unknown'}; {reflow_err}"
                            ),
                            extra={
                                "reflow_output": reflow_path,
                                "generation_mode": reflow_generation_mode,
                            },
                        )
                    elapsed_sec = round(time.time() - started_ts, 3)
                    deps._append_flow_event(
                        target_pid,
                        pid_paper,
                        flow="flow_b",
                        event="completed" if llm_ready else "failed",
                        run_id=run_id,
                        status="ready_B" if llm_ready else deps.Paper.STATUS_FAILED,
                        message=(
                            "Flow B ready: bilingual output generated"
                            if llm_ready
                            else "Flow B failed strict-ready gate: bilingual output exists but LLM reflow not ready"
                        ),
                        extra={
                            "elapsed_sec": elapsed_sec,
                            "output": trans_output,
                            "reflow_output": reflow_path if reflow_ok else "",
                        },
                    )
                    if llm_ready:
                        deps._update_paper_status(
                            target_pid,
                            pid_paper,
                            "ready_B",
                            f"Flow B ready: bilingual output generated [run_id={run_id}]",
                        )
                    else:
                        deps._update_paper_status(
                            target_pid,
                            pid_paper,
                            deps.Paper.STATUS_FAILED,
                            (
                                "Flow B strict-ready gate failed: expected LLM reflow mode in "
                                f"{','.join(sorted(deps._flowb_read_ready_generation_modes()))}, got "
                                f"{reflow_generation_mode or 'unknown'} [run_id={run_id}]"
                            ),
                        )
                else:
                    final_err = err_msg if "err_msg" in locals() and err_msg else "Flow B finished without bilingual output"
                    deps._append_flow_event(
                        target_pid,
                        pid_paper,
                        flow="flow_b",
                        event="failed",
                        run_id=run_id,
                        status=deps.Paper.STATUS_FAILED,
                        message=final_err,
                        extra={"output": trans_output},
                    )
                    deps._update_paper_status(
                        target_pid,
                        pid_paper,
                        deps.Paper.STATUS_FAILED,
                        f"{final_err} [run_id={run_id}]",
                    )
            except Exception as e:
                deps.logger.error("Flow B translation error for %s/%s: %s", target_pid, pid_paper, e)
                deps._append_flow_event(
                    target_pid,
                    pid_paper,
                    flow="flow_b",
                    event="exception",
                    run_id=run_id,
                    status=deps.Paper.STATUS_FAILED,
                    message=f"Flow B translation error: {e}",
                )
                deps._update_paper_status(
                    target_pid,
                    pid_paper,
                    deps.Paper.STATUS_FAILED,
                    f"Flow B translation error: {e} [run_id={run_id}]",
                )
            finally:
                try:
                    deps.db.session.remove()
                except Exception:
                    pass

    use_direct_thread = deps._to_bool(
        os.environ.get("LITERATURE_USE_DIRECT_THREAD"),
        deps._to_bool(os.environ.get("FLASK_DEBUG"), True),
    )
    if use_direct_thread:
        for pid_paper in queued_ids:
            run_id = str(run_ids.get(pid_paper, "") or "")
            threading.Thread(
                target=_translation_worker,
                args=(pid, pid_paper, run_id, app),
                daemon=True,
                name=f"literature-flowb-{pid}-{pid_paper}-{int(time.time())}",
            ).start()
    else:
        executor = deps._get_flowb_executor()
        for pid_paper in queued_ids:
            run_id = str(run_ids.get(pid_paper, "") or "")
            executor.submit(_translation_worker, pid, pid_paper, run_id, app)

    return deps.jsonify(
        {
            "status": "success",
            "message": f"Flow B initiated. queued={len(queued_ids)}",
            "details": {
                "queued": queued_ids,
                "run_ids": run_ids,
                "busy": busy_ids,
                "flow_a_not_ready": flow_a_not_ready,
                "flowb_workers_effective": flowb_workers_effective,
            },
        }
    )


@dataclass
class FlowBRuntimeDeps:
    DATA_ROOT: str
    BASE_DIR: str
    FLOWB_TIMEOUT_SEC: int
    FLOWB_SUBPROCESS_ENABLED: bool
    Paper: Any
    cap_worker_count: Callable[[int], int]
    get_cpu_limit_info: Callable[[], Any]
    _now_str: Callable[[], str]
    safe_join_under: Callable[..., str]
    _runtime_log_file_path: Callable[[], str]
    _tail_text_file: Callable[..., list]
    logger: Any


def build_job_config(deps: FlowBRuntimeDeps, flow: str, pid: str, paper_id: str, run_id: str = "") -> dict:
    requested_workers = max(1, int(os.environ.get("LITERATURE_MAX_WORKERS", "1")))
    effective_workers = deps.cap_worker_count(requested_workers)
    return {
        "flow": str(flow or ""),
        "pid": str(pid or ""),
        "paper_id": str(paper_id or ""),
        "run_id": str(run_id or ""),
        "created_at": deps._now_str(),
        "runtime": {
            "cpu_limit": deps.get_cpu_limit_info(),
            "flowa_workers_requested": requested_workers,
            "flowa_workers_effective": effective_workers,
            "flowb_workers_effective": effective_workers,
            "flowb_subprocess_enabled": deps.FLOWB_SUBPROCESS_ENABLED,
            "flowb_timeout_sec": deps.FLOWB_TIMEOUT_SEC,
        },
        "flow_b": {
            "nllb_mode": os.environ.get("LECTURE_NLLB_MODE", "hybrid"),
            "judge_mode": os.environ.get("LECTURE_LLM_JUDGE_MODE", "bus"),
            "llm_prompt_version": os.environ.get("LECTURE_LLM_PROMPT_VERSION", "judge_prompt_v1"),
        },
    }


def write_job_config(deps: FlowBRuntimeDeps, pid: str, paper_id: str, flow: str, run_id: str = "") -> str:
    try:
        payload = build_job_config(deps=deps, flow=flow, pid=pid, paper_id=paper_id, run_id=run_id)
        paper_dir = _paper_dir(deps.DATA_ROOT, pid, paper_id, for_write=True)
        job_dir = deps.safe_join_under(paper_dir, "_jobs")
        os.makedirs(job_dir, exist_ok=True)
        path = deps.safe_join_under(job_dir, f"{flow}_job_config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except Exception as e:
        deps.logger.warning("[JobConfig] failed to write %s config for %s/%s: %s", flow, pid, paper_id, e)
        return ""


def append_flow_event(
    deps: FlowBRuntimeDeps,
    pid: str,
    paper_id: str,
    flow: str,
    event: str,
    run_id: str = "",
    status: str = "",
    message: str = "",
    extra: dict | None = None,
) -> None:
    try:
        paper_dir = _paper_dir(deps.DATA_ROOT, pid, paper_id, for_write=True)
        job_dir = deps.safe_join_under(paper_dir, "_jobs")
        os.makedirs(job_dir, exist_ok=True)
        path = deps.safe_join_under(job_dir, f"{flow}_events.jsonl")
        payload = {
            "ts": deps._now_str(),
            "pid": pid,
            "paper_id": paper_id,
            "flow": flow,
            "event": event,
            "run_id": run_id,
            "status": status,
            "message": message,
            "process_id": os.getpid(),
            "thread": threading.current_thread().name,
        }
        if isinstance(extra, dict) and extra:
            payload["extra"] = extra
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        deps.logger.warning("[FlowEvent] failed to append event for %s/%s: %s", pid, paper_id, e)


def run_flowb_subprocess(
    deps: FlowBRuntimeDeps,
    pid: str,
    paper_id: str,
    fusion_input: str,
    trans_output: str,
    result_path: str,
) -> tuple[bool, str, dict]:
    try:
        if os.path.exists(result_path):
            os.remove(result_path)
    except Exception:
        pass

    cmd = [
        sys.executable,
        "-m",
        "app.core_pro.literature.literature_flowb_runner_cli",
        "--data-root",
        deps.DATA_ROOT,
        "--pid",
        pid,
        "--paper-id",
        paper_id,
        "--input-path",
        fusion_input,
        "--output-path",
        trans_output,
        "--result-path",
        result_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=deps.BASE_DIR,
            timeout=deps.FLOWB_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Flow B subprocess timeout > {deps.FLOWB_TIMEOUT_SEC}s", {"timeout": True}
    except Exception as e:
        return False, f"Flow B subprocess launch failed: {e}", {}

    if not os.path.exists(result_path):
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        msg = f"Flow B subprocess exited (code={proc.returncode}) without result file"
        if stderr:
            msg += f"; stderr={stderr[:300]}"
        elif stdout:
            msg += f"; stdout={stdout[:300]}"
        return False, msg, {"returncode": proc.returncode}

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return False, f"Failed to load Flow B subprocess result: {e}", {}

    ok = bool(payload.get("ok"))
    if not ok:
        return False, str(payload.get("error") or "Flow B subprocess returned not-ok"), payload
    return True, "", payload


def flowb_debug_snapshot(deps: FlowBRuntimeDeps, pid: str, paper_id: str, log_lines: int = 160) -> dict:
    paper = deps.Paper.query.filter_by(pid=pid, paper_id=paper_id).first()
    now = datetime.utcnow()
    status_raw = (paper.interpretation_status or "") if paper else ""
    status = status_raw.lower()
    updated_at = paper.updated_at if paper else None
    age_sec = None
    if isinstance(updated_at, datetime):
        ts = updated_at
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        age_sec = max(0.0, (now - ts).total_seconds())

    stale_sec = max(60, int(os.environ.get("LITERATURE_FLOWB_STALE_SEC", "900")))
    is_busy = status in {"processing_b", "translating_local", "judging_llm"}
    is_stale = bool(is_busy and age_sec is not None and age_sec >= stale_sec)

    paper_dir = _paper_dir(deps.DATA_ROOT, pid, paper_id, for_write=False)
    summary_path = deps.safe_join_under(paper_dir, "05_interprets", "summary.json")
    fusion_path = deps.safe_join_under(paper_dir, "05_interprets", "fusion", "full_text.json")
    flowb_out = deps.safe_join_under(paper_dir, "06_translates", "fusion", "full_text_trans.json")
    flowb_trace = deps.safe_join_under(paper_dir, "06_translates", "trace", "translation_trace.json")
    nllb_dir = deps.safe_join_under(paper_dir, "06_translates", "nllb")
    judge_dir = deps.safe_join_under(paper_dir, "06_translates", "judge")
    jobs_dir = deps.safe_join_under(paper_dir, "_jobs")
    event_path = deps.safe_join_under(jobs_dir, "flow_b_events.jsonl")
    result_path = deps.safe_join_under(jobs_dir, "flow_b_result.json")

    def _stat(path: str) -> dict:
        if not os.path.exists(path):
            return {"exists": False}
        try:
            st = os.stat(path)
            return {
                "exists": True,
                "size": int(st.st_size),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception:
            return {"exists": True}

    nllb_count = 0
    judge_count = 0
    try:
        if os.path.isdir(nllb_dir):
            nllb_count = len([f for f in os.listdir(nllb_dir) if f.endswith("_nllb.json")])
    except Exception:
        pass
    try:
        if os.path.isdir(judge_dir):
            judge_count = len([f for f in os.listdir(judge_dir) if f.endswith("_judged.json")])
    except Exception:
        pass

    runtime_log = deps._runtime_log_file_path()
    runtime_rows = deps._tail_text_file(runtime_log, lines=max(100, int(log_lines * 2)), max_bytes=512000)
    filters = [paper_id.lower(), "flow b", "run_translation", "processing_b", "translating_local", "judging_llm"]
    runtime_hits = []
    for row in runtime_rows:
        low = row.lower()
        if any(k in low for k in filters):
            runtime_hits.append(row)
    runtime_hits = runtime_hits[-max(20, min(int(log_lines), 400)) :]

    return {
        "pid": pid,
        "paper_id": paper_id,
        "db": {
            "interpretation_status": status_raw,
            "process_status": (paper.process_status or "") if paper else "",
            "process_log": (paper.process_log or "") if paper else "",
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
            "age_sec": round(float(age_sec), 3) if age_sec is not None else None,
            "flowb_stale_sec": stale_sec,
            "is_busy": is_busy,
            "is_stale": is_stale,
        },
        "artifacts": {
            "summary": _stat(summary_path),
            "fusion_full_text": _stat(fusion_path),
            "flowb_output": _stat(flowb_out),
            "flowb_trace": _stat(flowb_trace),
            "flowb_result": _stat(result_path),
            "nllb_count": nllb_count,
            "judge_count": judge_count,
        },
        "events_tail": deps._tail_text_file(event_path, lines=max(20, min(int(log_lines), 400))),
        "runtime_log": {
            "path": runtime_log,
            "tail_hits": runtime_hits,
        },
    }
