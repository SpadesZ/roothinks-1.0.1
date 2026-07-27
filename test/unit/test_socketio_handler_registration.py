# 檔案路徑: test/unit/test_socketio_handler_registration.py
# 產生時間: 2026-07-26 02:50 +08:00
# 版本: v1.0
# 模組定位:
#   回歸測試：確保 create_app 重複呼叫後 Socket.IO handler 仍然有效。
# 主要責任:
#   1. 鎖住 app/__init__.py 中「socketio.init_app 必須排在路由模組匯入之後」的順序。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   建立兩個 app，驗證第二個 app 的 /manu_ws 命名空間仍可連線並回應事件。
# 安全邊界:
#   - 僅使用 tmp_path 下的臨時 SQLite，不碰 repo 的 data/ 目錄。
# 維護提醒:
#   本測試對應的真實缺陷：
#     flask_socketio 的 @socketio.on 裝飾器在 self.server 已存在時會「直接註冊到
#     該 server」而不寫入 self.handlers；init_app 每次都重建 server 且只從
#     self.handlers 還原。因此若 init_app 早於路由模組匯入，第一個 app 的 handler
#     就只存在於第一個 server，之後任何 create_app 都會產生一個沒有任何 handler
#     的 server —— /manu_ws 連線全被拒，手稿即時協作靜默失效。
#   若有人把 app/__init__.py 的 socketio.init_app 移回路由匯入之前，本測試會紅。
# 驗證方式:
#   python -m pytest test/unit/test_socketio_handler_registration.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


def _make_app(tmp_path, name):
    from app import create_app, db

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / (name + '.db')}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / (name + '_manu.db')}"},
            "SOCKETIO_ASYNC_MODE": "threading",
        }
    )
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))
    return tmp_path


def test_manu_ws_handlers_survive_second_create_app(env):
    """第二次 create_app 之後 /manu_ws 仍必須可連線。"""
    from app import socketio

    _make_app(env, "first")
    second = _make_app(env, "second")

    client = second.test_client()
    sio = socketio.test_client(second, flask_test_client=client, namespace="/manu_ws")

    assert sio.is_connected("/manu_ws"), (
        "第二個 app 的 /manu_ws 連線被拒——"
        "socketio.init_app 很可能又被移到路由模組匯入之前了"
    )

    sio.disconnect(namespace="/manu_ws")


def test_manu_ws_events_still_dispatch_after_second_create_app(env):
    """不只連得上，事件也要真的派送到 handler。"""
    from app import socketio

    _make_app(env, "one")
    second = _make_app(env, "two")

    client = second.test_client()
    sio = socketio.test_client(second, flask_test_client=client, namespace="/manu_ws")
    sio.get_received("/manu_ws")

    # 缺 pid 的請求會走到 handler 並回一則 sys_msg；收得到就代表 handler 有掛上。
    sio.emit("cmd_list_blocks", {"section": "introduction"}, namespace="/manu_ws")
    received = sio.get_received("/manu_ws")

    assert received, "cmd_list_blocks 沒有任何回應，handler 未註冊"
    assert any(evt["name"] == "block_list" for evt in received)

    sio.disconnect(namespace="/manu_ws")


def test_handlers_are_recorded_for_reregistration(env):
    """
    /manu_ws 的 handler 必須存在於 socketio.handlers。

    這是上述缺陷的直接成因檢查：handler 若只掛在某個 server 實例上而沒進
    handlers 清單，重建 server 時就無從還原。
    """
    from app import socketio

    _make_app(env, "solo")

    namespaces = {ns for _msg, _fn, ns in socketio.handlers}
    assert "/manu_ws" in namespaces, (
        "/manu_ws handler 沒有登記在 socketio.handlers；"
        "init_app 重建 server 時將無法還原"
    )
