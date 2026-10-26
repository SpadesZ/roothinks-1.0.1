# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_data_safety_guard.py
# 目的: 根目錄 conftest.py 的三層資料防護必須真的有效：
#   1. data/ 有內容或環境指向真實資料時，在任何測試開始前就拒跑；
#   2. 寫入、建目錄、改名、刪除、開 SQLite 等動作在發生前被擋下（PermissionError），不會留下檔案；
#   3. 事後比對仍在。
# 邊界: 不建立 repo 的 data/；模擬 repo 一律放在 tmp_path；子程序不帶 DATABASE_URL。
# 驗證: python -m pytest test/unit/test_data_safety_guard.py -q
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO_DATA = ROOT / 'data'
_spec = importlib.util.spec_from_file_location('roothinks_root_conftest', ROOT / 'conftest.py')
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _data_state():
    return sorted(str(p) for p in REPO_DATA.rglob('*')) if REPO_DATA.exists() else None


@pytest.mark.parametrize('operation', [
    lambda: open(REPO_DATA / 'w.txt', 'w'),
    lambda: open(REPO_DATA / 'a.txt', 'ab'),
    lambda: open(REPO_DATA / 'x.txt', 'x'),
    lambda: os.open(REPO_DATA / 'o.txt', os.O_WRONLY | os.O_CREAT),
    lambda: (REPO_DATA / 'p.txt').write_text('x'),
    lambda: os.mkdir(REPO_DATA),
    lambda: os.makedirs(REPO_DATA / 'nested' / 'dir'),
    lambda: sqlite3.connect(REPO_DATA / 'roothinks.db'),
    lambda: sqlite3.connect(f'file:{REPO_DATA / "uri.db"}?mode=rwc', uri=True),
    lambda: tempfile.mkstemp(dir=REPO_DATA),
    lambda: os.remove(REPO_DATA / 'roothinks.db'),
    lambda: shutil.rmtree(REPO_DATA),
    lambda: os.utime(REPO_DATA),
], ids=['open-w', 'open-ab', 'open-x', 'os.open-creat', 'write_text', 'mkdir', 'makedirs', 'sqlite', 'sqlite-uri',
        'mkstemp', 'remove', 'rmtree', 'utime'])
def test_writes_into_repo_data_are_blocked_before_they_happen(operation):
    before = _data_state()
    with pytest.raises(PermissionError, match='測試不得寫入 repo 的 data/'):
        operation()
    assert _data_state() == before


