"""只讀新還原的合成資料，再用原 3.10 image 驗啟動/ACL/歷史/新增版本/匯出。"""
import hashlib,io,json,os,shutil,sqlite3,sys,zipfile
from pathlib import Path

for source in Path('/source').rglob('*'):
    if source.is_file() and not source.is_symlink():
        target=Path('/sandbox')/source.relative_to('/source');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
os.chdir('/sandbox');sys.path.insert(0,'/sandbox')
root=Path('/evidence/rollback-restored')
assert (root/'.restore-complete.json').is_file()
for key in ['SECRET_KEY','FERNET_KEY','API_BEARER_TOKEN','API_BEARER_TOKENS','OPENAI_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY','DATABASE_URL']:
    os.environ.pop(key,None)
os.environ.update(FLASK_ENV='development',PYTHON_DOTENV_DISABLED='1',AUTH_MODE='session',API_AUTH_ENABLED='0',SOCKETIO_ASYNC_MODE='threading',LOCK_ROOT='/tmp/rollback-locks')

def snapshot():
    result={}
    for name in ['app.db','manu.db']:
        with sqlite3.connect(root/name) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            tables=[row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            result[name]={table:connection.execute('SELECT * FROM "'+table.replace('"','""')+'" ORDER BY rowid').fetchall() for table in tables}
    return result

before=snapshot()
from app import create_app,db
from app.models import User,Project,ChapterComment,ProjectInvitation
from app.llm_service.llm_model import LLMModel
LLMModel.get_db_path=staticmethod(lambda:str(root/'llm.db'))
app=create_app({'TESTING':True,'AUTH_MODE':'session','API_AUTH_ENABLED':False,'WTF_CSRF_ENABLED':False,'RATELIMIT_ENABLED':False,
    'SQLALCHEMY_DATABASE_URI':f'sqlite:///{root/"app.db"}','SQLALCHEMY_BINDS':{'manuscript':f'sqlite:///{root/"manu.db"}'}})
from app.core_pro.literature import literature_routes
literature_routes.DATA_ROOT=str(root);literature_routes.literature_library_service=None
assert snapshot()==before,'startup changed restored research rows'
with app.app_context():
    author=User.query.filter_by(email='author@example.invalid').one()
    author_id=author.id
    assert ChapterComment.query.count()>0
    assert ProjectInvitation.query.filter_by(status='revoked').count()>0
client=app.test_client()
with client.session_transaction() as session:session['_user_id']=str(author_id);session['_fresh']=True
assert client.get('/manuscript?pid=SAIBROWSER-p',follow_redirects=True).status_code==200
from app.core_pro.manuscript.manuscript_io import ManuscriptIO
with app.app_context():
    retained=ManuscriptIO.load_paper_version('SAIBROWSER-p','V1')
    assert retained
    result=ManuscriptIO.save_block_version('SAIBROWSER-p','rollbackcheck','Synthetic rollback check','<p>Author autonomy remains at 300 K.</p>',updated_by='synthetic-author')
    assert result['ok']
    assert ManuscriptIO.load_paper_version('SAIBROWSER-p','V1')==retained
response=client.post('/manuscript/api/export/docx/SAIBROWSER-p',json={'html':'<p>Author autonomy remains at 300 K.</p>','title':'Synthetic rollback'})
assert response.status_code==200
with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
    assert archive.testzip() is None
    assert b'300 K' in archive.read('word/document.xml')
anonymous=app.test_client()
denied=anonymous.post('/manuscript/api/export/docx/SAIBROWSER-p',json={'html':'<p>Denied</p>'})
assert denied.status_code in [401,403]
assert snapshot()==before,'read/export changed restored research rows'
report=dict(python=sys.version,all_research_rows_preserved=True,professor_history_and_revocation=True,
    old_paper_unchanged=True,new_section_saved=True,authenticated_export=True,anonymous_export=denied.status_code,
    docx_sha256=hashlib.sha256(response.data).hexdigest(),production_data_access=False)
Path('/evidence/rollback-result.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
