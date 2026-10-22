# Roothinks source maintenance contract
# 目的：篩選決策與閱讀理由的舊版本不得覆蓋較新判斷；只用 tmp。
# 驗證：python -m pytest test/unit/test_literature_decision_revision.py -q
import pytest
from werkzeug.exceptions import Conflict

from app.services.literature_library import LiteratureLibrary


def test_stale_decision_preserves_confirmed_author_and_reason(tmp_path):
    library = LiteratureLibrary(str(tmp_path))
    library.merge_candidates('P-p', [{"title": "Synthetic paper", "doi": "10.1234/synthetic"}])
    entry_id = next(iter(library.load('P-p')['entries']))
    entry = library.update_entry('P-p', entry_id, {"screening_status": "included", "screening_note": "human reason"}, actor="alice", base_rev=0)
    assert entry['_rev'] == 1
    with pytest.raises(Conflict):
        library.update_entry('P-p', entry_id, {"screening_status": "excluded", "screening_note": "old tab"}, actor="bob", base_rev=0)
    current = library.load('P-p')['entries'][entry_id]
    assert current['screening_status'] == 'included'
    assert current['screening_decided_by'] == 'alice'
    assert current['screening_note'] == 'human reason'
