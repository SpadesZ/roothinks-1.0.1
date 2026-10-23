# Roothinks source maintenance contract
# 目的：真實 API／Socket／Task8，驗作者條件不可被裁切、版本審閱不可越權或遺失。
# 驗證：python -m pytest test/unit/test_sai_review_workflow.py -q
import json
import pytest
from test_sai_channel_acl import channel_app, client, socket, PID, MINE, HIDDEN, PUBLIC


@pytest.fixture
def workflow(channel_app,monkeypatch):
    from app import db
    from app.models import EvidenceSegment, MentorLink, User
    from app.services.literature_library import LiteratureLibrary
    app,ids,root = channel_app
    with app.app_context():
        lib = LiteratureLibrary(str(root))
        lib.merge_candidates(PID,[{'title':'Calibrated synthetic measurement','doi':'10.1234/review'}])
        row = next(e for e in lib.list_entries(PID) if e.get('doi'))
        entry = lib.update_entry(PID,row['entry_id'],{'paper_id':'source','screening_status':'included',
            'screening_note':'Only this sample and calibration','reading_note':'Use measured data only'},
            actor='editor@example.invalid',base_rev=row.get('_rev',0))
        segment = EvidenceSegment(project_id=PID,source_type='paper_segment',source_id='synthetic:seg',
            paper_id='source',segment_id='seg',text='Synthetic result applies only at 300 K.',content_hash='synthetic',
            metadata_json=json.dumps({'page':3,'section':'Results'}))
        db.session.add(segment)
        db.session.get(User,ids['owner']).system_role = 'mentor'
        if not MentorLink.query.filter_by(mentor_id=ids['owner'],mentee_id=ids['editor']).first():
            db.session.add(MentorLink(mentor_id=ids['owner'],mentee_id=ids['editor']))
        db.session.commit()
        segment_id = segment.id
    yield channel_app,entry,segment_id
    with app.app_context():
        db.session.delete(db.session.get(EvidenceSegment,segment_id))
        db.session.commit()


def confirm(workflow,**changes):
    bundle,entry,sid = workflow
    data = {'pid':PID,'section':MINE,'s_ver':'0.1','paragraph_text':PUBLIC,'purpose':'Explain this measurement',
        'claim':'Only the tested sample improved','conditions':'300 K; calibrated reference plane; TAIL_CONDITION_KEEP',
        'forbidden':'Do not generalize to unmeasured samples; TAIL_FORBIDDEN_KEEP',
        'entry_id':entry['entry_id'],'evidence_id':sid,'confirmed':True}
    data.update(changes)
    return client(bundle,'editor').post('/manuscript/api/paragraph/confirm',json=data)


def test_confirmation_keeps_full_conditions_and_server_provenance(workflow):
    response = confirm(workflow,decided_by='fake-professor',conditions='Limits '+('x '*1000)+'TAIL_CONDITION_KEEP')
    assert response.status_code == 201,response.json
    row = response.json['decision']
    assert row['decided_by'] == 'editor@example.invalid'
    contract = row['paragraph_context']
    assert contract['conditions'].endswith('TAIL_CONDITION_KEEP')
    assert contract['evidence']['text'] == 'Synthetic result applies only at 300 K.'
    assert contract['evidence']['locator']['page'] == 3
    assert contract['paragraph_hash'] and contract['evidence']['fingerprint']
    assert 'TAIL_FORBIDDEN_KEEP' in contract['forbidden']


@pytest.mark.parametrize('changes',[{'confirmed':False},{'conditions':''},{'paragraph_text':'not in version'},
    {'s_ver':'99.9'},{'evidence_id':999999},{'section':HIDDEN},{'contradictions':'Measurement temperatures disagree'}])
def test_invalid_or_contradictory_confirmation_is_explicit(workflow,changes):
    response = confirm(workflow,**changes)
    assert response.status_code in {400,409},response.json
    assert response.json.get('message')


