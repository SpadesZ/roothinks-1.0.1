# 檔案路徑: app/core_pro/manuscript/citation_ledger.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   Citation decision sidecar：記錄「使用者確認過」的引用決策。
# 主要責任:
#   1. 只在人明確確認（accept/reject）後追加記錄；系統永不自動寫入正文。
#   2. 回答「某篇論文在何時、哪一章、為了支持什麼主張而被引用」。
#   3. 整段 read-modify-write 在同一把 FileLock 內＋tmp+os.replace 原子落盤。
# 維護提醒:
#   - append-only：不提供刪除；改判用新決策覆蓋語意（最新為準）。
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from filelock import FileLock

from app.security import build_lock_path

LEDGER_SCHEMA_VERSION = 1
DECISION_STATUSES = ("accepted", "rejected")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger_path(data_root: str, pid: str) -> str:
    target_dir = os.path.join(data_root, pid, "manuscript")
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, "citation_ledger.json")


def _empty(pid: str) -> dict:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "project_id": pid, "decisions": []}


def record_decision(
    data_root: str,
    pid: str,
    *,
    section_id: str,
    status: str,
    paper_id: str = "",
    entry_id: str = "",
    claim_text: str = "",
    snippet: str = "",
    segment_ids: list[str] | None = None,
    decided_by: str = "user",
) -> dict:
    if status not in DECISION_STATUSES:
        raise ValueError(f"Invalid decision status: {status}")
    if not str(section_id or "").strip():
        raise ValueError("section_id is required")
    if not str(paper_id or "").strip() and not str(entry_id or "").strip():
        raise ValueError("paper_id or entry_id is required")

    decision = {
        "decision_id": uuid.uuid4().hex[:12],
        "section_id": str(section_id).strip(),
        "status": status,
        "paper_id": str(paper_id or "").strip(),
        "entry_id": str(entry_id or "").strip(),
        "claim_text": str(claim_text or "").strip()[:2000],
        "snippet": str(snippet or "").strip()[:1200],
        "segment_ids": [str(x) for x in (segment_ids or []) if str(x).strip()],
        "decided_by": str(decided_by or "user").strip() or "user",
        "decided_at": _now_iso(),
    }

    path = _ledger_path(data_root, pid)
    with FileLock(build_lock_path(path), timeout=10):
        data = _empty(pid)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("decisions"), list):
                data = loaded
        data["decisions"].append(decision)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    return decision


def list_decisions(data_root: str, pid: str, *, section_id: str = "", paper_id: str = "") -> list[dict]:
    path = _ledger_path(data_root, pid)
    if not os.path.exists(path):
        return []
    with FileLock(build_lock_path(path), timeout=10):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    decisions = data.get("decisions") if isinstance(data, dict) else []
    if not isinstance(decisions, list):
        return []
    out = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        if section_id and decision.get("section_id") != section_id:
            continue
        if paper_id and decision.get("paper_id") != paper_id:
            continue
        out.append(dict(decision))
    return out
