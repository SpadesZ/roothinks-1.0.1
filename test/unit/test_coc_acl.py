# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_coc_acl.py
# 子系統定位:
#   COC 這條新管道的權限防線。prompt 與 cmd_load_block 一樣是讀取管道，
#   本檔驗的是「章節層 ACL 有沒有跟著 COC 一起進到 prompt 這條路徑上」。
# 主要責任:
#   1. chat_message 必須驗目標章節的授權，不是只驗專案成員身分。
#   2. 限定編輯（coauthor）的 COC 不得包含 2C 全篇、未指派章節的正文與其 2A 歷史。
#   3. 以上都在「真正送給 provider 的字串」上檢查，不是只看中間層回傳值。
# 明確不負責:
#   - 不驗 can_read_section / can_write_section 本身的語意（見 test_chapter_perm.py）。
#   - 不驗 COC 的組裝順序與預算（見 test_coc_bundle.py）。
#   - 不驗論文證據的納入／排除（見 test_evidence_inclusion.py）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   _get_data_root 被 monkeypatch 導向 tmp_path/acldata；不碰真實 data/。
# 不變量:
#   - **每一個「不在場」斷言都必須有一個同資料、同路徑、只換角色的「在場」對照。**
#     上一輪踩過：檢索因為沒有共同詞元而回 0 筆，於是「排除的論文不在場」
#     從頭到尾沒被檢驗過卻是綠的。X 根本沒被產生時，「X 不在場」恆為真。
#     本檔的 owner 案例就是那個對照組：同一批哨兵在放行條件下必須進得了 prompt。
#   - 攔截點固定在 task_8drafter 匯入的 dispatch_task —— 離 provider 最近、
#     且仍拿得到完整 prompt 的位置。
# 相關 NOTE:
#   NOTE-012（COC 伺服器端組裝）、NOTE-015（prompt 是讀取管道，套用章節 ACL）。
# 驗證:
#   python -m pytest test/unit/test_coc_acl.py -q
# ---------------------------------------------------------------------------
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "COCACL-p"
MINE = "introduction"          # coauthor 被指派的章節
THEIRS = "method"              # coauthor 未被指派的章節

S_MINE = "SENTINEL_ASSIGNED_INTRO_BODY"
S_THEIRS = "SENTINEL_UNASSIGNED_METHOD_BODY"
S_THEIRS_CHAT = "SENTINEL_UNASSIGNED_METHOD_CHAT"
S_PAPER = "SENTINEL_2C_WHOLE_PAPER_BODY"
S_MY_CHAT = "SENTINEL_MY_OWN_CHAT_TURN"

USER_MSG = "請幫我續寫這一章"
TITLE = "COC ACL Paper"


# ---------------------------------------------------------------------------
# 環境
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'acl.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'acl_manu.db'}"},
        "SERVER_NAME": None,
    })
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with application.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=PID, name="COC ACL Test", status="formal"))
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    """
    測試資料一律落在 tmp_path。

    _get_data_root() 是從 SQLALCHEMY_DATABASE_URI 推導的，但 worker 路徑上
    有沒有 app context 並不保證，沒有 context 時它會 fallback 到 cwd/data ——
    那就是真實資料目錄。直接 monkeypatch 取值函式最保險。

    必須逐一 patch 每個模組：這些模組是 `from ... import _get_data_root`，
    各自持有一份獨立的名稱綁定，只 patch manuscript_io 對它們無效。
    本檔實測踩到：漏了 coc_bundle 那份，2A 歷史就跑去讀真實 data/ 而讀不到
    tmp 裡的哨兵，測試以「歷史沒進 prompt」的形式失敗。
    """
    root = tmp_path / "acldata"
    root.mkdir()
    from app.core_pro.manuscript import coc_bundle as cocb
    from app.core_pro.manuscript import manuscript_io as mio
    from app.core_pro.manuscript import manuscript_routes as mroutes
    from app.core_pro.manuscript import manuscript_ruling as mruling
    for module in (mio, mroutes, cocb, mruling):
        monkeypatch.setattr(module, "_get_data_root", lambda: str(root))
    return root


