# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_paq_chat_persistence.py
# 子系統定位:
#   PAQ 2A Co-Pilot 對話的伺服器持久化契約，走真實 HTTP（Flask test client），
#   不直接呼叫內部函式。
# 主要責任:
#   1. 前端實際打的那條路徑（POST /api/paq/run_task, task_2a_chat）**會**把對話寫到磁碟。
#   2. 寫進去的東西讀得回來（GET /api/paq/chat_history/<pid>），不是 write-only。
#   3. 最新的 taxonomy/cube 真的送達 execute_paq_chat。
#   4. 存檔失敗時回應明說 persisted=false，不得靜默。
#   5. 角色門檻：POST 要 editor、GET 只要 viewer。
# 明確不負責:
#   - 不驗 LLM 回覆品質（execute_paq_chat 一律被替換成假的，不會真的叫模型）。
#   - 不驗 10 分鐘續存視窗的邊界（那是 _save_chat_record 既有行為，本輪未動）。
#   - 不驗前端 loadChatHistory() 的 DOM 行為 —— 那要瀏覽器實測，見 HANDOFF。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 下游服務:
#   app.create_app() 建的 Flask app、tmp_path 底下的 sqlite 與 data 目錄。
# 讀寫或持久化位置:
#   全部在 tmp_path。chat 目錄由 SQLALCHEMY_DATABASE_URI 的 dirname 推導
#   （PaqCore._chat_dir 的既有行為），所以把 DB 指到 tmp 就同時隔離了檔案輸出。
#   **不碰真實 data/、不碰 data/roothinks.db。**
# 不變量:
#   - 「有檔案產生」與「讀得回來」必須分開斷言。只驗其中一半的話，
#     write-only（index_service 的現成教訓）或 read-nothing 都會假綠。
#   - AUTH_MODE 必須是 session：其他模式下角色守衛直接放行，ACL 斷言會假綠。
# 相關 NOTE:
#   NOTE(NOTE-022)：PAQ 2A 對話的持久化寫在沒有呼叫端的 PaqCore.run_paq_task 裡，
#   從未執行過；修在 route 而不是復活死碼，以及為什麼不把對話餵進 Drafter。
# 驗證:
#   python -m pytest test/unit/test_paq_chat_persistence.py -q
# ---------------------------------------------------------------------------
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "PAQCHT-p"


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        # session 模式是本檔的前提，不是裝飾：其他模式下角色守衛直接放行。
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'paq.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'paq_manu.db'}"},
        "SERVER_NAME": None,
    })
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _make_user_with_role(app, name, role):
    from app import db
    from app.models import Project, User, WorkspaceMember

    with app.app_context():
        user = User(username=name, email=f"{name}@test.local", system_role="user")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        if Project.query.filter_by(project_id=PID).first() is None:
            db.session.add(Project(project_id=PID, name="PAQ Chat", status="formal"))
        db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        db.session.commit()
        return user.id


