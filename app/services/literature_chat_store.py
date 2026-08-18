# Roothinks source maintenance contract
# 上下游: 由 Blueprint 或 matching task 呼叫，輸入專案/論文識別與內容，輸出正規化 metadata、segments 或檢索 context。
# 驗證: python -m pytest test/unit/test_literature_chat_persistence.py -q
# 檔案路徑: app/services/literature_chat_store.py
# 產生時間: 2026-08-17
# 版本: v0.1
# 模組定位:
#   Literature 對話式找文獻的持久層：對話紀錄 + 搜尋結果 TTL cache。
# 主要責任: 落地每一輪對話（含已驗證論文清單）並提供 query 快取，避免重複燒 grounding 額度。
#   1. 對話以 session 檔存放（lit-chat_YYYYMMDD-HHMMSS.json），
#      距上一份不到 _SESSION_WINDOW_SEC 就 append，否則開新檔——沿用 PAQ 2A 的既有慣例。
#   2. NOTE(NOTE-039)：**papers 與 meta 一起存**。只存文字的話重新整理後 hyperlink 就沒了，
#      使用者得重跑一次搜尋才拿得回連結，等於把驗證成本再付一次。
#   3. 搜尋 cache 以正規化 query 的 sha1 為 key，逾時 entry 在寫入時順手清掉。
# 安全邊界:
#   - 路徑一律經 validate_id + 相對 data_root 的 realpath 檢查，pid 不得逃出 data root。
#   - 讀取路徑不建目錄（同 literature_library 的教訓：查詢不該有副作用）。
# 維護提醒:
#   - 鎖內只能用 raw open/json，不可呼叫 load_json_locked/write_json_locked
#     （它們各自再拿同一把鎖，Windows msvcrt 下會自我死鎖）。
#   - 損毀的單一 session 檔一律跳過但**必須留 warning**，不得靜默吞掉。
# -----------------------------------------------------------------------------

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from filelock import FileLock

from app.security import build_lock_path, validate_id

logger = logging.getLogger("app.services.literature_chat_store")

CHAT_SCHEMA_VERSION = 1

# 10 分鐘內視為同一段對話，沿用 PaqCore._save_chat_record 的視窗。
_SESSION_WINDOW_SEC = 600
_SESSION_PREFIX = "lit-chat_"
_MAX_RECORDS = 60
_DEFAULT_CACHE_TTL_SEC = 6 * 60 * 60


def _read_int_env(key: str, default_val: int) -> int:
    try:
        val = int(str(os.environ.get(key, default_val)).strip())
        return val if val > 0 else default_val
    except Exception:
        return default_val


def cache_ttl_sec() -> int:
    return _read_int_env("LIT_CHAT_CACHE_TTL_SEC", _DEFAULT_CACHE_TTL_SEC)


def _tw_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


def query_key(query: str) -> str:
    return hashlib.sha1(normalize_query(query).encode("utf-8")).hexdigest()


