"""I10 custody 對抗控制：獨立稽核確認的缺陷不得回歸。

全部身分、金鑰與答案皆為合成資料，不是專家判斷；通過不代表獨立保管或學術資格成立。
驗證：python -m pytest test/unit/test_expert_custody_hardening.py -q
"""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import pytest
from cryptography.exceptions import InvalidSignature
from test_expert_evaluation import ev,setup,answer,filled,completed

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/expert_evaluation.py'


def plan_with(roles,owner_key):
    body=dict(version=ev.VERSION,evaluation_id='synthetic-only',owner_public_key=ev.public(owner_key),
        roles=roles,dataset_sha256='a'*64,rubric_sha256='b'*64,
        acceptance_criteria={'scope':'synthetic procedure, no scientific pass'})
    return ev.sign(body,owner_key),body['owner_public_key']


def role(name,key,public=None):
    return dict(id=name,expertise='synthetic test role',owner_approved=True,public_key=public or ev.public(key))


KEYS={name:(bytes([i])*32).hex() for i,name in
      enumerate(('owner','custodian','reviewer1','reviewer2','adjudicator'),1)}


@pytest.mark.parametrize('collapse',['reviewer2','custodian','adjudicator'])
def test_one_key_cannot_hold_two_roles_through_hex_casing(collapse):
    # bytes.fromhex 大小寫不敏感：raw 字串比較會把同一把金鑰當成兩個獨立角色。
    shared=ev.public(KEYS['reviewer1'])
    roles={r:role(r,KEYS[r]) for r in ('custodian','reviewer1','reviewer2','adjudicator')}
    roles['reviewer1']['public_key']=shared.lower()
    roles[collapse]['public_key']=shared.upper()
    signed,owner=plan_with(roles,KEYS['owner'])
    with pytest.raises(ValueError,match='overlap'):ev.plan(signed,owner)


def test_owner_key_cannot_be_reused_as_a_reviewer_in_another_hex_casing():
    roles={r:role(r,KEYS[r]) for r in ('custodian','reviewer1','reviewer2','adjudicator')}
    roles['reviewer2']['public_key']=ev.public(KEYS['owner']).upper()
    signed,owner=plan_with(roles,KEYS['owner'])
    with pytest.raises(ValueError,match='overlap'):ev.plan(signed,owner)


@pytest.mark.parametrize('bad',['', 'zz'*32, 'a'*63, 'a'*65, ev.public(KEYS['owner'])[:4]+' '+ev.public(KEYS['owner'])[4:]])
def test_malformed_public_keys_are_rejected_instead_of_silently_normalized(bad):
    with pytest.raises(ValueError):ev.keyid(bad)


def test_duplicate_json_keys_are_rejected_instead_of_last_wins(tmp_path):
    path=tmp_path/'dup.json'
    path.write_text('{"label":"CONTRADICT","label":"SUPPORT"}',encoding='utf-8')
    with pytest.raises(ValueError,match='Duplicate JSON key'):ev.read(path)


def test_signed_artifact_must_be_the_exact_bytes_that_were_signed(tmp_path):
    # 簽章覆蓋 canonical 重編碼；歸檔檔案若重新排版或夾帶重複鍵，人眼與其他解析器會看到別的內容。
    envelope=ev.sign(dict(version=ev.VERSION,kind='demo',label='SUPPORT'),KEYS['reviewer1'])
    canonical=tmp_path/'canonical.json';ev.save(canonical,envelope)
    assert ev.read_signed(canonical)==envelope
    pretty=tmp_path/'pretty.json'
    pretty.write_text(json.dumps(envelope,indent=2,ensure_ascii=True),encoding='utf-8')
    with pytest.raises(ValueError,match='canonical bytes'):ev.read_signed(pretty)
    smuggled=tmp_path/'smuggled.json'
    smuggled.write_text(ev.encoded(envelope).decode().replace(
        '"label":"SUPPORT"','"label":"CONTRADICT","label":"SUPPORT"'),encoding='utf-8')
    with pytest.raises(ValueError):ev.read_signed(smuggled)


@pytest.mark.parametrize('field,value',[('scientific_validation_pass',0),('agreement',None)])
def test_verified_gold_rejects_equal_but_retyped_values(field,value):
    # Python 的 == 認為 False==0、1.0==True；型別改寫會讓下游的 `is False` 失效。
    keys,_,p,owner,a,s=completed();gold=ev.freeze(a,s,None,p,owner,keys['custodian'])
    payload=json.loads(ev.encoded(gold['payload']))
    if field=='agreement':payload['agreement']['label_agreement']=True
    else:payload[field]=value
    with pytest.raises(ValueError,match='Gold differs'):
        ev.verified_gold(ev.sign(payload,keys['custodian']),p,owner)


def test_retyped_scientific_flag_inside_gold_is_rejected():
    keys,_,p,owner,a,s=completed();gold=ev.freeze(a,s,None,p,owner,keys['custodian'])
    payload=json.loads(ev.encoded(gold['payload']))
    assert payload['gold'][0]['annotation']['unsupported_generalization'] is False
    payload['gold'][0]['annotation']['unsupported_generalization']=0
    with pytest.raises(ValueError,match='Gold differs'):
        ev.verified_gold(ev.sign(payload,keys['custodian']),p,owner)


