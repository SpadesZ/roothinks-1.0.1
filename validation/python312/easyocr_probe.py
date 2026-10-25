"""公開 EasyOCR 1.7.2 權重；來源、原 SDK MD5 與下載 bytes SHA-256 皆留存。

download 模式在 host 下載，check 模式在 --network none 候選容器推論。
不以這張合成英文圖驗收繁中品質，不在看過輸出後放寬判準。
"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

MODELS = [
    ('craft_mlt_25k.pth', 'pre-v1.1.6/craft_mlt_25k.zip', '2f8227d2def4037cdb3b34389dcf9ec1'),
    ('chinese.pth', 'pre-v1.1.6/chinese.zip', 'dfba8e364cd98ed4fed7ad54d71e3965'),
    ('english_g2.pth', 'v1.3/english_g2.zip', '5864788e1821be9e454ec108d61b887d'),
]
mode, directory = sys.argv[1:]
directory = Path(directory)
if mode == 'download':
    directory.mkdir(exist_ok=False)
    manifest = []
    for filename, release, md5 in MODELS:
        url = 'https://github.com/JaidedAI/EasyOCR/releases/download/' + release
        archive = directory / Path(release).name
        request = urllib.request.Request(url, headers={'User-Agent': 'RootThinks-local-validation'})
        with urllib.request.urlopen(request, timeout=90) as response, archive.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        with zipfile.ZipFile(archive) as handle:
            content = handle.read(filename)
        assert hashlib.md5(content).hexdigest() == md5, filename
        (directory / filename).write_bytes(content)
        manifest.append(dict(url=url, filename=filename, sdk_md5=md5,
            sha256=hashlib.sha256(content).hexdigest(), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest()))
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))
elif mode == 'check':
    import easyocr
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    for item in json.loads((directory / 'manifest.json').read_text()):
        assert hashlib.sha256((directory / item['filename']).read_bytes()).hexdigest() == item['sha256']
    image = Image.new('RGB', (800, 130), 'white')
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 48)
    ImageDraw.Draw(image).text((25, 25), 'ROOTHINKS 2026', font=font, fill='black')
    array = np.array(image)
    results = {}
    for key, languages in [('bilingual_load_infer', ['en', 'ch_tra']), ('english_control', ['en'])]:
        reader = easyocr.Reader(languages, gpu=False, model_storage_directory=str(directory),
                                user_network_directory='/tmp/easyocr-user', download_enabled=False, verbose=False)
        text = reader.readtext(array, detail=0, workers=0)
        assert text and all(isinstance(value, str) and value.strip() for value in text), text
        if key == 'english_control':
            assert 'ROOTHINKS 2026' in ' '.join(text).upper(), text
        results[key] = text
    results.update(offline=True, chinese_accuracy_measured=False, cross_version_parity_measured=False)
    print(json.dumps(results))
else:
    raise ValueError(mode)
