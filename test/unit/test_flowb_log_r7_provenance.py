# Roothinks source maintenance contract
# 目的：Flow B 診斷快照（flowb_probe／recover_flowb）的「混合專案日誌」對抗性回歸測試（R7 安全輪）。
#   一行全站日誌只有在「寫入當下唯一的專案出處就是本專案」時才能給本專案成員看。
#   a) 執行緒池重用、繼承來的 context 不得把 B 的行標成 A。
#   b) ROOTHINKS_LOG_PID 只放在子程序的 env dict；web 行程即使繼承了這個環境變數，也不得把無出處的行標成某專案。
#   c) 子程序的輸出不會被父程序重新寫進全站日誌。
#   d) 同一請求授權了兩個專案、請求與背景 context 不一致、共用元件（模型單例）的行 → [pid=-]。
#   e) 快照的 events_tail／process_log／tail_hits 不含伺服器絕對路徑，也不含別的專案的事件。
#   f) 多行紀錄（traceback、訊息內嵌換行）與日誌尾端被切斷的第一行，都不能偽造或繼承出處欄位。
# 驗證：python -m pytest test/unit/test_flowb_log_r7_provenance.py -q
import json
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import log_provenance as lp
from app.core_pro.literature import literature_bflow as bflow
from app.llm_service import llm_usage

PA, PB = "R7LOGA-p", "R7LOGB-p"
STAMP = "2026-11-01 10:00:00,000 [INFO] 1/MainThread x"


@contextmanager
def runtime_log(tmp_path):
    """與 app/system_runtime.setup_system_logging 相同的掛法：handler 上的出處 filter＋TAG_FORMAT。"""
    path = tmp_path / "runtime.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.addFilter(lp.ProjectContextFilter())
    handler.setFormatter(logging.Formatter(lp.TAG_FORMAT))
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        yield path
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(old_level)


def visible(path, pid):
    rows = Path(path).read_text(encoding="utf-8").splitlines()
    return "\n".join(lp.lines_for_project(rows, pid)[0])


LOG = logging.getLogger("FlowBR7Test")


# --------------------------------------------------------------------------- Flow B 背景工作的測試替身
class _Query:
    def filter_by(self, **kw):
        return SimpleNamespace(first=lambda: None)


class _Paper:
    STATUS_FAILED = "failed"
    query = _Query()


def _fusion(root, pid, paper):
    from app.core_pro.storage_layout import resolve_literature_paper_dir
    d = Path(resolve_literature_paper_dir(str(root), pid, paper, for_write=True)) / "05_interprets" / "fusion"
    d.mkdir(parents=True, exist_ok=True)
    (d / "full_text.json").write_text("{}", encoding="utf-8")


def _route_deps(root, executor, *, subprocess_mode=True, shared=None, probe=None):
    from flask import current_app, jsonify, request
    from werkzeug.exceptions import BadRequest
    from app.security import safe_join_under

    def run_child(pid, paper_id, fusion_input, trans_output, result_path, engine="auto"):
        # 父程序的 Flow B worker 在等子程序；這一行由 worker 寫，出處應該是該篇所屬專案。
        LOG.info("[FlowB] %s/%s translating_local job line JOB_%s", pid, paper_id, paper_id)
        if probe is not None:
            probe(pid, paper_id)
        Path(trans_output).write_text("{}", encoding="utf-8")
        return True, "", {}

    def translator():
        if shared is not None:
            shared()
        return SimpleNamespace(translate_file=lambda src, dst, *a: (Path(dst).write_text("{}"), True)[1])

    return bflow.FlowBRouteDeps(
        request=request, jsonify=jsonify, current_app=current_app, logger=LOG,
        db=SimpleNamespace(session=SimpleNamespace(remove=lambda: None)), Paper=_Paper, BadRequest=BadRequest,
        DATA_ROOT=str(root), FLOWB_SUBPROCESS_ENABLED=subprocess_mode, FLOWB_TIMEOUT_SEC=60,
        cap_worker_count=lambda n: n, safe_join_under=safe_join_under, load_json_locked=lambda p, d=None: d,
        _normalize_literature_pid=lambda p: p, _safe_paper_id=lambda p: p,
        _dedupe_keep_order=lambda xs: list(dict.fromkeys(xs)), _new_run_id=lambda *a: "run1",
        _write_job_config=lambda *a, **k: "", _append_flow_event=lambda *a, **k: None,
        _update_paper_status=lambda pid, paper, status, msg="": LOG.info("[DB] Updated Paper status: %s -> %s", paper, status),
        _flowb_read_bool_env=lambda *a, **k: False, _flowb_compute_fusion_quality_metrics=lambda p: {},
        _generate_flowb_reflow_artifact=lambda **k: (True, "", "", "llm"),
        _flowb_is_ready_generation_mode=lambda m: True, _flowb_read_ready_generation_modes=lambda: {"llm"},
        _run_flowb_subprocess=run_child, _to_bool=lambda v, d=True: d if v is None else str(v) == "1",
        _get_flowb_executor=lambda: executor, get_nllb_translator=translator,
    )


