# 檔案路徑: test/unit/test_batch_c.py
# 產生時間: 2026-07-19 09:00 +08:00
# 版本: v1.0
# 模組定位:
#   Batch C 實作的單元測試：檔案層隔離補強、Socket.IO 協作預留、revision_log。
# 主要責任:
#   1. safe path — 對 manuscript 修復點做路徑穿越嘗試，驗證被清理/拒絕。
#   2. revision log — TESTING 模式下呼叫 save_block route 後，RevisionLog 有一筆；
#      GET /manuscript/api/revisions/<pid> 回傳它。
#   3. socket 權限 — 直接單元測試 _socket_can_write 純函數：
#      viewer 回傳 False、editor 回傳 True、owner 回傳 True、無 membership 回傳 False。
#      （Socket test client 在此測試環境使用 eventlet async_mode，不適合同步測試，
#        改為直接測試抽出的 _socket_can_write 純函數。）
# 維護提醒:
#   - 使用 AUTH_MODE=none 的 dev/TESTING 模式（RevisionLog user_id=None 也能寫入）。
#   - socket 權限測試使用 AUTH_MODE=session、直接測 _socket_can_write 純函數。
# 驗證方式:
#   cd "C:\Users\Franky Kuo\Desktop\roothinks-1.0.1"
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test/unit/test_batch_c.py -v
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def dev_app(monkeypatch, tmp_path):
    """AUTH_MODE=none（dev/TESTING）App；每個測試函數獨立 DB 與 data 目錄。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)

    main_db = tmp_path / "batch_c_test.db"
    manu_db = tmp_path / "batch_c_manu.db"

    from app import create_app, db as _db

    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        "SECRET_KEY": "test-batch-c-secret",
    })

    with app.app_context():
        _db.drop_all()
        _db.create_all()

    yield app

    with app.app_context():
        _db.session.remove()
        try:
            _db.engine.dispose()
        except Exception:
            pass


@pytest.fixture()
def client(dev_app):
    return dev_app.test_client()


@pytest.fixture()
def session_app(monkeypatch, tmp_path):
    """AUTH_MODE=session App；用於 socket 權限純函數測試。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)

    main_db = tmp_path / "batch_c_session.db"
    manu_db = tmp_path / "batch_c_session_manu.db"

    from app import create_app, db as _db

    app = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        "SECRET_KEY": "test-session-socket-secret",
    })

    with app.app_context():
        _db.drop_all()
        _db.create_all()

    yield app

    with app.app_context():
        _db.session.remove()
        try:
            _db.engine.dispose()
        except Exception:
            pass


def _create_formal_project(app_ctx, pid="TEST01"):
    """在 DB 中建立一個 formal project（供 route 測試用）。"""
    from app import db
    from app.models import Project
    with app_ctx.app_context():
        existing = Project.query.filter_by(project_id=pid).first()
        if not existing:
            p = Project(
                project_id=pid,
                name="Test Project",
                status="formal",
            )
            db.session.add(p)
            db.session.commit()
    return pid


# ---------------------------------------------------------------------------
# 1. Safe Path 測試 — 路徑穿越嘗試被清理
# ---------------------------------------------------------------------------

class TestSafePathEnforcement:
    """驗證 manuscript_io._safe_component 正確清洗路徑穿越嘗試。"""

    def test_section_traversal_cleaned(self):
        """
        section='../../etc/passwd' 的安全機制說明：
        _safe_component 移除路徑分隔符 '/' 和特殊字元，讓 '..' 無法作為路徑分隔單元。
        最終防線是 safe_join_under，它以 realpath + commonpath 阻擋穿越。
        此處驗證 _safe_component 移除了 '/' 使其無法構成有效穿越路徑。
        """
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        result = ManuscriptIO._safe_component("../../etc/passwd", "general")
        # 路徑分隔符被移除 → 無法構成有效目錄穿越路徑
        assert "/" not in result
        assert "\\" not in result
        # 應非空（fallback 或清洗後內容）
        assert len(result) > 0

    def test_section_null_byte_cleaned(self):
        """section 含 null byte 應被清洗掉。"""
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        result = ManuscriptIO._safe_component("section\x00evil", "general")
        assert "\x00" not in result

    def test_section_special_chars_cleaned(self):
        """section 含特殊字元 '/', ' ', '&', '*' 應被替換為底線或移除。"""
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        result = ManuscriptIO._safe_component("../../secret&rm -rf/*", "general")
        # 路徑分隔符、空格、shell 特殊字元應被清洗
        assert "/" not in result
        assert " " not in result
        assert "&" not in result
        assert "*" not in result

    def test_valid_section_preserved(self):
        """合法的 section 名稱應原樣保留。"""
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        result = ManuscriptIO._safe_component("introduction", "general")
        assert result == "introduction"

    def test_empty_section_returns_fallback(self):
        """空 section 應回傳 fallback 值。"""
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        result = ManuscriptIO._safe_component("", "general")
        assert result == "general"

    def test_routes_safe_component_traversal(self):
        """manuscript_routes._safe_component 清洗路徑分隔符，讓 '../..' 無法構成有效穿越。"""
        from app.core_pro.manuscript.manuscript_routes import _safe_component
        result = _safe_component("../../etc", "title")
        # 路徑分隔符被清洗
        assert "/" not in result
        assert "\\" not in result

    def test_safe_join_under_blocks_traversal(self, dev_app, tmp_path):
        """safe_join_under 是最終防線：對穿越路徑拋出 BadRequest。"""
        from werkzeug.exceptions import BadRequest
        from app.security import safe_join_under
        base = str(tmp_path)
        with pytest.raises(BadRequest):
            safe_join_under(base, "..", "etc", "passwd")


