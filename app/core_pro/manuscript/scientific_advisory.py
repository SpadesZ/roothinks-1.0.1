# 目的：提供可重現、可定位的主張／證據建議；規則涵蓋不到時明確棄權，不修改原文。
# 上下游：作者比較 API／生成後提示／外部盲測 → 純函式 → 原句、來源定位、警訊及未知。
# 邊界：沒有語意模型或科學真偽權威；文字對齊不等於證明，不阻擋任何研究動作。
# 驗證：test_scientific_advisory.py；規則及外部量測界線見 SAI_SCIENTIFIC_ADVISORY_PROTOCOL.md。
import hashlib
import re
from decimal import Decimal

ENGINE_VERSION='advisory-rules-1'
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


def assess_claims(text,sources,*,required_conditions=''):
    if not isinstance(text,str) or not text.strip() or len(text)>16000:
        raise ValueError('請提供 1–16000 字的主張；不會截掉尾端條件。')
    if not isinstance(sources,list) or len(sources)>8 or any(not isinstance(s.get('text'),str) or len(s['text'])>16000 for s in sources):
        raise ValueError('最多比較八份完整證據，每份不得超過 16000 字。')
    sentences=_sentences(text)
    if len(sentences)>64:raise ValueError('超過 64 個句子；請縮小比較範圍，原文不變。')
    candidates=[]
    for source in sources:
        for index,(start,end,quote) in enumerate(_sentences(source['text'])):
            candidates.append(dict(source=source,sentence_index=index,start=start,end=end,quote=quote))
    claims=[]
    for number,(start,end,claim) in enumerate(sentences):
        words=_tokens(claim)
        ranked=sorted(candidates,key=lambda c:len(words & _tokens(c['quote']))/max(1,len(words | _tokens(c['quote']))),reverse=True)[:3]
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
        source_conditions=_conditions(ranked[0]['source']['text']) if ranked else []
        for comparison,candidate in zip(comparisons,ranked):
            comparison['source_conditions']=_conditions(candidate['source']['text'])
        missing=[c for c in source_conditions+required if _canonical(c) not in _canonical(claim)]
        if missing:flag('missing_condition','本句未明示部分來源／作者條件；請核對同樣本及鄰文，不能推定條件已失效。',missing=list(dict.fromkeys(missing)))
        claim_values=_quantities(claim)
        for cidx,c in enumerate(ranked[:1]):
            source_text=c['source']['text']
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
            if score>=0.35 and (opposite or negative_diff):
                flag('possible_contradiction','相近文字中的方向／否定不同；可能有矛盾，也可能是不同研究條件。',cidx)
            if _canonical(claim) and _canonical(claim) in _canonical(c['quote']):relation='text_aligned'
        if any(i['code']=='possible_contradiction' for i in issues):relation='possible_contradiction'
        elif issues:relation='unknown'
        if relation=='unknown' and not any(i['code']=='no_evidence' for i in issues):
            flag('support_not_established','這些規則未建立本主張的支持關係；請閱讀對照原文或補充證據，不能當成主張已被否定。')
        claims.append(dict(id=hashlib.sha256(f'{number}:{claim}'.encode()).hexdigest()[:16],text=claim,start=start,end=end,
            relation=relation,findings=issues,comparisons=comparisons))
    return dict(engine=ENGINE_VERSION,advisory_only=True,scientific_proof=False,blocks_writing=False,
        original_text=text,claims=claims,analysis_basis='primary_text_candidate',
        uncertainty='規則只判讀文字最相近的候選來源；其他來源分開呈現。未涵蓋的語意、樣本對應與因果推論交由研究者核對。')
