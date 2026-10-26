# Roothinks source maintenance contract
# 目的：安全熱修（跨專案日誌外洩）的歷史暴露調查工具。只讀，不改任何檔案，不送出任何資料。
# 用法（由 Owner／維運在正式環境的「日誌副本」上執行；工程端沒有、也不應取得正式日誌）：
#   python scripts/security/log_exposure_audit.py --access <gunicorn 存取日誌...> --runtime <runtime.log 與輪替檔...> --out report.json
# 兩個問題：
#   1. 有沒有人呼叫過受影響端點？看 gunicorn 存取日誌（Dockerfile 以 --access-logfile - 寫到容器 stdout；
#      GCP 在 Cloud Logging）。存取日誌只有 IP、時間、路徑、狀態碼，沒有使用者；要對應使用者需另查登入紀錄。
#   2. 如果被呼叫，日誌裡有什麼？看 runtime.log（root logger INFO 全部寫入，含所有專案）。
#      報告只輸出「類別計數」與每類最多 3 個遮罩後樣本，不輸出原文，避免報告本身再次外洩。
# 判讀：存取日誌被輪替或保留期不足時，「沒找到呼叫」不代表「沒有發生」；報告會寫出涵蓋的時間範圍。
import argparse
import json
import re
import sys
from collections import Counter, defaultdict

ENDPOINTS = ("/api/literature/debug/runtime_log_tail", "/api/literature/debug/flowb_probe",
             "/api/literature/debug/recover_flowb")
# gunicorn 預設 access_log_format：%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"
ACCESS_RE = re.compile(r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<status>\d{3}) ')
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
CATEGORIES = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "bearer_or_key": re.compile(r"(?i)(bearer\s+[A-Za-z0-9._-]{12,}|sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|api[_-]?key\s*[=:]\s*\S{8,})"),
    "pid": re.compile(r"(?i)\bpid[=:\s'\"]+([A-Za-z0-9_-]{1,20})"),
    "server_path": re.compile(r"(/[A-Za-z0-9._-]+){3,}"),
    "prompt_fragment": re.compile(r"(?i)(semantic router|prompt|query)[^\n]{0,40}"),
    "paper_title_or_file": re.compile(r"(?i)(title|filename|\.pdf)"),
}


def mask(text):
    text = CATEGORIES["email"].sub(lambda m: m.group(0)[:2] + "***@***", text)
    text = CATEGORIES["bearer_or_key"].sub("***KEY***", text)
    return text[:160]


def scan_access(paths):
    hits, first, last, lines = [], None, None, 0
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = ACCESS_RE.match(line)
                if not m:
                    continue
                lines += 1
                first = first or m["time"]
                last = m["time"]
                if any(m["path"].startswith(e) for e in ENDPOINTS):
                    hits.append({"time": m["time"], "ip": m["ip"], "method": m["method"],
                                 "endpoint": m["path"].split("?")[0], "status": int(m["status"])})
    by_endpoint = defaultdict(Counter)
    for h in hits:
        by_endpoint[h["endpoint"]][h["status"]] += 1
    return {"parsed_lines": lines, "covers_from": first, "covers_to": last,
            "calls": {k: dict(v) for k, v in by_endpoint.items()},
            "successful_calls": [h for h in hits if h["status"] == 200],
            "note": "200 = 有回傳日誌內容；access 日誌沒有使用者身分。涵蓋範圍以外的時間無法判斷。"}


def scan_runtime(paths):
    counts, samples, pids, total, first, last = Counter(), defaultdict(list), Counter(), 0, None, None
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                total += 1
                t = TIME_RE.match(line)
                if t:
                    first = first or t.group(1)
                    last = t.group(1)
                for name, rx in CATEGORIES.items():
                    if rx.search(line):
                        counts[name] += 1
                        if len(samples[name]) < 3:
                            samples[name].append(mask(line.strip()))
                for m in CATEGORIES["pid"].finditer(line):
                    pids[m.group(1).lower().removesuffix("-p")] += 1
    return {"lines": total, "covers_from": first, "covers_to": last, "category_lines": dict(counts),
            "distinct_projects_mentioned": len(pids), "masked_samples": dict(samples),
            "note": "runtime_log_tail 每次最多回傳尾端 400 行；被讀到的是呼叫當時的尾端，不是整份檔案。"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--access", nargs="*", default=[])
    ap.add_argument("--runtime", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if not args.access and not args.runtime:
        ap.error("至少給一種日誌")
    report = {"endpoints": ENDPOINTS,
              "access": scan_access(args.access) if args.access else "NOT PROVIDED — 無法判斷是否被呼叫",
              "runtime": scan_runtime(args.runtime) if args.runtime else "NOT PROVIDED — 無法判斷暴露內容"}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