@pytest.fixture()
def flask_app(monkeypatch):
    from flask import Flask
    monkeypatch.setenv("LITERATURE_USE_DIRECT_THREAD", "0")
    monkeypatch.delenv("ROOTHINKS_LOG_PID", raising=False)
    return Flask("flowb-r7")


def _start(app, root, executor, pid, paper, **kw):
    """以 pid 的已授權請求呼叫真正的 run_translation_impl（授權成功 → bind_request_pid）。"""
    _fusion(root, pid, paper)
    with app.test_request_context(json={"pid": pid, "paper_ids": [paper], "engine": "auto"}):
        lp.bind_request_pid(pid)
        resp = bflow.run_translation_impl(_route_deps(root, executor, **kw))
    assert resp.get_json()["status"] == "success"


# --------------------------------------------------------------------------- a) 陳舊／繼承的 context
def test_a_reused_pool_thread_never_carries_the_previous_project(flask_app, tmp_path):
    seen = {}
    pool = ThreadPoolExecutor(max_workers=1)
    with runtime_log(tmp_path) as log:
        _start(flask_app, tmp_path, pool, PA, "pa1", probe=lambda pid, paper: seen.setdefault(paper, lp.canonical_pid(lp._current_pid())))
        pool.submit(lambda: None).result()
        _start(flask_app, tmp_path, pool, PB, "pb1", probe=lambda pid, paper: seen.setdefault(paper, lp.canonical_pid(lp._current_pid())))
        pool.submit(lambda: None).result()
        # 同一條池化執行緒上、沒有專案出處的共用工作（例如執行緒池的心跳）。
        pool.submit(lambda: LOG.info("[FlowB] pool heartbeat flow b pa1 pb1 HEARTBEAT")).result()
        pool.shutdown(wait=True)
    assert seen == {"pa1": "r7loga", "pb1": "r7logb"}
    a, b = visible(log, PA), visible(log, PB)
    assert "JOB_pa1" in a and "JOB_pb1" not in a
    assert "JOB_pb1" in b and "JOB_pa1" not in b
    assert "HEARTBEAT" not in a + b
    assert llm_usage.current_context()["pid"] is None


def test_a_stale_value_left_on_a_pool_thread_does_not_label_the_next_job(flask_app, tmp_path):
    pool = ThreadPoolExecutor(max_workers=1)
    # 某個元件在池化執行緒上 set_context(A) 後沒有清掉（例如中途拋例外）。
    pool.submit(llm_usage.set_context, PA, "pa-stale").result()
    with runtime_log(tmp_path) as log:
        _start(flask_app, tmp_path, pool, PB, "pb1")
        pool.submit(lambda: None).result()
        pool.submit(lambda: LOG.info("[FlowB] after job flow b pb1 AFTER_B")).result()
        pool.shutdown(wait=True)
    assert "JOB_pb1" not in visible(log, PA) and "JOB_pb1" in visible(log, PB)
    assert "AFTER_B" not in visible(log, PA) + visible(log, PB)


