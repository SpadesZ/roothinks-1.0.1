"""I11 語意實驗：WordNet 輔助的被動／名詞化論元還原。**未採用，保留為負結果。**

產品引擎維持凍結基線（`advisory-rules-4`）。本檔以附加原始碼的方式產生實驗引擎，
不修改產品檔案，沿用 I09 形態檢索實驗的同一手法。

用法：
    python scripts/semantic_frame_experiment.py engine <new-output.py>   # 產生實驗引擎
    python scripts/semantic_frame_experiment.py coverage <predictions.jsonl> <gold.jsonl>

量測結果與不採用的理由見 docs/engineering/I11_PROTOCOL.md 與 I11 交付文件：
已曝光 dev 300 案上標籤、定位與棄權原因**零變化**，未達協議預先固定的效果門檻。
WordNet 反義關係另經量測確認太稀疏且方向錯誤，不納入。
"""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'app/core_pro/manuscript/scientific_advisory.py'

# 附加在凍結引擎之後；重新繫結 _relation_frame，assess_relation 於呼叫時取用模組全域。
OVERLAY='''
# ===== I11 實驗覆蓋：WordNet 輔助的被動／名詞化論元還原（未採用） =====
from functools import lru_cache as _lru_cache
_ORIGINAL_RELATION_FRAME=_relation_frame
_WORDNET=None
_PASSIVE_AUX={'was','were','is','are','be','been','being','became','becomes'}


def wordnet():
    """選用詞彙語意資源；取不到就回 None，實驗引擎退回凍結基線行為。"""
    global _WORDNET
    if _WORDNET is None:
        try:
            from nltk.corpus import wordnet as module
            module.morphy('increased','v')  # 觸發載入；資料缺就丟 LookupError。
            _WORDNET=module
        except Exception:_WORDNET=False
    return _WORDNET or None


@_lru_cache(maxsize=4096)
def _verb_forms(word):
    """用 WordNet 還原屈折與名詞化的動詞原形；不猜測詞表外的對應。"""
    source=wordnet()
    if source is None:return ()
    forms=[]
    try:
        base=source.morphy(word,'v')
        if base:forms.append(base)
        for synset in source.synsets(word):
            for lemma in synset.lemmas():
                if lemma.name().casefold()!=word:continue
                for related in lemma.derivationally_related_forms():
                    if related.synset().pos()=='v':forms.append(related.name().casefold())
    except Exception:return ()
    return tuple(dict.fromkeys(forms))


def _frame_tokens(text):
    text=re.sub(r'^(?:we (?:found|observed|showed) that|the results showed that)\\s+','',text,flags=re.I)
    text=re.sub(r'^(?:background|methods|results|conclusions?)\\s*:?\\s+','',text,flags=re.I)
    return _canonical(text).split()


def _arguments(words):
    return tuple(w[:-1] if len(w)>5 and w.endswith('s') and not w.endswith(('ss','is','us','es')) else w
        for w in words if w not in _STOP|_NEGATIVE_WORDS|{'did','does','do','has','have','had','significantly'})


def _single_predicate(tokens):
    """沿用基線的單一謂詞紀律；擴充詞表後仍不接受有歧義的多謂詞句。"""
    extended=wordnet() is not None
    hits=[]
    for index,word in enumerate(tokens):
        for verb in ((word,)+_verb_forms(word) if extended else (word,)):
            if verb in _RELATIONS:hits.append((index,verb));break
    return hits[0] if len(hits)==1 else None


def _passive_arguments(tokens,index):
    """被動句：<客體> was/were <過去分詞> by <主體>。只認 by，不把 upon／after 當施事者。"""
    if index<1 or tokens[index-1] not in _PASSIVE_AUX:return None
    source=wordnet()
    if source is None or not source.morphy(tokens[index],'v'):return None
    tail=tokens[index+1:]
    if 'by' not in tail:return None
    offset=tail.index('by')
    subject=_arguments(tail[offset+1:]);object=_arguments(tokens[:index-1])
    return (subject,object) if subject and object else None


def _nominal_arguments(tokens,index):
    """名詞化：<名詞> of <客體> by <主體>。缺 of 或缺 by 就不還原。"""
    rest=tokens[index+1:]
    if not rest or rest[0]!='of' or 'by' not in rest:return None
    offset=rest.index('by')
    tail=rest[offset+1:]
    for stop,word in enumerate(tail):
        if word in _PASSIVE_AUX:tail=tail[:stop];break
    subject=_arguments(tail);object=_arguments(rest[1:offset])
    return (subject,object) if subject and object else None


def _relation_frame(text):
    """還原被動與名詞化的論元順序。基線能解析且不是被動時行為完全不變。"""
    tokens=_frame_tokens(text)
    found=_single_predicate(tokens)
    if found is not None:
        index,verb=found
        # 被動句的基線讀法會把主客體顛倒，所以即使基線解析成功也要修正。
        recovered=_passive_arguments(tokens,index) or (
            _nominal_arguments(tokens,index) if _ORIGINAL_RELATION_FRAME(text) is None else None)
        if recovered is not None:
            subject,object=recovered;root,family,sign=_RELATIONS[verb]
            return dict(subject=subject,object=object,family=family,sign=sign,
                negative=bool(set(tokens[:index])&_NEGATIVE_WORDS))
    return _ORIGINAL_RELATION_FRAME(text)
'''


