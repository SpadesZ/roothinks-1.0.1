# 檔案路徑: test/unit/test_manuscript_chat_ack.py
# 產生時間: 2026-08-06 01:30 +08:00
# 版本: v1.0
# 模組定位:
#   Manuscript chat_message 事件「一定要回話」契約的回歸測試。
# 主要責任:
#   1. save_chat_history 失敗時，仍必須先收到 job_queued（不得靜默死亡）。
#   2. 派送 job 失敗時，必須補一個 job_error 收尾。
#   3. 驗證失敗（訊息過長）時，必須收到 sys_msg。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   對應 manuscript_routes.py v1.8 的事件契約：
#     通過驗證 → 必定先 job_queued，之後必定 job_done/job_error/job_cancelled 其一；
#     未通過驗證 → 必定 sys_msg。兩者皆不得「什麼都不回」。
# 安全邊界:
#   - DATA_ROOT 以 monkeypatch 導向 tmp_path，不寫入 repo 的 data/ 目錄。
#   - _CHAT_EXECUTOR 一律被替換掉，測試不會真的打 LLM。
# 維護提醒:
#   - 本檔只驗 handler 內同步發生的事件，不驗背景 worker 的結果，
#     因為 worker 在另一條執行緒，事件時序在測試中不穩定。
#   - 原始事故：job_queued 原本排在 save_chat_history 之後，該函式的
#     FileLock(timeout=10) 逾時會讓 handler 死在 ack 之前，前端永久轉圈。
#     若有人把 emit 移回去，test_job_queued_survives_history_failure 會紅。
# 驗證方式:
#   python -m pytest test/unit/test_manuscript_chat_ack.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "CHATACK"
FORMAL_PID = "CHATACK-p"
SECTION = "abstract"


class _RecordingExecutor:
    """取代 _CHAT_EXECUTOR：只記錄有沒有被派送，絕不真的執行 LLM 工作。"""

    def __init__(self, explode=False):
        self.calls = []
        self.explode = explode

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        if self.explode:
            raise RuntimeError("simulated executor failure")
        return None


@pytest.fixture(scope="module")
def _app_bundle(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("chatack")

    mp.setenv("FLASK_ENV", "development")
    mp.delenv("APP_ENV", raising=False)
    mp.delenv("AUTH_MODE", raising=False)

    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    mp.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db, socketio

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'chat.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'chat_manu.db'}"},
            "SOCKETIO_ASYNC_MODE": "threading",
        }
    )

    data_root = tmp_path / "data"
    data_root.mkdir()
    from app.core_pro.manuscript import manuscript_io as mio
    from app.core_pro.manuscript import manuscript_routes as mroutes
    mp.setattr(mio, "_get_data_root", lambda: str(data_root))
    mp.setattr(mroutes, "_get_data_root", lambda: str(data_root))

    with app.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=FORMAL_PID, name="Chat Ack Test", status="formal"))
        db.session.commit()

    client = app.test_client()
    sio = socketio.test_client(app, flask_test_client=client, namespace="/manu_ws")
    assert sio.is_connected("/manu_ws")
    sio.get_received("/manu_ws")

    yield {"app": app, "sio": sio, "routes": mroutes}

    if sio.is_connected("/manu_ws"):
        sio.disconnect(namespace="/manu_ws")
    with app.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()
    mp.undo()


@pytest.fixture()
def bundle(_app_bundle, monkeypatch):
    """每個測試都換上假的 executor，確保不會真的送 LLM 請求。"""
    executor = _RecordingExecutor()
    monkeypatch.setattr(_app_bundle["routes"], "_CHAT_EXECUTOR", executor)
    _app_bundle["sio"].get_received("/manu_ws")
    return {**_app_bundle, "executor": executor, "monkeypatch": monkeypatch}


def _names(sio):
    return [e["name"] for e in sio.get_received("/manu_ws")]


def _send_chat(sio, msg="請撰寫草稿：Abstract 300 字"):
    sio.emit(
        "chat_message",
        {"msg": msg, "context": "", "pid": FORMAL_PID, "title": "Ack Paper", "section": SECTION},
        namespace="/manu_ws",
    )


def test_job_queued_is_emitted(bundle):
    """正常路徑：一定要收到 job_queued，而且工作有被派送出去。"""
    _send_chat(bundle["sio"])
    assert "job_queued" in _names(bundle["sio"])
    assert len(bundle["executor"].calls) == 1


def test_job_queued_survives_history_failure(bundle):
    """
    核心回歸：聊天歷史寫入爆炸不得吃掉整個請求。

    這正是線上事故的形狀 —— save_chat_history 走 write_json_locked 的
    FileLock(timeout=10)，鎖逾時就丟例外。只要 ack 排在它後面，
    前端就永遠收不到任何事件，轉圈指示器沒人收得掉。
    """
    def _boom(*args, **kwargs):
        raise TimeoutError("simulated file lock timeout")

    bundle["monkeypatch"].setattr(bundle["routes"], "save_chat_history", _boom)

    _send_chat(bundle["sio"])

    names = _names(bundle["sio"])
    assert "job_queued" in names, "歷史寫入失敗不得讓 handler 靜默死亡"
    # 歷史寫不進去，但草稿工作照樣要派送出去——使用者要的是草稿。
    assert len(bundle["executor"].calls) == 1


def test_dispatch_failure_emits_job_error(bundle):
    """ack 之後才失敗的話，必須補一個終止事件，否則前端會一直等下去。"""
    executor = _RecordingExecutor(explode=True)
    bundle["monkeypatch"].setattr(bundle["routes"], "_CHAT_EXECUTOR", executor)

    _send_chat(bundle["sio"])

    names = _names(bundle["sio"])
    assert "job_queued" in names
    assert "job_error" in names, "派送失敗必須回 job_error，不能只留一個永遠不結束的 job"


def test_validation_failure_emits_sys_msg(bundle):
    """未通過驗證的路徑也不能沉默：前端靠 sys_msg 收掉轉圈。"""
    from app.core_pro.manuscript.manuscript_routes import _MAX_CHAT_USER_MSG_CHARS

    _send_chat(bundle["sio"], msg="x" * (_MAX_CHAT_USER_MSG_CHARS + 1))

    names = _names(bundle["sio"])
    assert "sys_msg" in names
    assert "job_queued" not in names
    assert not bundle["executor"].calls
