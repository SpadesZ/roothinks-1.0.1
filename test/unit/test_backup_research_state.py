# 目的：用真實 RootThinks schema 證明研究資料、ACL、作者決策、版本及教授歷史可還原。
# 邊界：合成 app、暫存原件；先停止 lease 與 DB 連線，再還原到新目錄。
# 驗證：python -m pytest test/unit/test_backup_research_state.py -q
import pytest
from test_sai_channel_acl import channel_app,client,PID,MINE,PUBLIC
from test_sai_review_workflow import workflow,confirm
from app.research_backup import BackupError,acquire_runtime_lease,create_backup,restore_backup


def test_real_research_schema_and_history_round_trip(workflow,tmp_path,monkeypatch):
    from app import create_app,db
    from app.models import ChapterComment,ChapterReviewEvent,User,WorkspaceMember,Project,ProjectInvitation,RevisionLog
    from app.llm_service.llm_model import LLMModel
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    from app.core_pro.manuscript.citation_ledger import list_decisions
    bundle,_,_ = workflow
    app,ids,root = bundle
    decision = confirm(workflow).json['decision']
    # 新科學報告、原證據快照及作者回應同屬研究歷史，還原後不可只剩稿件。
    assessment=client(bundle,'editor').post('/manuscript/api/scientific/assess',json=dict(
        pid=PID,section=MINE,text='Hypothesis: an untested mechanism may operate.',evidence=[]))
    assert assessment.status_code==201,assessment.json
    report=assessment.json
    answer=client(bundle,'editor').post(f'/manuscript/api/scientific/reports/{report["report_id"]}/responses',json=dict(
        pid=PID,claim_id=report['claims'][0]['id'],view_snapshot=report['view_snapshot'],stance='retain_hypothesis',reason='Retain explicit uncertainty.'))
    assert answer.status_code==201,answer.json
    result = client(bundle,'owner').post(f'/api/mentor/mentees/{ids["editor"]}/reviews/{PID}/items',
        json={'kind':'comment','paper_version':'V1','decision_id':decision['decision_id'],'body':'Original professor condition check'})
    assert result.status_code==201,result.json
    cid = result.json['item']['comment']['id']
    response = client(bundle,'editor').post(f'/manuscript/api/chapter/{PID}/comments/{cid}/events',json=dict(
        action='respond',base_rev=0,body='Original author response',target_version='V1',revision_location=PUBLIC))
    assert response.status_code==201,response.json
    # 新教授邀請、原意見、作者回覆及撤權狀態也必須一起恢復；署名不得在重啟補權。
    owner = client(bundle,'owner')
    invitation = owner.post(f'/api/collaboration/project/{PID}/invitations',json=dict(
        email='outsider@example.invalid',kind='professor',role='viewer',sections=[]))
    assert invitation.status_code==201,invitation.json
    iid = invitation.json['invitation']['id']
    professor = client(bundle,'outsider')
    assert professor.post(f'/api/collaboration/invitations/{iid}/accept',json={'token':invitation.json['token']}).status_code==200
    second = professor.post(f'/api/mentor/mentees/{ids["owner"]}/reviews/{PID}/items',
        json=dict(kind='comment',paper_version='V1',body='Retain this professor review after removal'))
    assert second.status_code==201,second.json
    second_id = second.json['item']['comment']['id']
    assert owner.post(f'/manuscript/api/chapter/{PID}/comments/{second_id}/events',json=dict(
        action='respond',base_rev=0,body='Retained response to removed professor',target_version='V1',revision_location=PUBLIC)).status_code==201
    assert owner.post(f'/api/collaboration/invitations/{iid}/revoke',json={'reason':'保留歷史，恢復獨立研究'}).status_code==200
    with app.app_context():
        Project.query.filter_by(project_id=PID).one().members=[dict(name='Removed professor',role='共同主持人',
            emails=['outsider@example.invalid'],access_role='owner')]
        db.session.commit()
    original_pdf = root/PID/'literature/papers/retained/00_origins/source.pdf'
    original_pdf.parent.mkdir(parents=True,exist_ok=True)
    original_pdf.write_bytes(b'%PDF-1.4\nRetained synthetic research original')
    with pytest.raises(BackupError):
        create_backup(root,tmp_path/'must-not-run-live')
    with app.app_context():
        original_user_count = User.query.count()
        original_scientific=[(r.entity_type,r.payload_json) for r in RevisionLog.query.filter_by(pid=PID,entity_ref=report['report_id']).order_by(RevisionLog.id)]
        original_comment = db.session.get(ChapterComment,cid).to_dict()
        original_paper = ManuscriptIO.load_paper_version(PID,'V1')
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()
    app.extensions['research_runtime_lease'].release()
    backup,restored = tmp_path/'backup',tmp_path/'restored'
    manifest = create_backup(root,backup)
    assert manifest['databases']['app.db']['tables']['chapter_review_events']['rows']>=1
    assert 'manu.db' in manifest['databases']
    restore_backup(backup,restored)
    monkeypatch.setattr(LLMModel,'get_db_path',staticmethod(lambda:str(restored/'llm.db')))
    recovered = create_app({'TESTING':True,'AUTH_MODE':'session','API_AUTH_ENABLED':False,'WTF_CSRF_ENABLED':False,
        'RATELIMIT_ENABLED':False,'SQLALCHEMY_DATABASE_URI':f'sqlite:///{restored/"app.db"}',
        'SQLALCHEMY_BINDS':{'manuscript':f'sqlite:///{restored/"manu.db"}'}})
    try:
        with recovered.app_context():
            assert User.query.count()==original_user_count
            assert [(r.entity_type,r.payload_json) for r in RevisionLog.query.filter_by(pid=PID,entity_ref=report['report_id']).order_by(RevisionLog.id)]==original_scientific
            assert len(original_scientific)==2
            assert db.session.get(ChapterComment,cid).to_dict()==original_comment
            assert ChapterReviewEvent.query.filter_by(comment_id=cid).one().body=='Original author response'
            assert next(r for r in list_decisions(str(restored),PID) if r['decision_id']==decision['decision_id'])==decision
            assert ManuscriptIO.load_paper_version(PID,'V1')==original_paper
            assert WorkspaceMember.query.filter_by(pid=PID,user_id=ids['coauthor']).one().role=='coauthor'
            assert WorkspaceMember.query.filter_by(pid=PID,user_id=ids['outsider']).first() is None
            assert db.session.get(ProjectInvitation,iid).status=='revoked'
            assert db.session.get(ChapterComment,second_id).body=='Retain this professor review after removal'
            assert ChapterReviewEvent.query.filter_by(comment_id=second_id).one().body=='Retained response to removed professor'
        assert (restored/PID/'literature/papers/retained/00_origins/source.pdf').read_bytes()==original_pdf.read_bytes()
        outsider = recovered.test_client()
        with outsider.session_transaction() as session:
            session['_user_id']=str(ids['outsider']); session['_fresh']=True
        assert outsider.get(f'/manuscript/api/revisions/{PID}').status_code==403
        recovered_author = recovered.test_client()
        with recovered_author.session_transaction() as session:
            session['_user_id']=str(ids['owner']); session['_fresh']=True
        archive = recovered_author.get(f'/api/mentor/my-reviews/{PID}').json['items']
        assert any(row['comment']['id']==second_id and row['reviewer_active'] is False for row in archive)
    finally:
        recovered.extensions['research_runtime_lease'].release()
        with recovered.app_context():
            db.session.remove()
            for engine in db.engines.values():
                engine.dispose()


def test_failed_restore_cannot_be_started_as_a_new_empty_application(tmp_path):
    from test_research_backup import seed
    root = tmp_path/'source'
    seed(root)
    backup = tmp_path/'backup'
    create_backup(root,backup)
    destination = tmp_path/'incomplete'
    def interrupt(stage,*_):
        if stage=='before_restore_complete':
            raise OSError('Synthetic crash before publication')
    with pytest.raises(BackupError):
        restore_backup(backup,destination,fault=interrupt)
    with pytest.raises(BackupError):
        acquire_runtime_lease(destination)
    with pytest.raises(BackupError):
        create_backup(destination,tmp_path/'cannot-promote-incomplete')
    from app import create_app
    with pytest.raises(BackupError):
        create_app({'TESTING':True,'AUTH_MODE':'session','API_AUTH_ENABLED':False,
            'SQLALCHEMY_DATABASE_URI':f'sqlite:///{destination/"must-not-create.db"}',
            'SQLALCHEMY_BINDS':{'manuscript':f'sqlite:///{destination/"manuscript.db"}'}})
    assert not (destination/'must-not-create.db').exists()
