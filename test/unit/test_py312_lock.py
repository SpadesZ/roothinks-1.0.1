# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_py312_lock.py
# 目的: Python 3.12 遷移候選的鎖定檔必須涵蓋 requirements.txt 的每一項、版本落在其範圍內、每項都有 hash，
#       且不得帶入 PyPI torch 的 CUDA 套件；Dockerfile.py312 必須以 digest 釘基底並以 hash 安裝。
# 邊界: 只讀 repo 內的文字檔，不連網、不安裝。
# 驗證: python -m pytest test/unit/test_py312_lock.py -q
import re
from pathlib import Path

import pytest

packaging = pytest.importorskip('packaging.requirements')
from packaging.requirements import Requirement  # noqa: E402
from packaging.utils import canonicalize_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def locked():
    pins, current = {}, None
    for line in (ROOT / 'requirements-py312.lock').read_text(encoding='utf-8').splitlines():
        match = re.match(r'^([A-Za-z0-9._-]+)==([^\s\\]+)', line)
        if match:
            current = canonicalize_name(match.group(1))
            pins[current] = dict(version=match.group(2), hashes=0)
        elif current and '--hash=sha256:' in line:
            pins[current]['hashes'] += 1
    return pins


def requirements():
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            yield Requirement(line)


def test_lock_covers_every_requirement_within_its_range():
    pins = locked()
    for requirement in requirements():
        name = canonicalize_name(requirement.name)
        assert name in pins, f'{name} 沒有被鎖定'
        assert requirement.specifier.contains(pins[name]['version'], prereleases=True), (name, pins[name])


def test_every_locked_package_is_hash_pinned():
    pins = locked()
    assert len(pins) > 100
    assert [name for name, pin in pins.items() if pin['hashes'] == 0] == []


def test_lock_excludes_cuda_torch_packages():
    excluded = [name for name in locked() if re.match(r'^(torch|torchvision|triton|nvidia-|cuda-)', name)]
    assert excluded == []


def test_dockerfile_py312_pins_base_and_installs_with_hashes():
    text = (ROOT / 'Dockerfile.py312').read_text(encoding='utf-8')
    assert re.search(r'^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$', text, re.M)
    assert '--require-hashes --no-deps -r requirements-py312.lock' in text
    assert 'torch==2.6.0+cpu torchvision==0.21.0+cpu' in text
    assert text.index('requirements-py312.lock') < text.index('torch==2.6.0+cpu') < text.index('RUN pip check')


def test_production_dockerfile_stays_on_python310():
    assert re.search(r'^FROM python:3\.10-slim$', (ROOT / 'Dockerfile').read_text(encoding='utf-8'), re.M)
