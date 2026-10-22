# Roothinks source maintenance contract
# 目的：以完整 HTTP/session 重現全站管理越權；資料只在 tmp，外部 provider 不執行。
# 驗證：python -m pytest test/unit/test_llm_admin_acl.py -q
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ADMIN_ROUTES = [
    ("GET", "/api/llm/connection/list"),
    ("POST", "/api/llm/connection/create"),
    ("DELETE", "/api/llm/connection/delete/999"),
    ("POST", "/api/llm/connection/update"),
    ("POST", "/api/llm/connection/fetch_models"),
    ("POST", "/api/llm/connection/test"),
    ("GET", "/api/llm/binding/list"),
    ("POST", "/api/llm/binding/update"),
    ("POST", "/api/llm/binding/test"),
    ("POST", "/api/llm/binding/lock"),
    ("POST", "/api/llm/binding/unlock"),
    ("GET", "/api/llm/runtime/cpu"),
    ("POST", "/api/llm/runtime/cpu"),
    ("POST", "/api/llm/runtime/flowa_workers"),
]


@pytest.fixture(scope="module")
def admin_app(tmp_path_factory):
    from app import create_app, db
    from app.models import User, Project, WorkspaceMember
    from app.llm_service.llm_model import LLMModel
    temp = tmp_path_factory.mktemp("admin_acl")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FLASK_ENV", "development")
        patch.setattr(LLMModel, "get_db_path", staticmethod(lambda: str(temp / "llm.db")))
        app = create_app({
            "TESTING": True, "AUTH_MODE": "session", "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False, "API_AUTH_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{temp / 'main.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{temp / 'manu.db'}"},
        })
        identities = {}
        with app.app_context():
            db.create_all()
            db.session.add(Project(project_id="ADMIN-p", name="Synthetic", status="formal"))
            db.session.add(Project(project_id="PRIVATE-p", name="Other project", status="formal"))
            for role in ("owner", "editor", "viewer", "coauthor", "mentor", "admin"):
                user = User(username=role, email=f"{role}@example.invalid",
                            system_role=role if role in {"mentor", "admin"} else "user")
                user.set_password("synthetic-password-only")
                db.session.add(user)
                db.session.flush()
                identities[role] = user.id
                db.session.add(WorkspaceMember(user_id=user.id, pid="ADMIN-p",
                                               role=role if role not in {"mentor", "admin"} else "owner"))
            db.session.commit()
        yield app, identities
        with app.app_context():
            db.session.remove()
            for engine in db.engines.values():
                engine.dispose()


def _client(bundle, role=None):
    app, identities = bundle
    client = app.test_client()
    if role:
        with client.session_transaction() as session:
            session["_user_id"] = str(identities[role])
            session["_fresh"] = True
    return client


@pytest.mark.parametrize("role", ["owner", "editor", "viewer", "coauthor", "mentor"])
@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_project_role_cannot_administer_global_models(admin_app, monkeypatch, role, method, path):
    from app.llm_service.llm_model import LLMModel
    calls = []
    monkeypatch.setattr(LLMModel, "execute_query", staticmethod(lambda *a, **k: calls.append(a) or []))
    response = _client(admin_app, role).open(path, method=method, json={"pid": "ADMIN-p", "system_role": "admin"})
    assert response.status_code == 403
    assert calls == [], "拒絕前不可查詢、改動管理資料或呼叫 provider"


@pytest.mark.parametrize("path", ["/setup", "/lava_setup.html"])
def test_setup_page_uses_system_role(admin_app, path):
    assert _client(admin_app, "owner").get(path).status_code == 403
    assert _client(admin_app, "admin").get(path).status_code == 200


def test_admin_positive_control_and_anonymous_denial(admin_app):
    assert _client(admin_app).post("/api/llm/connection/create").status_code == 401
    response = _client(admin_app, "admin").post("/api/llm/connection/create")
    assert response.status_code == 200
    assert response.json["success"] is True
    listing = _client(admin_app, "admin").get("/api/llm/connection/list")
    assert listing.status_code == 200


def test_researcher_keeps_project_access(admin_app):
    response = _client(admin_app, "editor").get("/api/study/notes/load/ADMIN-p")
    assert response.status_code == 200


@pytest.mark.parametrize("acl,status", [({"projects": ["*"]}, 403), ({"system_role": "admin"}, 200), ("admin", 403)])
def test_bearer_requires_explicit_system_permission(admin_app, acl, status):
    app, _ = admin_app
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(app.config, "AUTH_MODE", "none")
        patch.setitem(app.config, "API_AUTH_ENABLED", True)
        patch.setitem(app.config, "API_BEARER_TOKENS", ["synthetic-token"])
        patch.setitem(app.config, "TOKEN_ACL_MAP", {"synthetic-token": acl})
        client = app.test_client()
        assert client.get("/api/llm/connection/list", headers={"Authorization": "Bearer synthetic-token"}).status_code == status
        assert client.get("/setup", headers={"Authorization": "Bearer synthetic-token"}).status_code == status
        assert client.get("/api/llm/connection/list").status_code == 401


@pytest.mark.parametrize("body,path,status", [
    ({"question": "q", "context": {"project_pid": "PRIVATE-p"}}, "/api/study/chat", 400),
    ({"pid": "PRIVATE-p", "question": "q"}, "/api/study/chat?pid=ADMIN-p", 400),
    ({"pid": "PRIVATE-p", "question": "q"}, "/api/study/chat", 403),
])
def test_study_pid_smuggling_never_reaches_model(admin_app, monkeypatch, body, path, status):
    from app.core_pro.study import study_routes
    calls = []
    monkeypatch.setattr(study_routes.tutor_engine, "process_query", lambda *a: calls.append(a) or "private")
    response = _client(admin_app, "editor").post(path, json=body)
    assert response.status_code == status
    assert calls == []


@pytest.mark.parametrize('role,status', [('owner',200),('editor',200),('viewer',403),('coauthor',403)])
def test_literature_decision_uses_authenticated_actor(admin_app, monkeypatch, tmp_path, role, status):
    from app.core_pro.literature import literature_routes
    from app.services.literature_library import LiteratureLibrary
    library = LiteratureLibrary(str(tmp_path))
    library.merge_candidates('ADMIN-p', [{'title':'Human controlled'}])
    eid = next(iter(library.load('ADMIN-p')['entries']))
    monkeypatch.setattr(literature_routes, 'get_literature_library', lambda:library)
    response = _client(admin_app, role).post('/api/literature/library/update', json={
        'pid':'ADMIN-p','entry_id':eid,'base_rev':0,'actor':'forged professor',
        'patch':{'screening_status':'included','screening_note':'calibrated condition',
                 'screening_decided_by':'forged professor','last_edited_by':'forged'}})
    assert response.status_code == status
    entry = library.load('ADMIN-p')['entries'][eid]
    if status == 200:
        assert entry['screening_decided_by'] == role+'@example.invalid'
        assert entry['last_edited_by'] == role+'@example.invalid'
        assert entry['history'][0]['actor'] == role+'@example.invalid'
        assert _client(admin_app, role).post('/api/literature/library/update', json={
            'pid':'ADMIN-p','entry_id':eid,'base_rev':0,'patch':{'reading_note':'old tab'}}).status_code == 409
    else:
        assert entry['screening_status'] == 'candidate'
    assert _client(admin_app, role).post('/api/literature/library/update', json={
        'pid':'PRIVATE-p','entry_id':eid,'base_rev':0,'patch':{'screening_status':'included'}}).status_code == 403