def test_a_worker_on_a_thread_that_inherited_another_projects_request(flask_app, tmp_path):
    """Python 3.14 thread_inherit_context／會複製 context 的執行器：B 的工作跑在帶著 A 請求 context 的執行緒上。"""
    import contextvars
    captured = {}
    with flask_app.test_request_context():
        lp.bind_request_pid(PA)
        captured["ctx"] = contextvars.copy_context()

    class Inheriting:
        def submit(self, fn, *args):
            t = threading.Thread(target=captured["ctx"].run, args=(fn, *args))
            t.start()
            t.join()

    with runtime_log(tmp_path) as log:
        _start(flask_app, tmp_path, Inheriting(), PB, "pb2")
    assert "JOB_pb2" not in visible(log, PA) and "JOB_pb2" in visible(log, PB)


# --------------------------------------------------------------------------- b) 行程層級的環境變數
def test_b_launcher_puts_the_pid_only_into_the_child_env(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(kw["env"])
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.delenv("ROOTHINKS_LOG_PID", raising=False)
    monkeypatch.setattr(bflow.subprocess, "run", fake_run)
    deps = SimpleNamespace(DATA_ROOT=str(tmp_path), BASE_DIR=str(tmp_path), FLOWB_TIMEOUT_SEC=5)
    for pid in (PA, PB):
        bflow.run_flowb_subprocess(deps, pid, "p1", "in.json", "out.json", str(tmp_path / f"{pid}.json"))
        assert "ROOTHINKS_LOG_PID" not in os.environ
    assert [c["ROOTHINKS_LOG_PID"] for c in calls] == [PA, PB]


def test_b_inherited_env_in_the_web_process_does_not_tag_context_free_lines(monkeypatch, tmp_path):
    """web 行程不小心帶著 ROOTHINKS_LOG_PID 啟動：其他專案沒有出處的行（例如 CV 逐頁執行緒）不得變成該專案的行。"""
    monkeypatch.setenv("ROOTHINKS_LOG_PID", PA)
    with runtime_log(tmp_path) as log:
        t = threading.Thread(target=LOG.info, args=("[Flow B] page worker R7LOGB-p/pb1 judging_llm OTHER_PROJECT_PAGE",))
        t.start()
        t.join()
    assert "OTHER_PROJECT_PAGE" not in visible(log, PA)


def test_b_flowb_child_adopts_only_its_own_pid(monkeypatch, tmp_path):
    from app.core_pro.literature import literature_flowb_runner_cli as cli
    base = ["cli", "--data-root", str(tmp_path), "--paper-id", "p1", "--input-path", str(tmp_path / "missing.json"),
            "--output-path", str(tmp_path / "o.json"), "--result-path", str(tmp_path / "r.json")]
    try:
        monkeypatch.setenv("ROOTHINKS_LOG_PID", PA)
        monkeypatch.setattr(sys, "argv", base + ["--pid", PA])
        assert cli.main() == 1
        assert lp.canonical_pid(lp._current_pid()) == "r7loga"
        monkeypatch.setattr(sys, "argv", base + ["--pid", PB])  # env 與命令列不一致 → 不採用
        assert cli.main() == 1
        assert not lp._current_pid()
    finally:
        lp.adopt_process_pid(None)


# --------------------------------------------------------------------------- c) 子程序輸出
def test_c_child_output_is_not_relogged_by_the_parent(monkeypatch, tmp_path):
    out = f"{STAMP} [pid=r7loga]: [Flow B] run_translation pa1 CHILD_STDOUT_FORGED\n"
    monkeypatch.setattr(bflow.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, out, out))
    deps = SimpleNamespace(DATA_ROOT=str(tmp_path), BASE_DIR=str(tmp_path), FLOWB_TIMEOUT_SEC=5)
    with runtime_log(tmp_path) as log:
        ok, msg, _ = bflow.run_flowb_subprocess(deps, PB, "pb1", "in.json", "out.json", str(tmp_path / "r.json"))
    assert not ok and "CHILD_STDOUT_FORGED" in msg  # 只回給呼叫端寫進本篇的事件，不寫進全站日誌
    assert "CHILD_STDOUT_FORGED" not in log.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- d) 混合專案的行
