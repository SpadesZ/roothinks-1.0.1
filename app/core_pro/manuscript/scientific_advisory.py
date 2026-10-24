# 目的：提供可重現、可定位的主張／證據建議；規則涵蓋不到時明確棄權，不修改原文。
# 上下游：作者比較 API／生成後提示／外部盲測 → 純函式 → 原句、來源定位、警訊及未知。
# 邊界：沒有語意模型或科學真偽權威；文字對齊不等於證明，不阻擋任何研究動作。
# 驗證：test_scientific_advisory.py；規則及外部量測界線見 SAI_SCIENTIFIC_ADVISORY_PROTOCOL.md。
import hashlib
import math
import re
from collections import Counter
from decimal import Decimal

ENGINE_VERSION='advisory-rules-4'
_STOP=set('a an the this that these those is are was were be been being of at on in for to and or with by from as it its we our'.split())
_LEMMA={w:root for root,words in {
    'increase':'increase increases increased increasing', 'decrease':'decrease decreases decreased decreasing',
    'reduce':'reduce reduces reduced reducing reduction', 'improve':'improve improves improved improvement',
}.items() for w in words.split()}
_QUANTITY=re.compile(r'(?<![\w.])([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*(°\s*C|GHz|MHz|kHz|Hz|nm|[μµu]m|mm|mW|dB|mg|mM|nM|K|%)(?!\w)',re.I)
_MODE_SIM=re.compile(r'\b(simulat\w*|predicted|numerical|theoretical)\b|模擬|理論預測',re.I)
_MODE_MEAS=re.compile(r'\b(measur\w*|fabricat\w*|experimentally|experimental)\b|實測|量測|實驗量得',re.I)
_NEGATION=re.compile(r'\b(no|not|never|without|neither)\b|未觀察|無顯著|沒有|並未|不會',re.I)
_APPLICABILITY={'k','°c','ghz','mhz','khz','hz','nm','μm','mm','mw','mg','mmolar','nmolar'}


def _canonical(text):
    return ' '.join(re.findall(r'\w+',str(text).casefold(),re.UNICODE))


def _tokens(text):
    return {_LEMMA.get(w,w) for w in _canonical(text).split() if w not in _STOP}


def _sentences(text):
    # ponytail: 依句尾標點切分；縮寫／跨句論證可能仍需人工核對，未宣稱完整 claim extraction。
    return [(m.start(),m.end(),m.group()) for m in re.finditer(r'\S.*?(?:[.!?。！？](?=\s|$)|$)',text,re.S)]


def _positive(text):
    text=re.sub(r'\bnot only\b','',text,flags=re.I)
    text=re.sub(r'\b(?:no|not|never|without|neither)\b[^.;!?。！？]*','',text,flags=re.I)
    return re.sub(r'(?:未|沒有|並未|不曾)[^。；;.!?]*','',text)


def _quantities(text):
    found={}
    for match in _QUANTITY.finditer(text):
        unit=match[2].replace(' ','').replace('µ','μ')
        # 大小寫有物理意義：mm 是毫米，mM 是濃度，不互換。
        unit={'mM':'mmolar','nM':'nmolar','um':'μm'}.get(unit,unit.casefold())
        found.setdefault(unit,{})[str(Decimal(match[1]).normalize())]=match.group()
    return found


def _conditions(text):
    result=[]
    for unit,values in _quantities(text).items():
        if unit in _APPLICABILITY and len(values)==1:result.extend(values.values())
    result.extend(m.group() for m in re.finditer(r'\b(?:sample|device)\s+[A-Za-z](?![A-Za-z])|\bin (?:vitro|vivo)\b|\bmice\b|\bmurine\b',text,re.I))
    return list(dict.fromkeys(result))


def rank_candidates(claim,candidates,*,method='bm25',limit=3):
    """只排名來源原句；不讀答案、不修改候選，也不推定語意支持。"""
    words=_tokens(claim)
    if method=='jaccard':
        return sorted(candidates,key=lambda c:len(words & _tokens(c['quote']))/max(1,len(words | _tokens(c['quote']))),reverse=True)[:limit]
    if method!='bm25':raise ValueError('未知檢索方法。')
    if not candidates:return []
    documents=[Counter(_LEMMA.get(w,w) for w in _canonical(c['quote']).split() if w not in _STOP) for c in candidates]
    average=sum(sum(d.values()) for d in documents)/len(documents) or 1
    df=Counter(word for d in documents for word in d)
    def score(document):
        norm=1.2*(.25+.75*sum(document.values())/average)
        return sum(math.log(1+(len(documents)-df[word]+.5)/(df[word]+.5))*document[word]*2.2/(document[word]+norm)
            for word in words if document[word])
    return [c for _,c in sorted(zip((score(d) for d in documents),candidates),key=lambda item:item[0],reverse=True)[:limit]]


