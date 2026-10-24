"""以手算的小案例檢查現象分項診斷；不用外部 gold 調整產品。

驗證：python -m pytest test/unit/test_scientific_phenomena.py -q
"""
import importlib.util
import json
from pathlib import Path
import pytest

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
spec=importlib.util.spec_from_file_location('scientific_phenomena',SCRIPTS/'scientific_phenomena.py')
phen=importlib.util.module_from_spec(spec);spec.loader.exec_module(phen)


def claim(reason,codes):
    return dict(claims=[dict(relation='unknown' if reason else 'text_aligned',abstention_reason=reason,
        findings=[dict(code=c) for c in codes])])


def test_retrieval_and_semantic_failures_are_counted_separately(tmp_path):
    gold=tmp_path/'gold.jsonl';pred=tmp_path/'pred.jsonl';out=tmp_path/'out'
    rows=[
        # 取回正確句但規則解析不了：語意失敗，不是檢索失敗。
        (1,'SUPPORT',{'7':[dict(label='SUPPORT',sentences=[0])]},[['7',0]],'UNKNOWN',
         claim('unsupported_sentence_structure',['support_not_established'])),
        # 取回錯句：檢索失敗。
        (2,'CONTRADICT',{'8':[dict(label='CONTRADICT',sentences=[1])]},[['8',5]],'UNKNOWN',
         claim('unsupported_sentence_structure',['support_not_established'])),
        # 官方沒有 rationale：不得當成已正確識別 NEI。
        (3,'NEI',{},[],'UNKNOWN',claim('unsupported_sentence_structure',['support_not_established'])),
        # 取回且判對。
        (4,'SUPPORT',{'9':[dict(label='SUPPORT',sentences=[0])]},[['9',0]],'SUPPORT',claim(None,[])),
        # gold SUPPORT 上發出矛盾類線索：必須記成誤報。
        (5,'SUPPORT',{'10':[dict(label='SUPPORT',sentences=[0])]},[['10',0]],'UNKNOWN',
         claim('different_arguments_or_conditions',['counter_evidence_lead','support_not_established'])),
    ]
    with gold.open('x',encoding='utf-8',newline='\n') as handle:
        for case,_,evidence,_,_,_ in rows:handle.write(json.dumps(dict(id=case,evidence=evidence))+'\n')
    with pred.open('x',encoding='utf-8',newline='\n') as handle:
        for case,_,_,localized,label,report in rows:
            handle.write(json.dumps(dict(id=case,label=label,localized=localized,primary=localized,
                error=None,report=report))+'\n')
    phen.analyse(pred,gold,out)
    summary=json.loads((out/'phenomena-summary.json').read_text(encoding='utf-8'))
    assert summary['stage_buckets']==dict(retrieved_then_abstained=2,retrieval_miss=1,
        no_official_rationale=1,retrieved_then_correct=1)
    split=summary['retrieval_vs_semantics']
    assert split['official_rationale_cases']==4 and split['retrieval_missed']==1
    assert split['retrieval_recalled']==3 and split['abstained_after_retrieval']==2
    assert split['semantic_failure_share_of_retrieved']==pytest.approx(2/3)
    # NEI 沒有 rationale，不能算進檢索成功，也不能算成判讀正確。
    assert summary['stage_by_gold_label']['no_official_rationale']=={'NEI':1}
    assert summary['contradiction_false_alarms_on_gold_support']=={'contradiction_lead':1}
    assert summary['abstention_reasons_after_successful_retrieval']==dict(
        unsupported_sentence_structure=1,different_arguments_or_conditions=1)
    assert summary['scientific_quality_pass'] is False and summary['expert_gold_available'] is False
    assert summary['condition_modality_causal_gold']=='NOT MEASURED'
    # 保留失敗輪：既有目錄不可覆寫。
    with pytest.raises(FileExistsError):phen.analyse(pred,gold,out)


def test_written_diagnostics_use_lf_so_hashes_reproduce(tmp_path):
    gold=tmp_path/'g.jsonl';pred=tmp_path/'p.jsonl';out=tmp_path/'o'
    gold.write_bytes(json.dumps(dict(id=1,evidence={})).encode()+b'\n')
    pred.write_bytes(json.dumps(dict(id=1,label='UNKNOWN',localized=[],primary=[],error=None,
        report=claim('unsupported_sentence_structure',['support_not_established']))).encode()+b'\n')
    phen.analyse(pred,gold,out)
    for name in ('phenomena-summary.json','phenomena-cases.jsonl'):
        assert b'\r' not in (out/name).read_bytes()
