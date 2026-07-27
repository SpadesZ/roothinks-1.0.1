# 檔案路徑: test/unit/test_manuscript_socket_versioning.py
# 產生時間: 2026-07-26 02:10 +08:00
# 版本: v1.0
# 模組定位:
#   Manuscript Socket.IO 版本／自動存檔事件的整合測試。
# 主要責任:
#   1. cmd_save_block 由伺服器指派版號（前端傳的 s_ver 不再被採信）。
#   2. cmd_autosave_block 寫草稿但不產生版本，且不寫 RevisionLog。
#   3. cmd_save_block 成功後草稿被清除。
#   4. cmd_delete_block_version 刪除單一版本。
#   5. cmd_save_paper 產生整數版並帶 sections manifest。
#   6. cmd_restore_paper_version 依 manifest 還原全文與各章節內容。
#   7. block_list 同時回傳舊契約 files 與新結構化 versions。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   使用臨時 SQLite 與 tmp_path 資料根目錄；AUTH_MODE 維持 none（dev 放行），
#   本檔專注驗版本語意，權限另由 test_chapter_perm.py 負責。
# 安全邊界:
#   - DATA_ROOT 以 monkeypatch 導向 tmp_path，不寫入 repo 的 data/ 目錄。
# 維護提醒:
#   - block_list 的 files 欄位是舊契約，e2e 測試與舊前端依賴它，不可移除。
# 驗證方式:
#   python -m pytest test/unit/test_manuscript_socket_versioning.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "SOCKPJ"
FORMAL_PID = "SOCKPJ-p"
SECTION = "introduction"


@pytest.fixture(scope="module")
def _app_bundle(tmp_path_factory):
    """
    App 與 socket client 只建立一次。

    每個測試各自 create_app 會讓 flask_socketio 的全域 singleton 重新綁定到新的
    server 實例，先前建立的 test_client 隨即失效（RuntimeError: not connected）。
    改為整個模組共用一組，測試間的隔離由下方 _clean_state 負責清資料。
    """
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("sockver")

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
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'sock.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'sock_manu.db'}"},
            "SOCKETIO_ASYNC_MODE": "threading",
        }
    )

    data_root = tmp_path / "data"
    data_root.mkdir()
    # manuscript_io 與 manuscript_routes 各自 import 了 _get_data_root，必須分別替換。
    from app.core_pro.manuscript import manuscript_io as mio
    from app.core_pro.manuscript import manuscript_routes as mroutes
    mp.setattr(mio, "_get_data_root", lambda: str(data_root))
    mp.setattr(mroutes, "_get_data_root", lambda: str(data_root))

    with app.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=FORMAL_PID, name="Socket Test", status="formal"))
        db.session.commit()

    client = app.test_client()
    sio = socketio.test_client(app, flask_test_client=client, namespace="/manu_ws")
    assert sio.is_connected("/manu_ws"), (
        f"socket connect refused: AUTH_MODE={app.config.get('AUTH_MODE')} "
        f"API_AUTH_ENABLED={app.config.get('API_AUTH_ENABLED')}"
    )
    sio.get_received("/manu_ws")  # 清掉連線問候訊息

    yield {"app": app, "sio": sio, "data_root": data_root}

    if sio.is_connected("/manu_ws"):
        sio.disconnect(namespace="/manu_ws")
    with app.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()
    mp.undo()


@pytest.fixture()
def bundle(_app_bundle):
    """每個測試前清掉手稿檔案與審計紀錄，讓版本號從 0.1 重新起算。"""
    import shutil

    from app import db
    from app.models import RevisionLog

    manuscript_dir = _app_bundle["data_root"] / FORMAL_PID / "manuscript"
    shutil.rmtree(manuscript_dir, ignore_errors=True)

    with _app_bundle["app"].app_context():
        RevisionLog.query.delete()
        db.session.commit()

    _app_bundle["sio"].get_received("/manu_ws")  # 清掉上一個測試的殘留事件
    return _app_bundle


def _events(sio, name):
    return [e for e in sio.get_received("/manu_ws") if e["name"] == name]


def _emit_save(sio, content, from_ver=None, section=SECTION):
    payload = {
        "pid": FORMAL_PID,
        "title": "Socket Paper",
        "section": section,
        "content": content,
    }
    if from_ver:
        payload["from_ver"] = from_ver
    sio.emit("cmd_save_block", payload, namespace="/manu_ws")
    acks = _events(sio, "save_ack")
    assert acks, "expected save_ack"
    return acks[-1]["args"][0]


# ---------------------------------------------------------------------------
# 章節版本
# ---------------------------------------------------------------------------


