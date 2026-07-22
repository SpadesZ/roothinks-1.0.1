# 檔案路徑: app/services/literature_library.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   持久文獻庫（library.json）：跨多輪、多主題搜尋累積候選與納入文獻。
# 主要責任:
#   1. Library 只管 metadata 與人工狀態；Paper 表只管 PDF/pipeline，兩者以
#      entry.paper_id 明確連結。
#   2. 所有寫入為「整段 read-modify-write 都在同一把 FileLock 內」＋
#      tmp+os.replace 原子落盤，避免多 worker 遺失更新。
#   3. merge 永不覆寫使用者狀態（screening_*/reading_*/paper_id/notes），
#      metadata 只補空缺，不打架。
#   4. 不捏造欄位：沒有 title 也沒有 doi 的項目直接拒收。
# 維護提醒:
#   - 鎖內只能用 raw open/json，不可呼叫 load_json_locked/write_json_locked
#     （它們各自再拿同一把鎖，Windows msvcrt 下會自我死鎖）。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from filelock import FileLock

from app.security import build_lock_path, validate_id

LIBRARY_SCHEMA_VERSION = 1

SCREENING_STATUSES = ("candidate", "included", "excluded")
READING_STATUSES = ("unread", "reading", "read")

# merge 時只補空缺的 metadata 欄位；使用者欄位永不由 merge 寫入。
_METADATA_FIELDS = ("title", "authors", "year", "venue", "doi", "url", "abstract", "citations")
_USER_FIELDS = ("screening_status", "screening_note", "reading_status", "reading_note", "paper_id")
_ABSTRACT_MAX_CHARS = 4000


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_title_key(title: str) -> str:
    return re.sub(r"\W+", "", str(title or "").lower())[:200]


