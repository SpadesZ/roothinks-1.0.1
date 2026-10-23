# 目的：研究者自主與授權分離；署名不升權、撤權不復活、教授完全選用。
# 上下游：合成 session → 真實專案 API／服務 → 隔離 SQLite 與檔案。
# 驗證：python -m pytest test/unit/test_sai_autonomy.py -q
from test_sai_channel_acl import channel_app, client, PID


def test_independent_project_needs_no_professor_or_pi(channel_app):
    from app import db
    from app.models import MentorLink, WorkspaceMember
    http = client(channel_app, 'owner')
    response = http.post('/api/project/create', json={'name': 'Independent synthetic research', 'members': []})
    assert response.status_code == 201, response.json
    with channel_app[0].app_context():
        assert not MentorLink.query.count()
        member = WorkspaceMember.query.filter_by(pid=response.json['pid'], user_id=channel_app[1]['owner']).one()
        assert member.role == 'owner'


def test_editor_cannot_escalate_via_academic_metadata(channel_app):
    from app import db
    from app.models import WorkspaceMember, Project
    response = client(channel_app, 'editor').post(f'/api/project/update/{PID}', json={'members': [
        {'name': 'Synthetic PI', 'role': '主持人', 'emails': ['editor@example.invalid'], 'access_role': 'owner'}]})
    assert response.status_code == 200, response.json
    with channel_app[0].app_context():
        member = WorkspaceMember.query.filter_by(pid=PID, user_id=channel_app[1]['editor']).one()
        observed = member.role
        member.role = 'editor'
        db.session.get(Project, Project.query.filter_by(project_id=PID).one().id).members = []
        db.session.commit()
        assert observed == 'editor', '署名 access_role 不得提升專案權限'


def test_removed_professor_is_not_regranted_from_authorship(channel_app):
    from app import db
    from app.models import WorkspaceMember, Project
    from app.project_portfolio.project_service import ProjectService
    with channel_app[0].app_context():
        project = Project.query.filter_by(project_id=PID).one()
        project.members = [{'name': 'Removed professor', 'role': '共同主持人', 'emails': ['outsider@example.invalid']}]
        db.session.commit()
        try:
            ProjectService.ensure_academic_lead_access()
            assert WorkspaceMember.query.filter_by(pid=PID, user_id=channel_app[1]['outsider']).first() is None
        finally:
            WorkspaceMember.query.filter_by(pid=PID, user_id=channel_app[1]['outsider']).delete()
            project.members = []
            db.session.commit()


def test_project_professor_cannot_enumerate_other_projects_or_personal_sessions(channel_app):
    from app import db
    from app.models import WorkspaceMember, MentorLink, User, UserSession
    from test_sai_channel_acl import OTHER
    with channel_app[0].app_context():
        owner = db.session.get(User,channel_app[1]['owner'])
        owner.system_role = 'mentor'
        relation = MentorLink(mentor_id=owner.id,mentee_id=channel_app[1]['editor'])
        membership = WorkspaceMember(user_id=channel_app[1]['editor'],pid=OTHER,role='editor')
        activity = UserSession(user_id=channel_app[1]['editor'])
        db.session.add_all([relation,membership,activity])
        db.session.commit()
        try:
            response = client(channel_app,'owner').get(f'/api/mentor/mentees/{channel_app[1]["editor"]}')
            assert response.status_code == 200,response.json
            assert OTHER not in response.get_data(as_text=True)
            assert response.json['sessions'] == []
            assert response.json['stats'].get('available') is False
        finally:
            owner.system_role = 'user'
            db.session.delete(relation)
            db.session.delete(membership)
            db.session.delete(activity)
            db.session.commit()
