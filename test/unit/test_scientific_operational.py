"""以獨立 SQLite 程序及真 commit 故障驗證關鍵路徑；只用合成 tmp。"""
import subprocess
import sys
import re
from test_sai_channel_acl import channel_app,client,socket,PID,MINE


def test_cross_process_logout_revokes_http_socket_and_queued_work(channel_app,monkeypatch):
    from app import socketio
    from app.core_pro.manuscript import manuscript_routes as routes
    app,ids,root=channel_app
    http=client(channel_app)
    assert http.post('/api/auth/login',json={'email':'editor@example.invalid','password':'synthetic-only'}).status_code==200
    with http.session_transaction() as session:reference=session['_auth_session_ref']
    queued=[];calls=[]
    class Delayed:
        def submit(self,fn,*args,**kwargs):queued.append((fn,args,kwargs))
    monkeypatch.setattr(routes,'_CHAT_EXECUTOR',Delayed())
    monkeypatch.setattr(routes.ai_drafter,'process_request',lambda **kwargs:calls.append(kwargs))
    sio=socketio.test_client(app,namespace='/manu_ws',flask_test_client=http,auth={'pid':PID})
    try:
        sio.get_received('/manu_ws')
        sio.emit('chat_message',dict(pid=PID,section=MINE,msg='Synthetic queued request'),namespace='/manu_ws')
        assert len(queued)==1
        code="import sqlite3,sys,datetime; c=sqlite3.connect(sys.argv[1]); c.execute('INSERT INTO revision_logs (pid,user_id,entity_type,entity_ref,action,summary,created_at) VALUES (?,?,?,?,?,?,?)',('',int(sys.argv[2]),'auth_session',sys.argv[3],'logout','Synthetic independent-process revocation',datetime.datetime.now(datetime.timezone.utc).isoformat())); c.commit(); c.close()"
        subprocess.run([sys.executable,'-c',code,str(root/'app.db'),str(ids['editor']),reference],check=True,timeout=10,capture_output=True)
        fn,args,kwargs=queued[0];fn(*args,**kwargs)
        events=sio.get_received('/manu_ws')
        assert not calls and any(e['name']=='job_error' for e in events)
        assert not any(e['name'] in {'ai_response','job_done'} for e in events)
        assert http.get('/manuscript/api/bootstrap/'+PID).status_code==401
        sio.emit('cmd_save_block',dict(pid=PID,section=MINE,title='Synthetic',content='REVOKED_CROSS_PROCESS'),namespace='/manu_ws')
        assert not any(e['name']=='save_ack' for e in sio.get_received('/manu_ws'))
        # 重新登入仍驗 CSRF／密碼；不能以換 cookie 讓舊 Socket 復活。
        monkeypatch.setitem(app.config,'WTF_CSRF_ENABLED',True)
        page=http.get('/auth/login');assert page.status_code==200
        token=re.search(r'name="csrf_token"[^>]*value="([^"]+)"',page.get_data(as_text=True)).group(1)
        assert http.post('/api/auth/login',json={'email':'editor@example.invalid','password':'synthetic-only'}).status_code==403
        assert http.post('/api/auth/login',json={'email':'editor@example.invalid','password':'wrong'},headers={'X-CSRFToken':token}).status_code==401
        assert http.post('/api/auth/login',json={'email':'editor@example.invalid','password':'synthetic-only'},headers={'X-CSRFToken':token}).status_code==200
        assert http.get('/manuscript/api/bootstrap/'+PID).status_code==200
        if sio.is_connected('/manu_ws'):
            sio.emit('cmd_save_block',dict(pid=PID,section=MINE,title='Synthetic',content='OLD_SOCKET_CANNOT_REVIVE'),namespace='/manu_ws')
            assert not any(e['name']=='save_ack' for e in sio.get_received('/manu_ws'))
    finally:
        if sio.is_connected('/manu_ws'):sio.disconnect(namespace='/manu_ws')


def test_real_advisory_commit_failure_rolls_back_and_delivers_original_draft(channel_app,monkeypatch):
    from app import db
    from app.models import RevisionLog
    from app.core_pro.manuscript import manuscript_routes as routes
    original='Hypothesis: a mechanism that still needs evidence.'
    class Inline:
        def submit(self,fn,*args,**kwargs):fn(*args,**kwargs)
    monkeypatch.setattr(routes,'_CHAT_EXECUTOR',Inline())
    monkeypatch.setattr(routes.ai_drafter,'process_request',lambda **kwargs:{'type':'draft','content':original})
    commit=db.session.commit;failed=[]
    def fail_only_advisory():
        if any(isinstance(r,RevisionLog) and r.entity_type=='scientific_advisory' for r in db.session.new):
            failed.append(True);raise OSError('Injected report DB commit failure')
        return commit()
    monkeypatch.setattr(db.session,'commit',fail_only_advisory)
    sio=socket(channel_app,'owner')
    try:
        sio.get_received('/manu_ws')
        sio.emit('chat_message',dict(pid=PID,section=MINE,msg='Draft a hypothesis without citations'),namespace='/manu_ws')
        events=sio.get_received('/manu_ws');response=next(e['args'][0] for e in events if e['name']=='ai_response')
        assert failed==[True] and response['content']==original
        assert response['meta']['scientific_advisory']['status']=='unavailable'
        assert any(e['name']=='job_done' for e in events)
        with channel_app[0].app_context():assert RevisionLog.query.filter_by(entity_type='scientific_advisory').count()==0
    finally:
        if sio.is_connected('/manu_ws'):sio.disconnect(namespace='/manu_ws')
