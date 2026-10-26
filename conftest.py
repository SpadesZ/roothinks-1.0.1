# Roothinks source maintenance contract
# 檔案路徑: conftest.py
# 模組定位: Roothinks 自動化驗收層；所有 pytest 執行的共同守門。
# 主要責任: 測試不得碰 repo 的 data/。正式主機上 ./data 就是正式資料 volume，在那份 checkout 跑測試不可以碰到研究資料。
# 上下游: pytest -> 本檔三層防護：
#   1. 執行前拒跑：data/ 已有任何檔案、data/ 是 symlink、或 DATABASE_URL／DATA_ROOT 指向非暫存的真實資料時，收集測試前就中止。
#   2. 事前阻擋：audit hook 在寫入、建目錄、改名、刪除、改權限、開 SQLite 之前，拒絕任何落在 data/ 的路徑。
#   3. 事後比對：執行前後比對 data/ 的檔案清單、大小與 mtime；有差異就讓整次執行失敗（抓子程序等 hook 管不到的寫入）。
# 維護邊界: 不讀檔案內容；不提供略過開關。create_app() 的預設 log 與 LLM DB 在測試期間導到 tmp。
#           audit hook 只管本程序；子程序（例如 Flow B runner、git）的寫入由第 3 層抓。
# 驗證: python -m pytest test/unit tests test/integration_e2e test/integration_smoke -q -p no:cacheprovider
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import unquote, urlparse

import pytest

REPO_DATA = Path(__file__).resolve().parent / 'data'
_DATA_REAL = os.path.realpath(REPO_DATA)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# audit 事件 -> 參數中哪些位置是路徑。open 另外判斷是否為寫入模式。
_PATH_EVENTS = {'os.mkdir': (0,), 'os.rename': (0, 1), 'os.remove': (0,), 'os.rmdir': (0,), 'shutil.rmtree': (0,),
                'os.truncate': (0,), 'os.link': (0, 1), 'os.symlink': (1,), 'os.chmod': (0,), 'os.chown': (0,),
                'os.utime': (0,), 'sqlite3.connect': (0,), 'shutil.copyfile': (1,), 'shutil.move': (0, 1)}


def _under_repo_data(path):
    if path is None or isinstance(path, int):
        return False
    try:
        text = os.fsdecode(path)
    except TypeError:
        return False
    if text.startswith('file:'):  # sqlite URI
        text = unquote(urlparse(text).path)
    if not text or text == ':memory:':
        return False
    real = os.path.realpath(text)
    return real == _DATA_REAL or real.startswith(_DATA_REAL + os.sep)


def blocked_reason(event, args):
    """回傳拒絕原因；不涉及 repo data/ 時回傳 None。給 audit hook 與負向測試共用。"""
    if event == 'open':
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if isinstance(mode, str):
            writing = any(ch in mode for ch in 'wax+')
        else:
            writing = bool((flags or 0) & _WRITE_FLAGS)
        return f'open({mode or flags})' if writing and _under_repo_data(path) else None
    positions = _PATH_EVENTS.get(event)
    if positions and any(_under_repo_data(args[i]) for i in positions if i < len(args)):
        return event
    return None


def _audit_hook(event, args):
    reason = blocked_reason(event, args)
    if reason:
        raise PermissionError(f'測試不得寫入 repo 的 data/（{REPO_DATA}）：{reason} {args[0] if args else ""}')


