# 檔案路徑: test/unit/test_members_access_sync.py
# 產生時間: 2026-07-27 15:00 +08:00
# 版本: v1.0
# 模組定位:
#   「人員組織 email 授予系統權限」的行為測試。
# 邊界：填 PI／Co-PI、email 或 access_role 不再發權；既有 membership 不變。
# 驗證：python -m pytest test/unit/test_members_access_sync.py -q
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
    from app.llm_service.llm_model import LLMModel
    monkeypatch.setattr(LLMModel, "get_db_path", staticmethod(lambda: str(tmp_path / "models.db")))

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


# 本輪產品契約取代舊的「署名自動授權」要求；保留角色／email／現有權限的負向控制。
@pytest.mark.parametrize('academic_role', ['主持人 (Principal Investigator)', '共同主持人 (Co-PI)', '合作人員'])
@pytest.mark.parametrize('access_role', ['', 'viewer', 'coauthor', 'editor', 'owner', 'superadmin'])
def test_academic_metadata_never_grants_access(app, academic_role, access_role):
    uid = _mkuser(app, 'academic')
    assert _sync(app, [_member('academic@test.local', access_role, academic_role=academic_role)]) == (0, [])
    from app.security import get_workspace_role
    with app.app_context():
        assert get_workspace_role(uid, PID) is None


@pytest.mark.parametrize('existing_role', ['owner', 'editor', 'viewer', 'coauthor'])
def test_academic_metadata_preserves_existing_permissions(app, existing_role):
    uid = _mkuser(app, 'existing')
    from app import db
    from app.models import WorkspaceMember
    from app.security import get_workspace_role
    with app.app_context():
        db.session.add(WorkspaceMember(user_id=uid, pid=PID, role=existing_role))
        db.session.commit()
    _sync(app, [_member('existing@test.local', 'owner')])
    _sync(app, [_member('existing@test.local', '')])
    with app.app_context():
        assert get_workspace_role(uid, PID) == existing_role


def test_unknown_and_multiple_emails_cannot_create_members(app):
    a, b = _mkuser(app, 'first'), _mkuser(app, 'second')
    member = _member('first@test.local', 'owner')
    member['emails'].extend(['second@test.local', 'unknown@test.local'])
    assert _sync(app, [member, _member('', 'editor')]) == (0, [])
    from app.models import WorkspaceMember
    with app.app_context():
        assert WorkspaceMember.query.filter(WorkspaceMember.user_id.in_([a,b])).count() == 0


def test_startup_compatibility_cannot_regrant_removed_academic(app):
    uid = _mkuser(app, 'removed')
    from app import db
    from app.models import Project, WorkspaceMember
    from app.project_portfolio.project_service import ProjectService
    with app.app_context():
        Project.query.filter_by(project_id=PID).one().members = [_member('removed@test.local', 'owner', academic_role='共同主持人 (Co-PI)')]
        db.session.commit()
        for _ in range(2):
            assert ProjectService.ensure_academic_lead_access() == (0, [])
            assert WorkspaceMember.query.filter_by(user_id=uid,pid=PID).first() is None
