# 目的：六個預先固定合成案例走作者確認與實際 Task8 dispatch；量測已知傳遞能力及未完成的科學判讀。
# 邊界：無教授、無真實 provider；人工答案只供事後評分。未知敘述不可評為支持。
# 驗證：python -m pytest test/unit/test_research_integrity_benchmark.py -q；契約見 SCIENTIFIC_BENCHMARK_PROTOCOL.md。
import copy
import json
import os
from pathlib import Path
import subprocess
import pytest
from test_sai_channel_acl import channel_app,client,socket,PID,MINE,PUBLIC
from scripts.benchmark_research_integrity import score,digest

FIXTURES = Path(__file__).resolve().parents[1]/'fixtures'
INPUT = FIXTURES/'research_integrity_cases.json'
GOLD = FIXTURES/'research_integrity_gold.json'


@pytest.fixture(scope='module')
def observations(channel_app):
    from app import db
    from app.models import EvidenceSegment,MentorLink
    from app.services.literature_library import LiteratureLibrary
    from app.core_pro.manuscript import manuscript_routes as routes
    from app.llm_service.matching_tasks import task_8drafter
    inputs = json.loads(INPUT.read_text(encoding='utf-8'))
    app,ids,root = channel_app
    references = {}
    with app.app_context():
        assert MentorLink.query.count()==0
        library = LiteratureLibrary(str(root))
        for source in inputs['sources']:
            doi = '10.0000/sai-bench-'+source['id']
            library.merge_candidates(PID,[{'title':source['title'],'doi':doi}])
            entry = next(e for e in library.list_entries(PID) if e.get('doi')==doi)
            entry = library.update_entry(PID,entry['entry_id'],{'paper_id':source['paper_id'],'screening_status':'included',
                'screening_note':'Synthetic author selection; retain source conditions','reading_note':'Bounded synthetic result'},
                actor='editor@example.invalid',base_rev=entry.get('_rev',0))
            segment = EvidenceSegment(project_id=PID,paper_id=source['paper_id'],source_id=source['source_id'],source_type='paper_segment',
                segment_id=source['id'],text=source['text'],content_hash=digest(INPUT),metadata_json=json.dumps({'page':source['page']}))
            db.session.add(segment)
            db.session.flush()
            references[source['id']] = (entry['entry_id'],segment.id)
        db.session.commit()
    values,active,captured = [],{},[]
    class Inline:
        def submit(self,fn,*args,**kwargs):
            fn(*args,**kwargs)
    def provider(task,prompt,**kwargs):
        captured.append(str(prompt))
        return {'ok':True,'text':json.dumps({'chat_message':'Controlled synthetic response','draft_content':active['case']['claim']})}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(routes,'_CHAT_EXECUTOR',Inline())
        patch.setattr(routes.ai_drafter,'_detect_intent',lambda *a,**k:'draft')
        patch.setattr(task_8drafter,'dispatch_task',provider)
        sio = socket(channel_app,'editor')
        try:
            for case in inputs['cases']:
                entry_id,evidence_id = references[case['source']]
                body = dict(pid=PID,section=MINE,s_ver='0.1',paragraph_text=PUBLIC,purpose='Explain a synthetic bounded result',
                    claim=case['claim'],conditions=case['conditions'],forbidden=case['forbidden'],entry_id=entry_id,
                    evidence_id=999999 if case.get('missing_evidence') else evidence_id,confirmed=True,
                    locator=case.get('locator'),decided_by='forged-professor')
                response = client(channel_app,'editor').post('/manuscript/api/paragraph/confirm',json=body)
                observed = dict(case_id=case['id'],accepted=response.status_code==201,http_status=response.status_code)
                if response.status_code==201:
                    decision = response.json['decision']
                    assert decision['decided_by']=='editor@example.invalid'
                    observed['context'] = decision['paragraph_context']
                    active['case'] = case
                    captured.clear()
                    sio.get_received('/manu_ws')
                    sio.emit('chat_message',dict(pid=PID,section=MINE,title='Synthetic benchmark',msg='Draft only the confirmed paragraph',
                        paragraph_decision_id=decision['decision_id']),namespace='/manu_ws')
                    events = sio.get_received('/manu_ws')
                    assert any(e['name']=='job_done' for e in events),events
                    assert len(captured)==1,events
                    observed['prompt'] = captured[0]
                    assert 'required_conditions' not in captured[0] and 'gold_verdict' not in captured[0]
                    ai = next(e['args'][0] for e in events if e['name']=='ai_response')
                    observed['output'] = ai['content']
                else:
                    assert response.status_code in {400,409},response.json
                values.append(observed)
        finally:
            sio.disconnect(namespace='/manu_ws')
    with app.app_context():
        assert MentorLink.query.count()==0
    return values


def test_measured_pipeline_and_unresolved_scientific_controls(observations):
    inputs,gold = json.loads(INPUT.read_text(encoding='utf-8')),json.loads(GOLD.read_text(encoding='utf-8'))
    result = score(inputs,gold,observations)
    metrics = result['metrics']
    assert metrics['requested']==6 and metrics['accepted']==5 and metrics['rejected']==1
    assert metrics['traceable_accepted']==metrics['input_conditions_preserved']==metrics['prompt_conditions_preserved']==5
    assert metrics['unavailable_evidence_rejected']==1
    assert metrics['scientific_validation_claimed']==0
    # 這是明確量測到的限制：受控未支持敘述仍會被保留，不能把工程通過寫成科學通過。
    assert metrics['unsupported_controls_returned']==metrics['unsupported_controls']==2
    assert metrics['missing_condition_controls_returned']==1
    assert metrics['evidence_condition_tokens_present']==18 and metrics['evidence_condition_tokens_required']==19
    output = os.environ.get('SAI_BENCHMARK_OUTPUT')
    if output:
        artifact = dict(code_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            input_sha256=digest(INPUT),gold_sha256=digest(GOLD),scorer_sha256=digest(Path(__file__).resolve().parents[2]/'scripts/benchmark_research_integrity.py'),
            result=result,observations=observations)
        with Path(output).open('x',encoding='utf-8') as handle:
            json.dump(artifact,handle,ensure_ascii=False,indent=2)


@pytest.mark.parametrize('mutation',['wrong_locator','missing_prompt_condition','unknown_output'])
def test_scorer_detects_adversarial_control_mutations(observations,mutation):
    inputs,gold = json.loads(INPUT.read_text(encoding='utf-8')),json.loads(GOLD.read_text(encoding='utf-8'))
    altered = copy.deepcopy(observations)
    first = next(o for o in altered if o['case_id']=='bounded')
    if mutation=='wrong_locator':
        first['context']['evidence']['locator']['page']=999
    elif mutation=='missing_prompt_condition':
        first['prompt']=first['prompt'].replace('300 K','')
    else:
        first['output']='A statement absent from the fixed claim catalogue.'
    before,after = score(inputs,gold,observations)['metrics'],score(inputs,gold,altered)['metrics']
    if mutation=='wrong_locator':
        assert after['traceable_accepted']==before['traceable_accepted']-1
    elif mutation=='missing_prompt_condition':
        assert after['prompt_conditions_preserved']==before['prompt_conditions_preserved']-1
    else:
        assert after['unknown_outputs']==before['unknown_outputs']+1
