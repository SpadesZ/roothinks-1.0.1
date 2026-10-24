"""I08 公開文獻標註材料與來源分組封存；不產生專家判斷，也不呼叫模型。

prepare <new-evaluator-dir>：只顯示聚合 manifest；key／正文永不進 Git。
open <evaluator-dir> <new-export-dir>：供未來保管人交付標註，不供本輪調參使用。
來源與分割契約：docs/engineering/I08_EVALUATION_PROTOCOL.md。
"""
import argparse
import base64
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import secrets
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION='i08-expert-packet-1'
API='https://www.ebi.ac.uk/europepmc/webservices/rest/'
QUERIES={
    'photonics':'(TITLE_ABS:"silicon photonics" OR TITLE_ABS:"optical waveguide")',
    'materials_energy':'(TITLE_ABS:"perovskite solar cell" OR TITLE_ABS:"battery cathode")',
    'life_sciences':'(TITLE_ABS:"randomized trial" AND TITLE_ABS:"diabetes")',
}


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf-8')
def digest(value):return hashlib.sha256(value).hexdigest()
def save(path,value):
    with path.open('xb') as handle:handle.write(encoded(value))


def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'RootThinks-I08-public-evaluation/1.0'})
    with urllib.request.urlopen(request,timeout=30) as response:
        raw=response.read(4_000_001)
    if len(raw)>4_000_000:raise ValueError('Public response exceeds bounded download')
    return raw


def article(raw,record,stratum):
    root=ET.fromstring(raw)
    if root.get('article-type')!='research-article':raise ValueError('Not an original research article')
    links=[v for node in root.findall('./front/article-meta/permissions/license') for v in node.attrib.values()]
    links+=[''.join(node.itertext()) for node in root.findall('./front/article-meta/permissions/license')]
    licenses=[url for link in links for url in re.findall(r'https?://creativecommons\.org/(?:licenses/by|publicdomain/zero)/[\d.]+/?',link)]
    if not licenses:raise ValueError('No verified CC BY / CC0 license in article XML')
    if not 2022<=int(record.get('pubYear',0))<=2025:raise ValueError('Publication outside fixed sampling years')
    abstract='\n'.join(' '.join(''.join(node.itertext()).split()) for node in root.findall('./front/article-meta/abstract'))
    if len(abstract)<100 or len(abstract)>16000:raise ValueError('Abstract outside complete-evidence bounds')
    doi=record.get('doi','').casefold()
    if not doi:raise ValueError('DOI required for group identity')
    return dict(source_id='epmc:'+record['pmcid'],doi=doi,title=record.get('title',''),authors=record.get('authorString',''),
        publication_year=record.get('pubYear'),sampling_stratum=stratum,text=abstract,
        locator={'url':API+record['pmcid']+'/fullTextXML','section':'abstract'},
        license_url=licenses[0],xml_sha256=digest(raw),fingerprint=digest(abstract.encode('utf-8')),
        full_text_xml=raw.decode('utf-8'),inference_scope='abstract')


def cases(source):
    # 候選選擇不依模型／答案；原句也不是自動支持標籤。
    sentences=re.findall(r'\S.*?(?:[.!?](?=\s|$)|$)',source['text'],re.S)
    quote=max(sentences,key=len)
    proposals=[('author_quote',quote),('unsupported_generalization','For all samples and all conditions, '+quote),
        ('modality_challenge','This was experimentally measured: '+quote),('missing_citation',quote)]
    number=re.search(r'(?<![\w.])\d+(?:\.\d+)?(?![\w.])',quote)
    if number:
        replacement=str(Decimal(number.group())+Decimal('1'))
        proposals.append(('numerical_challenge',quote[:number.start()]+replacement+quote[number.end():]))
    rows=[]
    for kind,claim in proposals:
        case_id=digest((source['doi']+':'+kind).encode())[:24]
        rows.append(dict(case_id=case_id,claim=claim,candidate_origin=kind,source_group=source['doi'],
            evidence_sources=[] if kind=='missing_citation' else [source['source_id']],
            original_quote=quote,gold=None,annotations=[blank_annotation(),blank_annotation()],adjudication=None))
    return rows


def blank_annotation():
    return dict(reviewer_id=None,expertise=None,label=None,evidence_sentence_locators=None,
        required_conditions=None,condition_fidelity=None,research_modality=None,
        causal_or_argument_reversal=None,unsupported_generalization=None,missing_citation=None,
        uncertainty=None,reason=None,confidence=None)


