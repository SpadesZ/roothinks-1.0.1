# Roothinks source maintenance contract
# 目的：以真實 session／Socket 驗 COL-01 通道與章節邊界，拒絕須早於 I/O 或模型呼叫。
# 邊界：獨立合成 DB／版本／決策；provider 與二進位交付點可觀測，無外部請求。
# 驗證：python -m pytest test/unit/test_sai_channel_acl.py -q
from pathlib import Path
import json

import pytest

PID = 'SAIVFY-p'
OTHER = 'SAIOTHER-p'
MINE = 'introduction'
HIDDEN = 'results'
PUBLIC = 'SENTINEL_ALLOWED_AUTHOR_PARAGRAPH'
PRIVATE = 'SENTINEL_PRIVATE_RESULTS_NEVER_LEAK'


@pytest.fixture(scope='module')
def channel_app(tmp_path_factory):
    from app import create_app, db
    from app.models import User, Project, WorkspaceMember, ChapterAssignment, RevisionLog
    from app.core_pro.literature import literature_routes
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    from app.core_pro.manuscript.citation_ledger import record_decision
    from app.llm_service.llm_model import LLMModel
    root = tmp_path_factory.mktemp('sai_channels')
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('FLASK_ENV', 'development')
        patch.setenv('LOCK_ROOT', str(root/'locks'))
        patch.setattr(LLMModel, 'get_db_path', staticmethod(lambda:str(root/'llm.db')))
        app = create_app({'TESTING':True, 'AUTH_MODE':'session', 'API_AUTH_ENABLED':False,
                          'WTF_CSRF_ENABLED':False, 'RATELIMIT_ENABLED':False,
                          'SQLALCHEMY_DATABASE_URI':f'sqlite:///{root / "app.db"}',
                          'SQLALCHEMY_BINDS':{'manuscript':f'sqlite:///{root / "manu.db"}'}})
        patch.setattr(literature_routes, 'DATA_ROOT', str(root))
        patch.setattr(literature_routes, 'literature_library_service', None)
        identities = {}
        with app.app_context():
            db.create_all()
            db.session.add_all([Project(project_id=p,name=p,status='formal') for p in (PID,OTHER)])
            for role in ('owner','editor','viewer','coauthor','outsider'):
                user = User(username=role,email=f'{role}@example.invalid',system_role='user')
                user.set_password('synthetic-only')
                db.session.add(user)
                db.session.flush()
                identities[role] = user.id
                if role != 'outsider':
                    db.session.add(WorkspaceMember(user_id=user.id,pid=PID,role=role))
            db.session.add(ChapterAssignment(pid=PID,section_key=MINE,user_id=identities['coauthor']))
            db.session.add_all([
                RevisionLog(pid=PID,user_id=identities['owner'],entity_type='manuscript_block',entity_ref=MINE,action='save',summary=PUBLIC),
                RevisionLog(pid=PID,user_id=identities['owner'],entity_type='manuscript_block',entity_ref=HIDDEN,action='save',summary=PRIVATE),
                RevisionLog(pid=PID,user_id=identities['owner'],entity_type='manuscript_paper',entity_ref='V1',action='save',summary=PRIVATE),
            ])
            db.session.commit()
            for section,text in ((MINE,PUBLIC),(HIDDEN,PRIVATE)):
                ManuscriptIO.save_block_version(PID,section,'Synthetic',f'<p>{text}</p>')
                record_decision(str(root),PID,section_id=section,status='accepted',paper_id='paper',claim_text=text,decided_by='owner')
            ManuscriptIO.save_paper_version(PID,'Synthetic',f'<p>{PUBLIC} {PRIVATE}</p>',sections={MINE:'0.1',HIDDEN:'0.1'})
        yield app, identities, root
        with app.app_context():
            db.session.remove()
            for engine in db.engines.values():
                engine.dispose()


def client(bundle, role=None):
    app, ids, _ = bundle
    http = app.test_client()
    if role:
        with http.session_transaction() as session:
            session['_user_id'] = str(ids[role])
            session['_fresh'] = True
    return http


def socket(bundle, role='coauthor'):
    from app import socketio
    app, _, _ = bundle
    return socketio.test_client(app, namespace='/manu_ws', flask_test_client=client(bundle,role), auth={'pid':PID})


