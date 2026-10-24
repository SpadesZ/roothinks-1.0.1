"""I07 可重現 label-blind 評估；prepare 隔離既有 gold，predict 不讀答案。

純 stdlib，僅接受明示的公開資料路徑。流程及限制見 SAI_SCIENTIFIC_ADVISORY_PROTOCOL.md。
輸出使用 exclusive create，保留失敗輪；不得在同批 gold 上調整引擎後重稱盲測。
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'app/core_pro/manuscript/scientific_advisory.py'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    with Path(path).open(encoding='utf-8') as handle:return [json.loads(line) for line in handle if line.strip()]


def write(path,rows):
    # newline 固定 LF：證據 hash 必須跨平台一致，否則獨立稽核在別的作業系統上重算不出同一個值。
    with Path(path).open('x',encoding='utf-8',newline='\n') as handle:
        for row in rows:handle.write(json.dumps(row,ensure_ascii=False)+'\n')


def prepare(data,output):
    output.mkdir(exist_ok=False)
    corpus={r['doc_id']:r for r in read(data/'corpus.jsonl')}
    inputs=[];gold=[]
    for row in read(data/'claims_dev.jsonl'):
        sources=[]
        # 候選只來自 cited_doc_ids，絕不從 evidence annotations 選來源或句子。
        for doc_id in row['cited_doc_ids']:
            doc=corpus[doc_id];text='';sentences=[]
            for index,sentence in enumerate(doc['abstract']):
                start=len(text);text+=sentence+'\n'
                sentences.append(dict(index=index,start=start,end=start+len(sentence)))
            sources.append(dict(source_id=str(doc_id),text=text.rstrip('\n'),locator={'doc_id':doc_id},sentences=sentences))
        inputs.append(dict(id=row['id'],claim=row['claim'],sources=sources))
        gold.append(dict(id=row['id'],evidence=row['evidence']))
    assert len({r['id'] for r in inputs})==len(inputs)
    write(output/'inputs.jsonl',inputs);write(output/'evaluator-only-gold.jsonl',gold)
    write(output/'preparation.jsonl',[dict(count=len(inputs),unique_cited_documents=len({s['source_id'] for r in inputs for s in r['sources']}),input_sha=digest(output/'inputs.jsonl'),gold_sha=digest(output/'evaluator-only-gold.jsonl'),
        files={name:digest(data/name) for name in ('corpus.jsonl','claims_dev.jsonl')})])


def engine(path=ENGINE):
    spec=importlib.util.spec_from_file_location('scientific_advisory',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def predict(inputs,output,*,engine_path=ENGINE,options=None):
    assess=engine(engine_path).assess_claims;rows=[]
    for row in read(inputs):
        try:
            assert set(row)=={'id','claim','sources'},'Unexpected inference fields'
            assert all(set(s)=={'source_id','text','locator','sentences'} for s in row['sources'])
            report=assess(row['claim'],row['sources'],**(options or {}))
            relations=[c['relation'] for c in report['claims']]
            label='CONTRADICT' if 'possible_contradiction' in relations else 'SUPPORT' if relations and all(r=='text_aligned' for r in relations) else 'UNKNOWN'
            localized=[];primary=[]
            for claim in report['claims']:
                for rank,comparison in enumerate(claim['comparisons']):
                    source=next(s for s in row['sources'] if s['source_id']==comparison['source_id'])
                    for sentence in source['sentences']:
                        if max(sentence['start'],comparison['start'])<min(sentence['end'],comparison['end']):
                            pair=[source['source_id'],sentence['index']]
                            if pair not in localized:localized.append(pair)
                            if rank==0 and pair not in primary:primary.append(pair)
            rows.append(dict(id=row['id'],label=label,localized=localized,primary=primary,report=report,error=None))
        except Exception as exc:
            rows.append(dict(id=row['id'],label='ERROR',localized=[],primary=[],error=type(exc).__name__+': '+str(exc)))
    write(output,rows)
    write(output.with_suffix('.manifest.jsonl'),[dict(engine_sha=digest(engine_path),options=options or {},runner_sha=digest(__file__),inputs_sha=digest(inputs),predictions_sha=digest(output),count=len(rows))])


def ratio(a,b):return a/b if b else None


def score(gold_file,predictions,output):
    gold=read(gold_file);pred=read(predictions);by_id={r['id']:r for r in pred}
    assert len(by_id)==len(pred),'Duplicate predictions'
    assert set(by_id)=={r['id'] for r in gold},'Missing or extra predictions'
    matrix={g:{p:0 for p in ('SUPPORT','CONTRADICT','UNKNOWN','ERROR')} for g in ('SUPPORT','CONTRADICT','NEI','AMBIGUOUS')}
    localized={k:dict(tp=0,fp=0,fn=0,claims=0) for k in ('primary','localized')}
    for row in gold:
        labels={g['label'] for groups in row['evidence'].values() for g in groups}
        label=next(iter(labels)) if len(labels)==1 else 'NEI' if not labels else 'AMBIGUOUS'
        prediction=by_id[row['id']];matrix[label][prediction['label']]+=1
        expected={(str(doc),sentence) for doc,groups in row['evidence'].items() for group in groups for sentence in group['sentences']}
        if expected:
            for key,counts in localized.items():
                selected={tuple(x) for x in prediction[key]}
                counts['tp']+=len(selected & expected);counts['fp']+=len(selected-expected);counts['fn']+=len(expected-selected);counts['claims']+=1
    binary={}
    for label in ('SUPPORT','CONTRADICT'):
        tp=matrix[label][label];fp=sum(values[label] for key,values in matrix.items() if key!=label)
        fn=sum(value for key,value in matrix[label].items() if key!=label);tn=len(gold)-tp-fp-fn
        binary[label]=dict(tp=tp,fp=fp,fn=fn,tn=tn,precision=ratio(tp,tp+fp),recall=ratio(tp,tp+fn),fpr=ratio(fp,fp+tn),fnr=ratio(fn,fn+tp))
    for counts in localized.values():
        counts.update(precision=ratio(counts['tp'],counts['tp']+counts['fp']),recall=ratio(counts['tp'],counts['tp']+counts['fn']))
    write(output,[dict(count=len(gold),matrix=matrix,one_vs_rest=binary,localization=localized,
        decisive_coverage=ratio(sum(r['label'] in ('SUPPORT','CONTRADICT') for r in pred),len(pred)),
        unknown=sum(r['label']=='UNKNOWN' for r in pred),errors=sum(r['label']=='ERROR' for r in pred),
        unique_selected_comparison_documents=len({c['source_id'] for r in pred if not r['error'] for claim in r['report']['claims'] for c in claim['comparisons']}),
        prediction_sha=digest(predictions),gold_sha=digest(gold_file),scientific_quality_pass=False,
        limits='Public dev label-blind; UNKNOWN is abstention, not a verified NEI finding. Localization scores union of official rationales; no condition/modality gold.')])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','predict','score']);parser.add_argument('paths',nargs='+',type=Path)
    args=parser.parse_args()
    {'prepare':prepare,'predict':predict,'score':score}[args.mode](*args.paths)