def _is_temporary(path):
    real = os.path.realpath(path)
    roots = {os.path.realpath(tempfile.gettempdir()), '/tmp', '/var/tmp'}
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def populated_data_reason(data_dir, environ):
    """執行前檢查；回傳拒跑原因，安全時回傳 None。"""
    data_dir = Path(data_dir)
    if data_dir.is_symlink():
        return f'{data_dir} 是指向 {os.path.realpath(data_dir)} 的 symlink，可能是掛載的正式資料'
    if data_dir.exists():
        if not data_dir.is_dir():
            return f'{data_dir} 存在但不是目錄'
        for path in data_dir.rglob('*'):
            if path.is_symlink() or not path.is_dir():
                return f'{data_dir} 已有內容（例如 {path.relative_to(data_dir)}）；請在沒有正式資料的 checkout 跑測試'
    url = (environ.get('DATABASE_URL') or '').strip()
    if url:
        scheme, _, rest = url.partition('://')
        if not scheme.split('+', 1)[0] == 'sqlite':
            return 'DATABASE_URL 指向非 SQLite 資料庫；測試不得連正式資料庫'
        # SQLAlchemy：sqlite:///相對路徑、sqlite:////絕對路徑、sqlite://（記憶體）
        target = rest[1:] if rest.startswith('/') else rest
        target = target.split('?', 1)[0]
        if target and target != ':memory:' and not _is_temporary(target):
            return f'DATABASE_URL 指向非暫存的 SQLite（{target}）'
    root = (environ.get('DATA_ROOT') or '').strip()
    if root and not _is_temporary(root) and Path(root).exists() and any(Path(root).iterdir()):
        return f'DATA_ROOT 指向非暫存且有內容的目錄（{root}）'
    return None


def _snapshot():
    entries = {}
    if REPO_DATA.exists():
        for path in REPO_DATA.rglob('*'):
            try:
                stat = path.lstat()
            except FileNotFoundError:
                continue
            is_file = path.is_file() and not path.is_symlink()
            entries[path.relative_to(REPO_DATA).as_posix()] = (
                'file' if is_file else 'dir' if path.is_dir() else 'other',
                stat.st_size if is_file else 0, stat.st_mtime_ns if is_file else 0)
    return entries


def pytest_configure(config):
    reason = populated_data_reason(REPO_DATA, os.environ)
    if reason:
        pytest.exit(f'拒絕執行測試：{reason}', returncode=pytest.ExitCode.USAGE_ERROR)
    config._roothinks_repo_data_before = _snapshot()
    if not getattr(sys, '_roothinks_data_hook', False):  # audit hook 無法移除，每個程序只裝一次
        sys.addaudithook(_audit_hook)
        sys._roothinks_data_hook = True


@pytest.fixture(scope='session', autouse=True)
def _default_runtime_paths_under_tmp(tmp_path_factory):
    """create_app() 預設會寫 data/_logs 與 data/sys/llm_match.db；測試一律改到 tmp。

    只改測試期間的預設值，不改產品行為；個別測試仍可再 monkeypatch 成自己的 tmp。
    """
    base = tmp_path_factory.mktemp('default-runtime')
    patch = pytest.MonkeyPatch()
    patch.setenv('LECTURE_SYSTEM_LOG_FILE', str(base / 'runtime.log'))
    try:
        from app.llm_service.llm_model import LLMModel
        from app.core_pro.literature import literature_routes
    except ImportError:  # 不需要 app 的測試環境
        LLMModel = literature_routes = None
    if LLMModel is not None:
        patch.setattr(LLMModel, 'get_db_path', staticmethod(lambda: str(base / 'llm_match.db')))
        (base / 'data').mkdir()
        patch.setattr(literature_routes, 'DATA_ROOT', str(base / 'data'))
    yield base
    patch.undo()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, '_roothinks_repo_data_before', None)
    if before is None:
        return
    after = _snapshot()
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(after) & set(before) if after[key] != before[key])
    if not (added or removed or changed):
        return
    reporter = session.config.pluginmanager.get_plugin('terminalreporter')
    lines = [f'測試寫入了 repo 的 data/（{REPO_DATA}）：新增 {len(added)}、修改 {len(changed)}、刪除 {len(removed)}']
    lines += [f'  + {name}' for name in added[:40]] + [f'  ~ {name}' for name in changed[:40]] + [f'  - {name}' for name in removed[:40]]
    for line in lines:
        reporter.write_line(line, red=True) if reporter else print(line)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
