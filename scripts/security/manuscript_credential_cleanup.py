# Roothinks source maintenance contract
# 目的：清理「舊前端把 token 寫進稿件 HTML」留下的歷史檔（hotfix H7 的後續）。可逆、以版本檔為單位、預設只演練。
# 範圍：<data-root>/<pid>/manuscript/ 底下所有 JSON（章節版本 V*.json、草稿、主論文版本、聊天紀錄……），
#       只改字串值裡 HTML 的 src／href 屬性中的 access_token／token／api_key 參數（app.security.strip_html_url_credentials），
#       其他文字、版本號、_rev、時間戳一律不動；只加一個 _credential_cleanup 欄位記錄這次清理。
# 三種模式（**正式資料只有 Owner 授權後、停機或維護窗口中才可執行 --apply**）：
#   --dry-run（預設）：只列出要改幾個檔、幾個參數；不寫任何東西。
#   --apply --backup-dir B --manifest M：先把原檔逐一複製到 B（保留相對路徑），再原子寫入；M 記錄每個檔的前後雜湊。
#   --restore --manifest M：檔案目前雜湊等於 M 記錄的「清理後」雜湊時，才從備份還原；被改過的檔拒絕還原並列出。
# 報告與 manifest 都**不含 token 值或稿件內容**，只有相對路徑、雜湊與計數。
# 驗證：python -m pytest test/unit/test_manuscript_credential_cleanup.py -q
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.security import _CRED_PARAM, _URL_ATTR, strip_html_url_credentials  # noqa: E402

MARKER = "_credential_cleanup"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_value(value, counter):
    if isinstance(value, str):
        cleaned = strip_html_url_credentials(value)
        if cleaned != value:
            # 只數 src／href 屬性裡被移除的憑證參數（正文中的 token= 不算，也不會被改）
            counter[0] += sum(len(_CRED_PARAM.findall(m.group(4))) for m in _URL_ATTR.finditer(value))
        return cleaned
    if isinstance(value, list):
        return [_clean_value(v, counter) for v in value]
    if isinstance(value, dict):
        return {k: (v if k == MARKER else _clean_value(v, counter)) for k, v in value.items()}
    return value


def _kind(rel: Path) -> str:
    parts = rel.parts
    if "block" in parts:
        return "draft" if rel.name.startswith("_draft") else "section_version"
    if "paper" in parts:
        return "paper_version"
    return "other"


def plan(data_root):
    """回傳 [(相對路徑, 原始位元組, 清理後位元組, 移除參數數)]；只讀。"""
    root = Path(data_root)
    out = []
    for path in sorted(root.glob("*/manuscript/**/*.json")):
        raw = path.read_bytes()
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        counter = [0]
        cleaned = _clean_value(doc, counter)
        if counter[0] == 0 and cleaned == doc:
            continue
        if isinstance(cleaned, dict):
            cleaned[MARKER] = {"at": datetime.now(timezone.utc).isoformat(), "removed_params": counter[0],
                               "original_sha256": _sha(raw), "tool": "manuscript_credential_cleanup/1"}
        new = json.dumps(cleaned, ensure_ascii=False, indent=2).encode("utf-8")
        out.append((path.relative_to(root), raw, new, counter[0]))
    return out


def _summary(items):
    by_kind = {}
    for rel, _, _, n in items:
        k = _kind(rel)
        by_kind.setdefault(k, {"files": 0, "params": 0})
        by_kind[k]["files"] += 1
        by_kind[k]["params"] += n
    return {"files": len(items), "params_removed": sum(n for *_, n in items), "by_kind": by_kind,
            "projects": len({rel.parts[0] for rel, *_ in items})}


def dry_run(data_root):
    return {"mode": "dry-run", "writes": 0, **_summary(plan(data_root))}


def _atomic_write(path: Path, data: bytes):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cleanup-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def apply(data_root, backup_dir, manifest_path):
    root, backup = Path(data_root), Path(backup_dir)
    if backup.exists() and any(backup.iterdir()):
        raise SystemExit("備份目錄必須是空的（避免覆蓋上一次的備份）")
    items = plan(root)
    entries = []
    for rel, raw, new, n in items:
        dest = backup / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        if _sha(dest.read_bytes()) != _sha(raw):
            raise SystemExit(f"備份驗證失敗：{rel}")
        entries.append({"path": str(rel), "kind": _kind(rel), "removed_params": n,
                        "before_sha256": _sha(raw), "after_sha256": _sha(new), "backup": str(rel)})
    # 全部備份完成後才開始改寫
    for (rel, raw, new, n), entry in zip(items, entries):
        _atomic_write(root / rel, new)
    manifest = {"schema": "roothinks.manuscript_credential_cleanup/1", "data_root": str(root.resolve()),
                "backup_dir": str(backup.resolve()), "applied_at": datetime.now(timezone.utc).isoformat(),
                "entries": entries, **_summary(items)}
    Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"mode": "apply", **_summary(items)}


def restore(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    root, backup = Path(manifest["data_root"]), Path(manifest["backup_dir"])
    restored, refused = 0, []
    for e in manifest["entries"]:
        target, source = root / e["path"], backup / e["backup"]
        if not target.exists() or _sha(target.read_bytes()) != e["after_sha256"]:
            refused.append(e["path"])  # 清理後又被改過（或刪掉）：不覆蓋使用者的新內容
            continue
        data = source.read_bytes()
        if _sha(data) != e["before_sha256"]:
            refused.append(e["path"])
            continue
        _atomic_write(target, data)
        restored += 1
    return {"mode": "restore", "restored": restored, "refused": refused}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", action="store_true")
    ap.add_argument("--backup-dir")
    ap.add_argument("--manifest")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.restore:
        if not args.manifest:
            ap.error("--restore 需要 --manifest")
        result = restore(args.manifest)
    elif args.apply:
        if not (args.data_root and args.backup_dir and args.manifest):
            ap.error("--apply 需要 --data-root、--backup-dir、--manifest")
        result = apply(args.data_root, args.backup_dir, args.manifest)
    else:
        if not args.data_root:
            ap.error("需要 --data-root")
        result = dry_run(args.data_root)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return result


if __name__ == "__main__":
    main()
