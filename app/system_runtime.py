# 檔案路徑: roothinks/app/system_runtime.py
# 產生時間: 2026-07-05 00:20 +08:00
# 版本: v1.1
# 模組定位:
#   系統執行期支撐:CPU thread 上限套用、系統 logging 初始化、
#   背景 system monitor(定期輸出 SYSTEM_METRICS 快照)。
# 主要責任:
#   1. apply_cpu_thread_limit_env / apply_torch_thread_limits。
#   2. setup_system_logging:RotatingFileHandler 掛載(冪等)。
#   3. start_system_monitor:daemon thread 定期記錄資源快照。
# 維護提醒:
#   - monitor 的防重(_MONITOR_STARTED)是「per-process」的:
#     gunicorn 多 worker 部署時每個 worker process 各有一份 monitor,
#     metrics 依 log 格式中的 %(process)d 區分屬於哪個 worker,
#     若只想要單份 metrics,除一個 worker 外以
#     LECTURE_SYSTEM_MONITOR_ENABLED=0 關閉其餘(或全域關閉)。
#   - monitor 為 daemon thread,不阻擋 process 退出,勿在其中做持久化寫入。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test -q(create_app 啟動路徑會執行本模組)。
# ------------------------------------------------------------------------------
import importlib.metadata
import json
import logging
import os
import platform
import socket
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Dict

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


LOGGER_NAME = "SystemRuntime"
_LOGGER = logging.getLogger(LOGGER_NAME)
_INIT_LOCK = threading.Lock()
_MONITOR_LOCK = threading.Lock()
_FILE_HANDLER_READY = False
_MONITOR_STARTED = False


THREAD_ENV_KEYS = [
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
]


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    try:
        value = int(str(os.environ.get(name, str(default))).strip())
        if value < min_value:
            return min_value
        return value
    except Exception:
        return int(default)


def _round_mb(value: int) -> float:
    return round(float(value) / (1024.0 * 1024.0), 2)


def _pkg_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return "unavailable"


def get_cpu_limit_info() -> Dict[str, int]:
    logical = max(1, int(os.cpu_count() or 1))
    physical = 0
    if psutil is not None:
        try:
            physical = int(psutil.cpu_count(logical=False) or 0)
        except Exception:
            physical = 0

    raw = str(os.environ.get("LECTURE_CPU_CORES", "")).strip()
    requested = 0
    if raw:
        try:
            requested = int(raw)
        except Exception:
            requested = 0

    if requested <= 0:
        effective = logical
    else:
        effective = min(logical, requested)

    return {
        "requested": requested,
        "logical": logical,
        "physical": physical,
        "effective": max(1, int(effective)),
    }


def get_effective_cpu_limit() -> int:
    return int(get_cpu_limit_info().get("effective", 1))


def cap_worker_count(requested_workers: int) -> int:
    try:
        requested = max(1, int(requested_workers))
    except Exception:
        requested = 1
    return min(requested, get_effective_cpu_limit())


def apply_cpu_thread_limit_env() -> Dict[str, object]:
    info = get_cpu_limit_info()
    effective = int(info["effective"])
    force_override = bool(info["requested"] > 0)
    applied = {}

    for key in THREAD_ENV_KEYS:
        current = str(os.environ.get(key, "")).strip()
        if current and not force_override:
            continue
        os.environ[key] = str(effective)
        applied[key] = str(effective)

    if force_override or not str(os.environ.get("TORCH_NUM_THREADS", "")).strip():
        os.environ["TORCH_NUM_THREADS"] = str(effective)
        applied["TORCH_NUM_THREADS"] = str(effective)

    if force_override or not str(os.environ.get("TORCH_INTEROP_THREADS", "")).strip():
        interop = max(1, min(4, effective))
        os.environ["TORCH_INTEROP_THREADS"] = str(interop)
        applied["TORCH_INTEROP_THREADS"] = str(interop)

    return {"cpu_limit": info, "applied_env": applied}


def apply_torch_thread_limits(torch_mod) -> Dict[str, int]:
    effective = get_effective_cpu_limit()
    applied = {}
    try:
        torch_mod.set_num_threads(effective)
        applied["torch_num_threads"] = effective
    except Exception:
        pass
    try:
        interop = max(1, min(4, effective))
        torch_mod.set_num_interop_threads(interop)
        applied["torch_num_interop_threads"] = interop
    except Exception:
        pass
    return applied


def _default_log_file(root_dir: str) -> str:
    return os.path.join(root_dir, "data", "_logs", "system", "runtime.log")


