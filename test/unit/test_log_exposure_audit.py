# Roothinks source maintenance contract
# 目的：歷史暴露調查工具（scripts/security/log_exposure_audit.py）本身安全且正確。
#   - 認得 gunicorn 存取日誌中的受影響端點，回報涵蓋時間範圍與「網址帶 token」的行數。
#   - 對抗性：報告不得含任何日誌原文——token（查詢字串、標頭、路徑、IP 欄位）、email、提示詞、標題都不得出現。
#     秘密值每次執行隨機產生，並在報告 JSON 全文搜尋。
#   - 缺少日誌時回報 NOT PROVIDED，不當作「沒事」。
# 驗證：python -m pytest test/unit/test_log_exposure_audit.py -q
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.security import log_exposure_audit as audit


def _secrets():
    return {k: f"{k.upper()}_{secrets.token_hex(12)}" for k in
            ("url_token", "url_access", "path_secret", "ip_secret", "ua_secret", "bearer", "api", "prompt", "title", "email")}


def _write(tmp_path, s):
    access = tmp_path / "access.log"
    access.write_text(
        '10.0.0.1 - - [01/Jul/2026:10:00:00 +0000] "GET /api/literature/library?pid=A HTTP/1.1" 200 10 "-" "ua"\n'
        f'10.0.0.2 - - [02/Jul/2026:10:00:00 +0000] "GET /api/literature/debug/runtime_log_tail?lines=400&access_token={s["url_access"]} HTTP/1.1" 200 99 "-" "{s["ua_secret"]}"\n'
        f'10.0.0.3 - - [03/Jul/2026:10:00:00 +0000] "GET /api/literature/debug/flowb_probe?pid=B&paper_id=p&token={s["url_token"]} HTTP/1.1" 403 9 "-" "ua"\n'
        f'10.0.0.4 - - [03/Jul/2026:11:00:00 +0000] "GET /api/literature/debug/runtime_log_tail/{s["path_secret"]} HTTP/1.1" 200 9 "-" "ua"\n'
        f'{s["ip_secret"]} - - [04/Jul/2026:10:00:00 +0000] "GET /api/literature/debug/runtime_log_tail HTTP/1.1" 200 9 "-" "ua"\n'
        f'10.0.0.5 - - [05/Jul/2026:10:00:00 +0000] "GET /manuscript/image/P-p/fig.png?access_token={s["url_access"]} HTTP/1.1" 200 9 "-" "ua"\n',
        encoding="utf-8")
    runtime = tmp_path / "runtime.log"
    runtime.write_text(
        f"2026-07-01 10:00:00,000 [INFO] 1/Main x: [Flow B] run_translation pid=ABC-p user {s['email']}@example.org\n"
        f"2026-07-01 10:00:01,000 [INFO] 1/Main x: auth header Bearer {s['bearer']} pid=XYZ\n"
        f"2026-07-01 10:00:02,000 [INFO] 1/Main x: GET /x?access_token={s['url_access']}\n"
        f"2026-07-01 10:00:03,000 [INFO] 1/Main x: api_key={s['api']}\n"
        f"2026-07-01 10:00:04,000 [INFO] 1/Main x: [Semantic Router] prompt {s['prompt']} title={s['title']}.pdf\n",
        encoding="utf-8")
    return access, runtime


def test_report_contains_no_log_text_at_all(tmp_path):
    s = _secrets()
    access, runtime = _write(tmp_path, s)
    out = tmp_path / "r.json"
    report = audit.main(["--access", str(access), "--runtime", str(runtime), "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    leaked = [k for k, v in s.items() if v in text]
    assert leaked == [], leaked
    assert "access_token=" not in text and "token=" not in text and "example.org" not in text
    assert report["contains_log_text"] is False


def test_access_findings_are_metadata_only(tmp_path):
    s = _secrets()
    access, runtime = _write(tmp_path, s)
    report = audit.main(["--access", str(access), "--runtime", str(runtime), "--out", str(tmp_path / "r.json")])
    acc = report["access"]
    assert acc["calls"] == {"/api/literature/debug/runtime_log_tail": {200: 3}, "/api/literature/debug/flowb_probe": {403: 1}}
    assert [c["ip"] for c in acc["successful_calls"]] == ["10.0.0.2", "10.0.0.4", "unparseable"]
    assert {c["endpoint"] for c in acc["successful_calls"]} == {"/api/literature/debug/runtime_log_tail"}
    assert acc["lines_with_token_in_url"] == 3 and acc["token_in_url_by_endpoint"]["other"] == 1
    assert acc["covers_from"] == "01/Jul/2026:10:00:00 +0000"
    rt = report["runtime"]
    assert rt["category_lines"]["email"] == 1 and rt["category_lines"]["credential_like"] >= 3
    assert rt["category_lines"]["url_token_param"] == 1 and rt["distinct_projects_mentioned"] == 2
    assert set(rt) == {"lines", "covers_from", "covers_to", "category_lines", "distinct_projects_mentioned", "note"}


def test_missing_logs_are_reported_as_unknown_not_clean(tmp_path):
    runtime = tmp_path / "runtime.log"
    runtime.write_text("2026-07-01 10:00:00,000 x\n", encoding="utf-8")
    report = audit.main(["--runtime", str(runtime), "--out", str(tmp_path / "r.json")])
    assert report["access"].startswith("NOT PROVIDED")


def test_manuscript_token_scan_counts_without_values(tmp_path):
    secret = "MS_" + secrets.token_hex(10)
    block = tmp_path / "data" / "P1-p" / "manuscript" / "block" / "intro"
    block.mkdir(parents=True)
    (block / "V0.1.json").write_text(json.dumps({"content": f'<img src="/manuscript/image/P1-p/a.png?access_token={secret}">'}), encoding="utf-8")
    (block / "V0.2.json").write_text(json.dumps({"content": "<p>clean</p>"}), encoding="utf-8")
    out = tmp_path / "r.json"
    report = audit.main(["--manuscript-data", str(tmp_path / "data"), "--out", str(out)])
    assert report["manuscript"]["token_params_found"] == 1 and report["manuscript"]["projects_affected"] == 1
    assert secret not in out.read_text(encoding="utf-8") and "P1-p" not in out.read_text(encoding="utf-8")
