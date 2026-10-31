# Roothinks source maintenance contract
# 目的：把 API_QUERY_TOKEN_ALLOWED 從 1 改成 0 前後的驗證（RUNBOOK_QUERY_TOKEN_SHUTOFF.md 的第 3、5 步）。
# 安全規則：
#   - 只用一支**專為驗證建立、驗完立即刪除**的臨時 token，從環境變數 RT_CHECK_TOKEN 讀（不放命令列，避免進 shell 歷史與程序列表）。
#   - 這支臨時 token 會在 query 測試中出現在網址——所以它必須是臨時的；正式用戶端的 token 永遠不要拿來跑本腳本。
#   - 另送一個**假的**金絲雀 token（RTCANARY-…）到網址，讓維運到 gunicorn 與上游代理／負載平衡器日誌搜尋：
#     gunicorn 應只看到 access_token=[REDACTED]；上游日誌若出現 RTCANARY 原文，代表上游記錄了查詢字串，需另行設定遮罩或保留期。
#   - 報告不含 token；金絲雀本身不是憑證，會列在報告中以便搜尋。
# 用法：RT_CHECK_TOKEN=<臨時 token> python scripts/security/query_token_shutoff_check.py --base-url https://host --pid P-p --expect-query 0 --out r.json
import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request


def http_fetch(base_url):
    def fetch(method, path, headers=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310（只連 Owner 指定的站）
                return resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {})
    return fetch


def run_checks(fetch, pid, token, expect_query_allowed):
    canary = "RTCANARY-" + secrets.token_hex(8)
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    status, headers = fetch("POST", "/manuscript/api/image_session", {"Authorization": f"Bearer {token}"}, {"pid": pid})
    cookie = headers.get("Set-Cookie", "")
    add("標頭 token 可用（換圖片票證）", status == 200, {"status": status})
    add("圖片票證 cookie 有 HttpOnly、Secure、SameSite=Strict",
        status == 200 and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Strict" in cookie,
        {"httponly": "HttpOnly" in cookie, "secure": "Secure" in cookie, "samesite_strict": "SameSite=Strict" in cookie})
    # 不存在的探針圖片：token 被接受時通過驗證、找不到檔案 → 404；被拒絕 → 401／403。不需要真的圖片。
    status, _ = fetch("GET", f"/manuscript/image/{pid}/rt-shutoff-probe.png?access_token={token}", {}, None)
    if expect_query_allowed:
        add("網址 token 仍被接受（遷移前）", status in (200, 404), {"status": status})
    else:
        add("網址 token 被拒絕（遷移後）", status in (401, 403), {"status": status})
    status, _ = fetch("GET", f"/manuscript/image/{pid}/rt-canary.png?access_token={canary}", {}, None)
    add("金絲雀請求已送出（到日誌搜尋）", True, {"status": status})
    return {"schema": "roothinks.query_token_shutoff_check/1", "expect_query_allowed": expect_query_allowed,
            "ok": all(c["ok"] for c in checks), "checks": checks, "canary": canary,
            "log_validation": f"在 gunicorn 存取日誌搜尋 {canary}：應找不到原文（只見 access_token=[REDACTED]）。"
                              f"在上游代理／負載平衡器／CDN 日誌搜尋 {canary}：若找到原文，上游會記錄查詢字串，需設定遮罩或縮短保留期。"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--pid", required=True)
    ap.add_argument("--expect-query", choices=("0", "1"), required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    token = os.environ.get("RT_CHECK_TOKEN", "").strip()
    if not token:
        ap.error("請以環境變數 RT_CHECK_TOKEN 提供臨時驗證 token（驗完立即從設定刪除）")
    report = run_checks(http_fetch(args.base_url), args.pid, token, args.expect_query == "1")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"ok": report["ok"], "canary": report["canary"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
