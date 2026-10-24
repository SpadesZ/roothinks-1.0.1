"""I09 離線專家流程。只驗程序，不產生答案、不認證專家或替代獨立保管。

計畫由 Owner 簽署；任務由保管人簽署；兩份標註及分歧裁決各自簽署。
所有輸出 exclusive create。真實 I08 holdout 不由此程式自動開封。
CLI：python scripts/expert_evaluation.py --help
"""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

VERSION='i09-expert-1'
LABELS={'SUPPORT','CONTRADICT','INSUFFICIENT','MIXED'}
FIELDS={'label','evidence_sentence_locators','required_conditions','condition_fidelity',
        'research_modality','causal_or_argument_reversal','unsupported_generalization',
        'missing_citation','uncertainty','reason','confidence'}
SCIENCE=FIELDS-{'reason','uncertainty','confidence'}


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path,value):
    with Path(path).open('xb') as handle:handle.write(encoded(value))


def public(private):
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private)).public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()


def sign(payload,private):
    payload=json.loads(encoded(payload))  # 簽署獨立快照，後續編輯不能連帶改動原始標註物件。
    return dict(payload=payload,signature=base64.b64encode(Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(private)).sign(encoded(payload))).decode('ascii'))


def verify(envelope,key):
    if set(envelope)!={'payload','signature'}:raise ValueError('Invalid signed envelope')
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
        base64.b64decode(envelope['signature'],validate=True),encoded(envelope['payload']))
    return envelope['payload']


def plan(envelope,owner):
    value=verify(envelope,owner)
    if value.get('version')!=VERSION or value.get('owner_public_key')!=owner:raise ValueError('Plan trust root mismatch')
    roles=value['roles']
    if set(roles)!={'custodian','reviewer1','reviewer2','adjudicator'}:raise ValueError('Four independent roles required')
    ids=[v['id'] for v in roles.values()];keys=[v['public_key'] for v in roles.values()]+[owner]
    if len(set(ids))!=4 or len(set(keys))!=5:raise ValueError('Role identities or keys overlap')
    for role,person in roles.items():
        if not person['id'].strip() or not person['expertise'].strip() or person['owner_approved'] is not True:
            raise ValueError('Owner must approve named roles and expertise')
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(person['public_key']))
    for field in ('dataset_sha256','rubric_sha256'):
        if len(value[field])!=64 or any(c not in '0123456789abcdef' for c in value[field]):raise ValueError('Invalid commitment')
    if not value['evaluation_id'].strip() or not value['acceptance_criteria']:raise ValueError('Fixed evaluation and criteria required')
    return value


def assignments(master,signed_plan,owner,private):
    p=plan(signed_plan,owner)
    if public(private)!=p['roles']['custodian']['public_key']:raise ValueError('Custodian key required')
    if digest(master)!=p['dataset_sha256']:raise ValueError('Dataset commitment mismatch')
    sources={s['source_id']:s for s in master['sources']};cases=master['cases']
    if not cases or len(sources)!=len(master['sources']) or len({c['case_id'] for c in cases})!=len(cases):
        raise ValueError('Empty or duplicate dataset identities')
    clean=[]
    for case in cases:
        selected=[]
        for source_id in case['evidence_sources']:
            source=sources[source_id]
            # 只交付該案可見的證據。機械變換提示、既有答案與完整 XML 母版不進任務。
            selected.append({k:source[k] for k in ('source_id','doi','title','text','locator','license_url')})
        prompt=dict(case_id=case['case_id'],claim=case['claim'],sources=selected,inference_scope='provided_text')
        clean.append(dict(prompt=prompt,case_sha256=digest(prompt),annotation=None))
    result=[]
    for role in ('reviewer1','reviewer2'):
        ordered=list(clean);secrets.SystemRandom().shuffle(ordered)
        result.append(sign(dict(version=VERSION,kind='assignment',plan_sha256=digest(signed_plan),
            role=role,reviewer_id=p['roles'][role]['id'],cases=ordered),private))
    return result


def assignment(envelope,p,signed_plan):
    a=verify(envelope,p['roles']['custodian']['public_key'])
    if a.get('version')!=VERSION or a.get('kind')!='assignment' or a.get('plan_sha256')!=digest(signed_plan):raise ValueError('Wrong assignment plan')
    if a['role'] not in ('reviewer1','reviewer2') or a['reviewer_id']!=p['roles'][a['role']]['id']:raise ValueError('Wrong reviewer')
    if not a['cases'] or len({c['prompt']['case_id'] for c in a['cases']})!=len(a['cases']):raise ValueError('Duplicate or empty cases')
    for c in a['cases']:
        if c['case_sha256']!=digest(c['prompt']) or c['annotation'] is not None:raise ValueError('Altered assignment')
    return a


