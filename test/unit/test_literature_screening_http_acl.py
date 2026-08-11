# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_literature_screening_http_acl.py
# 子系統定位:
#   NOTE-020 的 **HTTP 層**驗收：誰真的能透過 API 把論文標成 included。
#   姊妹檔 test_literature_screening_gate.py 驗的是 service 層（actor 必填），
#   它明寫「不驗 HTTP 角色判定」—— 那一段缺口由本檔補上。
# 主要責任:
#   1. owner（PI）與 editor（Co-PI）可以改 screening_status。
#   2. coauthor（章節限定編輯）與 viewer 一律 403。
#   3. 被拒絕時**狀態不得改變**（不是只回 403 但其實寫進去了）。
#   4. 通過時決策者要記成該登入帳號，不是請求體宣稱的人。
# 這個檔證明不了什麼（**很重要，別誤讀**）:
#   本輪在 route 加的 `require_workspace_role(pid, ROLE_EDITOR)` **是重複的一道**。
#   實測把它整段拿掉，viewer 與 coauthor 仍然 403 —— 因為
#   `enforce_project_ownership`（POST ⇒ editor）與模組守衛（coauthor 只能進
#   Manuscript）早就各自擋住了。因此本檔的 403 斷言是**行為契約**，
#   不是「新程式碼有效」的證據。新程式碼真正擋到東西的證明在
#   test_literature_screening_gate.py 的 actor 守衛（A/B 可紅）。
# 明確不負責:
#   - 不驗 library.json 的合併語意（見 tests/test_literature_library.py）。
#   - 不驗 COC 組裝（見 test_coc_producers.py）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   只在 tmp_path（DATA_ROOT 已 monkeypatch 到 tmp）；不碰真實 data/。
# 不變量:
#   - AUTH_MODE 必須是 session：require_workspace_role 在其他模式一律放行，
#     用預設模式跑這個檔會**全部通過但什麼都沒驗到**（假綠）。
#   - 每個「被擋下」的斷言都配一個「換成有權角色就成功」的對照組。
# 相關 NOTE:
#   NOTE-013（三分狀態決定寫作依據）、NOTE-020（角色門檻與 actor provenance）。
# 驗證:
#   python -m pytest test/unit/test_literature_screening_http_acl.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "LITACL-p"


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
        # session 模式是本檔的前提，不是裝飾：其他模式下
        # require_workspace_role 直接 return None（放行），全部斷言會假綠。
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'acl.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'acl_manu.db'}"},
        "SERVER_NAME": None,
    })
    application.config["WTF_CSRF_CHECK_DEFAULT"] = False

    # literature_routes 在 import 時就把 DATA_ROOT 綁成模組常數，
    # 且 get_literature_library() 是 lazy singleton —— 兩者都要釘到 tmp，
    # 否則測試會對真實 data/ 建立 library.json。
    from app.core_pro.literature import literature_routes as lit_routes

    monkeypatch.setattr(lit_routes, "DATA_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(lit_routes, "literature_library_service", None, raising=False)

    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


@pytest.fixture()
def entry_id(app, tmp_path):
    from app.services.literature_library import LiteratureLibrary

    lib = LiteratureLibrary(str(tmp_path))
    lib.merge_candidates(PID, [{"title": "A triage paper", "doi": "10.1/ACL"}])
    return list(lib.load(PID)["entries"].keys())[0]


def _make_user_with_role(app, name, role):
    from app import db
    from app.models import Project, User, WorkspaceMember

    with app.app_context():
        user = User(username=name, email=f"{name}@test.local", system_role="user")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        if Project.query.filter_by(project_id=PID).first() is None:
            db.session.add(Project(project_id=PID, name="Lit ACL", status="formal"))
        db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        db.session.commit()
        return user.id


def _login(client, name):
    resp = client.post("/api/auth/login",
                       json={"email": f"{name}@test.local", "password": "password123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _try_include(client, entry_id):
    return client.post("/api/literature/library/update", json={
        "pid": PID,
        "entry_id": entry_id,
        "patch": {"screening_status": "included"},
        "batch_id": "BATCH-HTTP",
    })


def _status_now(tmp_path, entry_id):
    from app.services.literature_library import LiteratureLibrary

    return LiteratureLibrary(str(tmp_path)).load(PID)["entries"][entry_id]


@pytest.mark.parametrize("role", ["owner", "editor"])
def test_pi_and_copi_can_include(app, tmp_path, entry_id, role):
    """
    PI（owner）與 Co-PI（editor）必須做得到 ——
    這是下面兩條「被擋下」的對照組。少了它，403 可能只是整條 API 壞掉。
    """
    _make_user_with_role(app, f"lead{role}", role)
    client = app.test_client()
    _login(client, f"lead{role}")

    resp = _try_include(client, entry_id)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    entry = _status_now(tmp_path, entry_id)
    assert entry["screening_status"] == "included"
    # 決策者取自登入身分，不是請求體。
    assert entry["screening_decided_by"] == f"lead{role}@test.local"
    assert entry["screening_batch_id"] == "BATCH-HTTP"
    assert entry["screening_decided_at"]


def test_viewer_cannot_include(app, tmp_path, entry_id):
    """
    viewer 不得決定寫作依據。

    **這一條不是本輪新門檻的證據**（實測拿掉那段程式碼，viewer 仍是 403）：
    擋下它的是既有的 `enforce_project_ownership` —— POST/PUT/PATCH 一律要 editor。
    本輪新加的 `require_workspace_role(pid, ROLE_EDITOR)` 目前是重複的一道。
    這條測試的價值是**行為契約**（viewer 永遠不能改 screening），
    不是「新程式碼有效」的證明；後者是 test_literature_screening_gate.py 的 actor 守衛。

    同時斷言狀態沒被改：只檢查 403 的話，「回了 403 但其實已寫進去」看不出來。
    """
    _make_user_with_role(app, "plainviewer", "viewer")
    client = app.test_client()
    _login(client, "plainviewer")

    resp = _try_include(client, entry_id)
    assert resp.status_code == 403, \
        f"viewer 竟然可以改 screening_status：{resp.get_data(as_text=True)}"
    assert _status_now(tmp_path, entry_id)["screening_status"] == "candidate", \
        "viewer 被回 403，但狀態其實已經被改掉了"


def test_coauthor_never_reaches_the_screening_gate_at_all(app, tmp_path, entry_id):
    """
    coauthor 的 403 **不是**本輪這道門擋的，是 `app/__init__.py` 的模組守衛
    （限定編輯只能進 Manuscript）。記錄這件事是為了不要誤讀證據：
    如果只看「coauthor 改不了 screening」就宣稱新門檻對它有效，
    那是把別人的防線算成自己的 —— 拿掉新門檻它照樣 403。

    證明方式：連**不含 screening_status** 的一般更新（寫閱讀筆記）也是 403。
    門檻只套在 screening，所以能擋下這種請求的只可能是更前面的模組守衛。
    """
    _make_user_with_role(app, "sectiononly", "coauthor")
    client = app.test_client()
    _login(client, "sectiononly")

    assert _try_include(client, entry_id).status_code == 403

    plain = client.post("/api/literature/library/update", json={
        "pid": PID, "entry_id": entry_id,
        "patch": {"reading_note": "第 3 節的 triage 流程可引用"},
    })
    assert plain.status_code == 403, (
        "coauthor 竟然進得了 literature 模組 —— 那麼上面那條 403 的來源就要重新確認，"
        f"實際：{plain.status_code} {plain.get_data(as_text=True)}"
    )
    assert _status_now(tmp_path, entry_id)["screening_status"] == "candidate"
