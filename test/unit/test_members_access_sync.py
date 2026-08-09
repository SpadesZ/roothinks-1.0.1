# 檔案路徑: test/unit/test_members_access_sync.py
# 產生時間: 2026-07-27 15:00 +08:00
# 版本: v1.0
# 模組定位:
#   「人員組織 email 授予系統權限」的行為測試。
# 主要責任:
#   1. access_role 有填且 email 對得到帳號 → 建立/更新 WorkspaceMember。
#   2. 一般成員 access_role 空白 → 僅列名；PI／Co-PI 最低為 editor。
#   3. email 未註冊 → 略過並回報，不中斷存檔。
#   4. 絕不在此移除既有 membership（移除只走「成員管理」）。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 安全邊界:
#   - 這條路徑會發放專案權限，等同邀請功能；除產品明定的 PI／Co-PI
#     最低 editor 外，其餘角色只依明確填寫的 access_role 授權。
# 維護提醒:
#   - 人員組織與成員管理是兩套並存介面；此同步刻意「只增不減」，
#     避免有人編輯人員組織就無聲踢掉協作者。
# 驗證方式:
#   python -m pytest test/unit/test_members_access_sync.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "ACCSYNC-p"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'acc.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'acc_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=PID, name="Access Sync", status="formal"))
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _mkuser(app, name):
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username=name, email=f"{name}@test.local")
        u.set_password("password123")
        db.session.add(u)
        db.session.commit()
        return u.id


def _member(email, access_role, name="X", academic_role="合作人員 (Collaborator)"):
    return {
        "role": academic_role,
        "access_role": access_role,
        "name": {"en": {"surname": name, "given": name}},
        "emails": [email] if email else [],
    }


def _sync(app, members):
    from app.project_portfolio.project_service import ProjectService
    with app.app_context():
        return ProjectService._sync_members_access(PID, members)


class TestMembersAccessSync:
    def test_grants_role_by_email(self, app):
        uid = _mkuser(app, "acc1")
        granted, skipped = _sync(app, [_member("acc1@test.local", "coauthor")])
        from app.security import get_workspace_role
        with app.app_context():
            assert granted == 1 and skipped == []
            assert get_workspace_role(uid, PID) == "coauthor"

    def test_blank_access_role_grants_nothing(self, app):
        uid = _mkuser(app, "acc2")
        granted, skipped = _sync(app, [_member("acc2@test.local", "")])
        from app.security import get_workspace_role
        with app.app_context():
            assert granted == 0
            assert get_workspace_role(uid, PID) is None

    def test_unregistered_email_is_skipped_not_fatal(self, app):
        granted, skipped = _sync(app, [_member("ghost@test.local", "editor")])
        assert granted == 0
        assert skipped == ["ghost@test.local"]

    def test_updates_existing_role(self, app):
        uid = _mkuser(app, "acc3")
        _sync(app, [_member("acc3@test.local", "viewer")])
        _sync(app, [_member("acc3@test.local", "editor")])
        from app.security import get_workspace_role
        with app.app_context():
            assert get_workspace_role(uid, PID) == "editor"

    def test_never_removes_membership(self, app):
        """把 access_role 清空不得踢掉既有協作者——移除只走成員管理。"""
        uid = _mkuser(app, "acc4")
        _sync(app, [_member("acc4@test.local", "editor")])
        _sync(app, [_member("acc4@test.local", "")])
        from app.security import get_workspace_role
        with app.app_context():
            assert get_workspace_role(uid, PID) == "editor"

    def test_unknown_role_ignored(self, app):
        uid = _mkuser(app, "acc5")
        granted, _ = _sync(app, [_member("acc5@test.local", "superadmin")])
        from app.security import get_workspace_role
        with app.app_context():
            assert granted == 0
            assert get_workspace_role(uid, PID) is None

    def test_uses_first_email_only(self, app):
        uid_a = _mkuser(app, "acca")
        uid_b = _mkuser(app, "accb")
        m = _member("acca@test.local", "viewer")
        m["emails"].append("accb@test.local")
        _sync(app, [m])
        from app.security import get_workspace_role
        with app.app_context():
            assert get_workspace_role(uid_a, PID) == "viewer"
            assert get_workspace_role(uid_b, PID) is None

    def test_multiple_members(self, app):
        a = _mkuser(app, "m1")
        b = _mkuser(app, "m2")
        granted, _ = _sync(app, [
            _member("m1@test.local", "owner"),
            _member("m2@test.local", "coauthor"),
        ])
        from app.security import get_workspace_role
        with app.app_context():
            assert granted == 2
            assert get_workspace_role(a, PID) == "owner"
            assert get_workspace_role(b, PID) == "coauthor"

    def test_no_email_is_skipped(self, app):
        granted, skipped = _sync(app, [_member("", "editor")])
        assert granted == 0 and skipped == []

    @pytest.mark.parametrize("academic_role", [
        "主持人 (Principal Investigator)",
        "共同主持人 (Co-PI)",
    ])
    def test_pi_and_copi_get_full_section_editing(self, app, academic_role):
        uid = _mkuser(app, "lead_" + ("pi" if academic_role.startswith("主持人") else "copi"))
        email = "lead_pi@test.local" if academic_role.startswith("主持人") else "lead_copi@test.local"
        granted, skipped = _sync(app, [
            _member(email, "coauthor", academic_role=academic_role),
        ])
        from app.security import get_workspace_role
        with app.app_context():
            assert granted == 1 and skipped == []
            assert get_workspace_role(uid, PID) == "editor"

    def test_pi_minimum_does_not_downgrade_owner(self, app):
        uid = _mkuser(app, "leadowner")
        _sync(app, [_member("leadowner@test.local", "owner")])
        _sync(app, [_member(
            "leadowner@test.local",
            "",
            academic_role="主持人 (Principal Investigator)",
        )])
        from app.security import get_workspace_role
        with app.app_context():
            assert get_workspace_role(uid, PID) == "owner"

    def test_existing_projects_can_be_backfilled_idempotently(self, app):
        uid = _mkuser(app, "oldcopi")
        member = _member(
            "oldcopi@test.local",
            "",
            academic_role="共同主持人 (Co-PI)",
        )
        from app import db
        from app.models import Project
        from app.project_portfolio.project_service import ProjectService
        with app.app_context():
            project = Project.query.filter_by(project_id=PID).one()
            project.members = [member]
            db.session.commit()
            assert ProjectService.ensure_academic_lead_access() == (1, [])
            assert ProjectService.ensure_academic_lead_access() == (0, [])
            from app.security import get_workspace_role
            assert get_workspace_role(uid, PID) == "editor"
