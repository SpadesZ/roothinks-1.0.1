# Roothinks source maintenance contract
# 目的：歷史暴露調查工具（scripts/security/log_exposure_audit.py）本身正確：認得 gunicorn 存取日誌中的受影響端點、
#       報告涵蓋時間範圍、runtime 日誌只輸出類別計數與遮罩樣本（不把 email 或金鑰原文寫進報告）。
# 驗證：python -m pytest test/unit/test_log_exposure_audit.py -q
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.security import log_exposure_audit as audit


FAKE_KEY = "sk-" + "fake" * 5  # 執行時組出，避免字面金鑰觸發秘密掃描


def test_access_hits_runtime_categories_and_masking(tmp_path):
    access = tmp_path / "access.log"
    access.write_text(
        '10.0.0.1 - - [01/Jul/2026:10:00:00 +0000] "GET /api/literature/library?pid=A HTTP/1.1" 200 10 "-" "ua"\n'
        '10.0.0.2 - - [02/Jul/2026:10:00:00 +0000] "GET /api/literature/debug/runtime_log_tail?lines=400 HTTP/1.1" 200 99 "-" "ua"\n'
        '10.0.0.3 - - [03/Jul/2026:10:00:00 +0000] "GET /api/literature/debug/flowb_probe?pid=B&paper_id=p HTTP/1.1" 403 9 "-" "ua"\n',
        encoding="utf-8")
    runtime = tmp_path / "runtime.log"
    runtime.write_text(
        "2026-07-01 10:00:00,000 [INFO] 1/Main x: [Flow B] run_translation pid=ABC-p user alice@example.org\n"
        f"2026-07-01 10:00:01,000 [INFO] 1/Main x: key api_key={FAKE_KEY} pid=XYZ\n",
        encoding="utf-8")
    out = tmp_path / "r.json"
    report = audit.main(["--access", str(access), "--runtime", str(runtime), "--out", str(out)])
    assert report["access"]["calls"] == {"/api/literature/debug/runtime_log_tail": {200: 1},
                                         "/api/literature/debug/flowb_probe": {403: 1}}
    assert [c["ip"] for c in report["access"]["successful_calls"]] == ["10.0.0.2"]
    assert report["access"]["covers_from"].startswith("01/Jul/2026")
    assert report["runtime"]["category_lines"]["email"] == 1 and report["runtime"]["distinct_projects_mentioned"] == 2
    text = out.read_text(encoding="utf-8")
    assert "alice@example.org" not in text and FAKE_KEY not in text
    assert json.loads(text)["runtime"]["lines"] == 2


def test_missing_logs_are_reported_as_unknown_not_clean(tmp_path):
    runtime = tmp_path / "runtime.log"
    runtime.write_text("2026-07-01 10:00:00,000 x\n", encoding="utf-8")
    report = audit.main(["--runtime", str(runtime), "--out", str(tmp_path / "r.json")])
    assert report["access"].startswith("NOT PROVIDED")