def disputed():
    keys,_,p,owner,a,s=completed();f=filled(a[1])
    next(r for r in f['annotations'] if r['case_id']=='c1')['annotation']['research_modality']['source']='simulation'
    s[1]=ev.submission(a[1],f,p,owner,keys['reviewer2'])
    _,cases,disputes=ev.reconcile(a,s,p,owner)
    resolution=dict(version=ev.VERSION,kind='adjudication',plan_sha256=ev.digest(p),
        submission_sha256=[ev.digest(v) for v in s],decisions=[dict(case_id='c1',
            case_sha256=cases['c1']['case_sha256'],annotation=answer(cases['c1']['prompt']),
            reason='Synthetic adjudication')])
    return keys,p,owner,a,s,resolution


@pytest.mark.parametrize('mutate',[
    lambda r:r.pop('version'),
    lambda r:r.pop('kind'),
    lambda r:r.update(kind='submission'),
    lambda r:r.update(model_prediction='SUPPORT'),
    lambda r:r['decisions'][0].update(model_prediction='SUPPORT'),
    lambda r:r['decisions'][0].update(note='leaked context'),
])
def test_adjudication_cannot_smuggle_extra_fields_into_frozen_gold(mutate):
    keys,p,owner,a,s,resolution=disputed()
    mutate(resolution)
    with pytest.raises(ValueError,match='Adjudication'):
        ev.freeze(a,s,ev.sign(resolution,keys['adjudicator']),p,owner,keys['custodian'])


def test_adjudication_with_blank_reason_is_rejected():
    keys,p,owner,a,s,resolution=disputed()
    resolution['decisions'][0]['reason']='   '
    with pytest.raises(ValueError,match='reason required'):
        ev.freeze(a,s,ev.sign(resolution,keys['adjudicator']),p,owner,keys['custodian'])


def test_each_assignment_round_is_distinct_so_labels_cannot_be_replayed():
    keys,master,p,owner,first=setup()
    second=ev.assignments(master,p,owner,keys['custodian'])
    nonces={a['payload']['round_nonce'] for a in first+second}
    assert len(nonces)==2,'每一輪必須有自己的 nonce'
    assert ev.digest(first[0])!=ev.digest(second[0]),'兩輪任務不得位元相同'
    submitted=ev.submission(first[0],filled(first[0]),p,owner,keys['reviewer1'])
    with pytest.raises(ValueError,match='binding mismatch'):
        ev.collect(second[0],submitted,p,owner)


def test_assignment_payload_rejects_missing_or_extra_fields():
    keys,master,p,owner,assigned=setup()
    parsed=ev.plan(p,owner)
    for mutate in (lambda a:a.pop('round_nonce'),lambda a:a.update(extra='x'),
                   lambda a:a.update(round_nonce='short')):
        payload=json.loads(ev.encoded(assigned[0]['payload']));mutate(payload)
        with pytest.raises(ValueError,match='(?i)assignment'):
            ev.assignment(ev.sign(payload,keys['custodian']),parsed,p)


@pytest.mark.parametrize('engine',['ZZ'+'a'*62,'A'*64,'a'*63,'','not a hash but exactly sixty four characters long padding!!!!!!!'])
def test_score_requires_a_real_engine_hash_not_any_64_characters(engine):
    keys,_,p,owner,a,s=completed();gold=ev.freeze(a,s,None,p,owner,keys['custodian'])
    predictions=dict(plan_sha256=ev.digest(p),engine_sha256=engine,
        predictions=[dict(case_id=r['case_id'],case_sha256=r['case_sha256'],label='UNKNOWN')
            for r in gold['payload']['gold']])
    with pytest.raises(ValueError):
        ev.score(gold,p,owner,predictions,ev.digest(predictions),ev.digest(gold))


def run(*args):
    return subprocess.run([sys.executable,str(SCRIPT),*map(str,args)],capture_output=True,text=True)


def test_keygen_never_leaves_the_private_key_world_readable(tmp_path):
    out=tmp_path/'key.json'
    assert run('keygen','--output',out).returncode==0
    assert stat.S_IMODE(out.stat().st_mode)==0o600
    assert 'private_key' in json.loads(out.read_text(encoding='utf-8'))
    # 同一路徑不得覆寫既有金鑰。
    assert run('keygen','--output',out).returncode!=0


def test_cli_refuses_to_run_without_a_separately_verified_owner_key(tmp_path):
    key=tmp_path/'k.json';assert run('keygen','--output',key).returncode==0
    body=tmp_path/'plan.json';body.write_text('{}',encoding='utf-8')
    result=run('sign-plan','--input',body,'--private-key',key,'--output',tmp_path/'o.json')
    assert result.returncode!=0 and 'Owner public key' in result.stderr
    bad=run('sign-plan','--input',body,'--private-key',key,'--owner-public','nothex',
        '--output',tmp_path/'o2.json')
    assert bad.returncode!=0 and 'public key encoding' in bad.stderr
