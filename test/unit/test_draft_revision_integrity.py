# Roothinks source maintenance contract
# 目的：同一使用者多分頁草稿 CAS；清除後仍不可重用舊 revision。
# 驗證：python -m pytest test/unit/test_draft_revision_integrity.py -q
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core_pro.manuscript.manuscript_io import ManuscriptIO

PID = "DRAFTREV-p"
SECTION = "introduction"


@pytest.fixture
def drafts(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core_pro.manuscript.manuscript_io._get_data_root", lambda: str(tmp_path))
    return tmp_path


def save(text, revision, owner=17):
    return ManuscriptIO.save_draft(PID, SECTION, "Title", text, owner_key=owner, base_rev=revision)


def test_stale_tab_keeps_first_writer(drafts):
    first = save("First human thought", 0)
    assert first["ok"] and first["rev"] == 1
    stale = save("Old tab", 0)
    assert stale["ok"] is False and stale["conflict"] is True
    assert ManuscriptIO.load_draft(PID, SECTION, owner_key=17)["content"] == "First human thought"
    assert save("Other researcher", 0, owner=18)["ok"]


def test_simultaneous_same_user_tabs_have_one_winner(drafts):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda text: save(text, 0), ["A", "B", "C", "D"]))
    assert sum(result["ok"] for result in results) == 1
    assert sum(bool(result.get("conflict")) for result in results) == 3


def test_clear_preserves_monotonic_revision_and_history(drafts):
    save("Already committed content", 0)
    assert ManuscriptIO.clear_draft(PID, SECTION, owner_key=17)
    assert ManuscriptIO.load_draft(PID, SECTION, owner_key=17) is None
    assert ManuscriptIO.draft_revision(PID, SECTION, owner_key=17) == 2
    assert save("Stale from before clear", 0)["ok"] is False
    assert save("Next edit", 2)["ok"] is True