def engine(output,baseline=ENGINE):
    """把實驗覆蓋附加到凍結引擎之後，寫出新檔；位元串接，不經平台行尾轉換。"""
    output=Path(output)
    output.write_bytes(Path(baseline).read_bytes()+OVERLAY.encode('utf-8'))
    return output


def load(path):
    spec=importlib.util.spec_from_file_location('semantic_experiment',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def read(path):
    with Path(path).open(encoding='utf-8') as handle:return [json.loads(line) for line in handle if line.strip()]


def coverage(predictions,gold_path,baseline=ENGINE):
    """量測謂詞覆蓋與還原命中，解釋為何擴充詞表在真實語料上零變化。"""
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        module=load(engine(Path(directory)/'experiment-engine.py',baseline))
    answers={r['id']:r for r in read(gold_path)}
    predicates=Counter();claims=Counter();recovered=Counter();blocked=Counter();examined=0
    for row in read(predictions):
        if row['error']:continue
        target=answers[row['id']]
        expected={(str(doc),s) for doc,groups in target['evidence'].items() for group in groups for s in group['sentences']}
        if not expected or not (expected & {tuple(x) for x in row['localized']}):continue
        for claim in row['report']['claims']:
            tokens=module._frame_tokens(claim['text'])
            total=sum(1 for w in tokens if any(v in module._RELATIONS for v in (w,)+module._verb_forms(w)))
            claims[min(total,3)]+=1
            for rank,comparison in enumerate(claim['comparisons']):
                if rank:continue
                examined+=1
                tokens=module._frame_tokens(comparison['quote'])
                hits=[i for i,w in enumerate(tokens) if any(v in module._RELATIONS for v in (w,)+module._verb_forms(w))]
                literal=[i for i,w in enumerate(tokens) if w in module._RELATIONS]
                predicates['wordnet_%d'%min(len(hits),3)]+=1
                predicates['literal_%d'%min(len(literal),3)]+=1
                if len(hits)==1:
                    index=hits[0]
                    if module._passive_arguments(tokens,index) is not None:recovered['passive_by']+=1
                    elif module._nominal_arguments(tokens,index) is not None:recovered['nominal_of_by']+=1
                    elif index>=1 and tokens[index-1] in module._PASSIVE_AUX:blocked['passive_without_by_agent']+=1
                    elif index+1<len(tokens) and tokens[index+1]=='of':blocked['nominal_without_by_agent']+=1
                    else:blocked['active_baseline_path']+=1
                elif not hits:blocked['no_predicate_in_inventory']+=1
                else:blocked['multiple_predicates_rejected']+=1
    return dict(adopted=False,reason='未達 I11_PROTOCOL.md 預先固定的效果門檻；已曝光 dev 上零變化。',
        diagnostic_only=True,examined_top_candidate_sentences=examined,
        source_predicate_counts=dict(predicates),claim_predicate_counts={str(k):v for k,v in claims.items()},
        restored=dict(recovered),not_restored=dict(blocked),
        note='謂詞覆蓋是主要瓶頸；擴充詞表同時增加多謂詞句，被單一謂詞紀律否決，兩者互相抵銷。')


if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='engine':print(engine(sys.argv[2]))
    elif mode=='coverage':print(json.dumps(coverage(sys.argv[2],sys.argv[3]),ensure_ascii=False,indent=2))
    else:raise SystemExit('未知模式；可用 engine 或 coverage。')