def test_confirmed_contract_reaches_provider_and_low_budget_or_changed_source_blocks(workflow,monkeypatch):
    from app import db
    from app.models import EvidenceSegment
    from app.core_pro.manuscript import manuscript_routes as routes
    from app.llm_service.matching_tasks import task_8drafter
    response = confirm(workflow)
    assert response.status_code == 201,response.json
    did = response.json['decision']['decision_id']
    bundle,_,sid = workflow
    captured = []
    class Inline:
        def submit(self,fn,*a,**k): fn(*a,**k)
    monkeypatch.setattr(routes,'_CHAT_EXECUTOR',Inline())
    monkeypatch.setattr(routes.ai_drafter,'_detect_intent',lambda *a,**k:'draft')
    monkeypatch.setattr(task_8drafter,'dispatch_task',lambda task,prompt,**kw:captured.append(str(prompt)) or
        {'ok':True,'text':'{"chat_message":"synthetic","draft_content":"synthetic"}'})
    sio = socket(bundle,'editor')
    payload = dict(pid=PID,section=MINE,title='Synthetic',msg='Draft only this paragraph',paragraph_decision_id=did)
    sio.emit('chat_message',payload,namespace='/manu_ws')
    events = sio.get_received('/manu_ws')
    assert len(captured) == 1,events
    assert all(value in captured[0] for value in ('TAIL_CONDITION_KEEP','TAIL_FORBIDDEN_KEEP','300 K.'))
    assert any(e['name']=='context_manifest' for e in events)
    from app.core_pro.manuscript.context_audit import prompt_fingerprint
    audits = [json.loads(p.read_text(encoding='utf-8')) for p in bundle[2].glob('manuscript_context_audit/**/*.json')]
    final = next(a for a in audits if 'stage:final_provider_request' in a['coc_notes'])
    assert final['prompt_fingerprint'] == prompt_fingerprint(captured[0])
    assert any(i['source_id']==did and i['fingerprint'] for i in final['coc_sources'])
    captured.clear()
    with monkeypatch.context() as patch:
        patch.setenv('LLM_REQUEST_MAX_TOKENS','200')
        sio.emit('chat_message',payload,namespace='/manu_ws')
        denied = sio.get_received('/manu_ws')
        assert not captured and '預算不足' in json.dumps(denied,ensure_ascii=False)
        assert any(e['name']=='job_error' for e in denied)
        assert not any(e['name']=='job_done' for e in denied)
    with monkeypatch.context() as patch:
        patch.setattr(routes,'_PROMPT_MAX_TOKENS',40)
        sio.emit('chat_message',payload,namespace='/manu_ws')
        denied = sio.get_received('/manu_ws')
        assert not captured and any(e['name']=='job_error' for e in denied)
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text = 'Changed result at 900 K'
        db.session.commit()
    sio.emit('chat_message',payload,namespace='/manu_ws')
    denied = sio.get_received('/manu_ws')
    assert not captured and any('來源' in json.dumps(e,ensure_ascii=False) for e in denied)
    sio.disconnect(namespace='/manu_ws')


