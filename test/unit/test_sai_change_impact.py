# 目的：來源變更可沿證據／主張／版本／審閱追蹤；重新確認追加歷史，不替作者或教授作決定。
# 邊界：合成來源、真實 API 與保存路徑；限定共同作者不能列舉未授權來源。
# 驗證：python -m pytest test/unit/test_sai_change_impact.py -q
import pytest
from test_sai_channel_acl import channel_app,client,PID,MINE,HIDDEN,PUBLIC
from test_sai_review_workflow import workflow,confirm


@pytest.fixture(autouse=True)
def isolated_decision_ledger(channel_app):
    # channel_app 是模組 fixture；每例只還原本身的暫存 ledger，避免上一例的已刪合成證據污染下一例。
    path = channel_app[2]/PID/'manuscript/citation_ledger.json'
    original = path.read_bytes()
    yield
    path.write_bytes(original)


def payload(workflow, **changes):
    _,entry,sid = workflow
    data = dict(pid=PID,section=MINE,s_ver='0.1',paragraph_text=PUBLIC,purpose='Explain measurement',
        claim='Only sample A improved',conditions='300 K; same calibration',forbidden='No other samples',
        entry_id=entry['entry_id'],evidence_id=sid,confirmed=True,change_reason='重新核對來源及使用界線')
    data.update(changes)
    return data


def report(workflow,actor='editor'):
    return client(workflow[0],actor).get(f'/manuscript/api/change-impact?pid={PID}')


def test_changed_source_propagates_to_claim_paper_and_professor_review(workflow):
    from app import db
    from app.models import EvidenceSegment
    bundle,_,sid = workflow
    decision = confirm(workflow).json['decision']
    review = client(bundle,'owner').post(f'/api/mentor/mentees/{bundle[1]["editor"]}/reviews/{PID}/items',
        json={'kind':'comment','paper_version':'V1','body':'Check measurement','decision_id':decision['decision_id']})
    assert review.status_code == 201,review.json
    initial = report(workflow)
    assert initial.status_code == 200,initial.json
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text = 'Revised source: only sample A at 300 K, unchanged reference plane.'
        db.session.commit()
    changed = report(workflow)
    assert changed.status_code == 200,changed.json
    data = changed.json
    claim = next(c for c in data['claims'] if c['decision_id']==decision['decision_id'])
    assert claim['status'] == 'needs_reconfirmation' and claim['issues']
    assert next(p for p in data['papers'] if p['version']=='V1')['status'] == 'needs_reconfirmation'
    assert next(r for r in data['reviews'] if r['id']==review.json['item']['comment']['id'])['status'] == 'needs_reconfirmation'
    assert data['edges'] and data['snapshot'] != initial.json['snapshot']


def test_author_reconfirmation_is_explicit_append_only_and_invalidates_old_choice(workflow):
    from app import db
    from app.models import EvidenceSegment
    from app.core_pro.manuscript.citation_ledger import list_decisions
    from app.core_pro.manuscript.paragraph_context import validate_context
    from werkzeug.exceptions import Conflict
    bundle,_,sid = workflow
    old = confirm(workflow).json['decision']
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text = 'Updated evidence at 300 K, sample A only.'
        db.session.commit()
    current = report(workflow)
    assert current.status_code == 200,current.json
    endpoint = f'/manuscript/api/paragraph/{old["decision_id"]}/reconfirm'
    data = payload(workflow,impact_snapshot=current.json['snapshot'])
    denied = client(bundle,'editor').post(endpoint,json=data | {'confirmed':False})
    assert denied.status_code == 400
    saved = client(bundle,'editor').post(endpoint,json=data)
    assert saved.status_code == 201,saved.json
    new = saved.json['decision']
    assert new['paragraph_context']['supersedes'] == old['decision_id']
    assert new['paragraph_context']['change_reason'] == data['change_reason']
    with bundle[0].app_context():
        rows = list_decisions(str(bundle[2]),PID)
        preserved = next(r for r in rows if r['decision_id']==old['decision_id'])
        assert preserved == old
        assert validate_context(str(bundle[2]),PID,new)['conditions'] == data['conditions']
        with pytest.raises(Conflict):
            validate_context(str(bundle[2]),PID,old)
    after = report(workflow).json
    assert next(c for c in after['claims'] if c['decision_id']==old['decision_id'])['status'] == 'superseded'
    assert next(c for c in after['claims'] if c['decision_id']==new['decision_id'])['status'] == 'current'
    assert next(p for p in after['papers'] if p['version']=='V1')['status'] == 'needs_reconfirmation', '作者確認新主張不等於自動核准舊稿'


