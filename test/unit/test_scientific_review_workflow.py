# 目的：科學建議／作者回應是真實版本歷史，無證據仍能寫作、保存及匯出。
# 驗證：python -m pytest test/unit/test_scientific_review_workflow.py -q
import json
import pytest
from test_sai_channel_acl import channel_app,client,socket,PID,MINE,HIDDEN,PUBLIC
from test_sai_review_workflow import workflow,confirm
from test_sai_change_impact import private_source


def request_body(workflow,**changes):
    _,entry,sid=workflow
    return dict(pid=PID,section=MINE,text='The fabricated device experimentally improved for all samples.',
        evidence=[{'entry_id':entry['entry_id'],'evidence_id':sid}],**changes)


def assess(workflow,actor='editor',body=None):
    return client(workflow[0],actor).post('/manuscript/api/scientific/assess',json=body or request_body(workflow))


def test_unsaved_claim_without_evidence_is_recorded_as_advisory_and_does_not_gate_workflow(workflow):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    body=dict(pid=PID,section='hypothesis',text='Hypothesis: an untested mechanism may operate.',evidence=[])
    response=assess(workflow,body=body)
    assert response.status_code==201,response.json
    report=response.json
    assert report['advisory_only'] is True and report['scientific_proof'] is False
    assert report['claims'][0]['relation']=='unknown' and report['original_text']==body['text']
    http=client(workflow[0],'editor')
    saved=http.post('/manuscript/api/draft/flush',json=dict(pid=PID,section=body['section'],content=body['text'],base_rev=0))
    assert saved.status_code==200,saved.json
    sio=socket(workflow[0],'editor')
    try:
        sio.get_received('/manu_ws')
        sio.emit('cmd_save_block',dict(pid=PID,section=body['section'],title='Hypothesis',content=body['text']),namespace='/manu_ws')
        assert any(e['name']=='save_ack' for e in sio.get_received('/manu_ws'))
    finally:sio.disconnect(namespace='/manu_ws')
    exported=http.post('/manuscript/api/export/docx/'+PID,json={'html':'<p>'+body['text']+'</p>'})
    assert exported.status_code==200 and exported.data.startswith(b'PK')


def test_author_response_is_append_only_not_scientific_proof_and_stale_view_is_explicit(workflow):
    from app import db
    from app.models import EvidenceSegment,RevisionLog
    report=assess(workflow).json
    rid=report['report_id'];original=json.dumps(report['claims'],sort_keys=True)
    endpoint=f'/manuscript/api/scientific/reports/{rid}/responses'
    body=dict(pid=PID,claim_id=report['claims'][0]['id'],stance='retain_hypothesis',reason='I retain this as an untested hypothesis.',view_snapshot=report['view_snapshot'],scientific_proof=True)
    assert client(workflow[0],'viewer').post(endpoint,json=body).status_code==403
    with workflow[0][0].app_context():
        db.session.get(EvidenceSegment,workflow[2]).text+=' Subsequent correction.';db.session.commit()
    assert client(workflow[0],'editor').post(endpoint,json=body).status_code==409
    current=client(workflow[0],'editor').get(f'/manuscript/api/scientific/reports/{rid}?pid={PID}').json
    assert current['stale'] is True
    body['view_snapshot']=current['view_snapshot']
    response=client(workflow[0],'editor').post(endpoint,json=body)
    assert response.status_code==201,response.json
    assert response.json['response']['scientific_proof'] is False
    after=client(workflow[0],'editor').get(f'/manuscript/api/scientific/reports/{rid}?pid={PID}').json
    assert json.dumps(after['claims'],sort_keys=True)==original and after['responses'][0]['reason']==body['reason']
    with workflow[0][0].app_context():
        assert RevisionLog.query.filter_by(entity_type='scientific_advisory',entity_ref=rid).count()==1


def test_scoped_author_cannot_guess_source_or_read_a_report_with_unshared_evidence(workflow,private_source):
    entry,sid=private_source
    body=dict(pid=PID,section=MINE,text='A private source claim.',evidence=[{'entry_id':entry['entry_id'],'evidence_id':sid}])
    assert assess(workflow,'coauthor',body).status_code==403
    report=assess(workflow,'editor',body)
    assert report.status_code==201,report.json
    rid=report.json['report_id']
    denied=client(workflow[0],'coauthor').get(f'/manuscript/api/scientific/reports/{rid}?pid={PID}')
    assert denied.status_code==403 and 'PRIVATE_UNASSIGNED_SOURCE' not in denied.get_data(as_text=True)
    assert client(workflow[0],'outsider').get(f'/manuscript/api/scientific/reports/{rid}?pid={PID}').status_code==403


