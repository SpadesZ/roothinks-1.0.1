# 檔案路徑: tests/test_context_chain_schema.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: context_chain schema migration regression tests; preserve old payload fixtures.

import json

from app.services.context_chain_service import ContextChainService, CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION


def test_old_context_without_schema_version_upgrades(tmp_path):
    svc = ContextChainService(data_root=str(tmp_path))
    path = tmp_path / "P1" / "context_chain.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"project_id": "P1", "version": 0, "layers": {}, "provenance_ledger": []}), encoding="utf-8")

    chain = svc.load_chain("P1")

    assert chain["schema_version"] == CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION


def test_new_context_writes_schema_version(tmp_path):
    svc = ContextChainService(data_root=str(tmp_path))
    chain = svc._empty_chain("P1")
    svc.save_chain("P1", chain)
    saved = json.loads((tmp_path / "P1" / "context_chain.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == CURRENT_CONTEXT_CHAIN_SCHEMA_VERSION


def test_corrupted_context_returns_failed_state(tmp_path):
    svc = ContextChainService(data_root=str(tmp_path))
    path = tmp_path / "P1" / "context_chain.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")

    chain = svc.load_chain("P1")

    assert chain["load_status"] == "failed"
    assert chain["error_code"] == "CONTEXT_CHAIN_INVALID"


def test_l2_max_packets_reads_default_config(monkeypatch):
    monkeypatch.delenv("CONTEXT_CHAIN_L2_MAX_PACKETS", raising=False)
    assert ContextChainService()._MAX_L2_PACKETS == 8