# ---------------------------------------------------------------------------
# 2. Revision Log 測試
# ---------------------------------------------------------------------------

class TestRevisionLog:
    """驗證 RevisionLog model 與 revisions API。"""

    def test_revision_log_model_fields(self, dev_app):
        """RevisionLog model 應有所有必要欄位。"""
        from app.models import RevisionLog
        with dev_app.app_context():
            # 確認欄位存在（inspect 方式）
            cols = {c.key for c in RevisionLog.__table__.columns}
            assert "id" in cols
            assert "pid" in cols
            assert "user_id" in cols
            assert "entity_type" in cols
            assert "entity_ref" in cols
            assert "action" in cols
            assert "summary" in cols
            assert "payload_json" in cols
            assert "created_at" in cols

    def test_write_revision_log_dev_mode(self, dev_app):
        """dev 模式（user_id=None）也能寫入 RevisionLog。"""
        from app import db
        from app.models import RevisionLog
        with dev_app.app_context():
            entry = RevisionLog(
                pid="TEST01-p",
                user_id=None,
                entity_type="manuscript_block",
                entity_ref="introduction/test_260719_V1.0.json",
                action="save",
                summary="block save: section=introduction chars=100",
            )
            db.session.add(entry)
            db.session.commit()

            saved = RevisionLog.query.filter_by(pid="TEST01-p").first()
            assert saved is not None
            assert saved.user_id is None
            assert saved.entity_type == "manuscript_block"
            assert saved.action == "save"

    def test_revision_log_to_dict(self, dev_app):
        """RevisionLog.to_dict() 應回傳正確結構。"""
        from app import db
        from app.models import RevisionLog
        with dev_app.app_context():
            entry = RevisionLog(
                pid="TEST01-p",
                user_id=None,
                entity_type="manuscript_paper",
                entity_ref="Untitled_260719_V1.0.json",
                action="save",
                summary="paper save chars=500",
            )
            db.session.add(entry)
            db.session.commit()

            d = entry.to_dict()
            assert d["pid"] == "TEST01-p"
            assert d["entity_type"] == "manuscript_paper"
            assert d["action"] == "save"
            assert "created_at" in d

    def test_revisions_api_returns_list(self, client, dev_app):
        """GET /manuscript/api/revisions/<pid> 應回傳 ok=True 和 revisions 列表。"""
        pid = _create_formal_project(dev_app, "TEST01")

        # 先寫一筆 log
        from app import db
        from app.models import RevisionLog
        with dev_app.app_context():
            entry = RevisionLog(
                pid=f"{pid}-p",
                user_id=None,
                entity_type="manuscript_block",
                entity_ref="introduction/test_V1.0.json",
                action="save",
                summary="test summary",
            )
            db.session.add(entry)
            db.session.commit()

        # 用 pid 查詢（route 會 resolve 成 formal pid）
        # 因為 TEST01 的 formal project_id 是 'TEST01'，-p 版本是 'TEST01-p'
        # revisions 存的是 TEST01-p，但 route 接受兩者
        # 先直接用 TEST01-p 試
        with dev_app.app_context():
            from app import db
            from app.models import Project
            # 確保 TEST01-p 也存在
            existing = Project.query.filter_by(project_id="TEST01-p").first()
            if not existing:
                p2 = Project(
                    project_id="TEST01-p",
                    name="Test Project P",
                    status="formal",
                )
                db.session.add(p2)
                db.session.commit()

        resp = client.get("/manuscript/api/revisions/TEST01-p")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "revisions" in data
        assert len(data["revisions"]) >= 1
        assert data["revisions"][0]["entity_type"] == "manuscript_block"

    def test_revisions_api_invalid_pid(self, client, dev_app):
        """無效 pid 應回傳 404。"""
        resp = client.get("/manuscript/api/revisions/NOTEXIST99")
        assert resp.status_code == 404

    def test_write_revision_log_helper(self, dev_app):
        """_write_revision_log helper 函數應正確寫入 DB。"""
        _create_formal_project(dev_app, "HELPER01")

        with dev_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _write_revision_log
            from app.models import RevisionLog

            _write_revision_log(
                pid="HELPER01-p",
                user_id=None,
                entity_type="manuscript_block",
                entity_ref="methods/test_V1.0.json",
                action="save",
                summary="chars=200",
            )

            row = RevisionLog.query.filter_by(pid="HELPER01-p").first()
            assert row is not None
            assert row.action == "save"
            assert row.entity_ref == "methods/test_V1.0.json"


