"""I08 已知 SciFact dev 的配對診斷，分開比較 R 與 A，絕不稱新盲測。

用法：python scripts/scientific_compare.py <frozen-engine.py> <inputs.jsonl> <gold.jsonl> <new-output-dir>
先產生全部五組預測，再讀 gold；R0A0 與 582e646 的 labels／定位必須相同。
"""
from collections import Counter
from pathlib import Path
import sys
from scientific_benchmark import read,write,digest,predict,score

FROZEN_SHA='f64cb7e7d520ef47779bb33fd8b9b5e6a1d421c5607c596e66f9ba1b8f7b1b9d'


def compare(baseline,inputs,gold,output):
    if digest(baseline)!=FROZEN_SHA:raise ValueError('Baseline is not the frozen 582e646 engine')
    output.mkdir(exist_ok=False)
    predict(inputs,output/'baseline.jsonl',engine_path=baseline)
    for retrieval in ('jaccard','bm25'):
        for assessment in ('legacy','compositional'):
            name=('R0' if retrieval=='jaccard' else 'R1')+('A0' if assessment=='legacy' else 'A1')
            predict(inputs,output/(name+'.jsonl'),options=dict(retrieval=retrieval,assessment=assessment))
    predict(inputs,output/'product.jsonl')
    def signature(path):return [(r['id'],r['label'],r['primary'],r['localized'],r['error']) for r in read(path)]
    parity=signature(output/'baseline.jsonl')==signature(output/'R0A0.jsonl')
    write(output/'parity.jsonl',[dict(matched=parity,baseline_sha=digest(baseline),inputs_sha=digest(inputs))])
    if not parity:raise ValueError('R0A0 differs from frozen baseline; stop before interpreting improvement')
    answers={row['id']:row for row in read(gold)};metrics={}
    for name in ('baseline','R0A0','R1A0','R0A1','R1A1','product'):
        path=output/(name+'.jsonl');score(gold,path,output/(name+'-metrics.jsonl'))
        metric=read(output/(name+'-metrics.jsonl'))[0];rows=read(path)
        flags=Counter(f['code'] for r in rows if not r['error'] for c in r['report']['claims'] for f in c['findings'])
        reason=Counter(c.get('abstention_reason','legacy_rule_unknown') for r in rows if not r['error'] for c in r['report']['claims'] if c['relation']=='unknown')
        hit=dict(primary=0,localized=0,denominator=0)
        for row in rows:
            expected={(str(doc),s) for doc,groups in answers[row['id']]['evidence'].items() for group in groups for s in group['sentences']}
            if not expected:continue
            hit['denominator']+=1
            for key in ('primary','localized'):hit[key]+=bool({tuple(s) for s in row[key]} & expected)
        metric.update(retrieval_claim_hits=hit,flags=dict(flags),abstention_reasons=dict(reason),
            mixed_evidence=sum(c['relation']=='mixed_evidence' for r in rows if not r['error'] for c in r['report']['claims']))
        metrics[name]=metric
    result=dict(diagnostic_only=True,fresh_blind=False,expert_holdout_evaluated=False,baseline_parity=parity,
        baseline_sha=digest(baseline),candidate_sha=digest(Path(__file__).parents[1]/'app/core_pro/manuscript/scientific_advisory.py'),
        input_sha=digest(inputs),gold_sha=digest(gold),groups=metrics)
    write(output/'comparison.jsonl',[result])
    print(__import__('json').dumps({name:dict(unknown=m['unknown'],support=m['one_vs_rest']['SUPPORT'],contradict=m['one_vs_rest']['CONTRADICT'],localization=m['localization']) for name,m in metrics.items()}))


if __name__=='__main__':compare(*(Path(p) for p in sys.argv[1:]))
