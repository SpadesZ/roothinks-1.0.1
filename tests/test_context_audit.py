# 檔案路徑: tests/test_context_audit.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: context audit sidecar regression tests; no external services.

import json

from app.core_pro.manuscript.context_audit import write_context_audit


def test_context_audit_file_is_written(tmp_path):
    path = write_context_audit(
        project_id="P1",
        section_id="intro",
        context_items=[{"source_id": "s1", "fingerprint": "fp1", "score": 1.0}],
        prompt="hello",
        data_root=str(tmp_path),
    )
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["injected_context_ids"] == ["s1"]
    assert payload["injected_context_fingerprints"] == ["fp1"]
    assert "api_key" not in json.dumps(payload).lower()
