"""I09 已曝光 SciFact dev 配對診斷。四組先預測，再讀 gold；不開真 holdout。

用法：python scripts/scientific_diagnose.py <I08-engine.py> <inputs.jsonl> <gold.jsonl> <new-output>
保留實驗引擎、逐案錯誤及失敗結果。形態檢索僅為實驗，不進產品。
"""
from collections import Counter
import json
from pathlib import Path
import sys
from scientific_benchmark import ENGINE,digest,read,write,predict,score

MORPH='''
# I09 固定實驗：只改檢索，候選數／前三句預算不變。不用 gold。
_original_rank=rank_candidates
def _morph(text):
    def stem(match):
        word=match.group().casefold()
        if len(word)>6:
            for suffix in ('ing','ed','es','s'):
                if word.endswith(suffix):return word[:-len(suffix)]
        return word
    return re.sub(r'[A-Za-z]+',stem,text)
def rank_candidates(claim,candidates,*,method='bm25',limit=3):
    normalized=[dict(c,quote=_morph(c['quote']),_original=c) for c in candidates]
    return [c['_original'] for c in _original_rank(_morph(claim),normalized,method=method,limit=limit)]
'''


def diagnose(baseline,inputs,gold,output):
    output.mkdir(exist_ok=False)
    # 固定實作及實驗檔，預測完成後才載答案；原始輸入與來源完全不改。
    versions={'I08':baseline,'I09':ENGINE}
    for name,path in list(versions.items()):
        frozen=output/(name+'-engine.py');frozen.write_bytes(path.read_bytes());versions[name]=frozen
        variant=output/(name+'-morph-engine.py');variant.write_text(path.read_text(encoding='utf-8')+MORPH,encoding='utf-8')
        versions[name+'-morph']=variant
    write(output/'protocol.jsonl',[dict(diagnostic_only=True,fresh_blind=False,holdout_accessed=False,
        inputs_sha256=digest(inputs),gold_path=str(gold),engines={k:digest(v) for k,v in versions.items()},
        candidates='provided cited documents only',budget=3,condition_gold='NOT MEASURED',
        morph_adopted=False,runner_sha256=digest(__file__))])
    for name,path in versions.items():predict(inputs,output/(name+'.jsonl'),engine_path=path)
    answers={r['id']:r for r in read(gold)};metrics={};details=[]
    for name in versions:
        predictions=output/(name+'.jsonl');score(gold,predictions,output/(name+'-metrics.jsonl'))
        metric=read(output/(name+'-metrics.jsonl'))[0];buckets=Counter();reasons=Counter();warnings=Counter()
        for row in read(predictions):
            target=answers[row['id']]
            expected={(str(doc),s) for doc,groups in target['evidence'].items() for group in groups for s in group['sentences']}
            labels={g['label'] for groups in target['evidence'].values() for g in groups}
            label=next(iter(labels)) if len(labels)==1 else 'NEI' if not labels else 'AMBIGUOUS'
            hit=bool(expected&{tuple(x) for x in row['localized']})
            bucket=('processing_error' if row['error'] else 'no_gold_rationale' if not expected else
                'retrieval_miss' if not hit else 'retrieved_abstained' if row['label']=='UNKNOWN' else
                'retrieved_correct' if row['label']==label else 'retrieved_wrong')
            buckets[bucket]+=1
            if not row['error']:
                for claim in row['report']['claims']:
                    if claim['relation']=='unknown':reasons[claim['abstention_reason']]+=1
                    warnings.update(f['code'] for f in claim['findings'])
            details.append(dict(experiment=name,id=row['id'],gold_label=label,predicted=row['label'],
                rationale_available=bool(expected),rationale_hit=hit,bucket=bucket))
        metric.update(failure_decomposition=dict(buckets),abstention_reasons=dict(reasons),warnings=dict(warnings))
        metrics[name]=metric
    write(output/'case-diagnosis.jsonl',details)
    write(output/'comparison.jsonl',[dict(diagnostic_only=True,fresh_blind=False,expert_holdout_evaluated=False,
        condition_fidelity='NOT MEASURED: no independent condition gold',groups=metrics,
        engines={k:digest(v) for k,v in versions.items()},inputs_sha256=digest(inputs),gold_sha256=digest(gold))])
    print(json.dumps({k:dict(unknown=m['unknown'],support=m['one_vs_rest']['SUPPORT'],
        contradict=m['one_vs_rest']['CONTRADICT'],localization=m['localization'],failure_decomposition=m['failure_decomposition']) for k,m in metrics.items()}))


if __name__=='__main__':diagnose(*(Path(p) for p in sys.argv[1:]))
