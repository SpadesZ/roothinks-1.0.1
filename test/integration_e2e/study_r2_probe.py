# Roothinks source maintenance contract
# 目的：Study R2 前後量測探針。用真的 create_app、session 登入與真的教授／團隊邀請流程，記錄每條路徑「實際」回應。
# 不做 assertion：同一支程式在改版前後各跑一次，輸出 JSON 給交付報告比較。所有資料在新 tmp，不碰 repo data/。
# 執行：python test/integration_e2e/study_r2_probe.py <輸出 JSON 路徑>
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
for key in ('DATABASE_URL', 'SECRET_KEY', 'FERNET_KEY', 'APP_ENV', 'AUTH_MODE'):
    os.environ.pop(key, None)
root = Path(tempfile.mkdtemp(prefix='roothinks-study-r2-probe-'))
os.environ.update(FLASK_ENV='development', LOCK_ROOT=str(root / 'locks'), LECTURE_SYSTEM_LOG_FILE=str(root / 'runtime.log'))

from app import create_app, db
from app.models import Project, User, WorkspaceMember
from app.llm_service.llm_model import LLMModel
LLMModel.get_db_path = staticmethod(lambda: str(root / 'llm.db'))
app = create_app({'TESTING': True, 'AUTH_MODE': 'session', 'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False,
                  'SQLALCHEMY_DATABASE_URI': f'sqlite:///{root / "app.db"}',
                  'SQLALCHEMY_BINDS': {'manuscript': f'sqlite:///{root / "manu.db"}'}})
app.config['WTF_CSRF_CHECK_DEFAULT'] = False
from app.core_pro.study import study_anchors

PID, OTHER, PAPER = 'PROBE-p', 'OTHERP-p', 'paper1'
QUOTE = 'The improvement was observed only at 300 K under one calibration protocol.'
CONTENT = [{'page': 1, 'blocks': [
    {'type': 'body', 'seq_id': 'p1_body_1', 'content': 'We report sample A. ' + QUOTE},
    {'type': 'body', 'seq_id': 'p1_body_2', 'content': 'The value was 10² m. Twice: the effect was small. Again the effect was small.'},
    {'type': 'body', 'seq_id': 'p1_body_3', 'content': 'This improvement is bounded.'},
]}]
SENTINELS = {'free_note': 'PRIVATE_FREE_NOTE_SENTINEL', 'conversation': 'PRIVATE_CONVERSATION_SENTINEL',
             'unshared_evidence': 'PRIVATE_EVIDENCE_SENTINEL', 'matrix': 'PRIVATE_MATRIX_SENTINEL'}


def write_paper(pid):
    base = root / pid / 'literature' / 'papers' / PAPER / '05_interprets' / 'fusion'
    base.mkdir(parents=True, exist_ok=True)
    (base / 'full_text.json').write_text(json.dumps({'paper_id': PAPER, 'content': CONTENT}), encoding='utf-8')


def client(name):
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': f'{name}@probe.local', 'password': 'password123'})
    assert r.status_code == 200, r.get_data(as_text=True)
    return c


def invite(owner, who, kind, pid=PID):
    r = owner.post(f'/api/collaboration/project/{pid}/invitations', json={'email': f'{who}@probe.local', 'kind': kind, 'role': 'viewer'})
    assert r.status_code == 201, r.get_data(as_text=True)
    inv = r.json['invitation']
    a = client(who).post(f"/api/collaboration/invitations/{inv['id']}/accept", json={'token': r.json['token']})
    assert a.status_code == 200, a.get_data(as_text=True)
    return inv['id']


def selection(**extra):
    return {'quote': QUOTE, 'side': 'original', 'ref': 'p1-b1', 'source': 'flowa_fusion', **extra}


def accept(c, base_rev, text, **extra):
    note = {'paper_id': PAPER, 'anchors': [selection()], 'statements': [
        {'kind': 'source_fact', 'evidence': [0]}, {'kind': 'human_hypothesis', 'text': text}], **extra}
    r = c.post('/api/study/evidence_notes/accept', json={'pid': PID, 'base_rev': base_rev, 'accepted': True, 'note': note})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.json['note']['note_id']


def contains(resp, needle):
    return needle in resp.get_data(as_text=True)


out = {'paths': {}, 'semantics': {}, 'ownership': {}}
with app.app_context():
    db.create_all()
    ids = {}
    for pid in (PID, OTHER):
        db.session.add(Project(project_id=pid, name=pid, status='formal'))
    for name in ('owner', 'editor', 'prof', 'teamviewer', 'outsider', 'otherowner'):
        u = User(username=name, email=f'{name}@probe.local', system_role='mentor' if name == 'prof' else 'user')
        u.set_password('password123')
        db.session.add(u)
        db.session.flush()
        ids[name] = u.id
    db.session.add_all([WorkspaceMember(user_id=ids['owner'], pid=PID, role='owner'),
                        WorkspaceMember(user_id=ids['editor'], pid=PID, role='editor'),
                        WorkspaceMember(user_id=ids['otherowner'], pid=OTHER, role='owner')])
    db.session.commit()
for pid in (PID, OTHER):
    write_paper(pid)

owner = client('owner')
prof_invitation = invite(owner, 'prof', 'professor')
invite(owner, 'teamviewer', 'team')