def locator(value,sources):
    if set(value)!={'source_id','start','end','quote'}:raise ValueError('Locator fields required')
    text=sources[value['source_id']]['text'];start=value['start'];end=value['end']
    if type(start)!=int or type(end)!=int or not 0<=start<end<=len(text) or text[start:end]!=value['quote']:
        raise ValueError('Evidence quote or offsets mismatch')


def annotation(value,prompt):
    if not isinstance(value,dict) or set(value)!=FIELDS:raise ValueError('Complete annotation fields required')
    if value['label'] not in LABELS or value['confidence'] not in {'low','medium','high'}:raise ValueError('Invalid label or confidence')
    if value['condition_fidelity'] not in {'preserved','missing','changed','unknown'}:raise ValueError('Invalid condition fidelity')
    for field in ('reason','uncertainty'):
        if not isinstance(value[field],str) or not value[field].strip():raise ValueError('Reason and uncertainty required')
    if set(value['research_modality'])!={'claim','source'} or any(v not in {'simulation','measurement','observational','mixed','unknown'} for v in value['research_modality'].values()):raise ValueError('Invalid modalities')
    for field in ('causal_or_argument_reversal','unsupported_generalization','missing_citation'):
        if value[field] is not None and type(value[field])!=bool:raise ValueError('Flags must be boolean or null')
    sources={s['source_id']:s for s in prompt['sources']}
    if not isinstance(value['evidence_sentence_locators'],list) or not isinstance(value['required_conditions'],list):raise ValueError('Evidence and conditions must be lists')
    for item in value['evidence_sentence_locators']:locator(item,sources)
    if value['label'] in {'SUPPORT','CONTRADICT','MIXED'} and not value['evidence_sentence_locators']:raise ValueError('Decisive labels need localized evidence')
    for condition in value['required_conditions']:
        if set(condition)!={'name','value','locator','claim_status'} or not isinstance(condition['name'],str) or not condition['name'].strip() or not isinstance(condition['value'],str) or not condition['value'].strip():raise ValueError('Condition name and value required')
        locator(condition['locator'],sources)
        if condition['value'] not in condition['locator']['quote'] or condition['claim_status'] not in {'preserved','missing','changed','unknown'}:raise ValueError('Condition value must occur in evidence')
    states={c['claim_status'] for c in value['required_conditions']}
    if value['condition_fidelity']=='preserved' and states-{'preserved'}:raise ValueError('Contradictory condition summary')
    return value


def submission(assigned,filled,signed_plan,owner,private):
    p=plan(signed_plan,owner);a=assignment(assigned,p,signed_plan);role=a['role']
    if public(private)!=p['roles'][role]['public_key']:raise ValueError('Assigned reviewer key required')
    expected={c['prompt']['case_id']:c for c in a['cases']}
    if set(filled)!={'annotations','declarations'} or filled['declarations']!={'independent':True,'no_predictions_seen':True,'conflicts_disclosed':True}:raise ValueError('Reviewer declarations required')
    rows=filled['annotations']
    if len(rows)!=len(expected) or {r['case_id'] for r in rows}!=set(expected):raise ValueError('Missing, duplicate or extra labels')
    for row in rows:
        if set(row)!={'case_id','case_sha256','annotation'} or row['case_sha256']!=expected[row['case_id']]['case_sha256']:raise ValueError('Case binding mismatch')
        annotation(row['annotation'],expected[row['case_id']]['prompt'])
    return sign(dict(version=VERSION,kind='submission',assignment_sha256=digest(assigned),
        role=role,reviewer_id=a['reviewer_id'],annotations=rows,declarations=filled['declarations']),private)


def collect(assigned,submitted,signed_plan,owner):
    p=plan(signed_plan,owner);a=assignment(assigned,p,signed_plan)
    s=verify(submitted,p['roles'][a['role']]['public_key'])
    # 再走同一完整驗證；簽章只證明持鑰者，不能略過欄位或證據檢查。
    if s.get('version')!=VERSION or s.get('kind')!='submission' or s.get('assignment_sha256')!=digest(assigned) or s.get('role')!=a['role'] or s.get('reviewer_id')!=a['reviewer_id']:raise ValueError('Submission binding mismatch')
    if s['declarations']!={'independent':True,'no_predictions_seen':True,'conflicts_disclosed':True}:raise ValueError('Declarations missing')
    rows=s['annotations'];expected={c['prompt']['case_id']:c for c in a['cases']}
    if len(rows)!=len(expected) or {r['case_id'] for r in rows}!=set(expected):raise ValueError('Incomplete labels')
    for r in rows:
        if set(r)!={'case_id','case_sha256','annotation'} or r['case_sha256']!=expected[r['case_id']]['case_sha256']:raise ValueError('Case mismatch')
        annotation(r['annotation'],expected[r['case_id']]['prompt'])
    return a,{r['case_id']:r['annotation'] for r in rows}


