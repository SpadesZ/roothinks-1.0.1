# Roothinks source maintenance contract
# 檔案路徑: conftest.py
# 模組定位: Roothinks 自動化驗收層；所有 pytest 執行的共同守門。
# 主要責任: 測試不得新增、修改或刪除 repo 的 data/。正式主機上 ./data 就是正式資料 volume，
#           在那份 checkout 跑測試不可以碰到研究資料。
# 上下游: pytest -> 本檔 -> 執行前後比對 data/ 的檔案清單、大小與 mtime；有差異就讓整次執行失敗。
# 維護邊界: 只做 stat，不讀檔案內容；不提供略過開關。create_app() 的預設 log 與 LLM DB 在測試期間導到 tmp。
# 驗證: python -m pytest test/unit tests test/integration_e2e test/integration_smoke -q -p no:cacheprovider
from pathlib import Path

import pytest

REPO_DATA = Path(__file__).resolve().parent / 'data'


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
    config._roothinks_repo_data_before = _snapshot()


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
