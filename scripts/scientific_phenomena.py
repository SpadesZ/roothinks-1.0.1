"""把檢索與語意失敗分開，再按科學現象逐類計數。

用法：python scripts/scientific_phenomena.py <predictions.jsonl> <gold.jsonl> <new-output-dir>

讀已產生的預測，不重新推論、不改引擎、不用 gold 選句。所有 UNKNOWN、ERROR 與
難例留在分母。現象分類用的是引擎自己發出的警訊代碼，**不是獨立專家標註**；
它量的是「規則說了什麼」，不是「科學上對不對」。
"""
from collections import Counter
import json
from pathlib import Path
import sys

# 警訊代碼 → 科學現象。缺引用在 dev 上恆為 0（每案都有被引用來源），仍留在表中以免誤讀為未量測。
PHENOMENA={'possible_contradiction':'contradiction','counter_evidence_lead':'contradiction_lead',
    'causality_not_established':'causality','numeric_mismatch':'quantity','missing_condition':'condition',
    'modality_mismatch':'simulation_vs_measurement','unsupported_generalization':'extrapolation',
    'no_evidence':'missing_citation','mixed_evidence':'mixed','support_not_established':'support_not_established'}


def read(path):
    with Path(path).open(encoding='utf-8') as handle:return [json.loads(line) for line in handle if line.strip()]


def write(path,payload):
    with Path(path).open('x',encoding='utf-8',newline='\n') as handle:
        handle.write(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')


def gold_label(row):
    labels={g['label'] for groups in row['evidence'].values() for g in groups}
    return next(iter(labels)) if len(labels)==1 else 'NEI' if not labels else 'AMBIGUOUS'


def analyse(predictions,gold_path,output):
    output=Path(output);output.mkdir(exist_ok=False)
    answers={r['id']:r for r in read(gold_path)}
    labels={i:gold_label(r) for i,r in answers.items()}
    rationale={i:{(str(doc),s) for doc,groups in r['evidence'].items() for group in groups for s in group['sentences']}
        for i,r in answers.items()}
    totals=Counter(labels.values())
    # 檢索與語意分開：先問 rationale 有沒有被取回，再問取回後規則做了什麼。
    stage=Counter();stage_by_label={}
    phenomenon=Counter();phenomenon_by_label={};reasons=Counter();reasons_retrieved=Counter()
    false_alarm=Counter()
    rows=[]
    for row in read(predictions):
        case=row['id'];label=labels[case];expected=rationale[case]
        hit=bool(expected&{tuple(x) for x in row['localized']})
        if row['error']:bucket='processing_error'
        elif not expected:bucket='no_official_rationale'
        elif not hit:bucket='retrieval_miss'
        elif row['label']=='UNKNOWN':bucket='retrieved_then_abstained'
        elif row['label']==label:bucket='retrieved_then_correct'
        else:bucket='retrieved_then_wrong'
        stage[bucket]+=1;stage_by_label.setdefault(bucket,Counter())[label]+=1
        codes=set()
        if not row['error']:
            for claim in row['report']['claims']:
                codes.update(f['code'] for f in claim['findings'])
                if claim['relation']=='unknown' and claim['abstention_reason']:
                    reasons[claim['abstention_reason']]+=1
                    if bucket=='retrieved_then_abstained':reasons_retrieved[claim['abstention_reason']]+=1
        for code in codes:
            name=PHENOMENA.get(code,'other:'+code)
            phenomenon[name]+=1;phenomenon_by_label.setdefault(name,Counter())[label]+=1
            # 誤報：在 gold 明示 SUPPORT 的案例上發出矛盾類警訊。
            if label=='SUPPORT' and name in {'contradiction','contradiction_lead','mixed'}:false_alarm[name]+=1
        rows.append(dict(id=case,gold=label,predicted=row['label'],rationale_available=bool(expected),
            rationale_hit=hit,stage=bucket,phenomena=sorted(PHENOMENA.get(c,'other:'+c) for c in codes)))
    retrieved=stage['retrieved_then_abstained']+stage['retrieved_then_correct']+stage['retrieved_then_wrong']
    summary=dict(
        note='開發診斷；SciFact dev 早已曝光，不是盲測。現象分類來自引擎自己的警訊代碼，不是獨立專家標註。',
        scientific_quality_pass=False,expert_gold_available=False,
        condition_modality_causal_gold='NOT MEASURED',probability_calibration='NOT MEASURED',
        cases=len(rows),gold_totals=dict(totals),
        retrieval_vs_semantics=dict(
            official_rationale_cases=sum(1 for r in rows if r['rationale_available']),
            retrieval_recalled=retrieved,retrieval_missed=stage['retrieval_miss'],
            abstained_after_retrieval=stage['retrieved_then_abstained'],
            correct_after_retrieval=stage['retrieved_then_correct'],
            wrong_after_retrieval=stage['retrieved_then_wrong'],
            semantic_failure_share_of_retrieved=stage['retrieved_then_abstained']/retrieved if retrieved else None),
        stage_buckets=dict(stage),stage_by_gold_label={k:dict(v) for k,v in stage_by_label.items()},
        phenomena=dict(phenomenon),phenomena_by_gold_label={k:dict(v) for k,v in phenomenon_by_label.items()},
        contradiction_false_alarms_on_gold_support=dict(false_alarm),
        abstention_reasons_all=dict(reasons),abstention_reasons_after_successful_retrieval=dict(reasons_retrieved))
    write(output/'phenomena-summary.json',summary)
    with (output/'phenomena-cases.jsonl').open('x',encoding='utf-8',newline='\n') as handle:
        for row in rows:handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':analyse(*sys.argv[1:])
