# 檔案路徑: test/unit/test_llm_binding_guard.py
# 產生時間: 2026-08-06 15:00 +08:00
# 版本: v1.0
# 模組定位:
#   task_bindings 不得被綁到非生成模型的回歸測試。
# 背景:
#   2026-08-06 事故：nvidia/nemotron-3.5-content-safety:free（4B guardrail）
#   被綁到 task_8drafter，草稿生成永遠出不來。llm_routes v0.4 補了兩道擋
#   —— 模型選單過濾、/connection/update 拒寫 —— 但兩道都在「模型」那一層。
#   已經躺在 llm_connections 裡的髒連線不需要重寫模型就能被重新綁到別的 task，
#   所以同一天 task_2a_chat 與 task_2cubegen 仍然綁在那顆 guardrail 上。
#   v0.5 把擋補在 /binding/update，這才是最後一道閘門。
# 主要責任:
#   1. /binding/update 綁到非生成模型時回 400，且不得寫進 DB。
#   2. 綁到一般模型時照常寫入。
#   3. 解除綁定（connection_id=None）不得被這道擋誤傷。
#   4. /connection/list 必須帶出 is_non_generative，前端才關得掉那個選項。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   對應 llm_routes.py v0.5。判斷依據是「連線目前掛的 model_name」，
#   不是使用者送上來的欄位 —— 送上來的只有 connection_id。
# 安全邊界:
#   - LLMModel.execute_query 全程被替換掉，測試不碰真的 llm_match.db。
# 維護提醒:
#   - 若有人把 /binding/update 的檢查拿掉，test_reject_binding_to_guardrail 會紅。
#   - NON_GENERATIVE_MODEL_HINTS 新增字串時，順手在 GUARDRAIL_MODELS 補一筆。
# 驗證方式:
#   python -m pytest test/unit/test_llm_binding_guard.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from flask import Flask

from app.llm_service import llm_routes
from app.llm_service.llm_model import LLMModel


CONN_GUARDRAIL = 10          # 事故當事人：nvidia/nemotron-3.5-content-safety:free
CONN_CHAT = 13               # google/gemini-2.5-flash
CONN_ROBOTICS = 8            # google/gemini-robotics-er-1.6-preview

CONNECTIONS = {
    CONN_ROBOTICS: "gemini-robotics-er-1.6-preview",
    CONN_GUARDRAIL: "nvidia/nemotron-3.5-content-safety:free",
    CONN_CHAT: "gemini-2.5-flash",
}


class _FakeDB:
    """記下所有寫入，好斷言「被擋下來時真的沒寫」。"""

    def __init__(self):
        self.writes = []

    def execute_query(self, sql, params=None, fetch_one=False, commit=False):
        if commit:
            self.writes.append((" ".join(sql.split()), params))
            return 1
        if "FROM llm_connections WHERE id" in sql:
            conn_id = (params or (None,))[0]
            if conn_id not in CONNECTIONS:
                return None
            return {"model_name": CONNECTIONS[conn_id]}
        if "FROM llm_connections" in sql:
            return [
                {"id": cid, "model_name": name, "api_key": "", "is_encrypted": 0, "status": "locked"}
                for cid, name in sorted(CONNECTIONS.items())
            ]
        return []


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(LLMModel, "execute_query", staticmethod(fake.execute_query))
    monkeypatch.setattr(
        LLMModel, "sanitize_connection_for_output", staticmethod(lambda row: dict(row or {}))
    )
    return fake


@pytest.fixture
def app():
    # 只需要一個能提供 request context 的殼；直接呼叫 view function，
    # 繞開 limiter 與登入，測的是這條路由自己的判斷。
    return Flask(__name__)


def _post_binding(app, task_id, conn_id):
    with app.test_request_context(
        "/api/llm/binding/update",
        method="POST",
        json={"task_id": task_id, "connection_id": conn_id},
    ):
        return llm_routes.update_binding()


# --- /binding/update -------------------------------------------------------

def test_reject_binding_to_guardrail(app, db):
    """核心：guardrail 模型不得被綁上任何 task，且不得寫進 DB。"""
    body, status = _post_binding(app, "task_2a_chat", CONN_GUARDRAIL)

    assert status == 400
    payload = body.get_json()
    assert payload["success"] is False
    assert "content-safety" in payload["message"]
    assert db.writes == [], "被擋下來卻還是寫進 task_bindings"


def test_reject_binding_to_robotics_model(app, db):
    """robotics 模型同樣不是拿來寫東西的（llm_match.db 裡真的躺著一顆）。"""
    _, status = _post_binding(app, "task_2cubegen", CONN_ROBOTICS)
    assert status == 400
    assert db.writes == []


def test_allow_binding_to_chat_model(app, db):
    """一般對話模型照常放行 —— 這道擋不能誤傷正常綁定。"""
    body, status = _post_binding(app, "task_2a_chat", CONN_CHAT)

    assert status == 200
    assert body.get_json()["success"] is True
    assert len(db.writes) == 1
    sql, params = db.writes[0]
    assert sql.startswith("UPDATE task_bindings SET connection_id")
    assert params == (CONN_CHAT, "task_2a_chat")


def test_allow_clearing_binding(app, db):
    """解除綁定送的是 None，查不到 model_name，不得被當成非生成模型擋掉。"""
    _, status = _post_binding(app, "task_2a_chat", None)
    assert status == 200
    assert db.writes[0][1] == (None, "task_2a_chat")


def test_unknown_connection_is_not_blocked_by_this_guard(app, db):
    """查不到的連線不歸這道擋管，維持既有行為，不要順手改變語意。"""
    _, status = _post_binding(app, "task_2a_chat", 9999)
    assert status == 200


# --- /connection/list ------------------------------------------------------

def test_connection_list_flags_non_generative(app, db):
    """前端要靠這個旗標把選項關成 disabled；沒有它，選單照樣選得到。"""
    with app.test_request_context("/api/llm/connection/list"):
        body, status = llm_routes.list_connections()

    assert status == 200
    flags = {c["id"]: c["is_non_generative"] for c in body.get_json()["connections"]}
    assert flags == {CONN_ROBOTICS: True, CONN_GUARDRAIL: True, CONN_CHAT: False}