def test_citation_list_filters_private_chapters(channel_app):
    response = client(channel_app,'coauthor').get(f'/manuscript/api/citation/decisions?pid={PID}')
    assert response.status_code == 200
    assert PUBLIC in response.get_data(as_text=True)
    assert PRIVATE not in response.get_data(as_text=True)
    denied = client(channel_app,'coauthor').get(f'/manuscript/api/citation/decisions?pid={PID}&section={HIDDEN}')
    assert denied.status_code == 403


def test_revision_list_filters_private_chapters_and_paper(channel_app):
    response = client(channel_app,'coauthor').get(f'/manuscript/api/revisions/{PID}')
    assert response.status_code == 200
    assert PUBLIC in response.get_data(as_text=True)
    assert PRIVATE not in response.get_data(as_text=True)
    assert client(channel_app,'editor').get(f'/manuscript/api/revisions/{PID}').get_json()['count'] == 3


@pytest.mark.parametrize('role,section', [('viewer',MINE),('coauthor',HIDDEN)])
def test_citation_write_needs_chapter_write_permission(channel_app,role,section):
    response = client(channel_app,role).post('/manuscript/api/citation/decide',json={
        'pid':PID,'section':section,'status':'accepted','paper_id':'paper','claim_text':'forged'})
    assert response.status_code == 403


def test_citation_actor_is_server_identity(channel_app):
    response = client(channel_app,'editor').post('/manuscript/api/citation/decide',json={
        'pid':PID,'section':MINE,'status':'accepted','paper_id':'paper','claim_text':'human', 'decided_by':'forged-professor'})
    assert response.status_code == 200
    assert response.json['decision']['decided_by'] == 'editor@example.invalid'


def test_query_body_project_smuggling_is_rejected(channel_app):
    response = client(channel_app,'editor').post(f'/manuscript/api/citation/decide?pid={PID}',json={
        'pid':OTHER,'section':MINE,'status':'accepted','paper_id':'paper','claim_text':PRIVATE})
    assert response.status_code == 400
    _,_,root = channel_app
    assert not (root/OTHER/'manuscript/citation_ledger.json').exists()


def test_suggestion_denial_precedes_retrieval(channel_app,monkeypatch):
    from app.core_pro.manuscript import citation_suggest
    calls = []
    monkeypatch.setattr(citation_suggest,'suggest_citations_for_paragraph',lambda *a,**k:calls.append(a) or [])
    http = client(channel_app,'coauthor')
    assert http.post('/manuscript/api/citation/suggest',json={'pid':PID,'section':HIDDEN,'text':'x'}).status_code == 403
    assert calls == []
    assert http.post('/manuscript/api/citation/suggest',json={'pid':PID,'section':MINE,'text':'x'}).status_code == 200
    assert calls


@pytest.mark.parametrize('role,status',[(None,302),('outsider',403),('coauthor',403),('viewer',200),('owner',200)])
def test_image_download_respects_project_and_unscoped_assets(channel_app,monkeypatch,role,status):
    from app.core_pro.manuscript import manuscript_routes
    calls = []
    def deliver(*args,**kwargs):
        calls.append(args)
        return PRIVATE
    monkeypatch.setattr(manuscript_routes,'send_from_directory',deliver)
    response = client(channel_app,role).get(f'/manuscript/image/{PID}/private.png')
    assert response.status_code == status
    assert bool(calls) == (status == 200)


@pytest.mark.parametrize('role,status',[(None,401),('outsider',403),('coauthor',403),('viewer',200),('owner',200)])
def test_docx_export_denies_before_conversion(channel_app,monkeypatch,role,status):
    from app.core_pro.manuscript import manuscript_routes
    calls = []
    monkeypatch.setattr(manuscript_routes,'build_docx',lambda *a:calls.append(a) or b'PK-synthetic-docx')
    response = client(channel_app,role).post(f'/manuscript/api/export/docx/{PID}',json={'html':'<p>own text</p>'})
    assert response.status_code == status
    assert bool(calls) == (status == 200)


