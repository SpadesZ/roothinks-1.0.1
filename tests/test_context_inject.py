# 檔案路徑: tests/test_context_inject.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: paragraph-level context injection regression tests; avoid live LLM calls.

from app.core_pro.manuscript.context_inject import build_injected_context_block, retrieve_paragraph_context
from app.services.context_chain_service import ContextChainService


def _seed_chain(tmp_path):
    svc = ContextChainService(data_root=str(tmp_path))
    chain = svc._empty_chain("P1")
    chain["layers"]["L2"]["summary_packets"] = [
        {
            "packet_id": "low",
            "kind": "literature_fact",
            "text": "unrelated nutrition note",
            "trace": {"paper_id": "A"},
        },
        {
            "packet_id": "high",
            "kind": "literature_fact",
            "text": "diabetes insulin resistance cohort mechanism",
            "trace": {"paper_id": "B"},
        },
    ]
    svc.save_chain("P1", chain)
    return svc


def test_retrieve_paragraph_context_returns_ranked_context(tmp_path):
    svc = _seed_chain(tmp_path)
    items = retrieve_paragraph_context(
        "P1",
        "diabetes mechanism",
        paragraph_goal="insulin resistance",
        top_k=2,
        context_chain_service=svc,
    )
    assert items[0]["source_id"] == "high"
    assert items[0]["paper_id"] == "B"
    assert items[0]["estimated_tokens"] > 0


def test_retrieve_respects_top_k_and_max_tokens(tmp_path):
    svc = _seed_chain(tmp_path)
    items = retrieve_paragraph_context("P1", "diabetes insulin resistance", top_k=1, max_tokens=3, context_chain_service=svc)
    assert len(items) == 1
    assert items[0]["estimated_tokens"] <= 3


def test_build_injected_context_block_is_readable(tmp_path):
    svc = _seed_chain(tmp_path)
    items = retrieve_paragraph_context("P1", "diabetes insulin resistance", context_chain_service=svc)
    block = build_injected_context_block(items)
    assert "[Context 1]" in block
    assert "source_type:" in block
    assert "context_pack_fingerprint:" in block
