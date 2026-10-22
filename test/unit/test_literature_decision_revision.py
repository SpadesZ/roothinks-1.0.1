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


def test_pdf_identity_is_explicit_and_versions_require_new_confirmation(tmp_path):
    library = LiteratureLibrary(str(tmp_path))
    pdf = library.register_upload('P-p', 'paper', 'Same title.pdf', source_sha256='a'*64, actor='researcher')
    library.merge_candidates('P-p', [{'title':'Same title','doi':'10.1234/one','source':'crossref'}])
    target = next(e for e in library.list_entries('P-p') if e.get('doi'))
    assert pdf['entry_id'] != target['entry_id']
    assert pdf['screening_status'] == 'candidate'
    linked = library.update_entry('P-p', target['entry_id'], {'paper_id':'paper'}, actor='researcher', base_rev=0)
    entries = library.load('P-p')['entries']
    assert entries[pdf['entry_id']]['linked_to'] == target['entry_id']
    assert linked['paper_id'] == 'paper'
    assert pdf['entry_id'] in linked['source_entry_ids']
    assert linked['uploaded_sources'][0]['sha256'] == 'a'*64
    confirmed = library.update_entry('P-p', target['entry_id'], {'screening_status':'included','screening_note':'Only calibrated data'}, actor='professor', base_rev=linked['_rev'])
    library.register_upload('P-p', 'paper_2', 'Same title.pdf', source_sha256='b'*64, actor='researcher')
    new = library.update_entry('P-p', target['entry_id'], {'paper_id':'paper_2'}, actor='researcher', base_rev=confirmed['_rev'])
    assert new['screening_status'] == 'candidate'
    assert new['paper_ids'] == ['paper', 'paper_2']
    assert any(h['previous'].get('screening_decided_by') == 'professor' for h in new['history'])
    assert new['last_edited_by'] == 'researcher'
    assert 'screening_decided_by' not in new


def test_note_edit_requires_reconfirmation_and_fingerprint_changes_same_second(tmp_path, monkeypatch):
    from app.core_pro.manuscript.coc_producers import load_literature_context
    monkeypatch.setattr('app.services.literature_library._now_iso', lambda:'2026-10-23T00:00:00Z')
    library = LiteratureLibrary(str(tmp_path))
    library.merge_candidates('P-p', [{'title':'Human decision'}])
    eid = next(iter(library.load('P-p')['entries']))
    one = library.update_entry('P-p', eid, {'screening_status':'included','screening_note':'reason one'}, actor='professor', base_rev=0)
    text, items, _ = load_literature_context('P-p', data_root=str(tmp_path), budget_tokens=2000)
    assert 'reason one' in text
    library.update_entry('P-p', eid, {'screening_status':'included','screening_note':'reason two'}, actor='professor', base_rev=one['_rev'])
    text2, items2, _ = load_literature_context('P-p', data_root=str(tmp_path), budget_tokens=2000)
    assert items[0]['fingerprint'] != items2[0]['fingerprint']
    assert 'reason two' in text2
    changed = library.update_entry('P-p', eid, {'reading_note':'unconfirmed new limitation'}, actor='researcher', base_rev=2)
    assert changed['screening_status'] == 'candidate'
    assert 'unconfirmed new limitation' not in load_literature_context('P-p', data_root=str(tmp_path), budget_tokens=2000)[0]


def test_pdf_cannot_be_attached_to_two_identities_or_skip_version_review(tmp_path):
    library = LiteratureLibrary(str(tmp_path))
    library.register_upload('P-p','pdf1','first.pdf',source_sha256='a'*64)
    library.merge_candidates('P-p',[{'title':'A','doi':'10.1234/a'},{'title':'B','doi':'10.1234/b'}])
    targets = [e for e in library.list_entries('P-p') if e.get('doi')]
    a = library.update_entry('P-p', targets[0]['entry_id'], {'paper_id':'pdf1'}, actor='student', base_rev=0)
    with pytest.raises(ValueError):
        library.update_entry('P-p', targets[1]['entry_id'], {'paper_id':'pdf1','screening_status':'included'}, actor='student', base_rev=0)
    library.register_upload('P-p','pdf2','second.pdf',source_sha256='b'*64)
    with pytest.raises(ValueError):
        library.update_entry('P-p', a['entry_id'], {'paper_id':'pdf2','screening_status':'included'}, actor='student', base_rev=a['_rev'])
    assert library.load('P-p')['entries'][a['entry_id']]['paper_id']=='pdf1'
