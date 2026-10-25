# Roothinks source maintenance contract
# 檔案路徑: scripts/bake_easyocr.py
# 主要責任: 僅下載指定公開 OCR 權重，先驗 archive 與 payload SHA-256，再寫入新模型目錄。
# 上下游: Docker build -> easyocr-source.json -> /opt/models/easyocr；原研究資料及 cache 不讀取。
# 維護邊界: 不解壓任意路徑，不接受私有來源，不在執行期隱式下載或執行權重。
# 驗證: 完整 Docker build 與 EasyOCR --network none 推論。
import hashlib
import io
import json
import os
from pathlib import Path
import urllib.request
import zipfile


def bake():
    manifest = json.loads(Path(__file__).with_name('easyocr-source.json').read_text(encoding='utf-8'))
    output = Path(os.environ.get('EASYOCR_MODULE_PATH', '/opt/models/easyocr'))
    output.mkdir(parents=True, exist_ok=False)
    for record in manifest['files']:
        request = urllib.request.Request(record['url'], headers={'User-Agent': 'RootThinks-pinned-model-build'})
        with urllib.request.urlopen(request, timeout=90) as response:
            archive = response.read()
        if hashlib.sha256(archive).hexdigest() != record['archive_sha256']:
            raise ValueError('EasyOCR archive integrity mismatch: ' + record['filename'])
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            content = handle.read(record['filename'])
        if hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError('EasyOCR weight integrity mismatch: ' + record['filename'])
        (output / record['filename']).write_bytes(content)
    (output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Pinned EasyOCR source integrity passed; runtime downloads disabled.')


if __name__ == '__main__':
    bake()