def test_versioned_professor_author_closed_loop_and_cas(workflow):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    bundle,_,_ = workflow
    app,ids,_ = bundle
    decision = confirm(workflow).json['decision']
    mentor = client(bundle,'owner')
    endpoint = f'/api/mentor/mentees/{ids["editor"]}/reviews/{PID}'
    created = mentor.post(endpoint+'/items',json={'kind':'comment','body':'Explain applicability',
        'paper_version':'V1','decision_id':decision['decision_id']})
    assert created.status_code == 201,created.json
    thread = created.json['item']['comment']
    cid = thread['id']
    events_url = f'/manuscript/api/chapter/{PID}/comments/{cid}/events'
    assert client(bundle,'coauthor').post(events_url,json={'action':'respond','body':'leak','base_rev':0,'target_version':'V1'}).status_code == 403
    with app.app_context():
        ManuscriptIO.save_paper_version(PID,'Revised','<p>New author response paragraph</p>',sections={MINE:'0.1'})
    old = mentor.get(endpoint+'?version=V1&compare=V2')
    assert old.status_code == 200 and PUBLIC in old.json['manuscript']['content']
    assert 'New author response paragraph' in old.json['diff']
    author = client(bundle,'editor')
    response = author.post(events_url,json={'action':'respond','body':'Added applicability conditions',
        'base_rev':0,'target_version':'V2','revision_location':'New author response paragraph'})
    assert response.status_code == 201,response.json
    assert response.json['thread']['state'] == 'responded'
    assert author.post(events_url,json={'action':'confirm','base_rev':1,'body':'self approve'}).status_code == 403
    assert client(bundle,'viewer').post(events_url,json={'action':'confirm','base_rev':1,'body':'other reviewer approve'}).status_code == 403
    assert author.post(events_url,json={'action':'respond','base_rev':0,'target_version':'V2','body':'stale'}).status_code == 409
    assert mentor.post(events_url,json={'action':'confirm','base_rev':1,'body':'Verified conditions'}).status_code == 201
    assert mentor.get(endpoint+'?version=V1').json['items'][0]['comment']['state'] == 'confirmed'
    assert author.patch(f'/manuscript/api/chapter/{PID}/comments/{cid}',json={'resolved':False}).status_code == 409
    assert mentor.delete(f'/manuscript/api/chapter/{PID}/comments/{cid}').status_code == 409
    comments = author.get(f'/manuscript/api/chapter/{PID}/comments?scope=paper&g_ver=V1').json['comments']
    current = next(row for row in comments if row['id']==cid)
    assert [e['action'] for e in current['events']] == ['respond','confirm']
    assert current['g_ver'] == 'V1'
    assert [e['actor_id'] for e in current['events']] == [ids['editor'],ids['owner']]