_RELATIONS={word:(root,family,sign) for root,family,sign,forms in (
    ('increase','quantity',1,'increase increases increased increasing'),
    ('decrease','quantity',-1,'decrease decreases decreased decreasing reduce reduces reduced reducing'),
    ('improve','quality',1,'improve improves improved improving'),
    ('worsen','quality',-1,'worsen worsens worsened worsening'),
    ('activate','activity',1,'activate activates activated activating'),
    ('inhibit','activity',-1,'inhibit inhibits inhibited inhibiting'),
    ('promote','promotion',1,'promote promotes promoted promoting'),
    ('suppress','promotion',-1,'suppress suppresses suppressed suppressing'),
    ('cause','causal',1,'cause causes caused causing'),
    ('associate','association',1,'associate associates associated associating'),
    ('correlate','correlation',1,'correlate correlates correlated correlating'),
) for word in forms.split()}
_HEDGE=re.compile(r'\b(may|might|could|hypothes\w*|suggest\w*|potential\w*)\b',re.I)
_UNESTABLISHED=re.compile(r'\b(?:no|insufficient|lack of) evidence\b|\b(?:unknown|unclear) whether\b|\bnot (?:yet )?(?:tested|established|demonstrated|measured)\b',re.I)
_REPORTED_STANCE=re.compile(r'\b(?:not true|false that|denies|deny|denied)\b',re.I)
_NEGATIVE_WORDS={'not','no','never','without','neither'}
# 已判定但未解析的範圍訊號；只能當待核對線索，不能升格為矛盾判定。
_LEAD_SCOPE_REASONS={'unresolved_stance_scope','source_does_not_establish_claim','significance_is_not_direction'}


def _relation_frame(text):
    text=re.sub(r'^(?:we (?:found|observed|showed) that|the results showed that)\s+','',text,flags=re.I)
    text=re.sub(r'^(?:background|methods|results|conclusions?)\s*:?\s+','',text,flags=re.I)
    tokens=_canonical(text).split()
    predicates=[i for i,w in enumerate(tokens) if w in _RELATIONS]
    if len(predicates)!=1:return None
    index=predicates[0];root,family,sign=_RELATIONS[tokens[index]]
    def argument(words):
        # ponytail: 僅規則句型與保守複數正規化；被動句、複合論證交由人核對，不聲稱 NLI。
        return tuple(w[:-1] if len(w)>5 and w.endswith('s') and not w.endswith(('ss','is','us','es')) else w
            for w in words if w not in _STOP|_NEGATIVE_WORDS|{'did','does','do','has','have','had','significantly'})
    subject=argument(tokens[:index]);object=argument(tokens[index+1:])
    if not subject or not object:return None
    return dict(subject=subject,object=object,family=family,sign=sign,
        negative=bool(set(tokens[:index])&_NEGATIVE_WORDS))


def assess_relation(claim,quote):
    """獨立的有界語句關係比較；主客體、否定及弱語氣不靠詞袋相似度猜測。"""
    def result(relation,reason):return dict(relation=relation,reason=reason,scientific_proof=False)
    if _REPORTED_STANCE.search(quote):return result('unknown','unresolved_stance_scope')
    if _UNESTABLISHED.search(quote):return result('unknown','source_does_not_establish_claim')
    if _HEDGE.search(quote) and not _HEDGE.search(claim):return result('unknown','source_hedged')
    if re.search(r'\b(?:not|no)\s+(?:statistically\s+)?significant(?:ly)?\b',quote,re.I):
        return result('unknown','significance_is_not_direction')
    values=_quantities(claim);source_values=_quantities(quote)
    if any(unit in source_values and set(numbers)-set(source_values[unit]) for unit,numbers in values.items()):
        return result('unknown','different_quantities')
    if ' '+_canonical(claim)+' ' in ' '+_canonical(quote)+' ':return result('text_aligned','exact_text_only')
    left=_relation_frame(claim);right=_relation_frame(quote)
    if not left or not right:return result('unknown','unsupported_sentence_structure')
    if (left['subject'],left['object'])!=(right['subject'],right['object']):return result('unknown','different_arguments_or_conditions')
    if left['family']!=right['family']:return result('unknown','different_relationship')
    if left['sign']!=right['sign'] and (left['negative'] or right['negative']):
        return result('unknown','negated_direction_is_not_inverse')
    opposed=(left['sign']!=right['sign']) != (left['negative']!=right['negative'])
    return result('possible_contradiction' if opposed else 'text_aligned','opposed_predicate_or_negation' if opposed else 'ordered_predicate_arguments')


