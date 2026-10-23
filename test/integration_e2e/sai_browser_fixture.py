# Roothinks source maintenance contract
# 目的：真實瀏覽器的離線合成站。只綁 localhost；SQLite、原件、鎖皆在新 tmp。
# 驗證：python test/integration_e2e/sai_browser_fixture.py；不得作正式服務使用。
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
for key in ('DATABASE_URL','SECRET_KEY','FERNET_KEY','APP_ENV','AUTH_MODE','SOCKETIO_MESSAGE_QUEUE',
            'API_BEARER_TOKEN','API_BEARER_TOKENS','OPENAI_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY'):
    os.environ.pop(key,None)
root = Path(tempfile.mkdtemp(prefix='roothinks-sai-browser-'))
os.environ.update(FLASK_ENV='development',PYTHON_DOTENV_DISABLED='1',SOCKETIO_ASYNC_MODE='threading',
    API_AUTH_ENABLED='0',LOCK_ROOT=str(root/'locks'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
    CORS_ALLOWED_ORIGINS='http://127.0.0.1:8893')

from app import create_app,db,socketio
from app.models import User,Project,WorkspaceMember,ChapterAssignment,MentorLink,EvidenceSegment
from app.llm_service.llm_model import LLMModel
LLMModel.get_db_path = staticmethod(lambda:str(root/'llm.db'))
app = create_app({'TESTING':True,'AUTH_MODE':'session','API_AUTH_ENABLED':False,'WTF_CSRF_ENABLED':False,
    'RATELIMIT_ENABLED':False,'SECRET_KEY':'synthetic-browser-only','SQLALCHEMY_DATABASE_URI':f'sqlite:///{root / "app.db"}',
    'SQLALCHEMY_BINDS':{'manuscript':f'sqlite:///{root / "manu.db"}'}})
from app.core_pro.literature import literature_routes
literature_routes.DATA_ROOT = str(root)
literature_routes.literature_library_service = None
from app.core_pro.manuscript.manuscript_io import ManuscriptIO
from app.services.literature_library import LiteratureLibrary
from app.llm_service.matching_tasks import task_8drafter
from app.core_pro.manuscript import manuscript_routes
manuscript_routes.ai_drafter._detect_intent = lambda *a,**k:'draft'
task_8drafter.dispatch_task = lambda task,prompt,**k: {'ok':True,'text':json.dumps({
    'chat_message':'合成模型替身已收到完整主張及條件。','draft_content':'This synthetic claim applies only to the tested sample at 300 K.'})}

PID='SAIBROWSER-p'
with app.app_context():
    db.create_all()
    db.session.add(Project(project_id=PID,name='SAI 合成研究',research_title='Synthetic bounded measurement',status='formal'))
    users={}
    for name,role,system_role in [('author','editor','user'),('professor','viewer','mentor'),('scoped','coauthor','user')]:
        user=User(username=name,email=f'{name}@example.invalid',system_role=system_role)
        user.set_password('Synthetic123!')
        db.session.add(user); db.session.flush(); users[name]=user.id
        db.session.add(WorkspaceMember(user_id=user.id,pid=PID,role=role))
    db.session.add(ChapterAssignment(pid=PID,section_key='introduction',user_id=users['scoped']))
    db.session.add(MentorLink(mentor_id=users['professor'],mentee_id=users['author']))
    db.session.commit()
    text='The measured improvement applies only to sample A at 300 K.'
    ManuscriptIO.save_block_version(PID,'introduction','Synthetic bounded measurement',f'<p>{text}</p>',updated_by='author')
    ManuscriptIO.save_block_version(PID,'results','Synthetic bounded measurement','<p>PRIVATE_RESULTS_BROWSER_SENTINEL</p>',updated_by='author')
    ManuscriptIO.save_paper_version(PID,'Synthetic bounded measurement',f'<p>{text}</p>',sections={'introduction':'0.1'})
    ManuscriptIO.save_paper_version(PID,'Synthetic revised measurement',f'<p>{text}</p><p>Calibration used the same reference plane. No claim about other samples is made.</p>',sections={'introduction':'0.1'})
    library=LiteratureLibrary(str(root))
    library.merge_candidates(PID,[{'title':'Synthetic calibrated measurement','doi':'10.1234/sai-browser'}])
    eid=next(iter(library.load(PID)['entries']))
    library.update_entry(PID,eid,{'paper_id':'synthetic','screening_status':'included',
        'screening_note':'Only matched samples and calibration','reading_note':'The measured improvement applies only to sample A.'},actor='author@example.invalid',base_rev=0)
    db.session.add(EvidenceSegment(project_id=PID,source_type='paper_segment',source_id='synthetic:p3',paper_id='synthetic',
        segment_id='s1',text='Sample A improved at 300 K under the same calibration. No other samples were measured.',
        content_hash=hashlib.sha256(b'synthetic').hexdigest(),metadata_json=json.dumps({'page':3,'section':'Results'})))
    db.session.commit()
print(json.dumps({'url':'http://127.0.0.1:8893','pid':PID,'root':str(root),'users':users}),flush=True)
socketio.run(app,host='127.0.0.1',port=8893,allow_unsafe_werkzeug=True,use_reloader=False,log_output=False)
