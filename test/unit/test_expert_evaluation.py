"""程序對抗控制；所有身分、密鑰與答案皆為合成資料，非專家判斷。"""
import copy
import importlib.util
from pathlib import Path
import pytest
import subprocess
import sys
from cryptography.exceptions import InvalidSignature

spec=importlib.util.spec_from_file_location('expert_evaluation',Path(__file__).parents[2]/'scripts/expert_evaluation.py')
ev=importlib.util.module_from_spec(spec);spec.loader.exec_module(ev)


def setup():
    keys={name:bytes([i])*32 for i,name in enumerate(('owner','custodian','reviewer1','reviewer2','adjudicator'),1)}
    keys={k:v.hex() for k,v in keys.items()}
    master=dict(sources=[dict(source_id='s1',doi='10.synthetic/test',title='Synthetic',text='Device A operated at 300 K.',
        locator={'section':'abstract'},license_url='https://creativecommons.org/licenses/by/4.0/',full_text_xml='PRIVATE MASTER')],
        cases=[dict(case_id='c1',claim='Device A operated at 300 K.',evidence_sources=['s1'],candidate_origin='hidden',gold='LEAK',annotations=['LEAK']),
               dict(case_id='c2',claim='Uncited hypothesis.',evidence_sources=[],candidate_origin='hidden',original_quote='LEAK')])
    p=dict(version=ev.VERSION,evaluation_id='synthetic-only',owner_public_key=ev.public(keys['owner']),
        roles={r:dict(id=r,public_key=ev.public(keys[r]),expertise='synthetic test role',owner_approved=True) for r in keys if r!='owner'},
        dataset_sha256=ev.digest(master),rubric_sha256='a'*64,acceptance_criteria={'scope':'synthetic procedure, no scientific pass'})
    signed=ev.sign(p,keys['owner']);owner=p['owner_public_key']
    assigned=ev.assignments(master,signed,owner,keys['custodian'])
    return keys,master,signed,owner,assigned


def answer(prompt):
    sources=prompt['sources'];loc=[]
    if sources:loc=[dict(source_id='s1',start=0,end=len(sources[0]['text']),quote=sources[0]['text'])]
    return dict(label='SUPPORT' if sources else 'INSUFFICIENT',evidence_sentence_locators=loc,
        required_conditions=[dict(name='temperature',value='300 K',locator=loc[0],claim_status='preserved')] if sources else [],
        condition_fidelity='preserved' if sources else 'unknown',research_modality={'claim':'unknown','source':'unknown'},
        causal_or_argument_reversal=None,unsupported_generalization=False,missing_citation=not bool(sources),
        uncertainty='Synthetic procedure control only.',reason='Literal fixture, not an expert answer.',confidence='low')


def filled(a):
    return dict(annotations=[dict(case_id=c['prompt']['case_id'],case_sha256=c['case_sha256'],annotation=answer(c['prompt'])) for c in a['payload']['cases']],
        declarations={'independent':True,'no_predictions_seen':True,'conflicts_disclosed':True})


def completed():
    keys,master,p,owner,assigned=setup()
    submitted=[ev.submission(a,filled(a),p,owner,keys[a['payload']['role']]) for a in assigned]
    return keys,master,p,owner,assigned,submitted


def test_two_independent_signed_reviews_preserve_originals_and_blinding(tmp_path):
    keys,master,p,owner,a,s=completed();before=copy.deepcopy([master,a,s])
    assert b'LEAK' not in ev.encoded(a) and b'PRIVATE MASTER' not in ev.encoded(a) and b'candidate_origin' not in ev.encoded(a)
    assert next(c for c in a[0]['payload']['cases'] if c['prompt']['case_id']=='c2')['prompt']['sources']==[]
    frozen=ev.freeze(a,s,None,p,owner,keys['custodian'])
    gold=ev.verify(frozen,ev.public(keys['custodian']))
    assert gold['submissions']==s and gold['assignments']==a and gold['disputes']==[]
    assert gold['scientific_validation_pass'] is False and [master,a,s]==before
    path=tmp_path/'round.json';ev.save(path,frozen)
    with pytest.raises(FileExistsError):ev.save(path,{'replacement':True})