def reconcile(assigned,submitted,signed_plan,owner):
    if len(assigned)!=2 or len(submitted)!=2:raise ValueError('Two independent submissions required')
    pairs=[collect(a,s,signed_plan,owner) for a,s in zip(assigned,submitted)]
    if {a['role'] for a,_ in pairs}!={'reviewer1','reviewer2'}:raise ValueError('Same reviewer twice')
    left={c['prompt']['case_id']:c for c in pairs[0][0]['cases']};right={c['prompt']['case_id']:c for c in pairs[1][0]['cases']}
    if left!=right:raise ValueError('Reviewers saw different cases or evidence')
    disputes=[]
    for cid in left:
        if any(pairs[0][1][cid][f]!=pairs[1][1][cid][f] for f in SCIENCE):disputes.append(cid)
    return pairs,left,sorted(disputes)


def freeze(assigned,submitted,resolution,signed_plan,owner,private):
    p=plan(signed_plan,owner)
    if private is not None and public(private)!=p['roles']['custodian']['public_key']:raise ValueError('Custodian key required')
    pairs,cases,disputes=reconcile(assigned,submitted,signed_plan,owner)
    resolved={}
    if disputes:
        if resolution is None:raise ValueError('Unresolved disputes')
        r=verify(resolution,p['roles']['adjudicator']['public_key'])
        if r['plan_sha256']!=digest(signed_plan) or r['submission_sha256']!=[digest(s) for s in submitted]:raise ValueError('Adjudication binding mismatch')
        rows=r['decisions']
        if len(rows)!=len(disputes) or {d['case_id'] for d in rows}!=set(disputes):raise ValueError('Every dispute needs adjudication')
        for row in rows:
            cid=row['case_id']
            if row['case_sha256']!=cases[cid]['case_sha256'] or not row['reason'].strip():raise ValueError('Adjudication case and reason required')
            resolved[cid]=annotation(row['annotation'],cases[cid]['prompt'])
    elif resolution is not None:raise ValueError('Unexpected adjudication')
    gold=[dict(case_id=cid,case_sha256=cases[cid]['case_sha256'],annotation=resolved.get(cid,pairs[0][1][cid]),
        adjudicated=cid in resolved) for cid in sorted(cases)]
    labels=[Counter(v['label'] for v in answers.values()) for _,answers in pairs]
    agreement=sum(pairs[0][1][cid]['label']==pairs[1][1][cid]['label'] for cid in cases)/len(cases)
    chance=sum(labels[0][label]*labels[1][label] for label in LABELS)/len(cases)**2
    # 原兩份標註、理由及裁決完整保留；不以最後答案覆寫歷史。
    payload=dict(version=VERSION,kind='frozen_gold',plan=signed_plan,assignments=assigned,
        submissions=submitted,adjudication=resolution,gold=gold,disputes=disputes,
        agreement=dict(label_agreement=agreement,cohen_kappa=(agreement-chance)/(1-chance) if chance!=1 else None,
            scientific_field_disputes=len(disputes),case_count=len(cases),independent_source_count=len({s['doi'] for c in cases.values() for s in c['prompt']['sources']})),
        scientific_validation_pass=False)
    return sign(payload,private) if private is not None else payload


def verified_gold(envelope,signed_plan,owner):
    p=plan(signed_plan,owner);g=verify(envelope,p['roles']['custodian']['public_key'])
    if g['plan']!=signed_plan:raise ValueError('Different gold plan')
    expected=freeze(g['assignments'],g['submissions'],g['adjudication'],signed_plan,owner,None)
    if g!=expected:raise ValueError('Gold differs from independently signed reviews or adjudication')
    return g


