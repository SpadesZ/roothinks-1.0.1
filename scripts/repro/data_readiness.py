# Roothinks source maintenance contract
# 檔案路徑: scripts/repro/data_readiness.py
# 目的: R4 正式遷移就緒檢查（資料面），補 deploy_preflight.py（環境、compose、DB 可開）沒涵蓋的部分。
#       只讀：SQLite 以 mode=ro 開啟；只讀 JSON；不建立、不修改、不刪除任何檔案（測試以前後雜湊驗證）。
# 用法（由 Owner／維運在「正式資料的副本」上執行；工程端不取得正式資料）:
#   python scripts/repro/data_readiness.py --data-root <副本 data/> --db <副本 roothinks.db> --out report.json
# 檢查與意義:
#   blockers（要先處理才遷移）: 版本化 JSON 文件破損（證據筆記、段落出處、文獻庫、Study 自由筆記）、歷史快照比 _rev 新。
#   warnings（遷移可以做，但行為會改變，需要告知）:
#     - 沒有 system_role=admin 的帳號 → 熱修後沒有人能讀全站日誌（產品功能不受影響）
#     - 舊導師（viewer＋MentorLink、沒有接受過邀請）數量 → REVIEW_SCOPE_LEGACY_MENTORS 若改 review_scope 會受影響
#     - 沒有建立者的 Study 對話 → R4 後只有 owner 能刪
#     - 有教授邀請的專案中非空的閱讀筆記 → R4 後教授看不到（PA-01 擴充，提議中）
#     - runtime.log 尾端沒有 [pid=…] 標記的比例 → 升級前的舊行不會出現在專案日誌視圖（預期）
# exit code: 0 沒有 blocker；2 有 blocker。
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

TAG = re.compile(r"\[pid=[A-Za-z0-9_-]{1,20}\]: ")


def _ro(db):
    return sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)


def _versioned_json(path):
    """回傳 (問題字串或 None, 資料)。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"JSON 無法解析：{type(exc).__name__}", None
    if not isinstance(doc, (dict, list)):
        return "根節點不是物件", None
    if isinstance(doc, dict):
        rev = doc.get("_rev", 0)
        if type(rev) is not int or rev < 0:
            return "_rev 不合法", doc
        history = Path(str(path) + ".history")
        if history.is_dir():
            revs = [int(p.stem) for p in history.glob("*.json") if p.stem.isdigit()]
            if revs and max(revs) >= rev:
                return f"歷史快照 {max(revs)} 不小於目前 _rev {rev}", doc
    return None, doc


def scan_documents(data_root):
    root = Path(data_root)
    patterns = {"evidence_notes": "*/study/*_evidence_notes.json",
                "paragraph_provenance": "*/manuscript/provenance/paragraph_links.json",
                "literature_library": "*/literature/library.json",
                "study_free_notes": "*/study/*_note.json"}
    out, blockers = {}, []
    for kind, pattern in patterns.items():
        files = list(root.glob(pattern))
        problems = []
        for path in files:
            problem, _ = _versioned_json(path)
            if problem:
                problems.append({"file": str(path.relative_to(root)), "problem": problem})
        out[kind] = {"files": len(files), "problems": problems}
        blockers += [f"{kind}: {p['file']}：{p['problem']}" for p in problems]
    return out, blockers


def scan_conversations(data_root):
    total = missing = 0
    for path in Path(data_root).glob("*/study/conversations/*.json"):
        total += 1
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("created_by") is None:
                missing += 1
        except Exception:
            missing += 1
    return {"total": total, "without_creator": missing}


def scan_db(db, data_root):
    con = _ro(db)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        info = {"tables_present": sorted(tables & {"users", "workspace_members", "mentor_links", "project_invitations"})}
        info["admin_accounts"] = con.execute("select count(*) from users where system_role='admin'").fetchone()[0] if "users" in tables else None
        legacy = []
        if {"workspace_members", "mentor_links", "project_invitations"} <= tables:
            rows = con.execute("""
                select wm.pid, wm.user_id from workspace_members wm
                where wm.role='viewer'
                  and exists (select 1 from mentor_links ml where ml.mentor_id=wm.user_id)
                  and not exists (select 1 from project_invitations pi where pi.accepted_by=wm.user_id and pi.pid=wm.pid
                                  and pi.status='accepted')""").fetchall()
            legacy = [{"pid": pid, "user_id": uid} for pid, uid in rows]
        info["legacy_mentor_viewers"] = legacy
        professor_pids = set()
        if "project_invitations" in tables:
            professor_pids = {r[0] for r in con.execute(
                "select distinct pid from project_invitations where kind='professor' and status='accepted'")}
    finally:
        con.close()
    hidden = 0
    for pid in professor_pids:
        path = Path(data_root) / pid / "literature" / "library.json"
        try:
            entries = (json.loads(path.read_text(encoding="utf-8")).get("entries") or {}).values()
            hidden += sum(1 for e in entries if str(e.get("reading_note") or "").strip())
        except Exception:
            continue
    info["reading_notes_hidden_from_professors"] = hidden
    info["projects_with_professor"] = len(professor_pids)
    return info


def scan_runtime_log(path, lines=2000):
    p = Path(path)
    if not p.exists():
        return {"present": False}
    tail = p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    tagged = sum(1 for line in tail if TAG.search(line))
    return {"present": True, "lines_checked": len(tail), "tagged": tagged, "untagged": len(tail) - tagged}


def run(data_root, db, runtime_log=None):
    documents, blockers = scan_documents(data_root)
    database = scan_db(db, data_root) if db else None
    conversations = scan_conversations(data_root)
    log = scan_runtime_log(runtime_log or Path(data_root) / "_logs" / "system" / "runtime.log")
    warnings = []
    if database and database["admin_accounts"] == 0:
        warnings.append("沒有 system_role=admin 帳號：熱修後沒有人能讀全站執行日誌（產品功能不受影響）")
    if database and database["legacy_mentor_viewers"]:
        warnings.append(f"舊導師 viewer {len(database['legacy_mentor_viewers'])} 筆：維持 REVIEW_SCOPE_LEGACY_MENTORS=keep 則不受影響")
    if conversations["without_creator"]:
        warnings.append(f"{conversations['without_creator']} 個 Study 對話沒有建立者：R4 後只有 owner 能刪除")
    if database and database["reading_notes_hidden_from_professors"]:
        warnings.append(f"{database['reading_notes_hidden_from_professors']} 則閱讀筆記在 R4 後不再給教授審閱身分看（PA-01 擴充，提議中）")
    if log.get("present") and log["untagged"]:
        warnings.append(f"runtime.log 尾端 {log['untagged']} 行沒有 [pid=…] 標記：升級前的舊行不會出現在專案日誌視圖（預期行為）")
    return {"schema": "roothinks.data_readiness/1", "read_only": True, "blockers": blockers, "warnings": warnings,
            "documents": documents, "database": database, "conversations": conversations, "runtime_log": log}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--db")
    ap.add_argument("--runtime-log")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    report = run(args.data_root, args.db, args.runtime_log)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 2 if report["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