@pytest.mark.parametrize('target',['claim','source','signature','plan','root','case_hash'])
def test_tampering_and_wrong_trust_root_are_rejected(target):
    keys,master,p,owner,a,s=completed()
    if target=='claim':a[0]['payload']['cases'][0]['prompt']['claim']='Altered'
    if target=='source':
        next(c for c in a[0]['payload']['cases'] if c['prompt']['sources'])['prompt']['sources'][0]['text']='Altered'
    if target=='signature':s[0]['signature']=s[1]['signature']
    if target=='plan':p['payload']['rubric_sha256']='b'*64
    if target=='root':owner=ev.public(keys['reviewer1'])
    if target=='case_hash':
        s[0]['payload']['annotations'][0]['case_sha256']='b'*64;s[0]=ev.sign(s[0]['payload'],keys['reviewer1'])
    with pytest.raises((ValueError,InvalidSignature)):ev.freeze(a,s,None,p,owner,keys['custodian'])


@pytest.mark.parametrize('change',['missing','duplicate','quote','offset','bool_offset','condition','reason','leak','decisive_without_evidence','declarations'])
def test_adversarial_labels_are_rejected_before_signing(change):
    keys,_,p,owner,a=setup();f=filled(a[0]);row=next(r for r in f['annotations'] if r['case_id']=='c1');v=row['annotation']
    if change=='missing':f['annotations'].pop()
    if change=='duplicate':f['annotations']=[row,row]
    if change=='quote':v['evidence_sentence_locators'][0]['quote']='Invented evidence'
    if change=='offset':v['evidence_sentence_locators'][0]['end']=999
    if change=='bool_offset':v['evidence_sentence_locators'][0]['start']=False
    if change=='condition':v['required_conditions'][0]['value']='500 K'
    if change=='reason':v['reason']=''
    if change=='leak':v['model_prediction']='SUPPORT'
    if change=='decisive_without_evidence':v['evidence_sentence_locators']=[]
    if change=='declarations':f['declarations']['no_predictions_seen']=False
    with pytest.raises(ValueError):ev.submission(a[0],f,p,owner,keys['reviewer1'])


def test_role_separation_and_duplicate_reviewer():
    keys,_,p,owner,a,s=completed()
    with pytest.raises(ValueError):ev.freeze([a[0],a[0]],[s[0],s[0]],None,p,owner,keys['custodian'])
    with pytest.raises(ValueError):ev.submission(a[0],filled(a[0]),p,owner,keys['reviewer2'])
    p['payload']['roles']['adjudicator']['public_key']=p['payload']['roles']['reviewer1']['public_key']
    p=ev.sign(p['payload'],keys['owner'])
    with pytest.raises(ValueError):ev.plan(p,owner)


def test_condition_disagreement_requires_independent_version_bound_adjudication():
    keys,_,p,owner,a,s=completed();f=filled(a[1]);row=next(r for r in f['annotations'] if r['case_id']=='c1')
    row['annotation']['research_modality']['source']='simulation'
    s[1]=ev.submission(a[1],f,p,owner,keys['reviewer2'])
    _,cases,disputes=ev.reconcile(a,s,p,owner)
    assert disputes==['c1']  # 相同 SUPPORT 仍有科學欄位分歧。
    with pytest.raises((ValueError,TypeError)):ev.freeze(a,s,None,p,owner,keys['custodian'])
    r=dict(version=ev.VERSION,kind='adjudication',plan_sha256=ev.digest(p),
        submission_sha256=[ev.digest(v) for v in s],decisions=[dict(case_id='c1',
        case_sha256=cases['c1']['case_sha256'],annotation=answer(cases['c1']['prompt']),reason='Synthetic adjudication')])
    with pytest.raises(InvalidSignature):ev.freeze(a,s,ev.sign(r,keys['reviewer1']),p,owner,keys['custodian'])
    frozen=ev.freeze(a,s,ev.sign(r,keys['adjudicator']),p,owner,keys['custodian'])
    assert frozen['payload']['gold'][0]['adjudicated'] is True
    assert frozen['payload']['submissions']==s
    r['submission_sha256'].reverse()
    with pytest.raises(ValueError):ev.freeze(a,s,ev.sign(r,keys['adjudicator']),p,owner,keys['custodian'])