def score(envelope,signed_plan,owner,predictions,prediction_sha256,gold_sha256):
    """預測與 gold 各自先交由保管人固定 hash；此函式不接受未完成標註。"""
    if digest(envelope)!=gold_sha256 or digest(predictions)!=prediction_sha256:raise ValueError('External commitment mismatch')
    g=verified_gold(envelope,signed_plan,owner)
    if predictions['plan_sha256']!=digest(signed_plan) or len(predictions['engine_sha256'])!=64:raise ValueError('Prediction plan or engine mismatch')
    rows=predictions['predictions'];expected={r['case_id']:r for r in g['gold']}
    if len(rows)!=len(expected) or {r['case_id'] for r in rows}!=set(expected):raise ValueError('Incomplete predictions')
    matrix={label:{p:0 for p in LABELS|{'UNKNOWN','ERROR'}} for label in LABELS}
    for r in rows:
        if r['case_sha256']!=expected[r['case_id']]['case_sha256'] or r['label'] not in LABELS|{'UNKNOWN','ERROR'}:raise ValueError('Prediction case or label mismatch')
        matrix[expected[r['case_id']]['annotation']['label']][r['label']]+=1
    binary={}
    for label in LABELS:
        tp=matrix[label][label];fp=sum(m[label] for g,m in matrix.items() if g!=label)
        fn=sum(n for predicted,n in matrix[label].items() if predicted!=label);tn=len(rows)-tp-fp-fn
        binary[label]=dict(tp=tp,fp=fp,fn=fn,tn=tn,precision=tp/(tp+fp) if tp+fp else None,
            recall=tp/(tp+fn) if tp+fn else None)
    return dict(count=len(rows),matrix=matrix,unknown=sum(r['label']=='UNKNOWN' for r in rows),
        one_vs_rest=binary,agreement=g['agreement'],
        errors=sum(r['label']=='ERROR' for r in rows),gold_sha256=gold_sha256,prediction_sha256=prediction_sha256,
        scientific_validation_pass=False,limits='Signatures do not prove expertise, independence or blind custody. No condition/modality performance measured by this label-only score.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['keygen','sign-plan','assign','submit','disputes','adjudicate','freeze','verify-gold','score'])
    parser.add_argument('--input',type=Path);parser.add_argument('--plan',type=Path)
    parser.add_argument('--owner-public',help='Owner 另行核對的公鑰；不可從待驗計畫自我取信')
    parser.add_argument('--private-key',type=Path);parser.add_argument('--assignment',type=Path,nargs=2)
    parser.add_argument('--submission',type=Path,nargs=2);parser.add_argument('--adjudication',type=Path)
    parser.add_argument('--gold',type=Path);parser.add_argument('--gold-sha256');parser.add_argument('--prediction-sha256')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();mode=args.mode
    if mode=='keygen':
        private=Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()).hex()
        save(args.output,dict(private_key=private,public_key=public(private)));args.output.chmod(0o600);return
    private=read(args.private_key)['private_key'] if args.private_key else None
    if mode in {'sign-plan','assign','submit','adjudicate','freeze'} and private is None:raise ValueError('Signing key required')
    if mode=='sign-plan':
        signed=sign(read(args.input),private);plan(signed,args.owner_public);save(args.output,signed);return
    signed_plan=read(args.plan);plan(signed_plan,args.owner_public)
    if mode in {'verify-gold','score'}:
        result=verified_gold(read(args.gold),signed_plan,args.owner_public) if mode=='verify-gold' else score(
            read(args.gold),signed_plan,args.owner_public,read(args.input),args.prediction_sha256,args.gold_sha256)
        save(args.output,result);return
    if mode=='assign':
        result=assignments(read(args.input),signed_plan,args.owner_public,private)
        args.output.mkdir(exist_ok=False)
        for i,row in enumerate(result,1):save(args.output/f'reviewer{i}.json',row)
        return
    if mode=='submit':
        save(args.output,submission(read(args.input)['assignment'],read(args.input)['filled'],signed_plan,args.owner_public,private));return
    assigned=[read(v) for v in args.assignment];submitted=[read(v) for v in args.submission]
    if mode=='adjudicate':
        p=plan(signed_plan,args.owner_public)
        if public(private)!=p['roles']['adjudicator']['public_key']:raise ValueError('Adjudicator key required')
        decisions=read(args.input)
        signed=sign(dict(plan_sha256=digest(signed_plan),submission_sha256=[digest(s) for s in submitted],decisions=decisions),private)
        freeze(assigned,submitted,signed,signed_plan,args.owner_public,None)
        save(args.output,signed);return
    if mode=='disputes':
        _,_,disputes=reconcile(assigned,submitted,signed_plan,args.owner_public)
        save(args.output,dict(disputes=disputes,plan_sha256=digest(signed_plan),submission_sha256=[digest(s) for s in submitted]));return
    save(args.output,freeze(assigned,submitted,read(args.adjudication) if args.adjudication else None,signed_plan,args.owner_public,private))


if __name__=='__main__':main()
