# Roothinks source maintenance contract
# 主要責任: 驗收 Literature 找文獻對話的伺服器持久化與角色門檻，走真實 HTTP。
# 上下游: pytest -> Flask test client -> literature_chat_routes -> LiteratureChatStore（tmp_path）。
# 檔案路徑: test/unit/test_literature_chat_persistence.py
# 建立時間: 2026-08-17 +08:00；版本: v1.0
# 模組定位: 「對話與已驗證連結重新整理後還在」這個承諾的護欄。
# 驗證契約:
#   1. POST /api/literature/chat **會**把這一輪寫到磁碟。
#   2. 寫進去的讀得回來（GET history），而且 papers 一起回來 —— 只驗其中一半的話，
#      write-only（index_service 的現成教訓）或 read-nothing 都會假綠。
#   3. papers 沒存回來 = 使用者重新整理後 hyperlink 消失，得再付一次驗證成本。
#   4. 搜尋 cache 命中時不再打搜尋腳，且 meta.cache_hit=true。
#   5. stage_errors 非空的結果不得進 cache（半套結果會被鎖住 6 小時）。
#   6. 角色門檻：POST 要 editor、GET 只要 viewer。
# 明確不負責:
#   - 不驗 LLM 回覆品質（run_chat_turn 一律被替換成假的）。
#   - 不驗前端 DOM 行為 —— 那要瀏覽器實測，見 HANDOFF。
# 安全邊界:
#   DATA_ROOT 與 chat store singleton 都釘到 tmp_path。**不碰真實 data/**。
#   AUTH_MODE 必須是 session：其他模式下角色守衛直接放行，ACL 斷言會假綠。
# 執行: python -m pytest test/unit/test_literature_chat_persistence.py -q
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "LITCHT-p"


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
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'litchat.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'litchat_manu.db'}"},
        "SERVER_NAME": None,
    })
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    # literature_routes 在 import 時就把 DATA_ROOT 綁成模組常數，
    # 且 store/library 都是 lazy singleton —— 三者都要釘到 tmp。
    from app.core_pro.literature import literature_routes as lit_routes

    monkeypatch.setattr(lit_routes, "DATA_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(lit_routes, "literature_chat_store_service", None, raising=False)
    monkeypatch.setattr(lit_routes, "literature_library_service", None, raising=False)

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
            db.session.add(Project(project_id=PID, name="Lit Chat", status="formal"))
        db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        db.session.commit()
        return user.id


def _login(client, name):
    resp = client.post("/api/auth/login",
                       json={"email": f"{name}@test.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


VERIFIED_PAPER = {
    "title": "Cortical processing of the auditory ventral stream",
    "authors": ["Rauschecker, J."],
    "year": 2009,
    "venue": "Nature Neuroscience",
    "doi": "10.1038/nn.2331",
    "url": "https://doi.org/10.1038/nn.2331",
    "link_kind": "doi",
    "scholar_url": "https://scholar.google.com/scholar?q=auditory",
    "source": "crossref",
    "consensus": "both",
}


@pytest.fixture()
def fake_turn(monkeypatch):
    """替換掉真正的 A/B/C 流程，並記下它收到的參數。"""
    from app.llm_service.matching_tasks import task_3a_litchat

    seen = {"history_lengths": [], "search_calls": []}

    def _fake(history, user_input, project_context=None, search_fn=None):
        seen["history_lengths"].append(len(history or []))
        seen["project_context"] = project_context
        result = search_fn(user_input) if search_fn else {"papers": [], "meta": {}}
        seen["search_calls"].append(result)
        meta = dict(result.get("meta") or {})
        meta["should_search"] = True
        return {
            "reply": f"echo:{user_input}",
            "papers": result.get("papers") or [],
            "meta": meta,
        }

    monkeypatch.setattr(task_3a_litchat, "run_chat_turn", _fake)
    return seen


@pytest.fixture()
def fake_scout(monkeypatch):
    from app.llm_service.matching_tasks import task_3bc_scout

    state = {"calls": 0, "result": {"papers": [dict(VERIFIED_PAPER)], "dropped": [], "meta": {"searched_by": ["B", "C"]}}}

    def _fake(query, limit=0):
        state["calls"] += 1
        return state["result"]

    monkeypatch.setattr(task_3bc_scout, "run_debate_search", _fake)
    return state


def _chat_files(tmp_path):
    return sorted((tmp_path / PID / "literature" / "chat").glob("lit-chat_*.json"))


def _send(client, message="幫我找聽覺腹側路徑的關鍵論文"):
    return client.post("/api/literature/chat", json={"pid": PID, "message": message})


def test_chat_turn_is_written_to_disk(app, tmp_path, fake_turn, fake_scout):
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    assert _chat_files(tmp_path) == [], "前置條件：一開始不該有 chat 檔"

    resp = _send(client)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["reply"] == "echo:幫我找聽覺腹側路徑的關鍵論文"
    assert body["persisted"] is True

    files = _chat_files(tmp_path)
    assert len(files) == 1, f"對話沒有落到磁碟: {files}"
    records = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["user"] == "幫我找聽覺腹側路徑的關鍵論文"
    assert records[0]["actor"] == "pi@test.local"


def test_written_chat_and_papers_are_readable_back(app, tmp_path, fake_turn, fake_scout):
    """papers 必須一起存回：只存文字的話重新整理後 hyperlink 就沒了。"""
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    _send(client, "第一句")
    _send(client, "第二句")

    resp = client.get(f"/api/literature/chat/history?pid={PID}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    records = resp.get_json()["records"]

    assert [r["user"] for r in records] == ["第一句", "第二句"]
    assert [r["ai"] for r in records] == ["echo:第一句", "echo:第二句"]
    assert records[0]["papers"][0]["url"] == "https://doi.org/10.1038/nn.2331"
    assert records[0]["papers"][0]["title"] == VERIFIED_PAPER["title"]


def test_history_is_fed_back_into_the_next_turn(app, tmp_path, fake_turn, fake_scout):
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    _send(client, "第一句")
    _send(client, "第二句")

    assert fake_turn["history_lengths"] == [0, 1], "第二輪沒有拿到第一輪的對話"


def test_repeated_query_hits_cache_and_skips_scouts(app, tmp_path, fake_turn, fake_scout):
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    _send(client, "auditory ventral stream")
    assert fake_scout["calls"] == 1

    resp = _send(client, "auditory ventral stream")
    assert fake_scout["calls"] == 1, "同一個查詢又燒了一次 grounding 額度"
    assert resp.get_json()["meta"]["cache_hit"] is True


def test_incomplete_search_is_not_cached(app, tmp_path, fake_turn, fake_scout):
    """stage_errors 非空代表跑一半，快取起來會讓接下來 6 小時都拿殘缺清單。"""
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    fake_scout["result"] = {
        "papers": [dict(VERIFIED_PAPER)],
        "dropped": [],
        "meta": {"stage_errors": {"scout_C": "timeout after 75s"}},
    }

    _send(client, "auditory ventral stream")
    _send(client, "auditory ventral stream")
    assert fake_scout["calls"] == 2, "殘缺結果被寫進 cache 了"


def test_clear_removes_history(app, tmp_path, fake_turn, fake_scout):
    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    _send(client)
    assert len(_chat_files(tmp_path)) == 1

    resp = client.post("/api/literature/chat/clear", json={"pid": PID})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["removed"] == 1
    assert _chat_files(tmp_path) == []

    history = client.get(f"/api/literature/chat/history?pid={PID}").get_json()
    assert history["records"] == []


def test_adopt_merges_as_candidate_only(app, tmp_path, fake_turn, fake_scout):
    """NOTE-013/NOTE-020：匯入永遠只產生 candidate，不得自動 included。"""
    from app.services.literature_library import LiteratureLibrary

    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    resp = client.post("/api/literature/chat/adopt", json={
        "pid": PID,
        # 前端就算送 screening_status 也不得生效（不是授權來源）。
        "papers": [dict(VERIFIED_PAPER, screening_status="included")],
        "topic": "auditory ventral stream",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["added"] == 1

    entries = LiteratureLibrary(str(tmp_path)).load(PID)["entries"]
    assert len(entries) == 1
    entry = list(entries.values())[0]
    assert entry["screening_status"] == "candidate"


def test_adopt_strips_non_metadata_fields_before_merge(app, tmp_path, monkeypatch):
    """
    route 的白名單要有自己的護欄。

    只驗「最終狀態是 candidate」不夠：`merge_candidates` 本來就忽略使用者欄位，
    所以那條斷言在 route 白名單被整段刪掉時仍然全綠（實測過，見 mutation M12）。
    這裡直接攔 merge_candidates，斷言**送進去的東西**就已經乾淨了 ——
    兩層各擋各的，缺一都會留下缺口（同 literature_library_routes 的 NOTE-020 註解）。
    """
    from app.core_pro.literature import literature_routes as lit_routes

    seen = {}

    class _SpyLibrary:
        def merge_candidates(self, pid, candidates, topic="", default_source=""):
            seen["candidates"] = candidates
            seen["default_source"] = default_source
            return {"added": len(candidates), "updated": 0, "total": len(candidates)}

    monkeypatch.setattr(lit_routes, "get_literature_library", lambda: _SpyLibrary())

    _make_user_with_role(app, "pi", "owner")
    client = app.test_client()
    _login(client, "pi")

    resp = client.post("/api/literature/chat/adopt", json={
        "pid": PID,
        "papers": [dict(
            VERIFIED_PAPER,
            screening_status="included",
            screening_decided_by="someone-else@evil.local",
            reading_status="read",
            paper_id="PAPER-HIJACK",
            notes="injected",
        )],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    payload = seen["candidates"][0]
    forbidden = {
        "screening_status", "screening_decided_by", "screening_batch_id",
        "reading_status", "reading_note", "screening_note", "paper_id", "notes",
    }
    leaked = forbidden & set(payload)
    assert not leaked, f"請求體的使用者欄位漏進 library merge: {sorted(leaked)}"
    assert payload["title"] == VERIFIED_PAPER["title"], "metadata 應該照常傳遞"
    assert seen["default_source"] == "lit_chat", "來源標記遺失就追不回這批是怎麼來的"


def test_viewer_cannot_post_but_can_read(app, tmp_path, fake_turn, fake_scout):
    _make_user_with_role(app, "viewer", "viewer")
    client = app.test_client()
    _login(client, "viewer")

    assert _send(client).status_code == 403
    assert client.post("/api/literature/chat/clear", json={"pid": PID}).status_code == 403
    assert client.get(f"/api/literature/chat/history?pid={PID}").status_code == 200
