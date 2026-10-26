# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_flowb_debug_data_root.py
# 目的: Flow B 的 LLM 除錯回覆（可能含論文內容）只能寫在 literature_routes.DATA_ROOT。
#       資料根目錄不可用時必須 fail closed：不寫任何檔案、不回退到 repo/data，並留下 warning。
# 驗證: python -m pytest test/unit/test_flowb_debug_data_root.py -q
import logging
from pathlib import Path
import sys

import pytest

REPO_DATA = Path(__file__).resolve().parents[2] / 'data'


def _write(helpers):
    helpers._flowb_write_debug_reply('FLOWBDBG-p', 'paper_1', 'task_5interpret_invalid_json_raw', 'raw reply')


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*')) if root.exists() else []


def test_debug_reply_follows_the_configured_data_root(monkeypatch, tmp_path):
    from app.core_pro.literature import literature_flowb_helpers as helpers
    from app.core_pro.literature import literature_routes
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(literature_routes, 'DATA_ROOT', str(tmp_path / 'data'))
    _write(helpers)
    written = list((tmp_path / 'data').rglob('*_task_5interpret_invalid_json_raw.txt'))
    assert [path.read_text(encoding='utf-8') for path in written] == ['raw reply']
    assert not (REPO_DATA / 'FLOWBDBG-p').exists()


@pytest.mark.parametrize('configured', ['missing', 'empty', 'file'])
def test_unavailable_data_root_fails_closed(monkeypatch, tmp_path, caplog, configured):
    from app.core_pro.literature import literature_flowb_helpers as helpers
    from app.core_pro.literature import literature_routes
    target = {'missing': tmp_path / 'not-created', 'empty': '', 'file': tmp_path / 'a-file'}[configured]
    if configured == 'file':
        target.write_text('not a directory')
    monkeypatch.setattr(literature_routes, 'DATA_ROOT', str(target))
    before = _files(tmp_path)
    with pytest.raises(helpers.FlowBDataRootUnavailable):
        helpers._flowb_data_root()
    with caplog.at_level(logging.WARNING, logger='LiteratureRoutes'):
        _write(helpers)
    assert _files(tmp_path) == before
    assert not REPO_DATA.exists() or not (REPO_DATA / 'FLOWBDBG-p').exists()
    assert any('FlowBDataRootUnavailable' in record.getMessage() for record in caplog.records)


def test_missing_routes_module_fails_closed_instead_of_using_repo_data(monkeypatch, caplog):
    import app.core_pro.literature as package
    from app.core_pro.literature import literature_flowb_helpers as helpers
    monkeypatch.delattr(package, 'literature_routes')
    monkeypatch.setitem(sys.modules, 'app.core_pro.literature.literature_routes', None)
    with pytest.raises(helpers.FlowBDataRootUnavailable) as raised:
        helpers._flowb_data_root()
    assert isinstance(raised.value.__cause__, ImportError)
    with caplog.at_level(logging.WARNING, logger='LiteratureRoutes'):
        _write(helpers)
    assert any('FlowBDataRootUnavailable' in record.getMessage() for record in caplog.records)
    assert not hasattr(helpers, '_FLOWB_DATA_ROOT')