def direction_leads(claim,comparisons):
    """保留未解析的方向／否定／立場差異作為待核對線索；不給關係判定，也不改變標籤。"""
    left=_relation_frame(claim);leads=[]
    for rank,comparison in enumerate(comparisons):
        quote=comparison['quote'];signals=[]
        right=_relation_frame(quote)
        # 同一關係族但方向相反，而主客體未能對應：可能是被動句、縮寫或不同實驗，規則無法解析。
        if left and right and left['family']==right['family'] and (
            (left['sign']!=right['sign'])!=(left['negative']!=right['negative'])) and (
            (left['subject'],left['object'])!=(right['subject'],right['object'])):
            signals.append('direction_differs_arguments_unresolved')
        if comparison.get('reason') in _LEAD_SCOPE_REASONS:signals.append(comparison['reason'])
        # 否定存在性不同；沿用既有 0.35 相似度門檻，只看最相近候選，不另設新參數。
        if rank==0 and bool(_NEGATION.search(claim))!=bool(_NEGATION.search(quote)) and comparison['similarity']>=0.35:
            signals.append('negation_scope_differs')
        if signals:leads.append(dict(comparison=rank,source_id=comparison['source_id'],
            sentence_index=comparison['sentence_index'],signals=signals))
    return leads


def assess_claims(text,sources,*,required_conditions='',retrieval='bm25',assessment='advisory'):
    if not isinstance(text,str) or not text.strip() or len(text)>16000:
        raise ValueError('請提供 1–16000 字的主張；不會截掉尾端條件。')
    if not isinstance(sources,list) or len(sources)>8 or any(not isinstance(s,dict) or not isinstance(s.get('text'),str) or len(s['text'])>16000 for s in sources):
        raise ValueError('最多比較八份完整證據，每份不得超過 16000 字。')
    if assessment not in {'legacy','compositional','advisory'}:raise ValueError('未知判讀方法。')
    sentences=_sentences(text)
    if len(sentences)>64:raise ValueError('超過 64 個句子；請縮小比較範圍，原文不變。')
    candidates=[]
    for source in sources:
        for index,(start,end,quote) in enumerate(_sentences(source['text'])):
            candidates.append(dict(source=source,sentence_index=index,start=start,end=end,quote=quote))
    claims=[]
    for number,(start,end,claim) in enumerate(sentences):
        words=_tokens(claim)
        ranked=rank_candidates(claim,candidates,method=retrieval)
        comparisons=[{**{k:c[k] for k in ('sentence_index','start','end','quote')},
            'source_id':c['source'].get('source_id',c['source'].get('id','unknown')),
            'evidence_id':c['source'].get('id'),'fingerprint':c['source'].get('fingerprint') or hashlib.sha256(c['source']['text'].encode()).hexdigest(),
            'locator':c['source'].get('locator',{}),'similarity':round(len(words & _tokens(c['quote']))/max(1,len(words | _tokens(c['quote']))),4)} for c in ranked]
        issues=[]
        def flag(code,message,comparison=None,**detail):
            if not any(i['code']==code for i in issues):issues.append(dict(code=code,message=message,comparison=comparison,**detail))
        relation='unknown'
        if not sources:flag('no_evidence','尚未選取證據。可保留假說、繼續寫作或稍後補證據。')
        required=[s.strip() for s in str(required_conditions).split(';') if s.strip()]
        # 不把不同來源的條件聯集當成單一實驗條件；只有最相近文字作規則判讀。
        source_conditions=_conditions(ranked[0]['quote'] if assessment!='legacy' else ranked[0]['source']['text']) if ranked else []
        for comparison,candidate in zip(comparisons,ranked):
            comparison['source_conditions']=_conditions(candidate['source']['text'])
        missing=[c for c in source_conditions+required if _canonical(c) not in _canonical(claim)]
        if missing:flag('missing_condition','本句未明示部分來源／作者條件；請核對同樣本及鄰文，不能推定條件已失效。',missing=list(dict.fromkeys(missing)))
        claim_values=_quantities(claim)
        for cidx,c in enumerate(ranked[:1]):
            source_text=c['source']['text'] if assessment=='legacy' else c['quote']
            source_values=_quantities(source_text)
            different=[v for unit,values in claim_values.items() if unit in source_values for key,v in values.items() if key not in source_values[unit]]
            if different:flag('numeric_mismatch','主張數值未在同單位來源中對應；可能是不同條件／指標，需人工核對。',cidx,unmatched=different)
            positive_claim=_positive(claim)
            positive_source=_positive(source_text)
            if _MODE_SIM.search(positive_source) and not _MODE_MEAS.search(positive_source) and _MODE_MEAS.search(positive_claim):
                flag('modality_mismatch','來源呈現模擬／預測，本句卻寫成實驗／量測；請核對研究類型。',cidx)
            if re.search(r'\b(all|every|always|universally)\b|所有|全部|任何',positive_claim,re.I) and (
                re.search(r'\bonly\b|not tested|not measured|僅|未測',source_text,re.I) or source_conditions):
                flag('unsupported_generalization','本句擴及全部／任何條件，來源有樣本或適用界線；目前來源未建立這項外推。',cidx)
            score=comparisons[cidx]['similarity']
            source_words=_tokens(c['quote'])
            opposite=(bool(words & {'increase','improve','higher','promote'}) and bool(source_words & {'decrease','reduce','lower','inhibit'})) or (
                bool(words & {'decrease','reduce','lower','inhibit'}) and bool(source_words & {'increase','improve','higher','promote'}))
            negative_diff=bool(_NEGATION.search(claim)) != bool(_NEGATION.search(c['quote']))
            if assessment=='legacy' and score>=0.35 and (opposite or negative_diff):
                flag('possible_contradiction','相近文字中的方向／否定不同；可能有矛盾，也可能是不同研究條件。',cidx)
            if _canonical(claim) and _canonical(claim) in _canonical(c['quote']):relation='text_aligned'
        abstention='legacy_rule_unknown'
        if assessment in {'legacy','advisory'}:
            if any(i['code']=='possible_contradiction' for i in issues):relation='possible_contradiction'
            elif issues:relation='unknown'
        if assessment!='legacy':
            usable=[]
            for index,comparison in enumerate(comparisons):
                judged=assess_relation(claim,comparison['quote']);comparison.update(judged)
                comparison['applicable_conditions']=_conditions(comparison['quote'])
                comparison['missing_conditions']=[c for c in comparison['applicable_conditions']+required if _canonical(c) not in _canonical(claim)]
                comparison['other_context_conditions']=[c for c in comparison['source_conditions'] if c not in comparison['applicable_conditions']]
                if judged['relation']!='unknown' and not comparison['missing_conditions']:usable.append((index,judged['relation']))
            observed={r for _,r in usable}
            if observed=={'text_aligned','possible_contradiction'}:
                relation='mixed_evidence';flag('mixed_evidence','候選來源同時呈現相同與相反關係；請核對條件，不由系統決定結論。')
            elif 'possible_contradiction' in observed and not any(i['code'] in {'modality_mismatch','numeric_mismatch','unsupported_generalization'} for i in issues):
                relation='possible_contradiction';flag('possible_contradiction','對應主客體的方向／否定相反；仍須核對來源條件。',next(i for i,r in usable if r==relation))
            else:
                relation='text_aligned' if 'text_aligned' in observed and not any(i['code'] in {'unsupported_generalization','modality_mismatch','numeric_mismatch'} for i in issues) else 'unknown'
            if comparisons and re.search(r'\bcaus(?:e|es|ed|ing|al)\b',claim,re.I) and re.search(r'\b(?:associat\w*|correlat\w*)\b',comparisons[0]['quote'],re.I) and not re.search(r'\bcaus(?:e|es|ed|ing|al)\b',comparisons[0]['quote'],re.I):
                flag('causality_not_established','候選原句描述關聯，本句寫成因果；關聯本身不能建立因果，請核對研究設計。',0)
            # 保守判讀會把方向衝突收斂成 unknown；線索另外列出，避免反證在摘要裡消失。
            leads=direction_leads(claim,comparisons) if relation not in {'possible_contradiction','mixed_evidence'} else []
            if leads:flag('counter_evidence_lead','候選原句有未解析的方向／否定／立場差異。這是待人工核對的線索，不是矛盾判定，也不改變上面的關係結論。',leads[0]['comparison'],leads=leads)
            abstention='conditions_or_warnings' if usable or any(c['relation']!='unknown' for c in comparisons) else comparisons[0]['reason'] if comparisons else 'no_evidence'
        if relation=='unknown' and not any(i['code']=='no_evidence' for i in issues):
            flag('support_not_established','這些規則未建立本主張的支持關係；請閱讀對照原文或補充證據，不能當成主張已被否定。')
        claims.append(dict(id=hashlib.sha256(f'{number}:{claim}'.encode()).hexdigest()[:16],text=claim,start=start,end=end,
            relation=relation,findings=issues,comparisons=comparisons,abstention_reason=abstention if relation=='unknown' else None))
    return dict(engine=ENGINE_VERSION,advisory_only=True,scientific_proof=False,blocks_writing=False,
        original_text=text,claims=claims,analysis_basis='ranked_candidates_separate_relations' if assessment!='legacy' else 'primary_text_candidate',retrieval=retrieval,assessment=assessment,
        uncertainty='檢索排名不代表支持。規則逐一比較候選，保留主客體與部分條件；其他上下文條件可能屬於不同實驗。未涵蓋的語意、條件適用性及因果推論仍須人工核對。')
