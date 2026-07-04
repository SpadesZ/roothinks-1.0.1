# 檔案路徑: app/core_pro/manuscript/context_audit.py
# 產生時間: 2026-07-04 19:05 +08:00
# 版本: v0.1
# 模組定位:
#   Manuscript context injection sidecar audit writer。
# 主要責任:
#   1. 保存每次注入 context 的 source ids / fingerprints / pack fingerprint。
#   2. 使用 tmp + os.replace 原子寫入。
# 維護提醒:
#   - 不保存 API key、完整 connection 設定或其他 secret。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core_pro.manuscript.context_inject import build_context_pack_metadata


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or ""))[:80] or "unknown"


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def write_context_audit(
    *,
    project_id: str,
    section_id: str,
    context_items: list[dict],
    prompt: str = "",
    provider: str | None = None,
    model: str | None = None,
    data_root: str | None = None,
) -> str:
    root = Path(data_root or "data") / "manuscript_context_audit" / _safe_name(project_id) / _safe_name(section_id)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{now}.json"
    tmp_path = path.with_suffix(".tmp")
    pack = build_context_pack_metadata(context_items)
    payload: dict[str, Any] = {
        "project_id": project_id,
        "section_id": section_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "injected_context_ids": [str(x.get("source_id") or "") for x in context_items],
        "injected_context_fingerprints": [str(x.get("fingerprint") or "") for x in context_items],
        "context_pack_fingerprint": pack["context_pack_fingerprint"],
        "prompt_fingerprint": prompt_fingerprint(prompt),
        "provider": provider or "",
        "model": model or "",
        "context_items": [
            {
                "source_type": x.get("source_type"),
                "source_id": x.get("source_id"),
                "paper_id": x.get("paper_id"),
                "segment_id": x.get("segment_id"),
                "fingerprint": x.get("fingerprint"),
                "score": x.get("score"),
                "estimated_tokens": x.get("estimated_tokens"),
            }
            for x in context_items
        ],
    }
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return str(path)
