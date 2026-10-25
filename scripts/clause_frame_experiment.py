"""I13 Gate A 實驗：子句範圍關係框架。**未通過 Gate A，未採用，保留為負結果。**

結果見 docs/engineering/SAI_I13_GATE_A_RESULT_2026-10-25.json：C1、C2 都只多出 1 個決定性
判定（G6 未過），且該判定來自把形容詞 aged 當成動詞的假 frame（G7 未過）。

依 docs/engineering/I13_GATE_A_PROTOCOL.md 實作，規則寫死不得事後調整。產品引擎維持凍結基線
（advisory-rules-4）；本檔以附加原始碼的方式從凍結引擎產生實驗引擎，沿用 I09／I11 的手法。

用法：
    NLTK_DATA=<dir> python scripts/clause_frame_experiment.py engine <new-output.py> [C1|C2]

C1 的謂詞詞表為 VerbNet ∪ 基線關係族動詞；C2 另加 PropBank（授權未釐清，僅供隔離評估）。
需要 NLTK 的 averaged_perceptron_tagger_eng（JSON 版）、wordnet、verbnet，C2 另需 propbank；
資料不入 Git。任何一項取不到，實驗引擎逐案退回凍結基線。
"""
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'app/core_pro/manuscript/scientific_advisory.py'

