"""I12 可行性探測：量測更大的謂詞詞表能把 I11 的零謂詞瓶頸降到多少。

**這是可行性探測，不是採用實驗。** 本輪沒有改動產品引擎，因此沒有採用門檻需要
預先登記；I11 預先固定的門檻與凍結基線保持不變（見 I11_PROTOCOL.md）。

用法：python scripts/semantic_resource_probe.py <predictions.jsonl> <gold.jsonl> <nltk-data-dir>

需要 NLTK 的 verbnet、propbank、wordnet 與 averaged_perceptron_tagger 資料。
這些資料不入 Git，由操作者自行以正常代理取檔（不要繞過 NLTK 的 SSRF 防護）。
對照基準：I11 實測 161 個首候選句中 93 句（57.8%）在 WordNet 擴充後仍零謂詞。
資料早已曝光，結果只能稱開發診斷。
"""
import json,re,sys
from collections import Counter
from pathlib import Path
import nltk
nltk.data.path.insert(0,sys.argv[3])
from nltk.corpus import verbnet,propbank,wordnet as wn

# 現行產品的 40 字謂詞詞表（取自凍結引擎）。
CURRENT=set()
for forms in ('increase increases increased increasing','decrease decreases decreased decreasing reduce reduces reduced reducing',
    'improve improves improved improving','worsen worsens worsened worsening','activate activates activated activating',
    'inhibit inhibits inhibited inhibiting','promote promotes promoted promoting','suppress suppresses suppressed suppressing',
    'cause causes caused causing','associate associates associated associating','correlate correlates correlated correlating'):
    CURRENT.update(forms.split())

def read(p):
    with open(p,encoding='utf-8') as fh:return [json.loads(l) for l in fh if l.strip()]

VN=set()
for cls in verbnet.classids():
    for lemma in verbnet.lemmas(cls):VN.add(lemma.casefold())
PB=set()
try:
    for roleset in propbank.rolesets():
        name=roleset.attrib.get('id','')
        if '.' in name:PB.add(name.split('.')[0].casefold())
except Exception as exc:print('propbank 讀取問題:',exc,file=sys.stderr)
BIG=VN|PB
print('詞表大小：現行產品 %d ／ VerbNet %d ／ PropBank %d ／ 聯集 %d'%(len(CURRENT),len(VN),len(PB),len(BIG)))

answers={r['id']:r for r in read(sys.argv[2])}
buckets=Counter();examined=0;detail=Counter()
for row in read(sys.argv[1]):
    if row['error']:continue
    g=answers[row['id']]
    exp={(str(d),s) for d,gs in g['evidence'].items() for grp in gs for s in grp['sentences']}
    if not exp or not (exp & {tuple(x) for x in row['localized']}):continue
    for claim in row['report']['claims']:
        for rank,comp in enumerate(claim['comparisons']):
            if rank:continue
            examined+=1
            for label,text in (('source',comp['quote']),('claim',claim['text'])):
                words=re.findall(r'[A-Za-z][A-Za-z-]*',text)
                tags=nltk.pos_tag(words)
                verbs=[w.casefold() for w,t in tags if t.startswith('VB')]
                lemmas={wn.morphy(v,'v') or v for v in verbs}
                cur=any(w.casefold() in CURRENT for w in words)
                big=bool(lemmas & BIG)
                buckets['%s_current_%s'%(label,'hit' if cur else 'MISS')]+=1
                buckets['%s_verbnet_propbank_%s'%(label,'hit' if big else 'MISS')]+=1
                if not cur and big:detail['%s_newly_covered'%label]+=1
                if label=='source':
                    buckets['source_verb_count_%s'%min(len(lemmas&BIG),3)]+=1
print('檢視首候選句數：',examined)
print()
print('=== 零謂詞率（句子層）===')
for label in ('source','claim'):
    cm=buckets['%s_current_MISS'%label];bm=buckets['%s_verbnet_propbank_MISS'%label]
    print('  %-7s 現行詞表 MISS %3d (%.1f%%)  →  VerbNet/PropBank MISS %3d (%.1f%%)   新覆蓋 +%d'%(
        label,cm,100*cm/examined,bm,100*bm/examined,detail['%s_newly_covered'%label]))
print()
print('=== 擴充後來源句的謂詞數分布（歧義風險）===')
for k in sorted(k for k in buckets if k.startswith('source_verb_count_')):
    print('  謂詞數 %s: %d'%(k.split('_')[-1],buckets[k]))