def test_frozen_gold_cannot_override_reviews_even_with_custodian_signature():
    keys,_,p,owner,a,s=completed();g=ev.freeze(a,s,None,p,owner,keys['custodian'])
    assert ev.verified_gold(g,p,owner)['agreement']['label_agreement']==1
    g['payload']['gold'][0]['annotation']['label']='CONTRADICT'
    altered=ev.sign(g['payload'],keys['custodian'])
    with pytest.raises(ValueError):ev.verified_gold(altered,p,owner)


def test_scoring_rejects_replay_partial_and_modified_predictions():
    keys,_,p,owner,a,s=completed();g=ev.freeze(a,s,None,p,owner,keys['custodian'])
    predictions=dict(plan_sha256=ev.digest(p),engine_sha256='b'*64,predictions=[dict(case_id=r['case_id'],
        case_sha256=r['case_sha256'],label='UNKNOWN') for r in g['payload']['gold']])
    result=ev.score(g,p,owner,predictions,ev.digest(predictions),ev.digest(g))
    assert result['unknown']==2 and result['scientific_validation_pass'] is False
    assert result['one_vs_rest']['SUPPORT']['fn']==1
    committed=ev.digest(predictions);predictions['predictions'][0]['label']='SUPPORT'
    with pytest.raises(ValueError):ev.score(g,p,owner,predictions,committed,ev.digest(g))
    predictions['predictions'].pop()
    with pytest.raises(ValueError):ev.score(g,p,owner,predictions,ev.digest(predictions),ev.digest(g))


def test_real_cli_round_trip_and_exclusive_outputs(tmp_path):
    keys,master,p,owner,_,_=completed();script=Path(ev.__file__)
    def cli(mode,*args):
        result=subprocess.run([sys.executable,str(script),mode,*map(str,args)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    for name,key in keys.items():ev.save(tmp_path/f'{name}.json',{'private_key':key})
    ev.save(tmp_path/'master.json',master);ev.save(tmp_path/'config.json',p['payload'])
    planfile=tmp_path/'plan.json'
    cli('sign-plan','--input',tmp_path/'config.json','--owner-public',owner,'--private-key',tmp_path/'owner.json','--output',planfile)
    common=['--plan',planfile,'--owner-public',owner]
    cli('assign',*common,'--input',tmp_path/'master.json','--private-key',tmp_path/'custodian.json','--output',tmp_path/'assigned')
    assignments=[];submissions=[]
    for i in (1,2):
        af=tmp_path/f'assigned/reviewer{i}.json';assignment=ev.read(af);sf=tmp_path/f'submission{i}.json'
        f=filled(assignment)
        if i==2:next(r for r in f['annotations'] if r['case_id']=='c1')['annotation']['research_modality']['source']='simulation'
        inputfile=tmp_path/f'filled{i}.json';ev.save(inputfile,dict(assignment=assignment,filled=f))
        cli('submit',*common,'--input',inputfile,'--private-key',tmp_path/f'reviewer{i}.json','--output',sf)
        assignments.append(af);submissions.append(sf)
    review=['--assignment',*assignments,'--submission',*submissions]
    cli('disputes',*common,*review,'--output',tmp_path/'disputes.json')
    assert ev.read(tmp_path/'disputes.json')['disputes']==['c1']
    case=next(c for c in ev.read(assignments[0])['payload']['cases'] if c['prompt']['case_id']=='c1')
    ev.save(tmp_path/'decisions.json',[dict(case_id='c1',case_sha256=case['case_sha256'],annotation=answer(case['prompt']),reason='Synthetic adjudication')])
    cli('adjudicate',*common,*review,'--input',tmp_path/'decisions.json','--private-key',tmp_path/'adjudicator.json','--output',tmp_path/'resolution.json')
    cli('freeze',*common,*review,'--adjudication',tmp_path/'resolution.json','--private-key',tmp_path/'custodian.json','--output',tmp_path/'gold.json')
    cli('verify-gold',*common,'--gold',tmp_path/'gold.json','--output',tmp_path/'verified.json')
    assert ev.read(tmp_path/'verified.json')['gold'][0]['adjudicated'] is True
    result=subprocess.run([sys.executable,str(script),'verify-gold',*map(str,common),'--gold',str(tmp_path/'gold.json'),'--output',str(tmp_path/'verified.json')],capture_output=True)
    assert result.returncode!=0 and b'FileExistsError' in result.stderr
