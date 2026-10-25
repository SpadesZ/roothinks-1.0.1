"""本機瀏覽器 QA 的真 npm bytes：驗 tarball integrity、樣板 SRI，禁止假資產。

只輸出至新 QA 目錄，不將第三方 bundles 放入 app 或 Git。
"""
import base64
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import tarfile
import urllib.request
import sys

ROOT=Path(__file__).resolve().parents[2]
PACKAGES={'bootstrap':'5.3.0','bootstrap-icons':'1.11.1','socket.io-client':'4.7.2',
    'mermaid':'10.9.6','katex':'0.16.9','split.js':'1.6.0','plotly.js-dist-min':'2.27.0'}


def fetch(url):
    with urllib.request.urlopen(url,timeout=60) as response:return response.read(40_000_001)


def prepare(output):
    output.mkdir(exist_ok=False);files={};packages={}
    for name,version in PACKAGES.items():
        metadata=json.loads(fetch(f'https://registry.npmjs.org/{name}/{version}'));raw=fetch(metadata['dist']['tarball'])
        expected=metadata['dist']['integrity'];algorithm,b64=expected.split('-',1)
        if algorithm!='sha512' or base64.b64encode(hashlib.sha512(raw).digest()).decode()!=b64:
            raise ValueError('npm tarball integrity mismatch: '+name)
        packages[name]=dict(version=version,integrity=expected,tarball=metadata['dist']['tarball'])
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
            for member in archive.getmembers():
                if not member.isfile():continue
                rel=Path(member.name)
                wanted=(name=='plotly.js-dist-min' and rel.as_posix()=='package/plotly.min.js') or (
                    name!='plotly.js-dist-min' and rel.as_posix().startswith(('package/dist/','package/font/')))
                if not wanted:continue
                target=output/name/Path(*rel.parts[1:])
                if not target.resolve().is_relative_to(output.resolve()):raise ValueError('Unsafe npm member')
                target.parent.mkdir(parents=True,exist_ok=True)
                data=archive.extractfile(member).read()
                with target.open('xb') as f:f.write(data)
                relative=Path(*rel.parts[1:]).as_posix()
                url=f'https://cdn.jsdelivr.net/npm/{name}@{version}/{relative}'
                files[url]=dict(path=str(target),sha256=hashlib.sha256(data).hexdigest())
    files['https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js']=files[
        'https://cdn.jsdelivr.net/npm/socket.io-client@4.7.2/dist/socket.io.min.js']
    files['https://cdnjs.cloudflare.com/ajax/libs/split.js/1.6.0/split.min.js']=files[
        'https://cdn.jsdelivr.net/npm/split.js@1.6.0/dist/split.min.js']
    files['https://cdn.plot.ly/plotly-2.27.0.min.js']=files[
        'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.27.0/plotly.min.js']
    files['https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js']=files[
        'https://cdn.jsdelivr.net/npm/mermaid@10.9.6/dist/mermaid.min.js']
    verified={}
    class Verify(HTMLParser):
        def handle_starttag(self,tag,attrs):
            values=dict(attrs);url=values.get('src') or values.get('href');integrity=values.get('integrity')
            if url not in files or not integrity:return
            algorithm,expected=integrity.split('-',1);data=Path(files[url]['path']).read_bytes()
            if base64.b64encode(hashlib.new(algorithm,data).digest()).decode()!=expected:raise ValueError('Template SRI mismatch: '+url)
            verified[url]=integrity
    parser=Verify()
    for path in (ROOT/'app/templates').rglob('*.html'):parser.feed(path.read_text(encoding='utf-8'))
    if len(verified)<6:raise ValueError('Expected six existing template SRI resources')
    manifest=dict(packages=packages,files=files,template_sri=verified,cdn_plotly_contacted=False)
    with (output/'manifest.json').open('x',encoding='utf-8') as f:json.dump(manifest,f,indent=2)
    print(json.dumps(dict(sri_resources=len(verified),files=len(files),output=str(output))))


if __name__=='__main__':prepare(Path(sys.argv[1]))