@pytest.fixture()
def make_user(app):
    def _make(name, role=None):
        from app import db
        from app.models import User, WorkspaceMember

        with app.app_context():
            user = User(username=name, email=f"{name}@test.local")
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()
            uid = user.id
            if role:
                db.session.add(WorkspaceMember(user_id=uid, pid=PID, role=role))
                db.session.commit()
        return uid

    return _make


def _assign(app, user_id, section):
    from app import db
    from app.models import ChapterAssignment

    with app.app_context():
        db.session.add(ChapterAssignment(pid=PID, section_key=section, user_id=user_id))
        db.session.commit()


def _login(client, name):
    resp = client.post(
        "/api/auth/login",
        json={"email": f"{name}@test.local", "password": "password123"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _sio(app, name):
    from app import socketio

    client = app.test_client()
    _login(client, name)
    sio = socketio.test_client(app, flask_test_client=client, namespace="/manu_ws")
    assert sio.is_connected("/manu_ws"), "socket 連線被拒"
    sio.get_received("/manu_ws")
    return sio


def _seed(app, data_root):
    """兩章正文、兩章 2A 歷史、一份 2C 全篇。哨兵字串各自唯一。"""
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO

    with app.app_context():
        ManuscriptIO.save_block_version(pid=PID, section=MINE, title=TITLE,
                                        content=f"<p>{S_MINE}</p>")
        ManuscriptIO.save_block_version(pid=PID, section=THEIRS, title=TITLE,
                                        content=f"<p>{S_THEIRS}</p>")
        ManuscriptIO.save_paper_version(pid=PID, title=TITLE,
                                        content=f"<p>{S_PAPER}</p>")

    chat_dir = os.path.join(str(data_root), PID, "manuscript", "chat")
    os.makedirs(chat_dir, exist_ok=True)
    for section, sentinel in ((MINE, S_MY_CHAT), (THEIRS, S_THEIRS_CHAT)):
        with open(os.path.join(chat_dir, f"chat_{section}.json"), "w", encoding="utf-8") as f:
            json.dump([{"role": "user", "content": sentinel, "type": "text"}], f,
                      ensure_ascii=False)


class _InlineExecutor:
    """
    同步跑 worker，好讓測試拿得到「真正送出的 prompt」。

    正式碼是 ThreadPoolExecutor；這裡換成同步只是為了讓斷言可觀測，
    被測的 _process_chat_job 與其下游完全是正式路徑。
    """

    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append(args)
        fn(*args, **kwargs)
        return None


def _drive_chat(app, monkeypatch, username, section):
    """以某個使用者跑一次完整生成，回傳 (socket 事件名, provider 收到的 prompt)。"""
    from app.core_pro.manuscript import manuscript_routes as mroutes
    from app.llm_service.matching_tasks import task_8drafter

    captured = []

    def fake_dispatch(task_id, prompt, **kwargs):
        captured.append(str(prompt))
        return {"ok": True, "text": "drafted."}

    monkeypatch.setattr(task_8drafter, "dispatch_task", fake_dispatch)
    # 意圖路由本身也是一次 LLM call，固定成 draft 讓測試只聚焦在寫作 prompt。
    monkeypatch.setattr(mroutes.ai_drafter, "_detect_intent", lambda *a, **k: "draft")
    executor = _InlineExecutor()
    monkeypatch.setattr(mroutes, "_CHAT_EXECUTOR", executor)

    sio = _sio(app, username)
    sio.emit("chat_message",
             {"pid": PID, "msg": USER_MSG, "section": section, "title": TITLE},
             namespace="/manu_ws")
    events = sio.get_received("/manu_ws")
    if sio.is_connected("/manu_ws"):
        sio.disconnect(namespace="/manu_ws")
    return [e["name"] for e in events], "\n".join(captured), events


# ---------------------------------------------------------------------------
# ① socket handler 有沒有驗目標章節
# ---------------------------------------------------------------------------


class TestChatMessageChecksTargetSection:
    def test_coauthor_blocked_on_unassigned_section(self, app, make_user, monkeypatch, data_root):
        """
        原始缺陷：handle_chat 只驗 workspace membership，section 直接取自請求體。
        限定編輯偽造 payload 指定未指派章節，就能拿到該章的生成結果。
        """
        uid = make_user("acl_co1", "coauthor")
        _assign(app, uid, MINE)
        _seed(app, data_root)

        names, prompt, events = _drive_chat(app, monkeypatch, "acl_co1", THEIRS)

        assert "job_queued" not in names, "未指派章節的生成請求竟然被受理"
        assert "sys_msg" in names
        assert any("權限不足" in (e["args"][0].get("msg") or "")
                   for e in events if e["name"] == "sys_msg")
        assert not prompt, "被拒絕的請求不該送出任何 provider request"

    def test_coauthor_allowed_on_assigned_section(self, app, make_user, monkeypatch, data_root):
        """對照組：這道 gate 不能把合法的人一起擋掉。"""
        uid = make_user("acl_co2", "coauthor")
        _assign(app, uid, MINE)
        _seed(app, data_root)

        names, prompt, _events = _drive_chat(app, monkeypatch, "acl_co2", MINE)

        assert "job_queued" in names, "被指派章節的生成請求被誤擋"
        assert prompt, "合法請求沒有送出 provider request"

    def test_viewer_cannot_draft_any_section(self, app, make_user, monkeypatch, data_root):
        """viewer 讀得到全部章節，但一章都不能寫，自然也不能叫 Drafter 產內容。"""
        make_user("acl_v1", "viewer")
        _seed(app, data_root)

        for section in (MINE, THEIRS):
            names, prompt, _e = _drive_chat(app, monkeypatch, "acl_v1", section)
            assert "job_queued" not in names, f"viewer 竟然能對 {section} 發動生成"
            assert not prompt

    def test_non_member_cannot_draft(self, app, make_user, monkeypatch, data_root):
        """非成員在專案層就該被擋下（回歸護欄，不是本輪新增的行為）。"""
        make_user("acl_out")
        _seed(app, data_root)

        names, prompt, _e = _drive_chat(app, monkeypatch, "acl_out", MINE)
        assert "job_queued" not in names
        assert not prompt


# ---------------------------------------------------------------------------
# ② COC 內容有沒有守住讀取範圍 —— 在 provider request 上檢查
# ---------------------------------------------------------------------------


class TestCocRespectsReadScopeAtProviderRequest:
    def test_owner_sees_everything(self, app, make_user, monkeypatch, data_root):
        """
        *** 這是下面那個測試的對照組，不是可有可無的正向測試。 ***

        沒有它，「2C 不在 coauthor 的 prompt 裡」可能只是因為 2C 從來沒被組進去過
        （檔案路徑錯、哨兵拼錯、bundle 根本沒跑）。這裡先證明同一批哨兵在放行
        條件下確實到得了 provider request，下面的否定斷言才有意義。
        """
        make_user("acl_owner", "owner")
        _seed(app, data_root)

        names, prompt, _e = _drive_chat(app, monkeypatch, "acl_owner", MINE)

        assert "job_queued" in names
        assert S_MINE in prompt, "目前章節正文沒進 provider request"
        assert S_PAPER in prompt, "2C 全篇沒進 provider request —— 否定斷言會空過"
        assert S_THEIRS in prompt, "其他章節沒進 provider request —— 否定斷言會空過"
        assert S_MY_CHAT in prompt, "本章 2A 歷史沒進 provider request"

    def test_scoped_coauthor_sees_only_own_section(self, app, make_user, monkeypatch, data_root):
        """
        限定編輯即使只指定自己的章節，也不得經由 COC 讀到 2C 全篇。

        2C 是所有章節組裝出來的成品，放行等於整篇繞過章節層讀取限制 ——
        原本 readable_sections 只限制得到「其他 2B 章節」那一段迴圈。
        """
        uid = make_user("acl_co3", "coauthor")
        _assign(app, uid, MINE)
        _seed(app, data_root)

        names, prompt, _e = _drive_chat(app, monkeypatch, "acl_co3", MINE)

        assert "job_queued" in names
        assert prompt
        assert S_MINE in prompt, "自己章節的正文被誤擋，這道 gate 開過頭了"
        assert S_MY_CHAT in prompt, "自己章節的 2A 歷史被誤擋"

        assert S_PAPER not in prompt, "限定編輯經由 COC 讀到了 2C 全篇"
        assert S_THEIRS not in prompt, "限定編輯經由 COC 讀到了未指派章節的正文"
        assert S_THEIRS_CHAT not in prompt, "限定編輯經由 COC 讀到了未指派章節的 2A 歷史"

    def test_open_access_widens_read_but_not_paper(self, app, make_user, monkeypatch, data_root):
        """
        owner 開啟 coauthor_open_access 後，限定編輯讀得到其他章節，
        但 2C 全篇仍由 _socket_can_read_paper 依同一個開關放行 —— 這裡固定住
        「讀取範圍放寬」與「寫入權不放寬」的分界，避免日後誤把兩者綁在一起。
        """
        from app import db
        from app.models import ProjectCollabSetting

        uid = make_user("acl_co4", "coauthor")
        _assign(app, uid, MINE)
        with app.app_context():
            db.session.add(ProjectCollabSetting(pid=PID, coauthor_open_access=True))
            db.session.commit()
        _seed(app, data_root)

        names, prompt, _e = _drive_chat(app, monkeypatch, "acl_co4", MINE)
        assert "job_queued" in names
        assert S_THEIRS in prompt, "開放後仍讀不到其他章節"

        # 開放的是讀取，寫入權不變：未指派章節仍不得發動生成。
        names2, prompt2, _e2 = _drive_chat(app, monkeypatch, "acl_co4", THEIRS)
        assert "job_queued" not in names2, "開放讀取的開關不得放寬寫入權"
        assert not prompt2


# ---------------------------------------------------------------------------
# ③ 組裝層本身：ACL 參數沒帶要當場爆，不能靜默放行
# ---------------------------------------------------------------------------


class TestBundleAclContract:
    def test_missing_acl_arguments_raise(self, app):
        """
        第一版把 ACL 參數設成寬鬆預設，新接的 socket 路徑一個都沒傳，
        於是限定編輯讀得到整篇。現在忘了帶會 TypeError，不會靜默外洩。
        """
        from app.core_pro.manuscript.coc_bundle import build_coc_bundle

        with app.app_context():
            with pytest.raises(TypeError):
                build_coc_bundle(PID, MINE, formal_pid=PID)

    def test_denied_sections_are_recorded_not_silent(self, app, data_root):
        """被權限擋掉的段落要留在 notes 裡，否則作者無從得知草稿少了什麼。"""
        from app.core_pro.manuscript.coc_bundle import build_coc_bundle

        _seed(app, data_root)
        with app.app_context():
            bundle = build_coc_bundle(
                PID, MINE,
                readable_sections=[MINE],
                can_read_current_section=True,
                include_paper=False,
                formal_pid=PID,
            )

        assert S_PAPER not in bundle["text"]
        assert any("paper_2c" in n for n in bundle["notes"]), bundle["notes"]


# ---------------------------------------------------------------------------
# ④ 任務 3：未標版本留言（s_ver=NULL）不得混入 COC（NOTE-010）
# ---------------------------------------------------------------------------


class TestCommentVersionFiltering:
    """
    NOTE(NOTE-010, 任務 3)：有給 s_ver 就嚴格篩選，s_ver=NULL 的舊留言不混入。

    每個「不在場」斷言都配一個「在場」對照組（與本檔其他測試相同的要求）。
    對照組：版本正確的留言確實進入 bundle["text"]。
    否定斷言：s_ver=NULL 的舊留言被排除，且排除數量出現在 notes 裡。

    測試透過 mock _build_coc_for_request 的留言查詢，避免需要完整的 socket 路徑。
    """

    S_VERSIONED_COMMENT = "SENTINEL_COMMENT_MATCHED_VERSION"
    S_NULL_VER_COMMENT = "SENTINEL_COMMENT_NULL_VERSION"
    S_WRONG_VER_COMMENT = "SENTINEL_COMMENT_WRONG_VERSION"

    def _make_comments(self, app, *, current_s_ver: str) -> None:
        """種三種留言：正確版本、s_ver=NULL（舊留言）、版本不符。

        author 是 relationship（FK 到 users.id），測試不需要真實 User，
        所以只設 author_id=None（nullable）讓 DB 接受即可。
        """
        from app import db
        from app.models import ChapterComment

        with app.app_context():
            # 正確版本的留言：s_ver 與目前版本吻合，應進入 bundle
            db.session.add(ChapterComment(
                pid=PID, section_key=MINE,
                author_id=None, body=self.S_VERSIONED_COMMENT,
                scope="section", s_ver=current_s_ver, resolved=False,
            ))
            # s_ver=NULL 的舊留言（schema 遷移遺留）：應被排除
            db.session.add(ChapterComment(
                pid=PID, section_key=MINE,
                author_id=None, body=self.S_NULL_VER_COMMENT,
                scope="section", s_ver=None, resolved=False,
            ))
            # 版本不符的留言（"9.9" 是刻意選的不可能出現在 fixture 的版本號）：應被排除
            # 不能用 "0.1"，因為 save_block_version 的第一版就是 "0.1"，
            # 那樣這張留言會剛好吻合目前版本，讓否定斷言變成恆真。
            db.session.add(ChapterComment(
                pid=PID, section_key=MINE,
                author_id=None, body=self.S_WRONG_VER_COMMENT,
                scope="section", s_ver="9.9", resolved=False,
            ))
            db.session.commit()

    def test_versioned_comment_reaches_bundle(self, app, data_root):
        """
        *** 這是否定斷言的對照組，不是可有可無的正向測試。 ***

        沒有這個測試，「NULL 版本留言不在 bundle 裡」可能只是因為
        留言根本沒有被讀到（路徑失效、查詢寫錯），而不是過濾有效。
        """
        from app.core_pro.manuscript.coc_bundle import build_coc_bundle, resolve_section_version

        _seed(app, data_root)
        with app.app_context():
            s_ver, _ = resolve_section_version(PID, MINE)
            self._make_comments(app, current_s_ver=s_ver)
            bundle = build_coc_bundle(
                PID, MINE,
                readable_sections=[MINE],
                can_read_current_section=True,
                include_paper=False,
                formal_pid=PID,
                review_comments=[
                    {"body": self.S_VERSIONED_COMMENT, "s_ver": s_ver, "author": "reviewer"}
                ],
            )

        assert self.S_VERSIONED_COMMENT in bundle["text"], \
            "版本正確的留言沒進 bundle —— 對照組失效，否定斷言無意義"

    def test_null_ver_comment_excluded_from_coc_for_request(self, app, data_root, monkeypatch):
        """
        主斷言：_build_coc_for_request 篩選後，s_ver=NULL 的留言不進 COC bundle。

        測試的是 route 層的 _build_coc_for_request，不只是 build_coc_bundle：
        s_ver=NULL 的過濾發生在組裝留言清單的那段迴圈裡（manuscript_routes.py）。

        _session_uid 需要 request context 才能取到 flask_login.current_user；
        直接在 app_context() 裡呼叫會 AttributeError('NoneType')。
        這裡 patch 成回傳 None（等同「無 auth 模式，不套用章節過濾」），
        讓測試聚焦在留言版本過濾邏輯，而不是 auth 路徑。
        """
        from app.core_pro.manuscript import manuscript_routes as mroutes

        # uid=None → can_read_current=True，留言查詢正常執行
        monkeypatch.setattr(mroutes, "_session_uid", lambda: None)

        _seed(app, data_root)
        with app.app_context():
            from app.core_pro.manuscript.coc_bundle import resolve_section_version
            s_ver, _ = resolve_section_version(PID, MINE)
            self._make_comments(app, current_s_ver=s_ver)

            # 直接呼叫 route 層的函式，不走 socket
            bundle = mroutes._build_coc_for_request(PID, MINE)

        # 主斷言：NULL 版本的留言不得出現在 bundle 的任何部分
        assert self.S_NULL_VER_COMMENT not in bundle["text"], \
            "s_ver=NULL 的舊留言混入了 COC bundle（任務 3 失效）"
        assert self.S_WRONG_VER_COMMENT not in bundle["text"], \
            "版本不符的留言混入了 COC bundle"

        # 被排除的留言數要在 notes 裡，否則作者不知道有多少意見沒進草稿
        assert any("excluded_comments" in n for n in bundle.get("notes", [])), \
            f"被排除留言的數量沒進 notes，作者無從得知 notes={bundle.get('notes')}"