def test_moves_and_copies_into_repo_data_are_blocked(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('research data')
    before = _data_state()
    for operation in (lambda: os.rename(source, REPO_DATA / 'moved.txt'),
                      lambda: os.replace(source, REPO_DATA / 'replaced.txt'),
                      lambda: shutil.copyfile(source, REPO_DATA / 'copied.txt'),
                      lambda: shutil.move(str(source), str(REPO_DATA / 'shutil-moved.txt'))):
        with pytest.raises(PermissionError):
            operation()
    assert source.read_text() == 'research data' and _data_state() == before


def test_symlink_into_repo_data_is_resolved_and_blocked(tmp_path):
    link = tmp_path / 'looks-harmless.txt'
    os.symlink(REPO_DATA / 'target.txt', link)
    with pytest.raises(PermissionError):
        open(link, 'w')
    assert guard.blocked_reason('open', (str(link), 'w', 0)) is not None


def test_reads_and_other_locations_are_not_blocked(tmp_path):
    (tmp_path / 'ok.txt').write_text('fine')
    assert guard.blocked_reason('open', (str(REPO_DATA / 'r.txt'), 'r', os.O_RDONLY)) is None
    assert guard.blocked_reason('open', (str(REPO_DATA / 'r.txt'), 'rb', os.O_RDONLY)) is None
    assert guard.blocked_reason('open', (str(REPO_DATA / 'w.txt'), None, os.O_RDONLY)) is None
    assert guard.blocked_reason('open', (str(tmp_path / 'w.txt'), 'w', 0)) is None
    assert guard.blocked_reason('sqlite3.connect', (':memory:',)) is None
    assert guard.blocked_reason('os.mkdir', (str(ROOT / 'database'),)) is None  # 名字相近但不在 data/ 底下


@pytest.mark.parametrize('layout,expected', [
    (None, None),
    ([], None),
    (['empty/nested/'], None),
    (['roothinks.db'], '已有內容'),
    (['P-p/literature/papers/a/source.pdf'], '已有內容'),
])
def test_populated_data_directory_is_refused(tmp_path, layout, expected):
    data = tmp_path / 'data'
    if layout is not None:
        data.mkdir()
        for item in layout:
            target = data / item
            if item.endswith('/'):
                target.mkdir(parents=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('x')
    reason = guard.populated_data_reason(data, {})
    assert reason is None if expected is None else expected in reason


def test_symlinked_data_and_real_databases_are_refused(tmp_path):
    volume = tmp_path / 'volume'
    volume.mkdir()
    os.symlink(volume, tmp_path / 'data')
    assert 'symlink' in guard.populated_data_reason(tmp_path / 'data', {})
    empty = tmp_path / 'absent'
    assert guard.populated_data_reason(empty, {'DATABASE_URL': 'postgresql://prod/roothinks'}) is not None
    assert guard.populated_data_reason(empty, {'DATABASE_URL': 'sqlite:////srv/roothinks/data/roothinks.db'}) is not None
    assert guard.populated_data_reason(empty, {'DATABASE_URL': 'sqlite+pysqlite:///data/roothinks.db'}) is not None
    assert guard.populated_data_reason(empty, {'DATABASE_URL': f'sqlite:///{tmp_path}/test.db'}) is None
    assert guard.populated_data_reason(empty, {'DATABASE_URL': 'sqlite://'}) is None
    research = ROOT / 'docs'
    assert guard.populated_data_reason(empty, {'DATA_ROOT': str(research)}) is not None


def _fake_repo(tmp_path, test_body, populate=False):
    repo = tmp_path / 'repo'
    repo.mkdir()
    shutil.copyfile(ROOT / 'conftest.py', repo / 'conftest.py')
    shutil.copyfile(ROOT / 'pytest.ini', repo / 'pytest.ini')
    (repo / 'test_probe.py').write_text(test_body, encoding='utf-8')
    if populate:
        (repo / 'data').mkdir()
        (repo / 'data' / 'roothinks.db').write_bytes(b'production rows')
    return repo


def _run(repo):
    env = {k: v for k, v in os.environ.items() if k not in ('DATABASE_URL', 'DATA_ROOT', 'PYTHONPATH')}
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=120)


def test_populated_checkout_refuses_before_any_test_runs(tmp_path):
    marker = tmp_path / 'test-ran.txt'
    repo = _fake_repo(tmp_path, f'def test_marker():\n    open({str(marker)!r}, "w").write("ran")\n', populate=True)
    done = _run(repo)
    assert done.returncode == pytest.ExitCode.USAGE_ERROR, done.stdout + done.stderr
    assert '拒絕執行測試' in done.stdout + done.stderr
    assert not marker.exists()
    assert (repo / 'data' / 'roothinks.db').read_bytes() == b'production rows'


def test_clean_checkout_runs_and_a_writing_test_is_stopped_in_a_fresh_process(tmp_path):
    marker = tmp_path / 'test-ran.txt'
    body = (f'def test_marker():\n    open({str(marker)!r}, "w").write("ran")\n\n'
            'def test_writes_data():\n    import os\n    os.makedirs("data/sys")\n')
    done = _run(_fake_repo(tmp_path, body))
    assert marker.read_text() == 'ran'
    assert done.returncode == pytest.ExitCode.TESTS_FAILED and 'PermissionError' in done.stdout
    assert not (tmp_path / 'repo' / 'data').exists()