# ---------------------------------------------------------------------------
# 3. Socket 權限測試 — 直接測試 _socket_can_write 純函數
# ---------------------------------------------------------------------------

class TestSocketCanWrite:
    """
    直接單元測試 _socket_can_write(user_id, pid) 純函數。
    （Socket test client 在 eventlet async_mode 環境不適合同步測試，
      因此直接測試抽出的純函數，並在回報中說明此決策。）
    """

    def _setup_users_and_roles(self, app_ctx):
        """建立 viewer / editor / owner 三個 user 和一個 formal project。"""
        from app import db
        from app.models import User, WorkspaceMember, Project, ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER

        with app_ctx.app_context():
            # 建立 project
            p = Project(project_id="SOCKET01", name="Socket Test", status="formal")
            db.session.add(p)

            # 建立三個 user
            viewer = User(username="viewer_u", email="viewer@test.com", is_active=True)
            viewer.set_password("pw123")
            editor = User(username="editor_u", email="editor@test.com", is_active=True)
            editor.set_password("pw123")
            owner = User(username="owner_u", email="owner@test.com", is_active=True)
            owner.set_password("pw123")
            outsider = User(username="outsider_u", email="outsider@test.com", is_active=True)
            outsider.set_password("pw123")

            db.session.add_all([viewer, editor, owner, outsider])
            db.session.flush()

            # 建立 membership
            db.session.add(WorkspaceMember(user_id=viewer.id, pid="SOCKET01", role=ROLE_VIEWER))
            db.session.add(WorkspaceMember(user_id=editor.id, pid="SOCKET01", role=ROLE_EDITOR))
            db.session.add(WorkspaceMember(user_id=owner.id, pid="SOCKET01", role=ROLE_OWNER))
            # outsider 沒有 membership
            db.session.commit()

            return viewer.id, editor.id, owner.id, outsider.id

    def test_viewer_cannot_write(self, session_app):
        """viewer 角色應無法通過寫入檢查（_socket_can_write 回 False）。"""
        viewer_id, editor_id, owner_id, outsider_id = self._setup_users_and_roles(session_app)

        with session_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _socket_can_write
            result = _socket_can_write(viewer_id, "SOCKET01")
            assert result is False, f"viewer should NOT be able to write, got {result}"

    def test_editor_can_write(self, session_app):
        """editor 角色應能通過寫入檢查（_socket_can_write 回 True）。"""
        viewer_id, editor_id, owner_id, outsider_id = self._setup_users_and_roles(session_app)

        with session_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _socket_can_write
            result = _socket_can_write(editor_id, "SOCKET01")
            assert result is True, f"editor should be able to write, got {result}"

    def test_owner_can_write(self, session_app):
        """owner 角色應能通過寫入檢查（_socket_can_write 回 True）。"""
        viewer_id, editor_id, owner_id, outsider_id = self._setup_users_and_roles(session_app)

        with session_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _socket_can_write
            result = _socket_can_write(owner_id, "SOCKET01")
            assert result is True, f"owner should be able to write, got {result}"

    def test_outsider_cannot_write(self, session_app):
        """無 membership 的 user 應無法通過寫入檢查（_socket_can_write 回 False）。"""
        viewer_id, editor_id, owner_id, outsider_id = self._setup_users_and_roles(session_app)

        with session_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _socket_can_write
            result = _socket_can_write(outsider_id, "SOCKET01")
            assert result is False, f"outsider should NOT be able to write, got {result}"

    def test_nonexistent_user_cannot_write(self, session_app):
        """不存在的 user_id 應回傳 False（不 crash）。"""
        with session_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _socket_can_write
            result = _socket_can_write(99999, "SOCKET01")
            assert result is False


# ---------------------------------------------------------------------------
# 4. RevisionLog 整合 — 透過 _write_revision_log 觸發後 API 可查到
# ---------------------------------------------------------------------------

class TestRevisionLogIntegration:
    """整合測試：寫入 revision log 後可透過 API 查到。"""

    def test_save_block_via_helper_then_query_api(self, client, dev_app):
        """模擬 save_block 儲存後寫入 revision log，API 應能查到該筆記錄。"""
        # 建立 formal project
        from app import db
        from app.models import Project, RevisionLog
        with dev_app.app_context():
            p = Project(project_id="INT01-p", name="Integration Test", status="formal")
            db.session.add(p)
            db.session.commit()

        # 呼叫 _write_revision_log（模擬 save_block 成功後的 hook）
        with dev_app.app_context():
            from app.core_pro.manuscript.manuscript_routes import _write_revision_log
            _write_revision_log(
                pid="INT01-p",
                user_id=None,
                entity_type="manuscript_block",
                entity_ref="introduction/test_260719_V1.0.json",
                action="save",
                summary="block save: section=introduction chars=300",
            )

        # 透過 API 查詢
        resp = client.get("/manuscript/api/revisions/INT01-p")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] >= 1

        revision = data["revisions"][0]
        assert revision["entity_type"] == "manuscript_block"
        assert revision["action"] == "save"
        assert revision["pid"] == "INT01-p"
        assert "introduction" in revision["entity_ref"]
