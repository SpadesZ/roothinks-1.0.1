# 目的：評分固定合成管線觀測，區分字串傳遞、來源條件及未受支持的控制敘述。
# 邊界：目錄外文字標未知；不用 LLM 自評，不輸出科學通過或品質改善結論。
# 驗證：python -m pytest test/unit/test_research_integrity_benchmark.py -q
import argparse
import hashlib
import json
from pathlib import Path


def score(inputs,gold,observations):
    sources = {s['id']:s for s in inputs['sources']}
    cases = {c['id']:c for c in inputs['cases']}
    if len(observations)!=len(cases) or {o['case_id'] for o in observations}!=set(cases):
        raise ValueError('所有預先固定案例必須各出現一次；不可剔除失敗案例。')
    metrics = dict(requested=len(cases),accepted=0,rejected=0,traceable_accepted=0,input_conditions_preserved=0,
        prompt_conditions_preserved=0,evidence_condition_tokens_present=0,evidence_condition_tokens_required=0,
        unsupported_controls=0,unsupported_controls_returned=0,missing_condition_controls_returned=0,
        unavailable_evidence_rejected=0,unknown_outputs=0,scientific_validation_claimed=0)
    for observation in observations:
        case = cases[observation['case_id']]
        verdict = gold['verdicts'][case['id']]
        if verdict=='unsupported':
            metrics['unsupported_controls'] += 1
        if not observation['accepted']:
            metrics['rejected'] += 1
            metrics['unavailable_evidence_rejected'] += int(verdict=='unavailable_evidence')
            continue
        metrics['accepted'] += 1
        source = sources[case['source']]
        context = observation['context']
        evidence = context['evidence']
        metrics['traceable_accepted'] += int(evidence['source_id']==source['source_id'] and evidence['locator']['page']==source['page']
                                           and evidence['text']==source['text'] and bool(evidence.get('fingerprint')))
        metrics['input_conditions_preserved'] += int(context['conditions']==case['conditions'] and context['forbidden']==case['forbidden'])
        metrics['prompt_conditions_preserved'] += int(case['conditions'] in observation['prompt'] and case['forbidden'] in observation['prompt'])
        required = gold['required_conditions'][case['source']]
        metrics['evidence_condition_tokens_required'] += len(required)
        metrics['evidence_condition_tokens_present'] += sum(token.casefold() in context['conditions'].casefold() for token in required)
        known = observation.get('output','').strip()==case['claim']
        metrics['unknown_outputs'] += int(not known)
        metrics['unsupported_controls_returned'] += int(known and verdict=='unsupported')
        metrics['missing_condition_controls_returned'] += int(known and verdict=='missing_condition')
        metrics['scientific_validation_claimed'] += int(context.get('scientific_status')!='author_confirmed_not_independently_validated')
    return dict(status='SYNTHETIC_PIPELINE_MEASURED_SCIENTIFIC_ACCEPTANCE_OPEN',metrics=metrics,
        limitations=['provider is a controlled substitute','catalogue matching is not semantic validation',
                     'two synthetic sources are not independent scientific samples','no claim of improved scientific quality'])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description='固定合成研究完整性評分；不判定真實科學正確性。')
    parser.add_argument('observations',type=Path)
    parser.add_argument('--inputs',type=Path,default=Path(__file__).resolve().parents[1]/'test/fixtures/research_integrity_cases.json')
    parser.add_argument('--gold',type=Path,default=Path(__file__).resolve().parents[1]/'test/fixtures/research_integrity_gold.json')
    args = parser.parse_args()
    observed = json.loads(args.observations.read_text(encoding='utf-8'))
    print(json.dumps(score(json.loads(args.inputs.read_text(encoding='utf-8')),json.loads(args.gold.read_text(encoding='utf-8')),
        observed['observations']),ensure_ascii=False,indent=2))
