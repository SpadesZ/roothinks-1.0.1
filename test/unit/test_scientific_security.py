# 目的：先重現專案列舉、session API CSRF、Socket 佇列／結果撤權及登出後寫入。
# 邊界：合成 session／DB；無真 provider。驗證：python -m pytest test/unit/test_scientific_security.py -q
import re,json
import pytest
from test_sai_channel_acl import channel_app,client,socket,PID,OTHER,MINE,PUBLIC


@pytest.mark.parametrize('path',[f'/manuscript/api/bootstrap/{PID}','/manuscript/api/formal_projects'])
def test_manuscript_project_picker_never_lists_unshared_project(channel_app,path):
    response=client(channel_app,'editor').get(path)
    assert response.status_code==200
    assert OTHER not in response.get_data(as_text=True)


def test_authenticated_api_write_requires_csrf_and_keeps_valid_token_path(channel_app,monkeypatch):
    app=channel_app[0]
    monkeypatch.setitem(app.config,'WTF_CSRF_ENABLED',True)
    http=client(channel_app,'editor')
    data=dict(pid=PID,section=MINE,status='accepted',paper_id='paper',claim_text='Synthetic CSRF control')
    denied=http.post('/manuscript/api/citation/decide',json=data)
    assert denied.status_code==403,denied.json
    assert 'CSRF' in denied.get_data(as_text=True)
    page=http.get('/collaboration').get_data(as_text=True)
    token=re.search(r'id="collaboration-csrf" value="([^"]+)"',page).group(1)
    accepted=http.post('/manuscript/api/citation/decide',json=data,headers={'X-CSRFToken':token})
    assert accepted.status_code==200,accepted.json
    body=json.dumps(dict(pid=PID,section=MINE,content='<p>CSRF beacon draft</p>',base_rev=0))
    assert http.post('/manuscript/api/draft/flush',data=body,content_type='text/plain').status_code==403
    flushed=http.post('/manuscript/api/draft/flush',data={'draft_json':body,'csrf_token':token})
    assert flushed.status_code==200,flushed.json
    assert flushed.json['success'] is True


@pytest.mark.parametrize('stage',['queued','provider'])
def test_revocation_prevents_queued_dispatch_and_completed_result_delivery(channel_app,monkeypatch,stage):
    from app import db
    from app.models import WorkspaceMember
    from app.core_pro.manuscript import manuscript_routes as routes
    app,ids,_=channel_app
    queue=[];calls=[];persisted=[]
    class Delayed:
        def submit(self,fn,*args,**kwargs):queue.append((fn,args,kwargs))
    def revoke():
        WorkspaceMember.query.filter_by(user_id=ids['editor'],pid=PID).delete()
        db.session.commit()
    def provider(**kwargs):
        calls.append(kwargs)
        if stage=='provider':revoke()
        return {'type':'draft','content':'REVOKED_RESULT_SENTINEL'}
    monkeypatch.setattr(routes,'_CHAT_EXECUTOR',Delayed())
    monkeypatch.setattr(routes.ai_drafter,'process_request',provider)
    monkeypatch.setattr(routes,'save_chat_history',lambda *a,**k:persisted.append(a))
    sio=socket(channel_app,'editor')
    try:
        sio.get_received('/manu_ws')
        sio.emit('chat_message',dict(pid=PID,section=MINE,msg='Write a bounded synthetic paragraph'),namespace='/manu_ws')
        assert len(queue)==1
        if stage=='queued':
            with app.app_context():revoke()
        fn,args,kwargs=queue[0];fn(*args,**kwargs)
        events=sio.get_received('/manu_ws')
        assert not any(e['name'] in {'ai_response','job_done'} for e in events),events
        assert not any(len(a)>2 and a[2]=='ai' for a in persisted)
        assert len(calls)==(0 if stage=='queued' else 1)
        assert any(e['name']=='job_error' for e in events)
    finally:
        if sio.is_connected('/manu_ws'):sio.disconnect(namespace='/manu_ws')
        with app.app_context():
            if not WorkspaceMember.query.filter_by(user_id=ids['editor'],pid=PID).first():
                db.session.add(WorkspaceMember(user_id=ids['editor'],pid=PID,role='editor'));db.session.commit()


def test_logged_out_browser_socket_cannot_continue_saving(channel_app):
    from app import socketio
    http=client(channel_app)
    assert http.post('/api/auth/login',json={'email':'editor@example.invalid','password':'synthetic-only'}).status_code==200
    sio=socketio.test_client(channel_app[0],namespace='/manu_ws',flask_test_client=http,auth={'pid':PID})
    try:
        sio.get_received('/manu_ws')
        assert http.post('/api/auth/logout').status_code==200
        sio.emit('cmd_save_block',dict(pid=PID,section=MINE,title='Synthetic',content='<p>LOGGED_OUT_SENTINEL</p>'),namespace='/manu_ws')
        assert not any(e['name']=='save_ack' for e in sio.get_received('/manu_ws'))
    finally:
        if sio.is_connected('/manu_ws'):sio.disconnect(namespace='/manu_ws')


def test_revoked_socket_does_not_receive_project_broadcast(channel_app):
    from app import db
    from app.models import WorkspaceMember
    app,ids,_=channel_app
    revoked=socket(channel_app,'viewer');author=socket(channel_app,'owner')
    try:
        with app.app_context():
            WorkspaceMember.query.filter_by(user_id=ids['viewer'],pid=PID).delete();db.session.commit()
        revoked.get_received('/manu_ws')
        author.emit('cmd_save_block',dict(pid=PID,section=MINE,title='Synthetic',content='<p>'+PUBLIC+'</p>'),namespace='/manu_ws')
        assert not any(e['name'] in {'peer_update','presence_update'} for e in revoked.get_received('/manu_ws'))
    finally:
        for sio in (revoked,author):
            if sio.is_connected('/manu_ws'):sio.disconnect(namespace='/manu_ws')
        with app.app_context():
            db.session.add(WorkspaceMember(user_id=ids['viewer'],pid=PID,role='viewer'));db.session.commit()
