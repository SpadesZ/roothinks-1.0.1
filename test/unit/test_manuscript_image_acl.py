# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_manuscript_image_acl.py
# 子系統定位:
#   `cmd_save_image` socket handler 的寫入授權（NOTE-033）與伺服器端編號（NOTE-034）。
# 主要責任:
#   1. viewer **不得**上傳圖片：舊 handler 只有 `_ensure_socket_project_access`
#      （專案成員即可），唯讀角色可以寫入專案目錄並佔用容量。
#   2. owner/editor 可以；coauthor 在自己被指派的章節可以。
#   3. handler 忽略前端送來的 fig_id，一律用伺服器推導的編號。
#   4. 型別被拒時要回得出**原因**（不是一句 "Image saving error."）。
# 明確不負責:
#   - magic bytes 的判斷細節在 test_manuscript_image_upload.py。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的 sqlite 與 data root；不碰真實 data/。
# ACL/安全邊界:
#   test_viewer_cannot_upload 是唯讀角色的寫入邊界。**放寬它等於讓 viewer
#   可以往別人的專案寫二進位檔。**
# 不變量:
#   - 被拒的上傳不得在 image 目錄留下任何檔案。
# 相關 NOTE:
#   NOTE-033、NOTE-034。
# 驗證:
#   python -m pytest test/unit/test_manuscript_image_acl.py -q
# ---------------------------------------------------------------------------
import base64
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "IMGACL-p"


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))
    monkeypatch.chdir(tmp_path)

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        "AUTH_MODE": "session",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'imgacl.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'imgacl_manu.db'}"},
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


def _mk_user(app, name, role):
    from app import db
    from app.models import Project, User, WorkspaceMember

    with app.app_context():
        if Project.query.filter_by(project_id=PID).first() is None:
            db.session.add(Project(project_id=PID, name="Img ACL", status="formal"))
        u = User(username=name, email=f"{name}@test.local", system_role="user")
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        if role:
            db.session.add(WorkspaceMember(user_id=u.id, pid=PID, role=role))
        db.session.commit()
        return u.id


def _emit_save_image(app, uid, section="introduction", fig_id=None):
    """直接呼叫 handler，並攔截它 emit 出去的事件。

    走 socketio 測試客戶端要一整套連線握手；這裡要驗的是 handler 內的判定，
    所以把 `emit` 換成收集器，並把 `_session_uid` 綁成受測使用者。
    回傳 (emitted_events, image_dir)。
    """
    from app.core_pro.manuscript import manuscript_routes as mr

    captured = []

    def fake_emit(event, payload=None, **kwargs):
        captured.append((event, payload))

    original_emit = mr.emit
    original_uid = mr._session_uid
    mr.emit = fake_emit
    mr._session_uid = lambda: uid

    # 專案層守衛在測試裡沒有 socket session，直接讓它通過 —— 這一條測試要驗的
    # 是**章節層寫入判定**，不是專案成員判定（那條另有測試）。
    original_access = mr._ensure_socket_project_access
    mr._ensure_socket_project_access = lambda *a, **k: True

    payload = {"pid": PID, "section": section, "image_data": _png(),
               "filename": "u.png", "caption": "cap", "source": "gallery_upload"}
    if fig_id is not None:
        payload["fig_id"] = fig_id

    try:
        with app.app_context():
            mr.handle_save_image(payload)
    finally:
        mr.emit = original_emit
        mr._session_uid = original_uid
        mr._ensure_socket_project_access = original_access

    return captured


def _saved_meta(events):
    for name, payload in events:
        if name == "image_saved" and isinstance(payload, dict) and payload.get("ok"):
            return payload.get("meta")
    return None


def _rejection(events):
    for name, payload in events:
        if name == "image_saved" and isinstance(payload, dict) and not payload.get("ok"):
            return payload
    return None


class TestWritePermission:
    def test_viewer_cannot_upload(self, app, tmp_path):
        uid = _mk_user(app, "imgviewer", "viewer")
        events = _emit_save_image(app, uid)

        assert _saved_meta(events) is None, "viewer 成功上傳了圖片"
        assert _rejection(events) == {"ok": False, "error": "forbidden"}
        img_dir = tmp_path / PID / "manuscript" / "image"
        assert not img_dir.exists() or not list(img_dir.glob("*.png")), (
            "viewer 的上傳在磁碟上留下了檔案")

    def test_owner_can_upload(self, app):
        uid = _mk_user(app, "imgowner", "owner")
        meta = _saved_meta(_emit_save_image(app, uid))
        assert meta is not None, "owner 應該可以上傳"
        assert meta["filename"].endswith(".png")

    def test_editor_can_upload(self, app):
        uid = _mk_user(app, "imgeditor", "editor")
        assert _saved_meta(_emit_save_image(app, uid)) is not None


class TestServerAssignsNumbers:
    def test_client_supplied_fig_id_is_ignored(self, app):
        """前端就算硬送 fig_id，伺服器也不採用（NOTE-034）。"""
        uid = _mk_user(app, "imgowner2", "owner")
        meta = _saved_meta(_emit_save_image(app, uid, fig_id="Figure 999"))
        assert meta is not None
        assert meta["fig_id"] == "Figure 1", (
            f"伺服器採用了前端送的編號：{meta['fig_id']}")

    def test_numbers_increment_across_uploads(self, app):
        uid = _mk_user(app, "imgowner3", "owner")
        labels = [_saved_meta(_emit_save_image(app, uid))["fig_id"] for _ in range(3)]
        assert labels == ["Figure 1", "Figure 2", "Figure 3"], labels


class TestRejectionIsExplained:
    def test_unsupported_type_reports_a_reason(self, app):
        """不能只回 "Image saving error." —— 使用者要知道換什麼格式重試。"""
        from app.core_pro.manuscript import manuscript_routes as mr

        uid = _mk_user(app, "imgowner4", "owner")
        captured = []
        original_emit, original_uid = mr.emit, mr._session_uid
        original_access = mr._ensure_socket_project_access
        mr.emit = lambda e, p=None, **k: captured.append((e, p))
        mr._session_uid = lambda: uid
        mr._ensure_socket_project_access = lambda *a, **k: True
        svg = base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
        ).decode("ascii")
        try:
            with app.app_context():
                mr.handle_save_image({
                    "pid": PID, "section": "introduction",
                    "image_data": "data:image/png;base64," + svg,
                    "filename": "evil.png", "caption": "c", "source": "gallery_upload",
                })
        finally:
            mr.emit, mr._session_uid = original_emit, original_uid
            mr._ensure_socket_project_access = original_access

        rejection = _rejection(captured)
        assert rejection is not None, "SVG 被接受了"
        assert "Unsupported image format" in str(rejection.get("error")), rejection