owner.post('/api/study/notes/save', json={'pid': PID, 'notes': SENTINELS['free_note'], 'base_rev': 0})
conv = owner.post('/api/study/conversations/create', json={'pid': PID, 'title': 'x'}).json['conv_id']
conv_path = root / PID / 'study' / 'conversations' / f'{conv}.json'
doc = json.loads(conv_path.read_text(encoding='utf-8'))
doc['messages'].append({'role': 'user', 'content': SENTINELS['conversation']})
conv_path.write_text(json.dumps(doc), encoding='utf-8')
(root / PID / 'study' / 'latest_matrix.json').write_text(json.dumps({'matrix_id': 'm1', 'rows': [SENTINELS['matrix']]}), encoding='utf-8')
private_note = accept(owner, 0, SENTINELS['unshared_evidence'])
shared_note = accept(owner, 1, 'SHARED_FOR_REVIEW')
owner.post('/api/study/evidence_notes/review', json={'pid': PID, 'note_id': shared_note, 'action': 'request'})

paths = {
    'free_note': (f'/api/study/notes/load/{PID}', 'free_note'),
    'conversation_list': (f'/api/study/{PID}/conversations', None),
    'conversation_get': (f'/api/study/conversation/{conv}?pid={PID}', 'conversation'),
    'matrix_latest': (f'/api/study/{PID}/matrix/latest', 'matrix'),
    'matrix_history': (f'/api/study/{PID}/matrix/history', None),
    'evidence_list': (f'/api/study/evidence_notes/{PID}', 'unshared_evidence'),
    'evidence_export': (f'/api/study/evidence_notes/{PID}/export', 'unshared_evidence'),
    'paper_list': (f'/api/study/paper_list/{PID}', None),
    'fulltext': (f'/api/study/get_fulltext/{PID}/{PAPER}', None),
    'cross_project_fulltext': (f'/api/study/get_fulltext/{OTHER}/{PAPER}', None),
    'mentor_detail_notes': (f"/api/mentor/mentees/{ids['owner']}?notes=1", 'free_note'),
    'mentor_study_reviews': (f"/api/mentor/mentees/{ids['owner']}/study-reviews/{PID}", 'unshared_evidence'),
}
viewers = {name: client(name) for name in ('prof', 'teamviewer', 'outsider')}
for label, (url, sentinel) in paths.items():
    row = {}
    for name, c in viewers.items():
        r = c.get(url)
        row[name] = {'status': r.status_code, 'leaks_private': bool(sentinel and contains(r, SENTINELS[sentinel]))}
        if label == 'mentor_detail_notes':
            row[name]['leaks_conversation'] = contains(r, SENTINELS['conversation'])
        if label in ('evidence_list', 'mentor_study_reviews'):
            row[name]['shows_shared'] = contains(r, 'SHARED_FOR_REVIEW')
    out['paths'][label] = row

# 撤銷後
from app.project_portfolio.collaboration import remove_project_access
from flask_login import login_user
with app.test_request_context():
    login_user(db.session.get(User, ids['owner']))
    remove_project_access(PID, ids['prof'], 'probe revoke')
    db.session.commit()
prof_after = client('prof')
out['paths']['after_revoke'] = {label: prof_after.get(url).status_code for label, (url, _) in paths.items()
                                if label in ('fulltext', 'evidence_list', 'mentor_study_reviews')}

# ---------- 證據語意 ----------
meta = {'pid': PID, 'paper_id': PAPER, 'source': 'flowa_fusion', 'source_sha256': 'x' * 64}
sem = out['semantics']
sem['superscript_false_match'] = bool(study_anchors.locate('The value was 10² m.', '102'))
sem['partial_word_match'] = bool(study_anchors.locate('This improvement is bounded.', 'prove'))
amb = study_anchors.resolve(CONTENT, {'quote': 'the effect was small', 'ref': 'p1-b2'}, meta)
sem['same_block_duplicate_status'] = amb.get('status')
sem['same_block_duplicate_offset_claimed'] = 'start' in amb
sem['verified_vocabulary'] = [v for v in ('verified', 'VERIFIED') if hasattr(study_anchors, v) or v in study_anchors.__dict__]
owner_list = client('owner').get(f'/api/study/evidence_notes/{PID}').json['notes']
sem['note_evidence_status_values'] = sorted({n.get('evidence_status') for n in owner_list})

# ---------- 所有權 ----------
editor = client('editor')
own = out['ownership']
r = editor.post('/api/study/evidence_notes/update', json={'pid': PID, 'base_rev': 3, 'note_id': private_note,
                                                          'statements': [{'kind': 'human_hypothesis', 'text': 'EDITOR_OVERWRITE'}]})
own['editor_overwrites_owner_note_status'] = r.status_code
own['owner_text_replaced'] = any(s['text'] == 'EDITOR_OVERWRITE' for n in client('owner').get(f'/api/study/evidence_notes/{PID}').json['notes']
                                 for s in n['statements'])
r = editor.post('/api/study/evidence_notes/review', json={'pid': PID, 'note_id': private_note, 'action': 'request'})
own['editor_requests_review_on_owner_note_status'] = r.status_code
text = json.dumps(out, ensure_ascii=False, indent=1)
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(text, encoding='utf-8')  # 日誌也會寫到 stdout，用檔案避免混雜
else:
    print(text)
