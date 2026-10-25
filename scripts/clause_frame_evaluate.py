"""I13 Gate A 評估器。依 docs/engineering/I13_GATE_A_PROTOCOL.md，兩階段執行。

第一階段 run：核對輸入與 gold hash → 先產生基線、C1、C2 全部預測 → 再讀 gold 評分 →
  產出指標、變動案例、失敗案例，以及**不含 gold 標籤**的稽核封包。
第二階段 finalize：讀入操作者稽核結果，計算 G7 並寫出最終判定。

用法：
    NLTK_DATA=<dir> python scripts/clause_frame_evaluate.py run <inputs.jsonl> <gold.jsonl> <new-output-dir>
    python scripts/clause_frame_evaluate.py finalize <output-dir> <audit.json>

資料早已曝光，結果只能稱開發證據。稽核由實作者執行，不是獨立專家。
"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys

SCRIPTS=Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPTS))
import scientific_benchmark as bench  # noqa: E402

spec=importlib.util.spec_from_file_location('clause_frame_experiment',SCRIPTS/'clause_frame_experiment.py')
lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)

INPUTS_SHA='ded7f378b97aca168037271d0725437b035c32f2ac033563d94122178ced1ce5'
GOLD_SHA='4374b195bd49dd3e2202d013e593955e00e078b7bd6f564307d992d041f4681f'
FROZEN_SHA='4c2034bf8fdbf2ad49276d136a0d37c6201240bd2570bffca5b1fbc0f5f7bd82'
WARNINGS={'missing_condition','numeric_mismatch','modality_mismatch','unsupported_generalization'}
AUDIT_SEED=13;AUDIT_SAMPLE=40


def save(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as handle:
        handle.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def gold_label(row):
    labels={g['label'] for groups in row['evidence'].values() for g in groups}
    return next(iter(labels)) if len(labels)==1 else 'NEI' if not labels else 'AMBIGUOUS'


def run(inputs,gold,output):
    inputs,gold,output=Path(inputs),Path(gold),Path(output)
    if bench.digest(inputs)!=INPUTS_SHA or bench.digest(gold)!=GOLD_SHA:
        raise SystemExit('輸入或 gold 的 hash 與協議不符；拒絕執行。')
    if bench.digest(bench.ENGINE)!=FROZEN_SHA:raise SystemExit('產品引擎不是凍結基線；拒絕執行。')
    output.mkdir(exist_ok=False)
    engines={'baseline':output/'baseline-engine.py'}
    engines['baseline'].write_bytes(bench.ENGINE.read_bytes())
    for variant in ('C1','C2'):engines[variant]=lab.engine(output/(variant+'-engine.py'),variant=variant)
    for name,path in engines.items():
        if name!='baseline' and lab.load(path)._i13_resources() is None:
            raise SystemExit('%s 的 NLTK 資源不可用；拒絕在退回基線的狀態下量測。'%name)
    # 先產生全部預測，之後才讀 gold。
    for name,path in engines.items():bench.predict(inputs,output/(name+'.jsonl'),engine_path=path)
    answers={r['id']:r for r in bench.read(gold)}
    labels={i:gold_label(r) for i,r in answers.items()}
    claims={r['id']:r['claim'] for r in bench.read(inputs)}
    totals=Counter(labels.values())
    preds={name:{r['id']:r for r in bench.read(output/(name+'.jsonl'))} for name in engines}
    metrics={}
    for name in engines:
        bench.score(gold,output/(name+'.jsonl'),output/(name+'-metrics.jsonl'))
        metrics[name]=bench.read(output/(name+'-metrics.jsonl'))[0]
    report=dict(protocol='docs/engineering/I13_GATE_A_PROTOCOL.md',diagnostic_only=True,fresh_blind=False,
        holdout_accessed=False,gold_totals=dict(totals),
        engines={k:bench.digest(v) for k,v in engines.items()},inputs_sha256=INPUTS_SHA,gold_sha256=GOLD_SHA,variants={})
    audit_packets={}
    base=preds['baseline']
    for name in ('C1','C2'):
        cand=preds[name];m=metrics[name]
        decisive=[i for i,r in cand.items() if r['label'] in ('SUPPORT','CONTRADICT')]
        correct=[i for i in decisive if cand[i]['label']==labels[i]]
        errors=[r for r in cand.values() if r['error']]
        autonomy=all(not r['error'] and r['report']['blocks_writing'] is False and r['report']['scientific_proof'] is False
            and r['report']['original_text']==claims[i] for i,r in cand.items())
        changed=[];new_frames=[];negation=[];condition=[]
        for i,r in cand.items():
            b=base[i]
            if r['label']!=b['label']:
                changed.append(dict(id=i,baseline=b['label'],candidate=r['label'],gold=labels[i],claim=claims[i]))
            if r['label'] in ('SUPPORT','CONTRADICT') and not r['error']:
                codes={f['code'] for c in r['report']['claims'] for f in c['findings']}
                if codes&WARNINGS:condition.append(dict(id=i,warnings=sorted(codes&WARNINGS),gold=labels[i],candidate=r['label']))
            if r['label']!=b['label'] and r['label'] in ('SUPPORT','CONTRADICT'):
                for c in r['report']['claims']:
                    for comp in c['comparisons']:
                        for pair in comp.get('i13_frames') or []:
                            new_frames.append(dict(id=i,claim_text=c['text'],source_quote=comp['quote'],
                                claim_frame=pair['claim'],source_frame=pair['source'],outcome=pair['outcome']))
                            if pair['claim']['negative']!=pair['source']['negative']:
                                negation.append(dict(id=i,candidate=r['label'],gold=labels[i],
                                    correct=r['label']==labels[i],claim=c['text'],quote=comp['quote']))
        regressions=[c for c in changed if c['baseline']==c['gold'] and c['candidate']!=c['gold']]
        fp=[dict(id=i,candidate=cand[i]['label'],gold=labels[i],claim=claims[i],
            quotes=[comp['quote'] for c in cand[i]['report']['claims'] for comp in c['comparisons'] if comp.get('i13_frames')] or
                   [c['comparisons'][0]['quote'] for c in cand[i]['report']['claims'] if c['comparisons']])
            for i in decisive if cand[i]['label']!=labels[i]]
        # G7 稽核 (b)：檢索成功的首候選來源句的全部子句 frame，固定種子抽樣，稽核前抽定。
        engine_module=lab.load(engines[name]);population=[]
        for i,r in cand.items():
            if r['error']:continue
            expected={(str(d),s) for d,gs in answers[i]['evidence'].items() for g in gs for s in g['sentences']}
            if not expected or not (expected & {tuple(x) for x in r['localized']}):continue
            for c in r['report']['claims']:
                if not c['comparisons']:continue
                quote=c['comparisons'][0]['quote']
                for clause,frame in engine_module.i13_frames(quote):
                    if frame:population.append(dict(id=i,source_quote=quote,clause=clause,frame=frame))
        sample=random.Random(AUDIT_SEED).sample(population,min(AUDIT_SAMPLE,len(population)))
        audit_packets[name]=dict(note='盲稽核封包：不含 gold 標籤。依協議評為 CORRECT／CROSS_CLAUSE／WRONG_ROLE／UNSCORABLE。',
            decisive_frames=[{k:v for k,v in f.items()} for f in new_frames],
            random_sample=sample,population_size=len(population))
        g4_s_on_c=m['matrix']['CONTRADICT']['SUPPORT'];g4_c_on_s=m['matrix']['SUPPORT']['CONTRADICT']
        tp=m['one_vs_rest']['SUPPORT']['tp']+m['one_vs_rest']['CONTRADICT']['tp']
        precision=len(correct)/len(decisive) if decisive else None
        report['variants'][name]=dict(
            confusion=m['matrix'],one_vs_rest=m['one_vs_rest'],unknown=m['unknown'],errors=len(errors),
            decisive=len(decisive),decisive_correct=len(correct),decisive_precision=precision,
            decisive_false_positives=fp,localization=m['localization'],
            changed_predictions=changed,regressions_vs_baseline=regressions,
            negation_decisions=negation,condition_warning_decisions=condition,
            gates=dict(
                G1='見 G1 執行紀錄（產品對抗控制在候選引擎下）',
                G2_autonomy=autonomy,
                G3_localization_identical=m['localization']==metrics['baseline']['localization'],
                G4_no_cross_polarity_errors=dict(contradict_on_gold_support=g4_c_on_s,support_on_gold_contradict=g4_s_on_c,
                    passed=g4_c_on_s==0 and g4_s_on_c==0),
                G5_precision=dict(value=precision,denominator=len(decisive),threshold=0.80,
                    passed=precision is not None and precision>=0.80),
                G6_true_positives=dict(value=tp,threshold=6,baseline=metrics['baseline']['one_vs_rest']['SUPPORT']['tp']
                    +metrics['baseline']['one_vs_rest']['CONTRADICT']['tp'],passed=tp>=6),
                G7='待稽核',G8='見對抗案例執行紀錄'))
    report['baseline']=dict(confusion=metrics['baseline']['matrix'],one_vs_rest=metrics['baseline']['one_vs_rest'],
        unknown=metrics['baseline']['unknown'],localization=metrics['baseline']['localization'])
    save(output/'gate-a-report.json',report)
    save(output/'audit-packets.json',audit_packets)
    for name,v in report['variants'].items():
        g=v['gates']
        print('%s decisive=%d correct=%d precision=%s TP=%d unknown=%d changed=%d regress=%d | G2=%s G3=%s G4=%s G5=%s G6=%s'%(
            name,v['decisive'],v['decisive_correct'],
            ('%.3f'%v['decisive_precision']) if v['decisive_precision'] is not None else 'n/a',
            g['G6_true_positives']['value'],v['unknown'],len(v['changed_predictions']),len(v['regressions_vs_baseline']),
            g['G2_autonomy'],g['G3_localization_identical'],g['G4_no_cross_polarity_errors']['passed'],
            g['G5_precision']['passed'],g['G6_true_positives']['passed']))
        print('   稽核母體 %d 個 frame；決定性 frame 對 %d 組'%(audit_packets[name]['population_size'],len(audit_packets[name]['decisive_frames'])))


def finalize(output,audit):
    output=Path(output);report=json.loads((output/'gate-a-report.json').read_text(encoding='utf-8'))
    judged=json.loads(Path(audit).read_text(encoding='utf-8'))
    for name,v in report['variants'].items():
        a=judged[name];dec=Counter(a['decisive']);smp=Counter(a['random_sample'])
        bad=dec['CROSS_CLAUSE']+dec['WRONG_ROLE'];dn=sum(dec.values());sn=sum(smp.values())
        v['gates']['G7_audit']=dict(auditor='實作者（非獨立專家），稽核時不看 gold 標籤',
            decisive=dict(counts=dict(dec),bad=bad,denominator=dn,rate=bad/dn if dn else None),
            random_sample=dict(counts=dict(smp),cross_clause=smp['CROSS_CLAUSE'],denominator=sn,
                rate=smp['CROSS_CLAUSE']/sn if sn else None),
            passed=(dn==0 or bad/dn<=0.10) and (sn>0 and smp['CROSS_CLAUSE']/sn<=0.20))
    save(output/'gate-a-final.json',report)
    print(json.dumps({n:v['gates']['G7_audit'] for n,v in report['variants'].items()},ensure_ascii=False,indent=2))


if __name__=='__main__':
    {'run':run,'finalize':finalize}[sys.argv[1]](*sys.argv[2:])
