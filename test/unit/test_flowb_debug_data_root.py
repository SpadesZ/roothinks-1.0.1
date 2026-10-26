# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_flowb_debug_data_root.py
# 目的: Flow B 的 LLM 除錯回覆（可能含論文內容）必須寫在 Flow B 其他產物所用的同一個 DATA_ROOT，
#       不能另外寫到寫死的 repo/data。正式環境兩者相同；測試把 DATA_ROOT 指到 tmp 時，必須跟著走。
# 驗證: python -m pytest test/unit/test_flowb_debug_data_root.py -q
from pathlib import Path


def test_debug_reply_follows_the_configured_data_root(monkeypatch, tmp_path):
    from app.core_pro.literature import literature_flowb_helpers as helpers
    from app.core_pro.literature import literature_routes
    monkeypatch.setattr(literature_routes, 'DATA_ROOT', str(tmp_path / 'data'))
    repo_data = Path(helpers._FLOWB_DATA_ROOT)
    helpers._flowb_write_debug_reply('FLOWBDBG-p', 'paper_1', 'task_5interpret_invalid_json_raw', 'raw reply')
    written = list((tmp_path / 'data').rglob('*_task_5interpret_invalid_json_raw.txt'))
    assert [path.read_text(encoding='utf-8') for path in written] == ['raw reply']
    assert not (repo_data / 'FLOWBDBG-p').exists()