def entry_key(meta: dict) -> str:
    """doi 優先、否則正規化 title；兩者皆無回傳空字串（呼叫端應拒收）。"""
    doi = str((meta or {}).get("doi") or "").strip().lower()
    if doi:
        raw = f"doi:{doi}"
    else:
        norm = _norm_title_key((meta or {}).get("title"))
        if not norm:
            return ""
        raw = f"title:{norm}"
    return "lib-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _clean_authors(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r";|, and | and ", value)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("literal") or item.get("name")
                if not name:
                    family = str(item.get("family") or "").strip()
                    given = str(item.get("given") or "").strip()
                    name = ", ".join(x for x in (family, given) if x)
                name = str(name or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                out.append(name)
        return out
    return []


def _coerce_year(value: Any) -> Optional[int]:
    match = re.search(r"(18|19|20|21)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def normalize_entry_metadata(raw: dict) -> dict:
    """把候選/匯入項目正規化為 library metadata；缺欄位保持空值，不捏造。"""
    raw = raw or {}

    def first(*keys):
        for key in keys:
            value = raw.get(key)
            if value not in (None, "", []):
                return value
        return ""

    return {
        "title": str(first("title", "paper_title", "display_name")).strip()[:500],
        "authors": _clean_authors(first("authors", "author")),
        "year": _coerce_year(first("year", "publish_year", "publish_date", "issued")),
        "venue": str(first("venue", "journal", "container_title", "container-title")).strip()[:300],
        "doi": str(first("doi", "DOI")).strip(),
        "url": str(first("url", "URL", "link")).strip(),
        "abstract": str(first("abstract")).strip()[:_ABSTRACT_MAX_CHARS],
        "citations": int(raw.get("citations") or 0),
    }


def csl_item_to_raw(item: dict) -> dict:
    """CSL JSON item → normalize_entry_metadata 可吃的 raw dict。"""
    item = item or {}
    year = ""
    issued = item.get("issued")
    if isinstance(issued, dict):
        parts = issued.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = str(parts[0][0])
    return {
        "title": item.get("title", ""),
        "authors": item.get("author", []),
        "year": year,
        "venue": item.get("container-title", ""),
        "doi": item.get("DOI", ""),
        "url": item.get("URL", ""),
        "abstract": item.get("abstract", ""),
    }


def looks_like_csl(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if "issued" in item or "container-title" in item or "DOI" in item:
        return True
    authors = item.get("author")
    return isinstance(authors, list) and any(
        isinstance(a, dict) and ("family" in a or "literal" in a) for a in authors
    )


class LiteratureLibrary:
    def __init__(self, data_root: str):
        self.data_root = data_root

    def _library_path(self, pid: str) -> str:
        safe_pid = validate_id(pid, "project_id")
        lit_dir = os.path.join(self.data_root, safe_pid, "literature")
        os.makedirs(lit_dir, exist_ok=True)
        return os.path.join(lit_dir, "library.json")

    def _empty(self, pid: str) -> dict:
        return {
            "schema_version": LIBRARY_SCHEMA_VERSION,
            "project_id": pid,
            "updated_at": _now_iso(),
            "entries": {},
        }

    def _read_unlocked(self, path: str, pid: str) -> dict:
        if not os.path.exists(path):
            return self._empty(pid)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            raise ValueError(f"Corrupted library file: {os.path.basename(path)}")
        data.setdefault("schema_version", LIBRARY_SCHEMA_VERSION)
        return data

    def _write_unlocked(self, path: str, data: dict) -> None:
        data["updated_at"] = _now_iso()
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, pid: str) -> dict:
        path = self._library_path(pid)
        with FileLock(build_lock_path(path), timeout=10):
            return self._read_unlocked(path, pid)

    def _new_entry(self, key: str, meta: dict, *, source: str, topic: str) -> dict:
        now = _now_iso()
        return {
            "entry_id": key,
            **meta,
            "sources": [source] if source else [],
            "search_topics": [topic] if topic else [],
            "confidence": float(meta.get("confidence") or 0.0),
            "is_verified": bool(meta.get("is_verified", False)),
            "screening_status": "candidate",
            "screening_note": "",
            "reading_status": "unread",
            "reading_note": "",
            "paper_id": "",
            "created_at": now,
            "updated_at": now,
        }

    def _merge_into_entry(self, entry: dict, meta: dict, *, source: str, topic: str) -> bool:
        changed = False
        for field in _METADATA_FIELDS:
            incoming = meta.get(field)
            current = entry.get(field)
            if incoming in (None, "", [], 0):
                continue
            if current in (None, "", [], 0):
                entry[field] = incoming
                changed = True
        if source and source not in entry.setdefault("sources", []):
            entry["sources"].append(source)
            changed = True
        if topic and topic not in entry.setdefault("search_topics", []):
            entry["search_topics"].append(topic)
            changed = True
        if bool(meta.get("is_verified")) and not entry.get("is_verified"):
            entry["is_verified"] = True
            changed = True
        try:
            incoming_conf = float(meta.get("confidence") or 0.0)
        except Exception:
            incoming_conf = 0.0
        if incoming_conf > float(entry.get("confidence") or 0.0):
            entry["confidence"] = incoming_conf
            changed = True
        if changed:
            entry["updated_at"] = _now_iso()
        return changed

    def merge_candidates(self, pid: str, candidates: list[dict], *, topic: str = "", default_source: str = "") -> dict:
        """
        搜尋/匯入候選合併進 library：只新增或補空缺，永不覆寫使用者狀態。
        整段 RMW 在同一把鎖內。
        """
        path = self._library_path(pid)
        added = updated = skipped = 0
        with FileLock(build_lock_path(path), timeout=10):
            data = self._read_unlocked(path, pid)
            entries = data["entries"]
            for candidate in candidates or []:
                if not isinstance(candidate, dict):
                    skipped += 1
                    continue
                meta = normalize_entry_metadata(candidate)
                if not meta["title"] and not meta["doi"]:
                    skipped += 1
                    continue
                meta["confidence"] = candidate.get("confidence", 0.0)
                meta["is_verified"] = candidate.get("is_verified", False)
                source = str(candidate.get("source") or default_source or "").strip()
                key = entry_key(meta)
                if key in entries:
                    if self._merge_into_entry(entries[key], meta, source=source, topic=topic):
                        updated += 1
                else:
                    entries[key] = self._new_entry(key, meta, source=source, topic=topic)
                    added += 1
            self._write_unlocked(path, data)
            total = len(entries)
        return {"added": added, "updated": updated, "skipped": skipped, "total": total}

    def update_entry(self, pid: str, entry_id: str, patch: dict) -> dict:
        """
        使用者主導的欄位更新（狀態、備註、paper_id 連結、metadata 補正）。
        screening 與 reading 狀態各自驗證，互不影響。
        """
        path = self._library_path(pid)
        patch = patch or {}

        screening = patch.get("screening_status")
        if screening is not None and screening not in SCREENING_STATUSES:
            raise ValueError(f"Invalid screening_status: {screening}")
        reading = patch.get("reading_status")
        if reading is not None and reading not in READING_STATUSES:
            raise ValueError(f"Invalid reading_status: {reading}")

        allowed = set(_USER_FIELDS) | set(_METADATA_FIELDS)
        with FileLock(build_lock_path(path), timeout=10):
            data = self._read_unlocked(path, pid)
            entry = data["entries"].get(str(entry_id or ""))
            if entry is None:
                raise KeyError(f"Library entry not found: {entry_id}")
            for key, value in patch.items():
                if key not in allowed:
                    continue
                if key == "authors":
                    value = _clean_authors(value)
                elif key == "year":
                    value = _coerce_year(value)
                elif key == "abstract":
                    value = str(value or "")[:_ABSTRACT_MAX_CHARS]
                elif isinstance(value, str):
                    value = value.strip()
                entry[key] = value
            entry["updated_at"] = _now_iso()
            self._write_unlocked(path, data)
            return dict(entry)

    def import_items(self, pid: str, items: list[dict]) -> dict:
        """外部批次匯入：自動辨識 CSL JSON 或 normalized dict。"""
        converted = []
        for item in items or []:
            if looks_like_csl(item):
                converted.append({**csl_item_to_raw(item), "source": "import"})
            elif isinstance(item, dict):
                converted.append({**item, "source": str(item.get("source") or "import")})
        return self.merge_candidates(pid, converted, default_source="import")

    def list_entries(
        self,
        pid: str,
        *,
        screening: str = "",
        reading: str = "",
        query: str = "",
    ) -> list[dict]:
        data = self.load(pid)
        out = []
        needle = str(query or "").strip().lower()
        for entry in data["entries"].values():
            if screening and entry.get("screening_status") != screening:
                continue
            if reading and entry.get("reading_status") != reading:
                continue
            if needle:
                haystack = " ".join(
                    [str(entry.get("title") or ""), " ".join(entry.get("authors") or []), str(entry.get("venue") or "")]
                ).lower()
                if needle not in haystack:
                    continue
            out.append(dict(entry))
        out.sort(key=lambda e: (str(e.get("screening_status")), -(e.get("year") or 0), str(e.get("title") or "")))
        return out

    def entries_for_export(self, pid: str, scope: str = "included") -> list[dict]:
        entries = self.list_entries(pid, screening="included" if scope == "included" else "")
        out = []
        for entry in entries:
            out.append(
                {
                    "title": entry.get("title") or "",
                    "authors": entry.get("authors") or [],
                    "year": str(entry.get("year") or ""),
                    "journal": entry.get("venue") or "",
                    "doi": entry.get("doi") or "",
                    "url": entry.get("url") or "",
                    "abstract": entry.get("abstract") or "",
                }
            )
        return out
