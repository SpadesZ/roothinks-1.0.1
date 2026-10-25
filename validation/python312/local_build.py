"""僅本機：由 tracked source 建乾淨 context，保留每輪 build 命令、hash 與失敗。

python validation/python312/local_build.py stage <new-qa-dir>
python validation/python312/local_build.py build <qa-dir> --target runtime --label v1
不執行 push、Compose、部署或正式容器操作。
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def stage(output):
    output.mkdir(exist_ok=False);context=output/'context';context.mkdir();records=[]
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    for name in subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0'):
        if not name:continue
        rel=Path(name)
        if rel.name.startswith('.env') or any(p in {'.git','.codegraph','.venv','data','logs','papers','models','output'} for p in rel.parts):continue
        src=ROOT/rel
        if not src.is_file() or src.is_symlink():continue
        dest=context/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
        records.append(dict(path=name,sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    # 正式 Dockerfile 用明示 COPY；保留原 .dockerignore，manifest 與實際 context 必須一致。
    save(output/'source-manifest.json',dict(sha=sha,files=records,
        original_worktree_unchanged=True,private_artifacts_copied=False))
    print(json.dumps(dict(sha=sha,files=len(records),context=str(context))))


def build(output,target,label,dockerfile='validation/python312/Dockerfile',base_image=None):
    manifest=json.loads((output/'source-manifest.json').read_text(encoding='utf-8'))
    name=f'roothinks-py312-local:{target}-{manifest["sha"][:8]}'
    iid=output/f'{target}-{label}.iid';log=output/f'{target}-{label}.txt'
    command=['docker','build','--platform','linux/amd64','--target',target,'--progress','plain',
        '--iidfile',str(iid),'-f',dockerfile,'-t',name]
    base_config_id=None
    if base_image:
        requested=base_image
        if base_image.startswith('sha256:'):
            # BuildKit 的 FROM 不能使用裸 image ID；先解析同一已存在映像的本機 tag。
            tags=json.loads(subprocess.check_output(['docker','image','inspect',base_image,'--format','{{json .RepoTags}}'],text=True))
            base_image=next(tag for tag in sorted(tags or []) if tag.startswith('roothinks-py312-'))
        base_config_id=subprocess.check_output(['docker','image','inspect',requested,'--format','{{.Id}}'],text=True).strip()
        actual=subprocess.check_output(['docker','image','inspect',base_image,'--format','{{.Id}}'],text=True).strip()
        if actual!=base_config_id:raise ValueError('Runtime tag changed before verification build')
        command.extend(['--build-arg','BASE_IMAGE='+base_image])
    command.append(str(output/'context'))
    receipt=dict(source_sha=manifest['sha'],command=command,tag=name,
        base_config_id=base_config_id,
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    with log.open('x',encoding='utf-8') as f:result=subprocess.run(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    receipt.update(exit_code=result.returncode,status='passed' if result.returncode==0 else 'failed',
        ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),image_id=iid.read_text().strip() if iid.exists() else None)
    save(output/f'{target}-{label}-receipt.json',receipt);print(json.dumps(receipt));return result.returncode


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['stage','build'])
    parser.add_argument('output',type=Path);parser.add_argument('--target',choices=['runtime','verification'],default='runtime')
    parser.add_argument('--label',default='v1');parser.add_argument('--dockerfile',default='validation/python312/Dockerfile')
    parser.add_argument('--base-image');args=parser.parse_args()
    if not args.label.isalnum():raise ValueError('Use an alphanumeric unique label')
    if args.mode=='stage':stage(args.output)
    else:raise SystemExit(build(args.output,args.target,args.label,args.dockerfile,args.base_image))
