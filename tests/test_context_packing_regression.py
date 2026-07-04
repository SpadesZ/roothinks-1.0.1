# 檔案路徑: tests/test_context_packing_regression.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: context packing budget/fingerprint tests; catch silent truncation regressions.

from app.core_pro.manuscript.context_inject import context_pack_fingerprint, retrieve_paragraph_context
from app.services.context_chain_service import ContextChainService


def _svc(tmp_path, text="alpha beta gamma"):
    svc = ContextChainService(data_root=str(tmp_path))
    chain = svc._empty_chain("P1")
    chain["layers"]["L2"]["summary_packets"] = [
        {"packet_id": "a", "kind": "fact", "text": text, "trace": {"paper_id": "PAPER-A"}},
        {"packet_id": "b", "kind": "fact", "text": "delta epsilon", "trace": {"paper_id": "PAPER-B"}},
    ]
    svc.save_chain("P1", chain)
    return svc


def test_same_input_stable_sorting_and_fingerprint(tmp_path):
    svc = _svc(tmp_path)
    a = retrieve_paragraph_context("P1", "alpha beta", context_chain_service=svc)
    b = retrieve_paragraph_context("P1", "alpha beta", context_chain_service=svc)
    assert [x["source_id"] for x in a] == [x["source_id"] for x in b]
    assert context_pack_fingerprint(a) == context_pack_fingerprint(b)


def test_keyword_overlap_ranks_higher_and_provenance_kept(tmp_path):
    svc = _svc(tmp_path)
    items = retrieve_paragraph_context("P1", "alpha beta", context_chain_service=svc)
    assert items[0]["source_id"] == "a"
    assert items[0]["paper_id"] == "PAPER-A"


def test_fingerprint_changes_when_context_changes(tmp_path):
    svc1 = _svc(tmp_path / "one", text="alpha beta gamma")
    svc2 = _svc(tmp_path / "two", text="alpha beta changed")
    a = retrieve_paragraph_context("P1", "alpha beta", context_chain_service=svc1)
    b = retrieve_paragraph_context("P1", "alpha beta", context_chain_service=svc2)
    assert context_pack_fingerprint(a) != context_pack_fingerprint(b)
