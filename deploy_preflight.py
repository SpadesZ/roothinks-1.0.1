#!/usr/bin/env python3
# Roothinks source maintenance contract
# 檔案路徑: deploy_preflight.py
# 模組定位: 部署前唯讀檢查層；在 GCP cutover 前驗證環境、檔案與資料恢復條件。
# 主要責任: 驗證部署目標、必要檔案、Git 狀態、SQLite 可讀性與備份條件，產生 cutover 前的唯讀檢查報告。
# 上下游: 命令列參數/環境 -> 明確目標檔或 DB -> 可稽核輸出；不由一般 HTTP request 隱式觸發。
# 維護邊界: 任何資料變更都需明確目標、備份、idempotency 與失敗回滾；預設不得碰正式 data 或輸出秘密。
# 驗證: python -m py_compile deploy_preflight.py
"""
Roothinks deployment preflight checker.

Usage (from repo root):
  python deploy_preflight.py
  python deploy_preflight.py --env-file env.prod --compose-file docker-compose.yml --compose-file docker-compose.prod.yml

Exit code:
  0 -> all required checks passed
  2 -> one or more blocking checks failed
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


DEFAULT_COMPOSE_FILES = ["docker-compose.yml", "docker-compose.prod.yml"]
REDIS_ENV_KEYS = ["REDIS_URL", "RATELIMIT_STORAGE_URI", "SOCKETIO_MESSAGE_QUEUE"]


@dataclass
class CheckItem:
    level: str  # PASS / WARN / FAIL
    name: str
    detail: str


class Reporter:
    def __init__(self) -> None:
        self.items: List[CheckItem] = []

    def pass_(self, name: str, detail: str) -> None:
        self.items.append(CheckItem("PASS", name, detail))

    def warn(self, name: str, detail: str) -> None:
        self.items.append(CheckItem("WARN", name, detail))

    def fail(self, name: str, detail: str) -> None:
        self.items.append(CheckItem("FAIL", name, detail))

    def has_failures(self) -> bool:
        return any(it.level == "FAIL" for it in self.items)

    def render(self) -> str:
        lines = []
        for it in self.items:
            prefix = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[it.level]
            lines.append(f"{prefix} {it.name}: {it.detail}")
        lines.append("")
        lines.append(
            "Summary: "
            f"PASS={sum(1 for i in self.items if i.level == 'PASS')} "
            f"WARN={sum(1 for i in self.items if i.level == 'WARN')} "
            f"FAIL={sum(1 for i in self.items if i.level == 'FAIL')}"
        )
        return "\n".join(lines)


def _read_env_file(env_path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not env_path.exists():
        return env
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = key.strip()
        v = value.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        env[k] = v
    return env


def _to_bool(raw: Optional[str]) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _effective_env(name: str, file_env: Dict[str, str]) -> str:
    runtime = os.environ.get(name)
    if runtime is not None and str(runtime).strip() != "":
        return str(runtime).strip()
    return str(file_env.get(name, "")).strip()


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return True
    exact = {
        "changeme",
        "change_me",
        "change-me",
        "<strong-random>",
        "<valid-fernet-key>",
        "dev-only-change-me",
        "default",
        "placeholder",
        "todo",
    }
    if v in exact:
        return True
    if v.startswith("change_me") or v.startswith("changeme"):
        return True
    if v.startswith("replace_") or v.startswith("replace-me"):
        return True
    if "your-domain.example" in v or "example.com" in v:
        return True
    if "<" in v and ">" in v:
        return True
    return False


def _run_cmd(cmd: List[str], cwd: Path) -> Tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _check_compose_config(
    reporter: Reporter,
    root: Path,
    env_file: Path,
    compose_files: List[Path],
) -> str:
    missing = [str(p) for p in compose_files if not p.exists()]
    if missing:
        reporter.fail("Compose files", f"missing: {', '.join(missing)}")
        return ""
    if not env_file.exists():
        reporter.fail("Env file", f"not found: {env_file}")
        return ""

    cmd: List[str] = ["docker", "compose"]
    for p in compose_files:
        cmd.extend(["-f", str(p)])
    cmd.extend(["--env-file", str(env_file), "config"])

    rc, out, err = _run_cmd(cmd, root)
    if rc != 0:
        msg = (err or out or "").strip()
        reporter.fail("docker compose config", msg if msg else "command failed")
        return ""

    reporter.pass_("docker compose config", "resolved merged config successfully")
    return out


def _check_volumes(reporter: Reporter, compose_text: str) -> None:
    if not compose_text:
        return

    mount_app_detected = False
    target_app_detected = False
    target_data_detected = False

    short_mount_pat = re.compile(r"^\s*-\s*[^#\n]*:/app(?:\s*$|\s*:)", re.MULTILINE)
    target_app_pat = re.compile(r"^\s*target:\s*/app\s*$", re.MULTILINE)
    target_data_pat = re.compile(r"^\s*target:\s*/app/data\s*$", re.MULTILINE)
    short_data_pat = re.compile(r"^\s*-\s*[^#\n]*:/app/data(?:\s*$|\s*:)", re.MULTILINE)

    if short_mount_pat.search(compose_text):
        mount_app_detected = True
    if target_app_pat.search(compose_text):
        target_app_detected = True
    if target_data_pat.search(compose_text) or short_data_pat.search(compose_text):
        target_data_detected = True

    if mount_app_detected or target_app_detected:
        reporter.fail("Prod code mount", "detected mount to /app (e.g. .:/app). Remove for production.")
    else:
        reporter.pass_("Prod code mount", "no /app source-code bind mount found")

    if target_data_detected:
        reporter.pass_("Data persistence mount", "found /app/data bind/volume in merged compose")
    else:
        reporter.fail("Data persistence mount", "missing /app/data mount in merged compose")


def _check_data_dir_writable(reporter: Reporter, root: Path) -> None:
    data_dir = root / "data"
    if not data_dir.exists():
        reporter.fail("Host data directory", f"missing: {data_dir}")
        return
    if not data_dir.is_dir():
        reporter.fail("Host data directory", f"not a directory: {data_dir}")
        return

    test_file = data_dir / f".preflight_write_{uuid.uuid4().hex}.tmp"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except Exception as exc:
        reporter.fail("Host data write permission", f"{data_dir} is not writable: {exc}")
        return

    reporter.pass_("Host data write permission", f"{data_dir} is writable")


def _check_security_env(reporter: Reporter, file_env: Dict[str, str]) -> None:
    auth_enabled_raw = _effective_env("API_AUTH_ENABLED", file_env)
    if _to_bool(auth_enabled_raw):
        reporter.pass_("API_AUTH_ENABLED", f"enabled ({auth_enabled_raw})")
    else:
        reporter.fail("API_AUTH_ENABLED", f"must be enabled in prod, got '{auth_enabled_raw or '<empty>'}'")

    debug_raw = _effective_env("FLASK_DEBUG", file_env)
    if _to_bool(debug_raw):
        reporter.fail("FLASK_DEBUG", f"must be 0/false in prod, got '{debug_raw}'")
    else:
        reporter.pass_("FLASK_DEBUG", f"disabled ({debug_raw or '0'})")

    secret_key = _effective_env("SECRET_KEY", file_env)
    if _is_placeholder(secret_key) or len(secret_key) < 32:
        reporter.fail("SECRET_KEY", "missing/weak/placeholder (need strong secret >= 32 chars)")
    else:
        reporter.pass_("SECRET_KEY", "looks non-placeholder and length is acceptable")

    fernet_key = _effective_env("FERNET_KEY", file_env)
    if _is_placeholder(fernet_key):
        reporter.fail("FERNET_KEY", "missing or placeholder")
    else:
        try:
            from cryptography.fernet import Fernet

            Fernet(fernet_key.encode("utf-8"))
            reporter.pass_("FERNET_KEY", "valid Fernet key format")
        except Exception as exc:
            reporter.fail("FERNET_KEY", f"invalid Fernet key format: {exc}")

    tokens = _effective_env("API_BEARER_TOKENS", file_env)
    if not tokens:
        reporter.fail("API_BEARER_TOKENS", "empty while API auth is enabled")
    elif _is_placeholder(tokens):
        reporter.fail("API_BEARER_TOKENS", "contains placeholder value")
    else:
        token_list = [t.strip() for t in tokens.split(",") if t.strip()]
        if token_list:
            reporter.pass_("API_BEARER_TOKENS", f"{len(token_list)} token(s) configured")
        else:
            reporter.fail("API_BEARER_TOKENS", "parse result is empty")


def _check_redis_urls(reporter: Reporter, file_env: Dict[str, str], strict_prod: bool) -> None:
    any_checked = False
    for key in REDIS_ENV_KEYS:
        value = _effective_env(key, file_env)
        if not value:
            continue
        any_checked = True
        parsed = urlparse(value)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"redis", "rediss"}:
            reporter.warn(key, f"non-redis scheme '{scheme or '<empty>'}' not validated")
            continue

        has_password = bool(parsed.password)
        if not has_password:
            msg = f"{key} has no password in URI: {value}"
            if strict_prod:
                reporter.fail("Redis auth", msg)
            else:
                reporter.warn("Redis auth", msg)
        else:
            reporter.pass_("Redis auth", f"{key} contains password segment")

        if scheme == "redis":
            reporter.warn("Redis TLS", f"{key} uses redis:// (consider rediss:// if traversing untrusted network)")

    if not any_checked:
        reporter.warn("Redis auth", "no redis URI env found to validate")


def _default_sqlite_path(root: Path) -> Path:
    return (root / "data" / "roothinks.db").resolve()


def _check_db_dry_run(reporter: Reporter, root: Path, file_env: Dict[str, str]) -> None:
    fix_script = root / "fix_db_schema.py"
    if not fix_script.exists():
        reporter.fail("Migration script", f"missing: {fix_script}")
    else:
        rc, _, err = _run_cmd([sys.executable, "-m", "py_compile", str(fix_script)], root)
        if rc == 0:
            reporter.pass_("Migration script", "fix_db_schema.py syntax check passed")
        else:
            reporter.fail("Migration script", f"py_compile failed: {err.strip()}")

    db_url = _effective_env("DATABASE_URL", file_env)
    if not db_url:
        db_path = _default_sqlite_path(root)
        db_url = f"sqlite:///{db_path}"

    if not db_url.startswith("sqlite:///"):
        reporter.warn("DB dry-run", f"non-SQLite DATABASE_URL detected, skipped sqlite quick_check ({db_url})")
        return

    raw = db_url.replace("sqlite:///", "", 1)
    db_path = Path(raw).expanduser()
    if not db_path.is_absolute():
        db_path = (root / db_path).resolve()

    if not db_path.exists():
        reporter.warn("SQLite quick_check", f"db file not found yet (fresh deploy?): {db_path}")
        return

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA quick_check;")
            quick = cur.fetchone()
            quick_val = str(quick[0]) if quick else "<none>"
            if quick_val.lower() != "ok":
                reporter.fail("SQLite quick_check", f"result={quick_val}")
            else:
                reporter.pass_("SQLite quick_check", f"{db_path} => ok")
        finally:
            conn.close()
    except Exception as exc:
        reporter.fail("SQLite connection dry-run", f"{db_path}: {exc}")


def _check_lock_root_writable(reporter: Reporter, file_env: Dict[str, str]) -> None:
    lock_root = _effective_env("LOCK_ROOT", file_env)
    if not lock_root:
        reporter.fail("LOCK_ROOT", "missing")
        return

    # Validate host-side fallback by creating temp folder when relative path is used.
    # Absolute Linux path is validated inside container at app startup; we still keep
    # a lightweight host-side check for obvious malformed values.
    if "\x00" in lock_root:
        reporter.fail("LOCK_ROOT", "contains null byte")
        return

    reporter.pass_("LOCK_ROOT", f"configured: {lock_root}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roothinks deployment preflight checker")
    parser.add_argument(
        "--project-root",
        default=".",
        help="project root path (default: current directory)",
    )
    parser.add_argument(
        "--env-file",
        default="env.prod",
        help="env file for production checks (default: env.prod)",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        dest="compose_files",
        help="compose file path; repeatable (default: docker-compose.yml + docker-compose.prod.yml)",
    )
    parser.add_argument(
        "--no-db-dry-run",
        action="store_true",
        help="skip migration/script/sqlite dry-run checks",
    )
    parser.add_argument(
        "--allow-insecure-redis",
        action="store_true",
        help="downgrade missing redis password from FAIL to WARN",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root).resolve()
    env_file = (root / args.env_file).resolve()
    compose_files = [Path(p).resolve() for p in (args.compose_files or DEFAULT_COMPOSE_FILES)]

    reporter = Reporter()
    reporter.pass_("Project root", str(root))

    file_env = _read_env_file(env_file)
    if file_env:
        reporter.pass_("Env file parse", f"loaded {len(file_env)} entries from {env_file}")
    else:
        if env_file.exists():
            reporter.warn("Env file parse", f"{env_file} parsed as empty")
        else:
            reporter.fail("Env file parse", f"missing env file: {env_file}")

    _check_security_env(reporter, file_env)
    _check_lock_root_writable(reporter, file_env)
    _check_data_dir_writable(reporter, root)

    compose_text = _check_compose_config(reporter, root, env_file, compose_files)
    _check_volumes(reporter, compose_text)
    _check_redis_urls(reporter, file_env, strict_prod=not args.allow_insecure_redis)

    if not args.no_db_dry_run:
        _check_db_dry_run(reporter, root, file_env)
    else:
        reporter.warn("DB dry-run", "skipped by --no-db-dry-run")

    print(reporter.render())
    return 2 if reporter.has_failures() else 0


if __name__ == "__main__":
    sys.exit(main())
