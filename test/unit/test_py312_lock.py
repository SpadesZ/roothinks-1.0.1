# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_py312_lock.py
# 主要責任: 驗正式 3.12 依賴完整性、CPU wheel 來源、單一 cv2 與 immutable 模型承諾。
# 上下游: requirements/model manifests/Dockerfiles -> 純離線政策檢查。
# 維護邊界: 不下載模型，不接正式資料；原分支的 3.10 保留斷言由 Owner 新契約取代。
# 驗證: python -m pytest test/unit/test_py312_lock.py -q
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[2]


def locked():
    pins, current = {}, None
    for line in (ROOT / 'requirements-py312.lock').read_text(encoding='utf-8').splitlines():
        match = re.match(r'^([A-Za-z0-9._-]+)(==([^\s\\]+)| @ ([^\s\\]+))', line)
        if match:
            current = canonicalize_name(match[1])
            version = match[3] if match[3] else str(parse_wheel_filename(unquote(urlparse(match[4]).path).rsplit('/', 1)[-1])[1])
            pins[current] = dict(version=version, url=match[4], hashes=[])
        elif current:
            pins[current]['hashes'].extend(re.findall(r'--hash=sha256:([0-9a-f]{64})', line))
    return pins


def test_all_direct_dependencies_are_covered_and_hashed():
    pins = locked()
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        requirement = Requirement(line)
        pin = pins[canonicalize_name(requirement.name)]
        assert requirement.specifier.contains(pin['version'], prereleases=True)
        if requirement.url:
            assert requirement.url == pin['url']
    assert pins and all(pin['hashes'] for pin in pins.values())


def test_cpu_supply_chain_and_single_cv2_provider():
    pins = locked()
    expected = {
        'torch': '4ca4a9394b0c771238a4f73590fdbbc4debad85ed0fa63d026ae1b085da7d6e2',
        'torchvision': 'b545d46f4d2f9d30381281cf22874bfe1d32a8a7b0ee8396fccde89f30c6a9d',
    }
    for name, digest in expected.items():
        assert urlparse(pins[name]['url']).hostname in {'download.pytorch.org', 'download-r2.pytorch.org'}
        assert pins[name]['version'].endswith('+cpu')
        assert pins[name]['hashes'] == [digest]
    assert not any(name.startswith(('nvidia-', 'cuda-', 'triton')) for name in pins)
    assert [name for name in pins if name.startswith('opencv-')] == ['opencv-python-headless']
    assert 'layoutparser' not in pins


def test_production_uses_pinned_312_and_hash_install_without_private_context():
    text = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert re.search(r'^FROM python:3\.12-slim-bookworm@sha256:[0-9a-f]{64} AS runtime$', text, re.M)
    assert '--require-hashes --only-binary=:all: -r /tmp/requirements-py312.lock' in text
    assert 'COPY . .' not in text
    assert 'EASYOCR_DOWNLOAD_ENABLED=0' in text
    assert 'tesseract-ocr-chi-tra' in text
    ignore = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    assert all(name in ignore for name in ['.env', '.env.*', 'data', '.codegraph', '*.db', '*.pth'])
    test = (ROOT / 'Dockerfile.test').read_text(encoding='utf-8')
    assert 'ARG BASE_IMAGE\n' in test and '--no-install-recommends git' in test
    assert '--require-hashes' in test


def test_public_models_have_fixed_provenance():
    nllb = json.loads((ROOT / 'scripts/nllb-source.json').read_text())
    assert nllb['revision'] == 'f8d333a098d19b4fd9a8b18f94170487ad3f821d'
    assert nllb['files']['pytorch_model.bin']['sha256'] == 'c266c2cfd19758b6d09c1fc31ecdf1e485509035f6b51dfe84f1ada83eefcc42'
    assert all('sha256' in item or 'git_blob' in item for item in nllb['files'].values())
    ocr = json.loads((ROOT / 'scripts/easyocr-source.json').read_text())
    assert {item['filename'] for item in ocr['files']} == {'craft_mlt_25k.pth', 'chinese.pth', 'english_g2.pth'}
    assert all(len(item['sha256']) == len(item['archive_sha256']) == 64 for item in ocr['files'])
