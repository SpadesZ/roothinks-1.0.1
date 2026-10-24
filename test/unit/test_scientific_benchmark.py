"""以獨立手算的小矩陣檢查評分器，不使用外部 gold 調整產品。"""
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('scientific_benchmark',Path(__file__).resolve().parents[2]/'scripts/scientific_benchmark.py')
bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)


def test_scoring_keeps_abstentions_errors_and_wrong_localizations(tmp_path):
    gold=tmp_path/'gold';pred=tmp_path/'pred';out=tmp_path/'score'
    bench.write(gold,[{'id':i,'evidence':e} for i,e in enumerate([
        {'7':[{'label':'SUPPORT','sentences':[1]}]},
        {'7':[{'label':'SUPPORT','sentences':[1]}]},
        {'8':[{'label':'CONTRADICT','sentences':[0]}]},{}])])
    bench.write(pred,[dict(id=i,label=label,primary=loc,localized=loc,error='injected' if label=='ERROR' else None,report={'claims':[]})
        for i,(label,loc) in enumerate([('SUPPORT',[['7',1]]),('UNKNOWN',[['7',2]]),('ERROR',[]),('SUPPORT',[])])])
    bench.score(gold,pred,out);result=bench.read(out)[0]
    assert result['one_vs_rest']['SUPPORT']==dict(tp=1,fp=1,fn=1,tn=1,precision=.5,recall=.5,fpr=.5,fnr=.5)
    assert result['localization']['primary']==dict(tp=1,fp=1,fn=2,claims=3,precision=.5,recall=1/3)
    assert result['unknown']==1 and result['errors']==1 and result['count']==4
    assert result['scientific_quality_pass'] is False
    with pytest.raises(FileExistsError):bench.score(gold,pred,out)


def test_prepare_uses_citations_not_gold_and_inference_rejects_answer_fields(tmp_path):
    data=tmp_path/'data';data.mkdir();output=tmp_path/'blind'
    bench.write(data/'corpus.jsonl',[dict(doc_id=1,abstract=['The literal source.']),dict(doc_id=2,abstract=['GOLD_ONLY_SENTINEL'])])
    bench.write(data/'claims_dev.jsonl',[dict(id=9,claim='The literal source.',cited_doc_ids=[1],evidence={'2':[{'label':'SUPPORT','sentences':[0]}]})])
    bench.prepare(data,output)
    assert 'GOLD_ONLY_SENTINEL' not in (output/'inputs.jsonl').read_text()
    row=bench.read(output/'inputs.jsonl')[0]
    assert set(row)=={'id','claim','sources'} and row['sources'][0]['source_id']=='1'
    bench.predict(output/'inputs.jsonl',output/'pred')
    assert bench.read(output/'pred')[0]['label']=='SUPPORT'
    row['label']='SUPPORT';bench.write(output/'tainted',[row]);bench.predict(output/'tainted',output/'rejected')
    assert bench.read(output/'rejected')[0]['label']=='ERROR'