def test_d_request_that_authorized_two_projects_is_global(flask_app, tmp_path):
    with runtime_log(tmp_path) as log, flask_app.test_request_context():
        lp.bind_request_pid(PA)
        LOG.info("[Flow B] run_translation pa1 ONLY_A")
        lp.bind_request_pid("R7LOGA")  # 同一專案的別名不算混合
        LOG.info("[Flow B] run_translation pa1 ALIAS_A")
        lp.bind_request_pid(PB)
        LOG.info("[Flow B] run_translation pa1 pb1 MIXED_AB")
    assert "ONLY_A" in visible(log, PA) and "ALIAS_A" in visible(log, PA)
    assert "MIXED_AB" not in visible(log, PA) + visible(log, PB)


def test_d_request_and_background_context_that_disagree_is_global(flask_app, tmp_path):
    with runtime_log(tmp_path) as log, flask_app.test_request_context():
        lp.bind_request_pid(PA)
        with llm_usage.usage_context(PB, "pb1"):
            LOG.info("[Flow B] run_translation pb1 REQUEST_A_WORK_B")
    assert "REQUEST_A_WORK_B" not in visible(log, PA) + visible(log, PB)


def test_d_shared_model_component_lines_are_global(flask_app, tmp_path):
    """共用的翻譯模型單例（lazy init、快取）的行可能列出多個專案；由誰觸發都不能標成那個專案。"""
    def shared():
        LOG.info("[HybridTranslator] shared NLLB cache warm: R7LOGB-p/pb-secret, R7LOGA-p/pa1 flow b SHARED_MODEL")

    pool = ThreadPoolExecutor(max_workers=1)
    with runtime_log(tmp_path) as log:
        _start(flask_app, tmp_path, pool, PA, "pa1", subprocess_mode=False, shared=shared)
        pool.shutdown(wait=True)
    rows = log.read_text(encoding="utf-8")
    assert rows.count("SHARED_MODEL") >= 2, rows  # 請求內檢查＋worker 內載入
    assert "SHARED_MODEL" not in visible(log, PA)


# --------------------------------------------------------------------------- e) 快照的其他欄位
def _snapshot_deps(root, log_path, process_log=""):
    from app.core_pro.literature.literature_routes import _tail_text_file
    from app.security import safe_join_under
    paper = SimpleNamespace(interpretation_status="processing_B", process_status="", process_log=process_log, updated_at=None)

    class Q:
        def filter_by(self, **kw):
            return SimpleNamespace(first=lambda: paper)

    return bflow.FlowBRuntimeDeps(
        DATA_ROOT=str(root), BASE_DIR=str(root), FLOWB_TIMEOUT_SEC=60, FLOWB_SUBPROCESS_ENABLED=True,
        Paper=SimpleNamespace(query=Q()), cap_worker_count=lambda n: n, get_cpu_limit_info=lambda: {},
        _now_str=lambda: "2026-11-01 10:00:00", safe_join_under=safe_join_under,
        _runtime_log_file_path=lambda: str(log_path), _tail_text_file=_tail_text_file, logger=LOG)


def test_e_snapshot_returns_no_absolute_server_paths(tmp_path):
    root = tmp_path / "srv-data"
    root.mkdir()
    log = tmp_path / "runtime.log"
    deps = _snapshot_deps(root, log, process_log=f"Flow B translation error: [Errno 2] {root}/R7LOGA-p/x.json [run_id=r]")
    out = f"{root}/R7LOGA-p/literature/pa1/06_translates/fusion/full_text_trans.json"
    bflow.append_flow_event(deps, PA, "pa1", flow="flow_b", event="completed", message="ok",
                            extra={"output": out, "payload": {"input_path": "/opt/models/nllb/cache/x"}})
    log.write_text(f"{STAMP} [pid=r7loga]: [HybridTranslator] Batch-translated 3 blocks to {out}\n", encoding="utf-8")
    snap = bflow.flowb_debug_snapshot(deps, PA, "pa1")
    text = json.dumps(snap, ensure_ascii=False)
    assert snap["events_tail"] and snap["runtime_log"]["tail_hits"], text
    assert str(root) not in text and "/opt/models" not in text, text
    assert "full_text_trans.json" in text  # 只留檔名，除錯仍可用