class TestSocketBlockVersioning:
    def test_server_assigns_version_ignoring_client_s_ver(self, bundle):
        """前端就算硬送 s_ver=9.9，伺服器仍從 0.1 起算。"""
        sio = bundle["sio"]
        sio.emit(
            "cmd_save_block",
            {
                "pid": FORMAL_PID,
                "title": "T",
                "section": SECTION,
                "content": "c",
                "s_ver": "9.9",
            },
            namespace="/manu_ws",
        )
        ack = _events(sio, "save_ack")[-1]["args"][0]
        assert ack["ver"] == "0.1"

    def test_successive_saves_increment(self, bundle):
        sio = bundle["sio"]
        assert _emit_save(sio, "a")["ver"] == "0.1"
        assert _emit_save(sio, "b")["ver"] == "0.2"
        assert _emit_save(sio, "c")["ver"] == "0.3"

    def test_editing_old_version_creates_v0_4_and_keeps_v0_1(self, bundle):
        """核心需求走完整 socket 路徑：改 V0.1 存檔得 V0.4，V0.1 仍在。"""
        sio = bundle["sio"]
        _emit_save(sio, "ORIGINAL")
        _emit_save(sio, "b")
        _emit_save(sio, "c")

        ack = _emit_save(sio, "EDITED FROM 0.1", from_ver="0.1")
        assert ack["ver"] == "0.4"
        assert ack["from_ver"] == "0.1"

        vers = {v["ver"] for v in ack["versions"]}
        assert vers == {"0.1", "0.2", "0.3", "0.4"}

        block_dir = bundle["data_root"] / FORMAL_PID / "manuscript" / "block" / SECTION
        import json
        original = json.loads((block_dir / "V0.1.json").read_text("utf-8"))
        assert original["content"] == "ORIGINAL"

    def test_save_ack_carries_structured_versions(self, bundle):
        sio = bundle["sio"]
        _emit_save(sio, "a")
        ack = _emit_save(sio, "b", from_ver="0.1")
        top = ack["versions"][0]
        assert top["ver"] == "0.2"
        assert top["from_ver"] == "0.1"
        assert top["filename"] == "V0.2.json"

    def test_block_list_keeps_files_and_adds_versions(self, bundle):
        """files 是舊契約（e2e 與舊前端依賴），versions 是新結構化清單。"""
        sio = bundle["sio"]
        _emit_save(sio, "a")
        _emit_save(sio, "b")

        sio.emit("cmd_list_blocks", {"pid": FORMAL_PID, "section": SECTION}, namespace="/manu_ws")
        payload = _events(sio, "block_list")[-1]["args"][0]

        assert payload["files"] == ["V0.2.json", "V0.1.json"]
        assert [v["ver"] for v in payload["versions"]] == ["0.2", "0.1"]
        assert payload["next_ver"] == "0.3"

    def test_delete_block_version(self, bundle):
        sio = bundle["sio"]
        _emit_save(sio, "a")
        _emit_save(sio, "b")

        sio.emit(
            "cmd_delete_block_version",
            {"pid": FORMAL_PID, "section": SECTION, "ver": "0.1"},
            namespace="/manu_ws",
        )
        payload = _events(sio, "block_version_deleted")[-1]["args"][0]
        assert payload["ok"] is True
        assert [v["ver"] for v in payload["versions"]] == ["0.2"]

    def test_delete_missing_version_reports_failure(self, bundle):
        sio = bundle["sio"]
        sio.emit(
            "cmd_delete_block_version",
            {"pid": FORMAL_PID, "section": SECTION, "ver": "9.9"},
            namespace="/manu_ws",
        )
        assert _events(sio, "block_version_deleted")[-1]["args"][0]["ok"] is False


# ---------------------------------------------------------------------------
# 自動存檔
# ---------------------------------------------------------------------------


class TestSocketAutosave:
    def _autosave(self, sio, content):
        sio.emit(
            "cmd_autosave_block",
            {"pid": FORMAL_PID, "title": "T", "section": SECTION, "content": content},
            namespace="/manu_ws",
        )
        acks = _events(sio, "autosave_ack")
        assert acks, "expected autosave_ack"
        return acks[-1]["args"][0]

    def test_autosave_creates_no_version(self, bundle):
        sio = bundle["sio"]
        for i in range(8):
            assert self._autosave(sio, f"typing {i}")["ok"] is True

        sio.emit("cmd_list_blocks", {"pid": FORMAL_PID, "section": SECTION}, namespace="/manu_ws")
        payload = _events(sio, "block_list")[-1]["args"][0]
        assert payload["versions"] == []
        assert payload["files"] == []

    def test_autosave_draft_is_readable(self, bundle):
        sio = bundle["sio"]
        self._autosave(sio, "unsaved work")

        sio.emit("cmd_load_draft", {"pid": FORMAL_PID, "section": SECTION}, namespace="/manu_ws")
        payload = _events(sio, "draft_loaded")[-1]["args"][0]
        assert payload["ok"] is True
        assert payload["draft"]["content"] == "unsaved work"

    def test_autosave_does_not_write_revision_log(self, bundle):
        """自動存檔每 1.5 秒觸發，寫審計表會把它灌爆。"""
        sio = bundle["sio"]
        for i in range(5):
            self._autosave(sio, f"x{i}")

        with bundle["app"].app_context():
            from app.models import RevisionLog
            assert RevisionLog.query.filter_by(pid=FORMAL_PID).count() == 0

    def test_explicit_save_clears_draft(self, bundle):
        """按下存檔後草稿要清掉，否則下次開啟會誤報「有未存檔內容」。"""
        sio = bundle["sio"]
        self._autosave(sio, "draft content")
        _emit_save(sio, "final content")

        sio.emit("cmd_load_draft", {"pid": FORMAL_PID, "section": SECTION}, namespace="/manu_ws")
        payload = _events(sio, "draft_loaded")[-1]["args"][0]
        assert payload["ok"] is False
        assert payload["draft"] is None

    def test_explicit_save_does_write_revision_log(self, bundle):
        sio = bundle["sio"]
        _emit_save(sio, "real save")
        with bundle["app"].app_context():
            from app.models import RevisionLog
            assert RevisionLog.query.filter_by(pid=FORMAL_PID).count() == 1