def test_socket_registry_filters_chapter_and_legacy_assets(channel_app,monkeypatch):
    from app.core_pro.manuscript.manuscript_image import ManuscriptImage
    monkeypatch.setattr(ManuscriptImage,'get_image_registry',staticmethod(lambda p:[
        {'id':'allowed','section':MINE,'caption':PUBLIC},
        {'id':'hidden','section':HIDDEN,'caption':PRIVATE},
        {'id':'legacy','caption':PRIVATE},
    ]))
    sio = socket(channel_app)
    sio.get_received('/manu_ws')
    sio.emit('cmd_get_image_registry',{'pid':PID},namespace='/manu_ws')
    events = sio.get_received('/manu_ws')
    sio.disconnect(namespace='/manu_ws')
    payload = next(e['args'][0] for e in events if e['name']=='image_registry_data')
    assert payload['ok'] is True
    assert [row['id'] for row in payload['registry']] == ['allowed']


def test_scoped_image_delivery_requires_matching_registry_section(channel_app,monkeypatch):
    from app.core_pro.manuscript import manuscript_routes
    monkeypatch.setattr(manuscript_routes.ManuscriptImage,'get_image_registry',staticmethod(lambda p:[
        {'filename':'allowed.png','section':MINE}, {'filename':'hidden.png','section':HIDDEN}]))
    calls = []
    monkeypatch.setattr(manuscript_routes,'send_from_directory',lambda *a,**k:calls.append(a) or PUBLIC)
    http = client(channel_app,'coauthor')
    assert http.get(f'/manuscript/image/{PID}/allowed.png').status_code == 200
    assert len(calls) == 1
    assert http.get(f'/manuscript/image/{PID}/hidden.png').status_code == 403
    assert len(calls) == 1


def test_socket_read_and_model_requests_keep_chapter_boundary(channel_app,monkeypatch):
    from app.core_pro.manuscript import manuscript_routes
    from app.llm_service.matching_tasks import task_8drafter
    class Inline:
        def submit(self,fn,*args,**kwargs):
            fn(*args,**kwargs)
    captured = []
    monkeypatch.setattr(manuscript_routes,'_CHAT_EXECUTOR',Inline())
    monkeypatch.setattr(manuscript_routes.ai_drafter,'_detect_intent',lambda *a,**k:'draft')
    monkeypatch.setattr(task_8drafter,'dispatch_task',lambda task,prompt,**kw:captured.append(str(prompt)) or {'ok':True,'text':'synthetic'})
    sio = socket(channel_app)
    sio.get_received('/manu_ws')
    sio.emit('cmd_load_block',{'pid':PID,'section':HIDDEN,'filename':'V0.1.json'},namespace='/manu_ws')
    sio.emit('cmd_load_paper',{'pid':PID,'g_ver':'V1'},namespace='/manu_ws')
    sio.emit('chat_message',{'pid':PID,'section':HIDDEN,'msg':'draft hidden chapter'},namespace='/manu_ws')
    denied = sio.get_received('/manu_ws')
    assert PRIVATE not in json.dumps(denied)
    assert captured == []
    sio.emit('chat_message',{'pid':PID,'title':'Synthetic','section':MINE,'msg':'Draft using the current author paragraph','readable_sections':[HIDDEN], 'include_paper':True},namespace='/manu_ws')
    events = sio.get_received('/manu_ws')
    sio.disconnect(namespace='/manu_ws')
    assert captured, events
    assert PUBLIC in '\n'.join(captured)
    assert PRIVATE not in '\n'.join(captured)


@pytest.mark.parametrize('method',['patch','delete'])
def test_revoked_chapter_comment_author_cannot_mutate(channel_app,method):
    from app import db
    from app.models import ChapterComment
    app, ids, _ = channel_app
    with app.app_context():
        row = ChapterComment(pid=PID,section_key=HIDDEN,scope='section',s_ver='0.1',author_id=ids['coauthor'],body=PRIVATE)
        db.session.add(row)
        db.session.commit()
        comment_id = row.id
    response = getattr(client(channel_app,'coauthor'),method)(f'/manuscript/api/chapter/{PID}/comments/{comment_id}',json={'body':'tamper','resolved':True})
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(ChapterComment,comment_id).body == PRIVATE