def _login(client, name):
    resp = client.post("/api/auth/login",
                       json={"email": f"{name}@test.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


@pytest.fixture()
def fake_chat(monkeypatch):
    """替換掉真正的 LLM 呼叫，並記下它收到的參數。"""
    from app.llm_service.matching_tasks import task2A_paqchat

    seen = {}

    def _fake(project_context, chat_history, user_input, taxonomy_data=None, cube_data=None):
        seen["taxonomy_data"] = taxonomy_data
        seen["cube_data"] = cube_data
        seen["user_input"] = user_input
        return True, {"reply": f"echo:{user_input}"}

    monkeypatch.setattr(task2A_paqchat, "execute_paq_chat", _fake)
    return seen


def _chat_files(tmp_path):
    return sorted((tmp_path / PID / "paq" / "chat").glob("paq-chat_*.json"))


def _send(client, message="定義一下 X 軸"):
    return client.post("/api/paq/run_task", json={
        "pid": PID,
        "task_id": "task_2a_chat",
        "input_data": {"user_input": message, "chat_history": []},
    })


def test_chat_turn_is_written_to_disk(app, tmp_path, fake_chat):
    """核心回歸：修好之前這裡一個檔案都不會出現（存檔寫在沒有呼叫端的死碼裡）。"""
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    assert _chat_files(tmp_path) == [], "前置條件：一開始不該有 chat 檔"

    resp = _send(client)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["data"]["reply"] == "echo:定義一下 X 軸"
    assert body["data"]["persisted"] is True

    files = _chat_files(tmp_path)
    assert len(files) == 1, f"對話沒有落到磁碟: {files}"
    records = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["user"] == "定義一下 X 軸"
    assert records[0]["ai"] == "echo:定義一下 X 軸"
    # 決策 provenance 同 NOTE-020：沒有主體的紀錄事後無法歸屬。
    assert records[0]["actor"] == "pi@test.local"
    assert records[0]["time"]


def test_written_chat_is_readable_back(app, tmp_path, fake_chat):
    """另一半：只驗有檔案的話，write-only 路徑照樣全綠（index_service 的教訓）。"""
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    _send(client, "第一句")
    _send(client, "第二句")

    resp = client.get(f"/api/paq/chat_history/{PID}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    records = resp.get_json()["data"]["records"]
    assert [r["user"] for r in records] == ["第一句", "第二句"]
    assert [r["ai"] for r in records] == ["echo:第一句", "echo:第二句"]


def test_taxonomy_and_cube_reach_the_chat_task(app, tmp_path, fake_chat):
    """v0.6 的『把最新 taxonomy/cube 餵給 chat』也只寫在死碼裡，同樣從未執行。"""
    from app import db
    from app.models import PaqSurvey, Project

    _make_user_with_role(app, "pi", "owner")
    with app.app_context():
        project = Project.query.filter_by(project_id=PID).first()
        db.session.add(PaqSurvey(
            project_ref_id=project.id,
            axis_labels={"x": "方法", "y": "情境", "z": "產出"},
            axis_tags={"x": ["RAG"]},
            cube_data=[{"x": 0, "y": 1, "z": 2}],
        ))
        db.session.commit()

    client = app.test_client()
    _login(client, "pi")
    assert _send(client).status_code == 200

    assert fake_chat["taxonomy_data"]["axis_labels"]["x"] == "方法"
    assert fake_chat["taxonomy_data"]["axis_tags"]["x"] == ["RAG"]
    assert fake_chat["cube_data"] == [{"x": 0, "y": 1, "z": 2}]


def test_persist_failure_is_surfaced_not_swallowed(app, tmp_path, fake_chat, monkeypatch):
    """存檔炸掉時使用者仍拿得到回覆，但必須知道這一輪沒存下來。"""
    from app.core_pro.paq.paq_core import PaqCore

    def _boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(PaqCore, "_save_chat_record", staticmethod(_boom))

    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    resp = _send(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["reply"] == "echo:定義一下 X 軸"
    assert body["data"]["persisted"] is False
    assert _chat_files(tmp_path) == []


def test_corrupt_session_file_does_not_hide_the_rest(app, tmp_path, fake_chat):
    """單一檔案損毀不該讓整段歷史消失，但也不得假裝那個檔不存在（會有 warning）。"""
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    _send(client, "好的那一句")
    chat_dir = tmp_path / PID / "paq" / "chat"
    (chat_dir / "paq-chat_000101-000000.json").write_text("{ not json", encoding="utf-8")

    resp = client.get(f"/api/paq/chat_history/{PID}")
    assert resp.status_code == 200
    records = resp.get_json()["data"]["records"]
    assert [r["user"] for r in records] == ["好的那一句"]


class TestPaqCacheBusting:
    """
    改了 paq_*.js 卻沒 bump paq.html 的 `?v=`，瀏覽器會吃舊檔：
    測試全綠、使用者看到的還是舊行為。本輪實際踩到（見 HANDOFF §3.15）。

    既有的 `test_manuscript_section_switch.py::TestCacheBusting` 只涵蓋
    manuscript_workspace.html，而且它比對的是 `url_for(...)` 形式；
    paq.html 用的是純路徑 `/static/js/...`，那支測試看不到這裡。
    """

    PAQ_HTML = PROJECT_ROOT / "app" / "templates" / "paq.html"

    @pytest.mark.parametrize("asset,minimum", [
        ("paq_initial.js", 0.4),   # NOTE-025 改寫 PID 解析（0.3 是 loadChatHistory 那輪）
        ("paq_interact.js", 0.2),  # loadChatHistory() 與 persisted 提示
        ("paq_project.js", 0.4),   # NOTE-026 錯誤分區 + NOTE-027 formal 鎖定範圍
    ])
    def test_changed_paq_assets_are_cache_busted(self, asset, minimum):
        html = self.PAQ_HTML.read_text(encoding="utf-8")
        # 錨定在 script src 上。寬鬆的 `{asset}.*\?v=` 會從別處的檔名一路吃到
        # 後面某個不相干資產的版本號（manuscript 那支測試已經踩過這個坑）。
        m = re.search(rf'src="/static/js/{re.escape(asset)}\?v=([\d.]+)"', html)
        assert m, f"paq.html 找不到 {asset} 的 ?v= 標記"
        assert float(m.group(1)) >= minimum, (
            f"{asset} 的 ?v={m.group(1)} 低於本次變更的 {minimum}，瀏覽器會吃到舊檔"
        )


def test_viewer_cannot_chat_but_can_read_history(app, tmp_path, fake_chat):
    """POST 走 editor 門檻、GET 走 viewer 門檻，兩邊都要驗到才算描述完整。"""
    _make_user_with_role(app, "pi", "owner")
    _make_user_with_role(app, "reader", "viewer")

    owner_client = app.test_client()
    _login(owner_client, "pi")
    assert _send(owner_client, "擁有者留下的話").status_code == 200

    viewer_client = app.test_client()
    _login(viewer_client, "reader")

    blocked = _send(viewer_client, "viewer 想插話")
    assert blocked.status_code == 403, blocked.get_data(as_text=True)

    readable = viewer_client.get(f"/api/paq/chat_history/{PID}")
    assert readable.status_code == 200, readable.get_data(as_text=True)
    records = readable.get_json()["data"]["records"]
    assert [r["user"] for r in records] == ["擁有者留下的話"]