def _build_system_profile(root_dir: str) -> Dict[str, object]:
    info = get_cpu_limit_info()
    profile = {
        "event": "system_profile",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": socket.gethostname(),
        "cwd": os.getcwd(),
        "root_dir": root_dir,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "cpu": info,
        "packages": {
            "flask": _pkg_version("flask"),
            "sqlalchemy": _pkg_version("sqlalchemy"),
            "torch": _pkg_version("torch"),
            "transformers": _pkg_version("transformers"),
            "easyocr": _pkg_version("easyocr"),
            "openai": _pkg_version("openai"),
            "requests": _pkg_version("requests"),
            "psutil": _pkg_version("psutil"),
        },
    }

    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            profile["memory"] = {
                "total_mb": _round_mb(int(vm.total)),
                "available_mb": _round_mb(int(vm.available)),
                "used_percent": float(vm.percent),
            }
        except Exception:
            pass
        try:
            du = psutil.disk_usage(root_dir)
            profile["disk"] = {
                "total_mb": _round_mb(int(du.total)),
                "free_mb": _round_mb(int(du.free)),
                "used_percent": float(du.percent),
            }
        except Exception:
            pass
    return profile


def _build_runtime_snapshot() -> Dict[str, object]:
    snapshot = {
        "event": "system_metrics",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_limit": get_effective_cpu_limit(),
    }

    if psutil is not None:
        try:
            snapshot["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        except Exception:
            pass
        try:
            vm = psutil.virtual_memory()
            snapshot["memory_percent"] = float(vm.percent)
            snapshot["memory_available_mb"] = _round_mb(int(vm.available))
        except Exception:
            pass
        try:
            proc = psutil.Process(os.getpid())
            snapshot["process"] = {
                "pid": proc.pid,
                "cpu_percent": float(proc.cpu_percent(interval=None)),
                "rss_mb": _round_mb(int(proc.memory_info().rss)),
                "threads": int(proc.num_threads()),
            }
        except Exception:
            pass

    try:
        load = os.getloadavg()
        snapshot["load_avg"] = [round(float(x), 3) for x in load]
    except Exception:
        pass
    return snapshot


def setup_system_logging(root_dir: str) -> Dict[str, object]:
    global _FILE_HANDLER_READY
    with _INIT_LOCK:
        log_path = str(os.environ.get("LECTURE_SYSTEM_LOG_FILE", "")).strip()
        if not log_path:
            log_path = _default_log_file(root_dir)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        max_mb = _env_int("LECTURE_SYSTEM_LOG_MAX_MB", 32, min_value=1)
        backup_count = _env_int("LECTURE_SYSTEM_LOG_BACKUP_COUNT", 7, min_value=1)

        if not _FILE_HANDLER_READY:
            handler = RotatingFileHandler(
                log_path,
                maxBytes=max_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding="utf-8",
            )
            handler.setLevel(logging.INFO)
            handler.set_name("system_runtime_file_handler")
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(process)d/%(threadName)s %(name)s: %(message)s"
                )
            )

            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            if not any(h.get_name() == "system_runtime_file_handler" for h in root_logger.handlers):
                root_logger.addHandler(handler)
            _FILE_HANDLER_READY = True

        profile = _build_system_profile(root_dir)
        _LOGGER.info("[SYSTEM_PROFILE] %s", json.dumps(profile, ensure_ascii=False))
        return {"ok": True, "log_file": log_path, "cpu_limit": get_cpu_limit_info()}


def start_system_monitor(root_dir: str) -> Dict[str, object]:
    global _MONITOR_STARTED
    enabled = str(os.environ.get("LECTURE_SYSTEM_MONITOR_ENABLED", "1")).strip() != "0"
    if not enabled:
        return {"ok": True, "enabled": False}

    interval = _env_int("LECTURE_SYSTEM_MONITOR_INTERVAL_SEC", 15, min_value=2)

    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return {"ok": True, "enabled": True, "already_started": True}
        _MONITOR_STARTED = True

        def _worker():
            _LOGGER.info("[SYSTEM_MONITOR] started interval_sec=%s root_dir=%s", interval, root_dir)
            while True:
                try:
                    snapshot = _build_runtime_snapshot()
                    _LOGGER.info("[SYSTEM_METRICS] %s", json.dumps(snapshot, ensure_ascii=False))
                except Exception as e:  # pragma: no cover
                    _LOGGER.warning("[SYSTEM_MONITOR] snapshot failed: %s", e)
                time.sleep(interval)

        t = threading.Thread(target=_worker, daemon=True, name="system-monitor")
        t.start()
        return {"ok": True, "enabled": True, "interval_sec": interval}
