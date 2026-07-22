# 檔案路徑: tests/test_citation_ledger.py
# 產生時間: 2026-07-19
# 維護提醒: citation decision sidecar——人確認才記錄、可回溯章節/主張/證據。

import pytest

from app.core_pro.manuscript.citation_ledger import list_decisions, record_decision


def test_record_and_query_decision(tmp_path):
    root = str(tmp_path)
    decision = record_decision(
        root,
        "P1",
        section_id="introduction",
        status="accepted",
        paper_id="paper-1",
        claim_text="insulin resistance has been reported",
        snippet="...evidence snippet...",
        segment_ids=["sec-1"],
    )
    assert decision["status"] == "accepted"
    assert decision["decided_at"]

    all_rows = list_decisions(root, "P1")
    assert len(all_rows) == 1
    assert all_rows[0]["section_id"] == "introduction"
    assert all_rows[0]["paper_id"] == "paper-1"


def test_filter_by_section_and_paper(tmp_path):
    root = str(tmp_path)
    record_decision(root, "P1", section_id="intro", status="accepted", paper_id="paper-1")
    record_decision(root, "P1", section_id="methods", status="rejected", paper_id="paper-2")

    assert len(list_decisions(root, "P1", section_id="intro")) == 1
    assert len(list_decisions(root, "P1", paper_id="paper-2")) == 1
    assert list_decisions(root, "P1", section_id="methods")[0]["status"] == "rejected"


def test_invalid_status_rejected(tmp_path):
    with pytest.raises(ValueError):
        record_decision(str(tmp_path), "P1", section_id="intro", status="maybe", paper_id="paper-1")


def test_requires_section_and_identity(tmp_path):
    root = str(tmp_path)
    with pytest.raises(ValueError):
        record_decision(root, "P1", section_id="", status="accepted", paper_id="paper-1")
    with pytest.raises(ValueError):
        record_decision(root, "P1", section_id="intro", status="accepted")


def test_append_only_accumulates(tmp_path):
    root = str(tmp_path)
    for i in range(3):
        record_decision(root, "P1", section_id="intro", status="accepted", paper_id=f"paper-{i}")
    assert len(list_decisions(root, "P1")) == 3


def test_empty_when_no_ledger(tmp_path):
    assert list_decisions(str(tmp_path), "P1") == []