OVERLAY='''
# ===== I13 實驗覆蓋：子句範圍關係框架（Gate A 評估，不整合） =====
_ORIGINAL_ASSESS_RELATION=assess_relation
_I13_STATE={}
# 只在基線因句型或論元而棄權時啟動；其他保守閘門一律不覆寫。
_I13_TRIGGER={'unsupported_sentence_structure','different_arguments_or_conditions'}
_I13_CUT={'where','which','that','while','whereas','although','because','when','after','before','whether'}
_I13_LIGHT={'be','have','do'}
_I13_BE={'be','am','is','are','was','were','been','being'}


def _i13_resources():
    """NLTK 詞性與謂詞詞表；任何一項取不到就回 None，逐案退回凍結基線。"""
    if 'ok' in _I13_STATE:return _I13_STATE['ok']
    try:
        import nltk
        from nltk.corpus import verbnet,wordnet
        nltk.pos_tag(['Treatment','increases','survival'],lang='eng')  # 觸發 JSON tagger 載入
        wordnet.morphy('increased','v')
        inventory={lemma.casefold() for cls in verbnet.classids() for lemma in verbnet.lemmas(cls)}
        if _I13_VARIANT=='C2':
            from nltk.corpus import propbank
            for roleset in propbank.rolesets():
                name=roleset.attrib.get('id','')
                if '.' in name:inventory.add(name.split('.')[0].casefold())
        inventory|=set(_RELATIONS)|{root for root,family,sign in _RELATIONS.values()}
        _I13_STATE['ok']=dict(nltk=nltk,wordnet=wordnet,inventory=frozenset(inventory-_I13_LIGHT))
    except Exception:
        _I13_STATE['ok']=None
    return _I13_STATE['ok']


def _i13_arguments(words):
    # 與基線 _relation_frame 內嵌的 argument() 完全相同的正規化。
    return tuple(w[:-1] if len(w)>5 and w.endswith('s') and not w.endswith(('ss','is','us','es')) else w
        for w in words if w not in _STOP|_NEGATIVE_WORDS|{'did','does','do','has','have','had','significantly'})


def _i13_clauses(text):
    resources=_i13_resources()
    text=re.sub(r'^(?:we (?:found|observed|showed) that|the results showed that)\\s+','',text,flags=re.I)
    text=re.sub(r'^(?:background|methods|results|conclusions?)\\s*:?\\s+','',text,flags=re.I)
    tokens=re.findall(r'\\w+|[,;:]',text)
    if not tokens:return []
    tagged=resources['nltk'].pos_tag(tokens,lang='eng')
    clauses=[];current=[]
    for index,(word,tag) in enumerate(tagged):
        low=word.casefold()
        cut=word in (',',';',':') or low in _I13_CUT or (
            low in ('and','or') and any(t.startswith('VB') for _,t in tagged[index+1:index+7]))
        if cut:
            if current:clauses.append(current)
            current=[];continue
        current.append((word,tag))
    if current:clauses.append(current)
    return clauses


def _i13_frame(clause):
    resources=_i13_resources();wordnet=resources['wordnet'];inventory=resources['inventory']
    words=[w.casefold() for w,_ in clause]
    hits=[]
    for index,(word,tag) in enumerate(clause):
        if not tag.startswith('VB'):continue
        lemma=wordnet.morphy(word.casefold(),'v') or word.casefold()
        if lemma in inventory:hits.append((index,lemma,tag))
    if len(hits)!=1:return None  # 單一謂詞紀律以子句為單位
    index,lemma,tag=hits[0]
    before=index-1
    while before>=0 and clause[before][1].startswith('RB'):before-=1
    passive=tag=='VBN' and before>=0 and words[before] in _I13_BE
    if passive:
        tail=words[index+1:]
        if 'by' not in tail:return None  # 被動但沒有施事者：不猜主詞
        cut=tail.index('by')
        subject=_i13_arguments(tail[cut+1:]);object=_i13_arguments(words[:before])
    else:
        subject=_i13_arguments(words[:index]);object=_i13_arguments(words[index+1:])
    if not subject or not object:return None
    curated=_RELATIONS.get(lemma)
    family,sign=(curated[1],curated[2]) if curated else ('lemma:'+lemma,1)
    return dict(subject=subject,object=object,family=family,sign=sign,
        negative=bool(set(words[:index])&_NEGATIVE_WORDS),lemma=lemma,passive=passive,
        clause=' '.join(w for w,_ in clause))


def _i13_compare(left,right):
    if (left['subject'],left['object'])!=(right['subject'],right['object']):return None
    if left['family']!=right['family']:return None
    if left['sign']!=right['sign'] and (left['negative'] or right['negative']):return None
    opposed=(left['sign']!=right['sign'])!=(left['negative']!=right['negative'])
    return 'opposed' if opposed else 'aligned'


def i13_frames(text):
    """供稽核用：回傳句子切出的每個子句與其 frame（無 frame 為 None）。"""
    if _i13_resources() is None:return []
    return [(' '.join(w for w,_ in c),_i13_frame(c)) for c in _i13_clauses(text)]


def assess_relation(claim,quote):
    judged=_ORIGINAL_ASSESS_RELATION(claim,quote)
    if judged['reason'] not in _I13_TRIGGER or _i13_resources() is None:return judged
    claims=[f for c in _i13_clauses(claim) for f in [_i13_frame(c)] if f]
    sources=[f for c in _i13_clauses(quote) for f in [_i13_frame(c)] if f]
    pairs=[(a,b,o) for a in claims for b in sources for o in [_i13_compare(a,b)] if o]
    outcomes={o for _,_,o in pairs}
    if outcomes not in ({'aligned'},{'opposed'}):return judged
    used=[dict(claim=a,source=b,outcome=o) for a,b,o in pairs]
    if outcomes=={'aligned'}:
        return dict(relation='text_aligned',reason='clause_ordered_predicate_arguments',scientific_proof=False,i13_frames=used)
    return dict(relation='possible_contradiction',reason='clause_opposed_predicate_or_negation',scientific_proof=False,i13_frames=used)
'''


def engine(output,variant='C1',baseline=ENGINE):
    """把實驗覆蓋附加到凍結引擎之後；位元串接，不經平台行尾轉換。"""
    if variant not in ('C1','C2'):raise ValueError('未知變體；只接受 C1 或 C2。')
    output=Path(output)
    output.write_bytes(Path(baseline).read_bytes()+("\n_I13_VARIANT=%r\n"%variant).encode('utf-8')+OVERLAY.encode('utf-8'))
    return output


def load(path):
    spec=importlib.util.spec_from_file_location('clause_frame_experiment_engine_'+Path(path).parent.name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


if __name__=='__main__':
    if sys.argv[1]!='engine':raise SystemExit('只支援 engine 模式。')
    print(engine(sys.argv[2],*(sys.argv[3:4] or ['C1'])))
