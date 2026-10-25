"""本機候選專用：固定公開 revision／原檔雜湊，拒絕缺權重，再驗離線重載。

不改現行 bake_nllb.py；只在無正式掛載的候選 image build 執行。
"""
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from huggingface_hub import hf_hub_download
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def bake():
    manifest=json.loads(Path(__file__).with_name('nllb-source.json').read_text(encoding='utf-8'))
    output=Path(os.environ.get('NLLB_MODEL_DIR','/opt/models/nllb'))
    if output.exists():raise FileExistsError('Model destination must be new')
    with tempfile.TemporaryDirectory(prefix='public-nllb-') as folder:
        stage=Path(folder);actual={}
        for name,expected in manifest['files'].items():
            downloaded=Path(hf_hub_download(manifest['repository'],name,revision=manifest['revision'],
                cache_dir='/tmp/public-hf-cache',token=False))
            if 'sha256' in expected:
                if digest(downloaded)!=expected['sha256'] or downloaded.stat().st_size!=expected['bytes']:
                    raise ValueError('Public model file integrity mismatch: '+name)
            else:
                raw=downloaded.read_bytes()
                if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=expected['git_blob']:
                    raise ValueError('Public metadata blob mismatch: '+name)
            shutil.copyfile(downloaded,stage/name);actual[name]=digest(downloaded)
        tokenizer=AutoTokenizer.from_pretrained(stage,local_files_only=True)
        model,info=AutoModelForSeq2SeqLM.from_pretrained(stage,local_files_only=True,
            use_safetensors=False,output_loading_info=True)
        if any(info.get(k) for k in ('missing_keys','unexpected_keys','mismatched_keys','error_msgs')):
            raise ValueError('Model state mismatch: '+repr(info))
        model.save_pretrained(output,safe_serialization=True,max_shard_size='2GB')
        tokenizer.save_pretrained(output);shutil.copyfile(stage/'README.md',output/'SOURCE_README.md')
        del model;gc.collect()
        reloaded,info=AutoModelForSeq2SeqLM.from_pretrained(output,local_files_only=True,output_loading_info=True)
        if any(info.get(k) for k in ('missing_keys','unexpected_keys','mismatched_keys','error_msgs')):
            raise ValueError('Offline reload state mismatch: '+repr(info))
        del reloaded;gc.collect()
        manifest['source_sha256']=actual
        manifest['output_sha256']={p.name:digest(p) for p in output.iterdir() if p.is_file()}
        (output/'local-build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
    shutil.rmtree('/tmp/public-hf-cache')  # 只刪除此 build 新建的公開下載快取。
    print('Pinned public NLLB integrity and offline reload passed.',flush=True)


if __name__=='__main__':bake()