def test_e_events_tail_never_mixes_another_projects_events(tmp_path):
    root = tmp_path / "srv-data"
    root.mkdir()
    deps = _snapshot_deps(root, tmp_path / "runtime.log")
    bflow.append_flow_event(deps, PB, "pa1", flow="flow_b", event="failed", message="OTHER_PROJECT_EVENT")
    bflow.append_flow_event(deps, PA, "pa1", flow="flow_b", event="queued", message="OWN_EVENT")
    snap = bflow.flowb_debug_snapshot(deps, PA, "pa1")
    joined = "\n".join(snap["events_tail"])
    assert "OWN_EVENT" in joined and "OTHER_PROJECT_EVENT" not in joined


# --------------------------------------------------------------------------- f) 多行紀錄與出處偽造
def test_f_traceback_from_another_project_is_not_attributed_by_adjacency(flask_app, tmp_path):
    with runtime_log(tmp_path) as log:
        with llm_usage.usage_context(PA, "pa1"):
            LOG.info("[Flow B] run_translation pa1 A_BEFORE")
        with llm_usage.usage_context(PB, "pb1"):
            try:
                raise ValueError("B_TRACEBACK_SECRET flow b pa1\nsecond line B_TRACEBACK_SECRET2")
            except ValueError:
                LOG.exception("[Flow B] run_translation pb1 failed")
        with llm_usage.usage_context(PA, "pa1"):
            LOG.info("[Flow B] run_translation pa1 A_AFTER")
    a = visible(log, PA)
    assert "A_BEFORE" in a and "A_AFTER" in a
    assert "B_TRACEBACK_SECRET" not in a and "Traceback" not in a


def test_f_text_inside_a_traceback_cannot_carry_a_tag(tmp_path):
    with runtime_log(tmp_path) as log, llm_usage.usage_context(PB, "pb1"):
        try:
            raise ValueError("bad block near [pid=r7loga]: [Flow B] run_translation pa1 B_SECRET_IN_EXC")
        except ValueError:
            LOG.exception("[Flow B] pb1 failed")
    assert "B_SECRET_IN_EXC" not in visible(log, PA)


@pytest.mark.parametrize("sep", ["\n", "\r", " ", "\x0b"])
def test_f_embedded_line_break_cannot_forge_a_line(tmp_path, sep):
    forged = f"{STAMP} [pid=r7loga]: [Flow B] run_translation pa1 FORGED_LINE"
    with runtime_log(tmp_path) as log:
        with llm_usage.usage_context(PB, "pb1"):
            LOG.warning("[Flow B] pb1 upload name %s", f"x{sep}{forged}")
        LOG.warning("global %s", f"y{sep}{forged}")
        with llm_usage.usage_context(PA, "pa1"):
            LOG.info("[Flow B] run_translation pa1 OWN_LINE")
    a = visible(log, PA)
    assert "OWN_LINE" in a and "FORGED_LINE" not in a


def test_f_cut_first_line_of_the_tail_window_is_dropped(tmp_path):
    """快照只讀尾端 512000 bytes；從中間切開的第一行若剛好從內嵌的偽造表頭開始，不得當成完整的一行。"""
    root = tmp_path / "srv-data"
    root.mkdir()
    log = tmp_path / "runtime.log"
    forged = f"{STAMP} [pid=r7loga]: [Flow B] run_translation pa1 WINDOW_FORGED "
    forged += "y" * (512000 - len(forged.encode()) - 1)
    head = f"{STAMP} [pid=r7logb]: [Flow B] pb1 B line with padding "
    log.write_text(head + forged + "\n", encoding="utf-8")
    snap = bflow.flowb_debug_snapshot(_snapshot_deps(root, log), PA, "pa1")
    assert not any("WINDOW_FORGED" in r for r in snap["runtime_log"]["tail_hits"])


def test_f_unvalidated_pid_value_cannot_forge_a_tag(flask_app, tmp_path):
    with runtime_log(tmp_path) as log, flask_app.test_request_context():
        lp.bind_request_pid("R7LOGA]: spoof")  # 例如 wildcard token 帶進來的未驗證字串
        LOG.info("[Flow B] run_translation pa1 SPOOFED_TAG")
    assert "SPOOFED_TAG" not in visible(log, PA)