def test_stale_impact_snapshot_is_not_silently_reconfirmed(workflow):
    from app import db
    from app.models import EvidenceSegment
    old = confirm(workflow).json['decision']
    snapshot = report(workflow)
    assert snapshot.status_code == 200,snapshot.json
    with workflow[0][0].app_context():
        db.session.get(EvidenceSegment,workflow[2]).text += ' Subsequent source change.'
        db.session.commit()
    response = client(workflow[0],'editor').post(f'/manuscript/api/paragraph/{old["decision_id"]}/reconfirm',
        json=payload(workflow,impact_snapshot=snapshot.json['snapshot']))
    assert response.status_code == 409,response.json


def test_new_paper_records_decision_dependencies_without_rewriting_old_version(workflow):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    old = confirm(workflow).json['decision']
    with workflow[0][0].app_context():
        original = ManuscriptIO.load_paper_version(PID,'V1')
        saved = ManuscriptIO.save_paper_version(PID,'Synthetic',f'<p>{PUBLIC}</p>',sections={MINE:'0.1'})
        loaded = ManuscriptIO.load_paper_version(PID,saved['g_ver'])
        assert old['decision_id'] in loaded['context_decisions']
        assert ManuscriptIO.load_paper_version(PID,'V1') == original


@pytest.fixture
def private_source(workflow):
    from app import db
    from app.models import EvidenceSegment
    from app.services.literature_library import LiteratureLibrary
    bundle,_,_ = workflow
    with bundle[0].app_context():
        library = LiteratureLibrary(str(bundle[2]))
        library.merge_candidates(PID,[{'title':'Unassigned private source','doi':'10.1234/hidden-context'}])
        entry = next(e for e in library.list_entries(PID) if e.get('doi')=='10.1234/hidden-context')
        entry = library.update_entry(PID,entry['entry_id'],{'paper_id':'hidden-source','screening_status':'included',
            'screening_note':'PRIVATE_UNASSIGNED_SOURCE','reading_note':'PRIVATE_UNASSIGNED_SOURCE'},actor='owner',base_rev=entry.get('_rev',0))
        segment = EvidenceSegment(project_id=PID,paper_id='hidden-source',source_id='hidden',source_type='paper_segment',
            segment_id='hidden',text='PRIVATE_UNASSIGNED_SOURCE',content_hash='synthetic-private',metadata_json='{"page":9}')
        db.session.add(segment)
        db.session.commit()
        sid = segment.id
    yield entry,sid
    with bundle[0].app_context():
        db.session.delete(db.session.get(EvidenceSegment,sid))
        db.session.commit()


def test_scoped_source_picker_never_discloses_unassigned_library(workflow,private_source):
    response = client(workflow[0],'coauthor').get(f'/manuscript/api/paragraph/context?pid={PID}&section={MINE}')
    assert response.status_code == 200,response.json
    assert 'PRIVATE_UNASSIGNED_SOURCE' not in response.get_data(as_text=True)


def test_scoped_author_cannot_guess_unassigned_evidence(workflow,private_source):
    entry,sid = private_source
    response = client(workflow[0],'coauthor').post('/manuscript/api/paragraph/confirm',json=payload(workflow,
        entry_id=entry['entry_id'],evidence_id=sid))
    assert response.status_code == 403,response.json
    assert 'PRIVATE_UNASSIGNED_SOURCE' not in response.get_data(as_text=True)


def test_scoped_author_can_use_newly_reconfirmed_evidence_after_source_change(workflow):
    from app import db
    from app.models import EvidenceSegment
    bundle,_,sid = workflow
    old = confirm(workflow).json['decision']
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text = 'Corrected source: sample A at 300 K only.'
        db.session.commit()
    assert client(bundle,'coauthor').post('/manuscript/api/paragraph/confirm',json=payload(workflow)).status_code == 403
    fresh = report(workflow).json
    saved = client(bundle,'editor').post(f'/manuscript/api/paragraph/{old["decision_id"]}/reconfirm',
        json=payload(workflow,impact_snapshot=fresh['snapshot']))
    assert saved.status_code == 201,saved.json
    response = client(bundle,'coauthor').post('/manuscript/api/paragraph/confirm',json=payload(workflow))
    assert response.status_code == 201,response.json


def test_impact_report_and_reconfirmation_keep_project_acl(workflow):
    assert report(workflow,'outsider').status_code == 403
    old = confirm(workflow).json['decision']
    response = client(workflow[0],'viewer').post(f'/manuscript/api/paragraph/{old["decision_id"]}/reconfirm',
        json=payload(workflow,impact_snapshot='forged'))
    assert response.status_code == 403,response.json


