#!/usr/bin/env python
# Roothinks source maintenance contract
# 檔案路徑: scripts/py312_lock.py
# 模組定位: 維運/資料處理 CLI 層；由人工或隔離測試明確執行，不是常駐 request path。
# 主要責任: 由 uv 產生的完整 hash 鎖定檔，移除 PyPI torch 的 CUDA 套件，產出 Dockerfile.py312 用的 requirements-py312.lock。
# 上下游: uv pip compile 的輸出 -> 本工具 -> requirements-py312.lock；Dockerfile.py312 以 --require-hashes --no-deps 安裝。
# 維護邊界: 只改寫明確指定的輸出檔；不連網、不碰 data/ 或任何 DB。torch/torchvision 改由 Dockerfile 從 PyTorch CPU index 安裝。
# 驗證: python -m pytest test/unit/test_py312_lock.py -q
#路徑(./scripts/py312_lock.py) #版本 v0.1 #更版時間 20261025
#功能概要:
#1. filter <完整鎖定> <輸出>：刪除 torch、torchvision、triton、nvidia-*、cuda-* 項目（含其 hash 行）。
#2. 產生步驟（在 repo 根目錄執行）：
#   uv pip compile requirements.txt --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
#     --generate-hashes --no-header -c <3.10 現行解析的 == 約束，另加 torch==2.6.0、torchvision==0.21.0> -o full.txt
#   python scripts/py312_lock.py filter full.txt requirements-py312.lock
import re
import sys
from pathlib import Path

# PyPI 的 torch 是 CUDA 版，會帶進 triton 與 nvidia-*；CPU 版 torch 不需要它們。
EXCLUDED = re.compile(r'^(torch|torchvision|triton|nvidia-[a-z0-9-]+|cuda-[a-z0-9-]+)$')
HEADER = """\
#路徑(./requirements-py312.lock) #版本 v0.1 #更版時間 20261025
# Python 3.12 遷移候選的完整鎖定（含 hash）。產生方式見 scripts/py312_lock.py。
# 版本刻意與 2026-10-25 的 Python 3.10 解析相同，只少 3.10 才需要的 async-timeout、
# exceptiongroup、tomli；遷移只換直譯器，不同時升級套件。
# torch==2.6.0+cpu 與 torchvision==0.21.0+cpu 不在這裡，由 Dockerfile.py312 從 PyTorch CPU index 安裝。
"""


def entries(text):
    """把 uv 輸出切成 (name, block) 序列；block 含該項目的 hash 與 via 註解。"""
    blocks, current = [], None
    for line in text.splitlines(keepends=True):
        match = re.match(r'^([A-Za-z0-9._-]+)==', line)
        if match:
            current = [match.group(1).lower(), [line]]
            blocks.append(current)
        elif current is not None:
            current[1].append(line)
    return [(name, ''.join(lines)) for name, lines in blocks]


def filter_lock(source, target):
    kept = [block for name, block in entries(Path(source).read_text(encoding='utf-8')) if not EXCLUDED.match(name)]
    Path(target).write_text(HEADER + ''.join(kept), encoding='utf-8', newline='\n')
    return len(kept)


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != 'filter':
        sys.exit('用法: python scripts/py312_lock.py filter <完整鎖定> <輸出>')
    print(filter_lock(sys.argv[2], sys.argv[3]))