def seal(output,pilot,holdout,rejections,queries):
    if {r['doi'] for r in pilot}&{r['doi'] for r in holdout}:raise ValueError('Source groups overlap')
    packet=dict(version=VERSION,sources=holdout,cases=[c for s in holdout for c in cases(s)],expert_labels_complete=False)
    raw=encoded(packet);key=AESGCM.generate_key(bit_length=256);nonce=secrets.token_bytes(12)
    ciphertext=nonce+AESGCM(key).encrypt(nonce,raw,VERSION.encode())
    with (output/'holdout.aesgcm').open('xb') as handle:handle.write(ciphertext)
    save(output/'evaluator-key.json',dict(version=VERSION,key=base64.b64encode(key).decode()))
    (output/'evaluator-key.json').chmod(0o600)
    save(output/'pilot-labeling.json',dict(version=VERSION,sources=pilot,cases=[c for s in pilot for c in cases(s)],expert_labels_complete=False))
    manifest=dict(version=VERSION,pilot_documents=len(pilot),holdout_documents=len(holdout),
        pilot_cases=sum(len(cases(s)) for s in pilot),holdout_cases=len(packet['cases']),
        domains={d:dict(pilot=sum(s['sampling_stratum']==d for s in pilot),holdout=sum(s['sampling_stratum']==d for s in holdout)) for d in QUERIES},
        holdout_plaintext_sha256=digest(raw),holdout_ciphertext_sha256=digest(ciphertext),
        source_groups_disjoint=True,expert_labels_complete=False,expert_judgments_created=0,
        inference_performed=False,independent_custodian_assigned=False,queries=queries,rejections=rejections)
    save(output/'public-manifest.json',manifest)
    return manifest


def record_from_xml(raw,pmcid):
    root=ET.fromstring(raw);meta=root.find('./front/article-meta')
    if meta is None:raise ValueError('Missing article metadata')
    doi=meta.find("article-id[@pub-id-type='doi']")
    year=meta.find('pub-date/year');title=meta.find('title-group/article-title')
    return dict(pmcid=pmcid,doi=doi.text if doi is not None else '',pubYear=year.text if year is not None else '',
        title=''.join(title.itertext()) if title is not None else '',
        authorString='; '.join(' '.join(''.join(n.itertext()).split()) for n in meta.findall('contrib-group/contrib/name')))


def prepare(output,seeds=None):
    output.mkdir(exist_ok=False);seed=secrets.token_bytes(32);pilot=[];holdout=[];used=set();rejected=[];queries={}
    seed_registry=json.loads(seeds.read_text(encoding='utf-8')) if seeds else None
    if seed_registry and set(seed_registry)!=set(QUERIES):raise ValueError('Seed strata must match protocol')
    for stratum,query in QUERIES.items():
        query+=' AND FIRST_PDATE:[2022-01-01 TO 2025-12-31] AND OPEN_ACCESS:Y AND LICENSE:cc-by'
        queries[stratum]=query if not seeds else 'public-seeds-sha256:'+digest(seeds.read_bytes())
        if seed_registry:
            records=[dict(pmcid=pmcid) for pmcid in seed_registry[stratum]]
        else:
            search_url=API+'search?'+urllib.parse.urlencode(dict(query=query,format='json',pageSize=40,sort='FIRST_PDATE_D'))
            response=fetch(search_url);save(output/(stratum+'-search.json'),json.loads(response))
            records=json.loads(response)['resultList']['result']
        accepted=[]
        for record in records:
            if not record.get('pmcid') or record.get('doi','').casefold() in used:continue
            try:
                raw=fetch(API+record['pmcid']+'/fullTextXML')
                if seed_registry:record=record_from_xml(raw,record['pmcid'])
                source=article(raw,record,stratum)
            except Exception as exc:
                rejected.append(dict(stratum=stratum,record_hash=digest(str(record.get('id')).encode()),reason=type(exc).__name__+': '+str(exc)));continue
            if source['doi'] in used:continue
            accepted.append(source);used.add(source['doi'])
            if len(accepted)==4:break
        if len(accepted)!=4:raise ValueError('Insufficient licensed research articles for '+stratum)
        accepted.sort(key=lambda s:digest(seed+s['doi'].encode()))
        pilot.extend(accepted[:2]);holdout.extend(accepted[2:])
        # 搜尋回應可能透露 holdout 身分，不保留公開的未封存 sidecar。
        if not seed_registry:(output/(stratum+'-search.json')).unlink()
    manifest=seal(output,pilot,holdout,rejected,queries)
    print(json.dumps(manifest,ensure_ascii=True))


def open_packet(folder,output):
    manifest=json.loads((folder/'public-manifest.json').read_text(encoding='utf-8'))
    data=(folder/'holdout.aesgcm').read_bytes()
    if digest(data)!=manifest['holdout_ciphertext_sha256']:raise ValueError('Ciphertext integrity mismatch')
    key=base64.b64decode(json.loads((folder/'evaluator-key.json').read_text())['key'])
    raw=AESGCM(key).decrypt(data[:12],data[12:],VERSION.encode())
    if digest(raw)!=manifest['holdout_plaintext_sha256']:raise ValueError('Plaintext integrity mismatch')
    output.mkdir(exist_ok=False)
    with (output/'holdout-labeling.json').open('xb') as handle:handle.write(raw)
    save(output/'custodian-export.json',dict(plaintext_sha256=digest(raw),expert_labels_complete=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['prepare','open']);parser.add_argument('paths',nargs='+',type=Path);parser.add_argument('--seeds',type=Path)
    args=parser.parse_args()
    if args.mode=='prepare':prepare(*args.paths,seeds=args.seeds)
    else:open_packet(*args.paths)
