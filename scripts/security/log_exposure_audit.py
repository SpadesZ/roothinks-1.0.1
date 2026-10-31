# Roothinks source maintenance contract
# 目的：安全熱修（跨專案日誌外洩）的歷史暴露調查工具。只讀，不改任何檔案，不送出任何資料。
# 用法（由 Owner／維運在「授權取得的日誌副本」上執行；工程端只用合成資料測試）：
#   python scripts/security/log_exposure_audit.py --access <gunicorn 存取日誌...> --runtime <runtime.log 與輪替檔...> --out report.json
# 安全預設（v2）：報告**不含任何日誌原文**，連遮罩後的樣本也沒有。
#   - 存取日誌：只輸出解析後、格式已驗證的欄位（時間、IP、方法、狀態碼），端點名稱一律輸出程式內建常數，
#     查詢字串永遠不輸出；另外計算「網址帶 token 參數」的行數（證明 token 曾被記錄，但不輸出 token）。
#   - runtime 日誌：只輸出各類別的行數與時間範圍；專案只給數量。
#   v1 的遮罩樣本只遮 email 與部分金鑰格式，網址參數 access_token=…、token=…、提示詞片段會原樣進報告——已移除。
# 判讀：日誌被輪替或保留期不足時，「沒找到呼叫」不代表「沒有發生」；報告寫出涵蓋的時間範圍。
import argparse
import ipaddress
import json
import re
import sys
from collections import Counter, defaultdict

ENDPOINTS = ("/api/literature/debug/runtime_log_tail", "/api/literature/debug/flowb_probe",
             "/api/literature/debug/recover_flowb")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
# gunicorn 預設 access_log_format：%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"
ACCESS_RE = re.compile(r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<status>\d{3}) ')
TIME_FMT = re.compile(r"^\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}$")
RUNTIME_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
URL_TOKEN = re.compile(r"(?i)[?&](access_token|token|api_key|apikey|key)=")
CATEGORIES = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "credential_like": re.compile(r"(?i)(bearer\s+[A-Za-z0-9._-]{12,}|sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|"
                                  r"(api[_-]?key|access_token|token|password|secret)\s*[=:]\s*\S{6,})"),
    "url_token_param": URL_TOKEN,
    "project_id": re.compile(r"(?i)\bpid[=:\s'\"]+[A-Za-z0-9_-]{1,20}"),
    "server_path": re.compile(r"(/[A-Za-z0-9._-]+){3,}"),
    "prompt_or_user_text": re.compile(r"(?i)(semantic router|prompt|query|user_notes|content)"),
    "paper_title_or_file": re.compile(r"(?i)(title|filename|\.pdf)"),
    "traceback": re.compile(r"Traceback \(most recent call last\)|^\s+File \""),
}


def _safe_ip(raw):
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return "unparseable"


def _endpoint(path):
    """回傳內建常數（不是日誌原文）；不屬於受影響端點時回 None。"""
    bare = path.split("?", 1)[0]
    for e in ENDPOINTS:
        if bare == e or bare.startswith(e + "/"):
            return e
    return None


def scan_access(paths):
    hits, first, last, lines, token_lines = [], None, None, 0, 0
    token_paths = Counter()
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = ACCESS_RE.match(line)
                if not m:
                    continue
                lines += 1
                stamp = m["time"] if TIME_FMT.match(m["time"]) else None
                first = first or stamp
                last = stamp or last
                if URL_TOKEN.search(m["path"]):
                    token_lines += 1
                    token_paths[_endpoint(m["path"]) or "other"] += 1
                endpoint = _endpoint(m["path"])
                if endpoint:
                    hits.append({"time": stamp or "unparseable", "ip": _safe_ip(m["ip"]),
                                 "method": m["method"] if m["method"] in METHODS else "other",
                                 "endpoint": endpoint, "status": int(m["status"])})
    by_endpoint = defaultdict(Counter)
    for h in hits:
        by_endpoint[h["endpoint"]][h["status"]] += 1
    return {"parsed_lines": lines, "covers_from": first, "covers_to": last,
            "calls": {k: dict(v) for k, v in by_endpoint.items()},
            "successful_calls": [h for h in hits if h["status"] == 200],
            "lines_with_token_in_url": token_lines,
            "token_in_url_by_endpoint": dict(token_paths),
            "note": "200 = 有回傳日誌內容；存取日誌沒有使用者身分。lines_with_token_in_url > 0 代表 token 原文曾被寫進存取日誌，"
                    "應視為已暴露並考慮輪替。涵蓋範圍以外的時間無法判斷。報告不含任何查詢字串或日誌原文。"}


def scan_runtime(paths):
    counts, total, first, last = Counter(), 0, None, None
    projects = set()
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                total += 1
                t = RUNTIME_TIME.match(line)
                if t:
                    first = first or t.group(1)
                    last = t.group(1)
                for name, rx in CATEGORIES.items():
                    if rx.search(line):
                        counts[name] += 1
                for m in re.finditer(r"(?i)\bpid[=:\s'\"]+([A-Za-z0-9_-]{1,20})", line):
                    projects.add(m.group(1).lower().removesuffix("-p"))
    return {"lines": total, "covers_from": first, "covers_to": last, "category_lines": dict(counts),
            "distinct_projects_mentioned": len(projects),
            "note": "只有類別計數，沒有任何原文樣本。runtime_log_tail 每次最多回傳尾端 400 行；被讀到的是呼叫當時的尾端。"}


def scan_manuscript(data_root):
    """已存稿件（章節版本、草稿、主論文版本）中帶 token 參數的網址：只計數，不輸出值、路徑也只給專案數。"""
    from pathlib import Path
    files = hits = 0
    projects = set()
    for path in Path(data_root).glob("*/manuscript/**/*.json"):
        files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = len(URL_TOKEN.findall(text))
        if n:
            hits += n
            projects.add(path.relative_to(data_root).parts[0])
    return {"json_files_scanned": files, "token_params_found": hits, "projects_affected": len(projects),
            "note": "token_params_found > 0：舊前端把 token 寫進了稿件 HTML（<img src>），版本檔、匯出與協作者都可能看過；"
                    "應視為已暴露並考慮輪替。修補後新存檔會去除，讀取舊版時也會去除，但舊檔本身未改寫（需 Owner 授權才清理）。"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="唯讀的日誌暴露調查；報告不含日誌原文。")
    ap.add_argument("--access", nargs="*", default=[])
    ap.add_argument("--runtime", nargs="*", default=[])
    ap.add_argument("--manuscript-data", help="data 根目錄副本；統計稿件檔中帶 token 的網址數")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if not args.access and not args.runtime and not args.manuscript_data:
        ap.error("至少給一種日誌")
    report = {"schema": "roothinks.log_exposure_audit/2", "contains_log_text": False, "endpoints": ENDPOINTS,
              "access": scan_access(args.access) if args.access else "NOT PROVIDED — 無法判斷是否被呼叫",
              "runtime": scan_runtime(args.runtime) if args.runtime else "NOT PROVIDED — 無法判斷暴露內容",
              "manuscript": scan_manuscript(args.manuscript_data) if args.manuscript_data else "NOT PROVIDED"}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
