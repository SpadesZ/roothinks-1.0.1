"""本機無網路完整回歸；以 argv list 留下可重現命令，不經 shell 轉譯。

python validation/python312/regression.py <sha256:image-id> <new-evidence-dir>
只接明示的候選 image ID，掛載空證據目錄，不掛正式 data/env。
"""
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys

image, destination = sys.argv[1:]
if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
    raise ValueError('Use an immutable image ID')
output = Path(destination).resolve()
output.mkdir(exist_ok=False)
root = Path(__file__).resolve().parents[2]
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
command = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '2', '--memory', '4g',
           '--security-opt', 'no-new-privileges', '-e', 'SOCKETIO_ASYNC_MODE=threading',
           '--mount', f'type=bind,source={output},target=/evidence', image,
           'python', 'scripts/prepare_test_runtime.py', 'python', '-m', 'pytest',
           'test/unit', 'tests', 'test/integration_e2e', 'test/integration_smoke',
           '-q', '-p', 'no:cacheprovider', '--tb=short', '--junitxml=/evidence/full.xml']
receipt = dict(source_sha=sha, image_id=image, command=command,
               started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
with (output / 'stdout.txt').open('x', encoding='utf-8') as handle:
    result = subprocess.run(command, cwd=root, stdout=handle, stderr=subprocess.STDOUT)
receipt.update(exit_code=result.returncode, ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
with (output / 'receipt.json').open('x', encoding='utf-8') as handle:
    json.dump(receipt, handle, indent=2)
print(json.dumps(receipt))
raise SystemExit(result.returncode)