def test_selected_human_context_produces_post_generation_comparison_without_rewriting(workflow,monkeypatch):
    from app.core_pro.manuscript import manuscript_routes as routes
    from app.llm_service.matching_tasks import task_8drafter
    decision=confirm(workflow).json['decision']
    output='All samples improve at every temperature.'
    class Inline:
        def submit(self,fn,*args,**kwargs):fn(*args,**kwargs)
    monkeypatch.setattr(routes,'_CHAT_EXECUTOR',Inline())
    monkeypatch.setattr(task_8drafter,'dispatch_task',lambda *a,**k:{'ok':True,'text':json.dumps({'draft_content':output,'chat_message':'Synthetic control'})})
    sio=socket(workflow[0],'editor')
    try:
        sio.get_received('/manu_ws')
        sio.emit('chat_message',dict(pid=PID,section=MINE,title='Synthetic',msg='Draft the selected claim',paragraph_decision_id=decision['decision_id']),namespace='/manu_ws')
        events=sio.get_received('/manu_ws')
        result=next(e['args'][0] for e in events if e['name']=='ai_response')
        assert result['content']==output
        advisory=result['meta']['scientific_advisory']
        assert advisory['advisory_only'] is True and advisory['scientific_proof'] is False
        assert 'unsupported_generalization' in {f['code'] for c in advisory['claims'] for f in c['findings']}
    finally:sio.disconnect(namespace='/manu_ws')


@pytest.mark.parametrize('failure',['analysis','persistence'])
def test_advisory_failure_preserves_original_generated_draft(workflow,monkeypatch,failure):
    from app.core_pro.manuscript import manuscript_routes as routes,scientific_routes
    output='Hypothesis: an unsupported mechanism may operate.'
    class Inline:
        def submit(self,fn,*args,**kwargs):fn(*args,**kwargs)
    def fail(*args,**kwargs):raise RuntimeError('Injected advisory failure')
    monkeypatch.setattr(routes,'_CHAT_EXECUTOR',Inline())
    monkeypatch.setattr(routes.ai_drafter,'process_request',lambda **kwargs:{'type':'draft','content':output})
    monkeypatch.setattr(scientific_routes,'assess_claims' if failure=='analysis' else 'save_advisory',fail)
    sio=socket(workflow[0],'editor')
    try:
        sio.get_received('/manu_ws')
        sio.emit('chat_message',dict(pid=PID,section=MINE,msg='Draft an explicit hypothesis'),namespace='/manu_ws')
        events=sio.get_received('/manu_ws')
        result=next(e['args'][0] for e in events if e['name']=='ai_response')
        assert result['content']==output
        assert result['meta']['scientific_advisory']['status']=='unavailable'
        assert result['meta']['scientific_advisory']['blocks_writing'] is False
        assert any(e['name']=='job_done' for e in events)
    finally:sio.disconnect(namespace='/manu_ws')


def test_advisory_rejects_cross_chapter_forgery_and_malformed_input(workflow):
    http=client(workflow[0],'coauthor')
    body=request_body(workflow);body['section']=HIDDEN
    assert http.post('/manuscript/api/scientific/assess',json=body).status_code==403
    for value in ([],None,'not an object'):
        assert client(workflow[0],'editor').post('/manuscript/api/scientific/assess',json=value).status_code==400
    body=request_body(workflow);body['text']='A source comparison.'
    body.update(actor_id=999,scientific_proof=True)
    body['evidence'][0].update(text='FORGED_SOURCE_SENTINEL',locator={'page':999})
    response=client(workflow[0],'editor').post('/manuscript/api/scientific/assess',json=body)
    assert response.status_code==201,response.json
    assert response.json['actor_id']==workflow[0][1]['editor']
    assert response.json['scientific_proof'] is False
    assert 'FORGED_SOURCE_SENTINEL' not in response.get_data(as_text=True)
    assert all(c['locator']!={'page':999} for c in response.json['claims'][0]['comparisons'])
    report=response.json
    assert client(workflow[0],'coauthor').get(f'/manuscript/api/scientific/reports?pid={PID}&section={HIDDEN}').status_code==403
    assert client(workflow[0],'outsider').get(f'/manuscript/api/scientific/reports/{report["report_id"]}?pid={PID}').status_code==403
