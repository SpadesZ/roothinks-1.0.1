# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/services/llm_response_cache.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   LLM response cache 的保守檔案型 utility。
# 主要責任: 以 task/model/prompt fingerprint 管理可選 LLM response cache，隔離專案且不保存秘密。
#   1. 產生不含 secret 的 cache key。
#   2. 只快取成功 response。
#   3. 預設 opt-in，不改變既有 dispatcher 行為。
# 維護提醒:
#   - cache payload 不得包含 API key、connection raw row 或 provider secret。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def is_llm_cache_enabled() -> bool:
    raw = str(os.environ.get("LLM_RESPONSE_CACHE_ENABLED", "false")).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def build_llm_cache_key(
    *,
    task_type: str,
    prompt: str,
    provider: str = "",
    model: str = "",
    params: dict[str, Any] | None = None,
    context_fingerprint: str = "",
) -> str:
    payload = {
        "task_type": task_type,
        "prompt": prompt,
        "provider": provider,
        "model": model,
        "params": params or {},
        "context_fingerprint": context_fingerprint,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cache_dir(base_dir: str | None = None) -> Path:
    root = Path(base_dir or os.getcwd())
    return root / ".cache" / "llm_responses"


def load_cached_response(cache_key: str, *, base_dir: str | None = None) -> dict[str, Any] | None:
    path = get_cache_dir(base_dir) / f"{cache_key}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    return payload


def save_cached_response(cache_key: str, response: dict[str, Any], *, base_dir: str | None = None) -> None:
    if not response.get("ok"):
        return
    cache_dir = get_cache_dir(base_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key}.json"
    tmp_path = path.with_suffix(".tmp")
    payload = {
        "ok": True,
        "created_at": int(time.time()),
        "cache_key": cache_key,
        "response": {
            "text": str(response.get("text", "")),
        },
    }
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