def test_simultaneous_review_responses_have_one_winner(workflow):
    from concurrent.futures import ThreadPoolExecutor
    bundle,_,_ = workflow
    ids=bundle[1]
    created=client(bundle,'owner').post(f'/api/mentor/mentees/{ids["editor"]}/reviews/{PID}/items',
        json={'kind':'comment','body':'Concurrent response test','paper_version':'V1'})
    cid=created.json['item']['comment']['id']
    url=f'/manuscript/api/chapter/{PID}/comments/{cid}/events'
    def respond(n):
        return client(bundle,'editor').post(url,json={'action':'respond','base_rev':0,'body':f'Human response {n}',
            'target_version':'V1','revision_location':PUBLIC}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses=list(pool.map(respond,range(4)))
    assert sorted(statuses)==[201,409,409,409]


def test_additive_review_table_preserves_existing_comment(tmp_path):
    from sqlalchemy import create_engine,text,inspect
    from app.models import ChapterReviewEvent
    engine=create_engine(f'sqlite:///{tmp_path / "old.db"}')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE chapter_comments (id INTEGER PRIMARY KEY, body TEXT)'))
        connection.execute(text("INSERT INTO chapter_comments VALUES (1,'Original professor thought')"))
        # 與既有 create_all／migration 共用的實際 Table 建立路徑；不 mock 遺失的 Alembic。
        ChapterReviewEvent.__table__.create(bind=connection,checkfirst=True)
        ChapterReviewEvent.__table__.create(bind=connection,checkfirst=True)
        assert connection.execute(text('SELECT body FROM chapter_comments')).scalar()=='Original professor thought'
        assert 'chapter_review_events' in inspect(connection).get_table_names()
        assert any(c['column_names']==['comment_id','revision'] for c in inspect(connection).get_unique_constraints('chapter_review_events'))
    engine.dispose()


def test_professor_cannot_review_nonexistent_version(workflow):
    bundle,_,_ = workflow
    response = client(bundle,'owner').post(f'/api/mentor/mentees/{bundle[1]["editor"]}/reviews/{PID}/items',
        json={'kind':'comment','body':'invalid target','paper_version':'V999'})
    assert response.status_code == 409


def test_resource_author_download_and_revocation(workflow):
    import io
    from app import db
    from app.models import MentorLink
    bundle,_,_ = workflow
    app,ids,_ = bundle
    mentor = client(bundle,'owner')
    raw = b'%PDF-1.4\nSynthetic review resource'
    response = mentor.post(f'/api/mentor/mentees/{ids["editor"]}/reviews/{PID}/items',data={
        'kind':'resource_pdf','paper_version':'V1','file':(io.BytesIO(raw),'synthetic.pdf')},content_type='multipart/form-data')
    assert response.status_code == 201,response.json
    url = response.json['item']['download_url']
    assert client(bundle,'editor').get(url).data == mentor.get(url).data == raw
    assert client(bundle,'coauthor').get(url).status_code == 403
    with app.app_context():
        link = MentorLink.query.filter_by(mentor_id=ids['owner'],mentee_id=ids['editor']).first()
        db.session.delete(link)
        db.session.commit()
    # 本輪契約：移除指導關係不使研究者失去已收到的原證據；教授仍須當下授權。
    assert client(bundle,'editor').get(url).data == raw
    assert mentor.get(url).status_code == 403
    assert client(bundle,'viewer').get(url).status_code == 403
    from app.models import WorkspaceMember
    with app.app_context():
        member = WorkspaceMember.query.filter_by(user_id=ids['editor'],pid=PID).one()
        member.role = 'coauthor'
        db.session.commit()
        try:
            assert client(bundle,'editor').get(url).status_code == 403
        finally:
            member.role = 'editor'
            db.session.commit()


def test_changed_evidence_cannot_be_silently_closed_by_professor(workflow):
    from app import db
    from app.models import EvidenceSegment
    bundle,_,sid=workflow
    decision=confirm(workflow).json['decision']
    endpoint=f'/api/mentor/mentees/{bundle[1]["editor"]}/reviews/{PID}'
    professor=client(bundle,'owner')
    created=professor.post(endpoint+'/items',json={'kind':'comment','paper_version':'V1','body':'Recheck source',
        'decision_id':decision['decision_id']})
    cid=created.json['item']['comment']['id']
    url=f'/manuscript/api/chapter/{PID}/comments/{cid}/events'
    response=client(bundle,'editor').post(url,json={'action':'respond','base_rev':0,'body':'Responded',
        'target_version':'V1','revision_location':PUBLIC})
    assert response.status_code==201
    with bundle[0].app_context():
        db.session.get(EvidenceSegment,sid).text='Contradictory replacement result'
        db.session.commit()
    attempted=professor.post(url,json={'action':'confirm','base_rev':1,'body':'Blindly approve'})
    assert attempted.status_code==409 and '來源' in attempted.json['message']
    item=next(i for i in professor.get(endpoint).json['items'] if i['comment'] and i['comment']['id']==cid)
    assert item['context_issues'] and item['comment']['state']=='responded'


def test_legacy_review_is_visible_with_unknown_version(workflow):
    from app import db
    from app.models import MentorReviewItem
    bundle,_,_=workflow
    with bundle[0].app_context():
        row=MentorReviewItem(mentor_id=bundle[1]['owner'],mentee_id=bundle[1]['editor'],pid=PID,
            kind='comment',body='Historical unversioned professor intent')
        db.session.add(row); db.session.commit(); item_id=row.id
    result=client(bundle,'editor').get(f'/api/mentor/my-reviews/{PID}?version=V1')
    item=next(i for i in result.json['items'] if i['id']==item_id)
    assert item['paper_version'] is None and item['comment'] is None


@pytest.mark.parametrize('role,section',[('viewer',MINE),('outsider',MINE),('coauthor',HIDDEN)])
def test_paragraph_confirmation_acl_before_evidence_lookup(workflow,role,section):
    response = client(workflow[0],role).post('/manuscript/api/paragraph/confirm',json={'pid':PID,'section':section,'evidence_id':999999})
    assert response.status_code == 403


def test_whole_coc_block_drops_manifest_and_reports_budget(monkeypatch):
    from app.core_pro.manuscript.manuscript_routes import _pack_prompt_context
    large='Hidden tail '+('x '*500)
    bundle={'text':large,'blocks':[{'tier':'literature','text':large,'items':[{'source_id':'omitted'}]}],
            'items':[{'source_id':'omitted'}]}
    text,left,notes,items = _pack_prompt_context(frontend_context='',coc_bundle=bundle,total_budget=30,section=MINE,job_id='test')
    assert 'Hidden tail' not in text and items==[] and any('完整區塊' in n for n in notes)