class LiteratureChatStore:
    def __init__(self, data_root: str):
        self.data_root = data_root

    # -- paths ------------------------------------------------------------
    def _literature_dir(self, pid: str, *, create_dir: bool = False) -> str:
        safe_pid = validate_id(pid, "project_id")
        lit_dir = os.path.join(self.data_root, safe_pid, "literature")
        root_real = os.path.realpath(self.data_root)
        if os.path.commonpath([root_real, os.path.realpath(lit_dir)]) != root_real:
            raise ValueError("Invalid literature path")
        if create_dir:
            os.makedirs(lit_dir, exist_ok=True)
        return lit_dir

    def chat_dir(self, pid: str, *, create_dir: bool = False) -> str:
        chat_dir = os.path.join(self._literature_dir(pid, create_dir=create_dir), "chat")
        if create_dir:
            os.makedirs(chat_dir, exist_ok=True)
        return chat_dir

    def _cache_path(self, pid: str, *, create_dir: bool = False) -> str:
        return os.path.join(
            self._literature_dir(pid, create_dir=create_dir), "chat_search_cache.json"
        )

    # -- conversation -----------------------------------------------------
    def load_records(self, pid: str, limit: int = 40) -> list[dict]:
        """最舊在前，最多 limit 筆。目錄不存在就回空，不建目錄。"""
        try:
            chat_dir = self.chat_dir(pid, create_dir=False)
        except Exception as e:
            logger.warning("[LitChat] 無法解析 chat 目錄 pid=%s: %s", pid, e)
            return []
        if not os.path.isdir(chat_dir):
            return []

        records: list[dict] = []
        # 檔名帶 YYYYMMDD-HHMMSS，字典序即時間序。
        for path in sorted(glob.glob(os.path.join(chat_dir, f"{_SESSION_PREFIX}*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning("[LitChat] 略過損毀的 chat 檔 %s: %s", os.path.basename(path), e)
                continue
            if not isinstance(data, list):
                logger.warning("[LitChat] 略過非 list 的 chat 檔 %s", os.path.basename(path))
                continue
            records.extend(item for item in data if isinstance(item, dict))

        limit = max(1, min(int(limit or 40), _MAX_RECORDS))
        return records[-limit:] if len(records) > limit else records

    def _active_session_path(self, chat_dir: str) -> Optional[str]:
        files = glob.glob(os.path.join(chat_dir, f"{_SESSION_PREFIX}*.json"))
        if not files:
            return None
        latest = max(files, key=os.path.getmtime)
        if (time.time() - os.path.getmtime(latest)) <= _SESSION_WINDOW_SEC:
            return latest
        return None

    def append_record(
        self,
        pid: str,
        *,
        user_message: str,
        ai_reply: str,
        papers: list[dict] = None,
        meta: dict = None,
        actor: str = "",
    ) -> str:
        """寫入一輪對話，回傳落地的檔案路徑。失敗一律拋例外，由 route 明說 persisted=false。"""
        chat_dir = self.chat_dir(pid, create_dir=True)
        record = {
            "schema_version": CHAT_SCHEMA_VERSION,
            "time": _tw_now().strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
            "actor": str(actor or ""),
            "user": str(user_message or ""),
            "ai": str(ai_reply or ""),
            "papers": list(papers or []),
            "meta": dict(meta or {}),
        }

        target = self._active_session_path(chat_dir)
        if target is None:
            target = os.path.join(
                chat_dir, f"{_SESSION_PREFIX}{_tw_now().strftime('%Y%m%d-%H%M%S')}.json"
            )

        with FileLock(build_lock_path(target), timeout=10):
            existing: list = []
            if os.path.exists(target):
                try:
                    with open(target, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        existing = loaded
                    else:
                        logger.warning(
                            "[LitChat] session 檔 %s 不是 list，改開新檔避免覆寫",
                            os.path.basename(target),
                        )
                        target = os.path.join(
                            chat_dir,
                            f"{_SESSION_PREFIX}{_tw_now().strftime('%Y%m%d-%H%M%S')}.json",
                        )
                except Exception as e:
                    logger.warning(
                        "[LitChat] session 檔 %s 讀取失敗（%s），改開新檔避免覆寫",
                        os.path.basename(target), e,
                    )
                    target = os.path.join(
                        chat_dir,
                        f"{_SESSION_PREFIX}{_tw_now().strftime('%Y%m%d-%H%M%S')}.json",
                    )
                    existing = []

            existing.append(record)
            tmp_path = f"{target}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, target)
        return target

    def clear(self, pid: str) -> int:
        """刪除所有 session 檔，回傳刪除數。搜尋 cache 不動——那是省錢用的，與對話無關。"""
        try:
            chat_dir = self.chat_dir(pid, create_dir=False)
        except Exception as e:
            logger.warning("[LitChat] clear 無法解析 chat 目錄 pid=%s: %s", pid, e)
            return 0
        if not os.path.isdir(chat_dir):
            return 0

        removed = 0
        for path in glob.glob(os.path.join(chat_dir, f"{_SESSION_PREFIX}*.json")):
            try:
                os.remove(path)
                removed += 1
            except Exception as e:
                logger.warning("[LitChat] 刪除 %s 失敗: %s", os.path.basename(path), e)
        return removed

    # -- search cache -----------------------------------------------------
    def load_cached_search(self, pid: str, query: str) -> dict:
        """命中回搜尋結果 dict，未命中或過期回 {}。讀取不建目錄、不取鎖。"""
        try:
            path = self._cache_path(pid, create_dir=False)
        except Exception:
            return {}
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("[LitChat] 搜尋 cache 讀取失敗 pid=%s: %s", pid, e)
            return {}

        entry = ((data or {}).get("entries") or {}).get(query_key(query))
        if not isinstance(entry, dict):
            return {}
        if (time.time() - float(entry.get("timestamp") or 0)) > cache_ttl_sec():
            return {}
        result = entry.get("result")
        return result if isinstance(result, dict) else {}

    def save_cached_search(self, pid: str, query: str, result: dict) -> None:
        """
        寫入搜尋 cache。失敗只記 warning —— 快取是最佳化，壞掉不該讓使用者拿不到結果。

        NOTE(NOTE-039): stage_errors 非空的結果**不入 cache**——那是不完整的搜尋，
        快取起來會讓接下來 6 小時都拿到同一份殘缺清單，而且看不出來原因。
        """
        if not isinstance(result, dict) or not result.get("papers"):
            return
        if (result.get("meta") or {}).get("stage_errors"):
            return
        try:
            path = self._cache_path(pid, create_dir=True)
            now_ts = time.time()
            ttl = cache_ttl_sec()
            with FileLock(build_lock_path(path), timeout=10):
                data: dict[str, Any] = {}
                if os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            data = loaded
                    except Exception as e:
                        logger.warning("[LitChat] 搜尋 cache 損毀，重建 pid=%s: %s", pid, e)

                entries = data.get("entries")
                if not isinstance(entries, dict):
                    entries = {}
                entries = {
                    k: v
                    for k, v in entries.items()
                    if isinstance(v, dict)
                    and (now_ts - float(v.get("timestamp") or 0)) <= ttl
                }
                entries[query_key(query)] = {
                    "timestamp": now_ts,
                    "query": normalize_query(query),
                    "result": result,
                }
                data["schema_version"] = CHAT_SCHEMA_VERSION
                data["entries"] = entries

                tmp_path = f"{path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, path)
        except Exception as e:
            logger.warning("[LitChat] 搜尋 cache 寫入失敗 pid=%s: %s", pid, e)
