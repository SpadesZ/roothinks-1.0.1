# 目的：教授／團隊邀請必須雙方確認且可撤銷；學術身分不能授權或讀其他研究。
# 上下游：真實 session 與邀請 API → WorkspaceMember／MentorLink／不可刪除研究歷史。
# 驗證：python -m pytest test/unit/test_sai_invitations.py -q
import pytest
from test_sai_channel_acl import channel_app, client, PID, MINE, HIDDEN


def invite(bundle, **changes):
    data = dict(email='outsider@example.invalid', kind='professor', role='viewer', sections=[])
    data.update(changes)
    return client(bundle,'owner').post(f'/api/collaboration/project/{PID}/invitations',json=data)


def test_professor_profile_is_not_project_or_admin_permission(channel_app):
    http = client(channel_app,'outsider')
    response = http.post('/api/collaboration/profile',json={'academic_role':'professor','affiliation':'Synthetic university','system_role':'admin'})
    assert response.status_code == 200,response.json
    assert response.json['profile']['academic_role'] == 'professor'
    from app import db
    from app.models import User, WorkspaceMember
    with channel_app[0].app_context():
        assert db.session.get(User,channel_app[1]['outsider']).system_role == 'user'
        assert not WorkspaceMember.query.filter_by(user_id=channel_app[1]['outsider']).count()
    assert http.get(f'/manuscript/api/revisions/{PID}').status_code == 403


@pytest.mark.parametrize('actor',['editor','viewer','coauthor','outsider'])
def test_only_project_owner_can_invite(channel_app,actor):
    response = client(channel_app,actor).post(f'/api/collaboration/project/{PID}/invitations',json={
        'email':'outsider@example.invalid','kind':'professor','role':'viewer'})
    assert response.status_code == 403


def test_invitation_token_identity_single_use_and_revocation_preserve_history(channel_app):
    from app import db
    from app.models import WorkspaceMember, ChapterComment, User
    created = invite(channel_app)
    assert created.status_code == 201,created.json
    iid, token = created.json['invitation']['id'],created.json['token']
    endpoint = f'/api/collaboration/invitations/{iid}/accept'
    assert client(channel_app,'viewer').post(endpoint,json={'token':token}).status_code == 403
    assert client(channel_app,'outsider').post(endpoint,json={'token':'wrong'}).status_code == 403
    assert client(channel_app,'outsider').get(f'/manuscript/api/revisions/{PID}').status_code == 403
    accepted = client(channel_app,'outsider').post(endpoint,json={'token':token,'role':'owner','system_role':'admin'})
    assert accepted.status_code == 200,accepted.json
    assert client(channel_app,'outsider').post(endpoint,json={'token':token}).status_code == 409
    with channel_app[0].app_context():
        assert WorkspaceMember.query.filter_by(pid=PID,user_id=channel_app[1]['outsider']).one().role == 'viewer'
        assert db.session.get(User,channel_app[1]['outsider']).system_role == 'user'
    reviewer = client(channel_app,'outsider')
    endpoint_review = f'/api/mentor/mentees/{channel_app[1]["owner"]}/reviews/{PID}'
    review = reviewer.post(endpoint_review+'/items',json={'kind':'comment','body':'Keep original evidence.','paper_version':'V1'})
    assert review.status_code == 201,review.json
    with channel_app[0].app_context():
        before = [(r.id,r.body) for r in ChapterComment.query.filter_by(pid=PID).all()]
    revoked = client(channel_app,'owner').post(f'/api/collaboration/invitations/{iid}/revoke',json={'reason':'研究者改為獨立研究'})
    assert revoked.status_code == 200,revoked.json
    assert reviewer.get(endpoint_review).status_code == 403
    assert reviewer.get(f'/manuscript/api/revisions/{PID}').status_code == 403
    archived = client(channel_app,'owner').get(f'/api/mentor/my-reviews/{PID}')
    assert archived.status_code == 200,archived.json
    assert any(item['id']==review.json['item']['id'] for item in archived.json['items']), '移除教授不可隱藏作者已收到的審閱歷史'
    with channel_app[0].app_context():
        assert [(r.id,r.body) for r in ChapterComment.query.filter_by(pid=PID).all()] == before
    history = client(channel_app,'owner').get(f'/api/collaboration/project/{PID}/invitations').json['invitations']
    row = next(r for r in history if r['id']==iid)
    assert [r['action'] for r in row['history']] == ['created','accepted','revoked']
    assert token not in str(history)