# ---------------------------------------------------------------------------
# 主論文 manifest 與還原
# ---------------------------------------------------------------------------


class TestSocketPaperManifest:
    def _save_paper(self, sio, content, from_ver=None):
        payload = {"pid": FORMAL_PID, "title": "Socket Paper", "content": content}
        if from_ver:
            payload["from_ver"] = from_ver
        sio.emit("cmd_save_paper", payload, namespace="/manu_ws")
        acks = [
            e for e in _events(sio, "save_ack") if e["args"][0].get("target") == "paper"
        ]
        assert acks, "expected paper save_ack"
        return acks[-1]["args"][0]

    def test_paper_version_is_integer(self, bundle):
        sio = bundle["sio"]
        assert self._save_paper(sio, "body1")["g_ver"] == "V1"
        assert self._save_paper(sio, "body2")["g_ver"] == "V2"

    def test_manifest_captures_current_section_versions(self, bundle):
        """未指定 sections 時，伺服器自動蒐集各章節目前最新版。"""
        sio = bundle["sio"]
        _emit_save(sio, "intro a")
        _emit_save(sio, "intro b")
        _emit_save(sio, "method a", section="method")

        ack = self._save_paper(sio, "<p>assembled</p>")
        assert ack["sections"]["introduction"] == "0.2"
        assert ack["sections"]["method"] == "0.1"

    def test_restore_paper_version_brings_back_section_contents(self, bundle):
        """manifest 的核心價值：一鍵把各章節回到該版當時的內容。"""
        sio = bundle["sio"]
        _emit_save(sio, "INTRO V1 CONTENT")
        self._save_paper(sio, "<p>paper v1</p>")

        # 之後章節又改了兩版
        _emit_save(sio, "intro later")
        _emit_save(sio, "intro even later")

        sio.emit(
            "cmd_restore_paper_version",
            {"pid": FORMAL_PID, "ver": "V1"},
            namespace="/manu_ws",
        )
        payload = _events(sio, "paper_restored")[-1]["args"][0]

        assert payload["ok"] is True
        assert payload["g_ver"] == "V1"
        assert payload["content"] == "<p>paper v1</p>"
        assert payload["sections"]["introduction"]["ver"] == "0.1"
        assert payload["sections"]["introduction"]["content"] == "INTRO V1 CONTENT"
        assert payload["missing"] == []

    def test_restore_reports_missing_deleted_versions(self, bundle):
        """章節版本被刪除後還原該主論文版，應回報缺漏而不是整批失敗。"""
        sio = bundle["sio"]
        _emit_save(sio, "intro original")
        self._save_paper(sio, "<p>v1</p>")

        sio.emit(
            "cmd_delete_block_version",
            {"pid": FORMAL_PID, "section": SECTION, "ver": "0.1"},
            namespace="/manu_ws",
        )
        sio.get_received("/manu_ws")

        sio.emit(
            "cmd_restore_paper_version",
            {"pid": FORMAL_PID, "ver": "V1"},
            namespace="/manu_ws",
        )
        payload = _events(sio, "paper_restored")[-1]["args"][0]
        assert payload["ok"] is True
        assert payload["missing"] == ["introduction@V0.1"]

    def test_restore_unknown_version(self, bundle):
        sio = bundle["sio"]
        sio.emit(
            "cmd_restore_paper_version",
            {"pid": FORMAL_PID, "ver": "V99"},
            namespace="/manu_ws",
        )
        assert _events(sio, "paper_restored")[-1]["args"][0]["ok"] is False

    def test_paper_list_includes_versions(self, bundle):
        sio = bundle["sio"]
        self._save_paper(sio, "a")
        self._save_paper(sio, "b", from_ver="V1")

        sio.emit(
            "cmd_list_papers",
            {"pid": FORMAL_PID, "title": "Socket Paper"},
            namespace="/manu_ws",
        )
        payload = _events(sio, "paper_list")[-1]["args"][0]
        assert payload["files"] == ["V2.json", "V1.json"]
        assert [v["g_ver"] for v in payload["versions"]] == ["V2", "V1"]
        assert payload["versions"][0]["from_ver"] == "V1"