def test_paper_and_original_professor_reconfirm_separately_then_become_stale_again(workflow):
    from app import db
    from app.models import EvidenceSegment
    bundle,_,sid = workflow
    old = confirm(workflow).json['decision']
    review = client(bundle,'owner').post(f'/api/mentor/mentees/{bundle[1]["editor"]}/reviews/{PID}/items',
        json={'kind':'comment','paper_version':'V1','body':'Check conditions','decision_id':old['decision_id']})
    assert review.status_code == 201,review.json
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text = 'Updated source remains limited to sample A at 300 K.'
        db.session.commit()
    before = report(workflow).json
    saved = client(bundle,'editor').post(f'/manuscript/api/paragraph/{old["decision_id"]}/reconfirm',
        json=payload(workflow,impact_snapshot=before['snapshot']))
    assert saved.status_code == 201,saved.json
    did = saved.json['decision']['decision_id']
    endpoint = '/manuscript/api/change-impact/reconfirm'
    fresh = report(workflow).json
    paper_data = dict(pid=PID,kind='paper',target_id='V1',confirmed=True,reason='已核對相同段落與新來源',
        claim_ids=[did],impact_snapshot=fresh['snapshot'])
    assert client(bundle,'viewer').post(endpoint,json=paper_data).status_code == 403
    assert client(bundle,'editor').post(endpoint,json=paper_data | {'confirmed':False}).status_code == 400
    assert client(bundle,'editor').post(endpoint,json=paper_data | {'claim_ids':[old['decision_id']]}).status_code == 409
    response = client(bundle,'editor').post(endpoint,json=paper_data)
    assert response.status_code == 201,response.json
    current = report(workflow).json
    paper = next(p for p in current['papers'] if p['version']=='V1')
    assert paper['status'] in {'reconfirmed','partially_reconfirmed'}
    cid = review.json['item']['comment']['id']
    assert next(r for r in current['reviews'] if r['id']==cid)['status'] == 'needs_reconfirmation'
    review_data = paper_data | dict(kind='review',target_id=cid,impact_snapshot=current['snapshot'])
    assert client(bundle,'editor').post(endpoint,json=review_data).status_code == 403
    response = client(bundle,'owner').post(endpoint,json=review_data)
    assert response.status_code == 201,response.json
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text += ' Another source correction.'
        db.session.commit()
    again = report(workflow).json
    assert next(p for p in again['papers'] if p['version']=='V1')['status'] == 'needs_reconfirmation'
    assert next(r for r in again['reviews'] if r['id']==cid)['status'] == 'needs_reconfirmation'


def test_new_confirmation_cannot_cover_different_version_or_skip_original_dependency(workflow):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    old = confirm(workflow).json['decision']
    with workflow[0][0].app_context():
        ManuscriptIO.save_block_version(PID,MINE,'Revised',f'<p>{PUBLIC} Revised.</p>')
    current = report(workflow).json
    saved = client(workflow[0],'editor').post(f'/manuscript/api/paragraph/{old["decision_id"]}/reconfirm',
        json=payload(workflow,s_ver='0.2',paragraph_text=PUBLIC+' Revised.',impact_snapshot=current['snapshot']))
    assert saved.status_code == 201,saved.json
    fresh = report(workflow).json
    response = client(workflow[0],'editor').post('/manuscript/api/change-impact/reconfirm',json=dict(
        pid=PID,kind='paper',target_id='V1',confirmed=True,reason='不能把新章節確認套到舊稿',
        claim_ids=[saved.json['decision']['decision_id']],impact_snapshot=fresh['snapshot']))
    assert response.status_code == 409,response.json


def test_original_pdf_change_invalidates_context_even_if_index_text_is_unchanged(workflow):
    import hashlib
    from app.services.literature_library import LiteratureLibrary
    bundle,_,sid = workflow
    with bundle[0].app_context():
        original = bundle[2]/PID/'literature/papers/source/00_origins/source.pdf'
        original.parent.mkdir(parents=True,exist_ok=True)
        original.write_bytes(b'%PDF-1.4\nOriginal synthetic evidence')
        library = LiteratureLibrary(str(bundle[2]))
        entry = library.register_upload(PID,'source','original.pdf',source_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),actor='owner')
        entry = library.update_entry(PID,entry['entry_id'],{'screening_status':'included','screening_note':'Verified original'},
            actor='owner',base_rev=entry.get('_rev',0))
    saved = client(bundle,'editor').post('/manuscript/api/paragraph/confirm',json=payload(workflow,entry_id=entry['entry_id']))
    assert saved.status_code == 201,saved.json
    original.write_bytes(b'%PDF-1.4\nTampered synthetic evidence')
    changed = report(workflow)
    assert changed.status_code == 200,changed.json
    claim = next(c for c in changed.json['claims'] if c['decision_id']==saved.json['decision']['decision_id'])
    assert claim['status']=='needs_reconfirmation'
    assert any('PDF' in issue['message'] for issue in claim['issues'])
