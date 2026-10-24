"""量測已在 I06 固定的六控制；合成結果不當作外部文獻人工 gold。

用法：python scripts/scientific_control_report.py <new-output.jsonl>
此報表器在 SciFact 評分後補寫，只讀既有控制，不改凍結引擎或外部成績。
"""
import json
from pathlib import Path
import sys
from scientific_benchmark import ROOT,engine,write,digest

fixture=ROOT/'test/fixtures/research_integrity_cases.json'
data=json.loads(fixture.read_text(encoding='utf-8'));assess=engine().assess_claims
expected={'all_samples':'unsupported_generalization','missing_temperature':'missing_condition',
    'simulation_as_measurement':'modality_mismatch','missing_evidence':'no_evidence'}
rows=[];counts=dict(tp=0,fp=0,fn=0,tn=0)
for case in data['cases']:
    original=next(s for s in data['sources'] if s['id']==case['source'])
    source=dict(original,locator={'page':original['page']})
    report=assess(case['claim'],[] if case.get('missing_evidence') else [source],required_conditions=case['conditions'])
    flags={f['code'] for c in report['claims'] for f in c['findings']}
    observed={v.casefold() for c in report['claims'] for f in c['findings'] if f['code']=='missing_condition' for v in f['missing']}
    # 固定人工條件詞表；不能用引擎抽取結果當期望值。
    terms=['sample A','300 K','10 GHz','chip facet'] if case['source']=='measurement' else ['device B','1550 nm','simulation']
    conditions=[]
    if not case.get('missing_evidence'):
        for term in terms:
            missing=term.casefold() not in case['claim'].casefold();detected=term.casefold() in observed
            counts['tp' if missing and detected else 'fn' if missing else 'fp' if detected else 'tn']+=1
            conditions.append(dict(term=term,missing=missing,detected_missing=detected))
    invariant=report['original_text']==case['claim']
    localized=all(source['text'][c['start']:c['end']]==c['quote'] and c['locator']=={'page':original['page']}
        for claim in report['claims'] for c in claim['comparisons'])
    assert invariant and localized
    if case['id'] in expected:assert expected[case['id']] in flags,(case['id'],flags)
    rows.append(dict(id=case['id'],expected_warning=expected.get(case['id']),findings=sorted(flags),
        original_unchanged=invariant,quote_offsets_exact=localized,condition_comparisons=conditions))
write(Path(sys.argv[1]),[dict(synthetic_only=True,fixture_sha=digest(fixture),condition_term_confusion=counts,
    expected_warnings_detected=sum(r['expected_warning'] in r['findings'] for r in rows if r['expected_warning']),expected_warning_count=len(expected),
    cases=rows,limits='Fixed authored controls; lexical terms only; no expert scientific quality or independent condition/modality gold.')])