def test_team_invitation_only_grants_selected_chapter_and_can_be_removed(channel_app):
    from app import db
    from app.models import WorkspaceMember, ChapterAssignment
    created = invite(channel_app,kind='team',role='coauthor',sections=[MINE])
    assert created.status_code == 201,created.json
    iid = created.json['invitation']['id']
    user = client(channel_app,'outsider')
    assert user.post(f'/api/collaboration/invitations/{iid}/accept',json={'token':created.json['token']}).status_code == 200
    assert user.get(f'/manuscript/api/paragraph/context?pid={PID}&section={MINE}').status_code == 200
    assert user.get(f'/manuscript/api/paragraph/context?pid={PID}&section={HIDDEN}').status_code == 403
    assert client(channel_app,'owner').delete(f'/api/project/{PID}/members/outsider').status_code == 200
    assert user.get(f'/manuscript/api/revisions/{PID}').status_code == 403
    with channel_app[0].app_context():
        assert not WorkspaceMember.query.filter_by(user_id=channel_app[1]['outsider'],pid=PID).count()
        assert not ChapterAssignment.query.filter_by(user_id=channel_app[1]['outsider'],pid=PID).count()


@pytest.mark.parametrize('changes',[{'kind':'admin'},{'role':'owner'},{'kind':'professor','role':'editor'},
    {'kind':'team','role':'coauthor','sections':[]},{'email':'not-email'}])
def test_invalid_or_privileged_invitation_is_rejected(channel_app,changes):
    response = invite(channel_app,**changes)
    assert response.status_code == 400,response.json


def test_invitation_endpoints_require_csrf_when_enabled(channel_app):
    import re
    app = channel_app[0]
    previous = app.config['WTF_CSRF_ENABLED']
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        http = client(channel_app,'outsider')
        assert http.post('/api/collaboration/profile',json={'academic_role':'researcher'}).status_code == 403
        page = http.get('/collaboration')
        assert page.status_code == 200
        token = re.search(r'id="collaboration-csrf" value="([^"]+)"',page.get_data(as_text=True)).group(1)
        response = http.post('/api/collaboration/profile',json={'academic_role':'researcher'},headers={'X-CSRFToken':token})
        assert response.status_code == 200,response.json
    finally:
        app.config['WTF_CSRF_ENABLED'] = previous


@pytest.mark.parametrize('payload',[['unexpected'],'unexpected',42])
def test_invitation_and_profile_reject_non_object_json(channel_app,payload):
    assert client(channel_app,'outsider').post('/api/collaboration/profile',json=payload).status_code == 400
    assert client(channel_app,'owner').post(f'/api/collaboration/project/{PID}/invitations',json=payload).status_code == 400


def test_expired_and_revoked_invitation_never_grant_access(channel_app):
    from app import db
    from app.models import ProjectInvitation,WorkspaceMember
    from datetime import datetime,timedelta,timezone
    created = invite(channel_app)
    assert created.status_code == 201,created.json
    iid,token = created.json['invitation']['id'],created.json['token']
    with channel_app[0].app_context():
        db.session.get(ProjectInvitation,iid).expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        db.session.commit()
    endpoint = f'/api/collaboration/invitations/{iid}/accept'
    assert client(channel_app,'outsider').post(endpoint,json={'token':token}).status_code == 409
    assert client(channel_app,'owner').post(f'/api/collaboration/invitations/{iid}/revoke',json={'reason':'過期重發'}).status_code == 200
    assert client(channel_app,'outsider').post(endpoint,json={'token':token}).status_code == 409
    with channel_app[0].app_context():
        assert not WorkspaceMember.query.filter_by(pid=PID,user_id=channel_app[1]['outsider']).count()


def test_revocation_cannot_remove_last_owner_after_member_role_changes(channel_app):
    from app import db
    from app.models import WorkspaceMember
    owner = client(channel_app,'owner')
    project = owner.post('/api/project/create',json={'name':'Synthetic sole-owner boundary','members':[]})
    assert project.status_code == 201,project.json
    pid = project.json['pid']
    created = owner.post(f'/api/collaboration/project/{pid}/invitations',json={
        'email':'outsider@example.invalid','kind':'team','role':'editor','sections':[]})
    assert created.status_code == 201,created.json
    iid = created.json['invitation']['id']
    target = client(channel_app,'outsider')
    assert target.post(f'/api/collaboration/invitations/{iid}/accept',json={'token':created.json['token']}).status_code == 200
    assert owner.patch(f'/api/project/{pid}/members/outsider',json={'role':'owner'}).status_code == 200
    assert owner.patch(f'/api/project/{pid}/members/owner',json={'role':'viewer'}).status_code == 200
    response = target.post(f'/api/collaboration/invitations/{iid}/revoke',json={'reason':'不能使專案失去最後擁有者'})
    assert response.status_code == 409,response.json
    with channel_app[0].app_context():
        assert WorkspaceMember.query.filter_by(pid=pid,user_id=channel_app[1]['outsider']).one().role == 'owner'
