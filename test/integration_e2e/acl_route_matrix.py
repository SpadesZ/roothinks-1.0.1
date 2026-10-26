# Roothinks source maintenance contract
# 目的：跨模組權限矩陣稽核。自動列舉 app 的每一條 GET 路由，以六種角色實際呼叫，偵測哨兵資料是否外洩。
# 角色：owner、editor、教授（教授邀請，審閱身分）、舊導師（viewer＋MentorLink，沒有邀請）、團隊 viewer、外人（另一專案 owner）。
# 哨兵：Study 自由筆記／對話／矩陣／未分享證據筆記、Literature AI 對話與閱讀筆記、PAQ AI 對話、手稿章節、執行日誌。
# 不做 assertion：輸出 JSON 給稽核報告；回歸測試另在 test/unit/test_acl_route_matrix.py 鎖定結論。資料全在新 tmp。
# 執行：python test/integration_e2e/acl_route_matrix.py <輸出 JSON>
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
for key in ('DATABASE_URL', 'SECRET_KEY', 'FERNET_KEY', 'APP_ENV', 'AUTH_MODE'):
    os.environ.pop(key, None)
ROOT = Path(tempfile.mkdtemp(prefix='roothinks-acl-matrix-'))
LOG = ROOT / 'runtime.log'
os.environ.update(FLASK_ENV='development', LOCK_ROOT=str(ROOT / 'locks'), LECTURE_SYSTEM_LOG_FILE=str(LOG))

PID, OTHER, PAPER = 'ACLP1-p', 'ACLP2-p', 'paper1'
SENTINELS = {
    'study_free_note': 'SENT_STUDY_FREE_NOTE', 'study_conversation': 'SENT_STUDY_CONVERSATION',
    'study_matrix': 'SENT_STUDY_MATRIX', 'study_unshared_evidence': 'SENT_STUDY_UNSHARED_EVIDENCE',
    'literature_chat': 'SENT_LITERATURE_CHAT', 'literature_reading_note': 'SENT_LITERATURE_READING_NOTE',
    'paq_chat': 'SENT_PAQ_CHAT', 'manuscript_section': 'SENT_MANUSCRIPT_SECTION', 'runtime_log': 'SENT_RUNTIME_LOG',
    'other_project_log': 'SENT_OTHER_PROJECT_LOG',
}
ROLES = ('owner', 'editor', 'prof', 'legacymentor', 'teamviewer', 'outsider')


def build_app():
    from app import create_app
    from app.llm_service.llm_model import LLMModel
    LLMModel.get_db_path = staticmethod(lambda: str(ROOT / 'llm.db'))
    app = create_app({'TESTING': True, 'AUTH_MODE': 'session', 'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False,
                      'SQLALCHEMY_DATABASE_URI': f'sqlite:///{ROOT / "app.db"}',
                      'SQLALCHEMY_BINDS': {'manuscript': f'sqlite:///{ROOT / "manu.db"}'}})
    app.config['WTF_CSRF_CHECK_DEFAULT'] = False
    from app.core_pro.literature import literature_routes
    literature_routes.DATA_ROOT = str(ROOT)
    literature_routes.literature_library_service = None
    literature_routes.literature_chat_store_service = None
    return app


def login(app, name):
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': f'{name}@acl.local', 'password': 'password123'})
    assert r.status_code == 200, r.get_data(as_text=True)
    return c


def invite(app, owner, who, kind):
    r = owner.post(f'/api/collaboration/project/{PID}/invitations', json={'email': f'{who}@acl.local', 'kind': kind, 'role': 'viewer'})
    assert r.status_code == 201, r.get_data(as_text=True)
    a = login(app, who).post(f"/api/collaboration/invitations/{r.json['invitation']['id']}/accept", json={'token': r.json['token']})
    assert a.status_code == 200, a.get_data(as_text=True)


def seed(app):
    from app import db
    from app.models import MentorLink, Paper, Project, User, WorkspaceMember
    ids = {}
    with app.app_context():
        db.create_all()
        for pid in (PID, OTHER):
            db.session.add(Project(project_id=pid, name=pid, research_title=pid, status='formal'))
        for name in ROLES:
            u = User(username=name, email=f'{name}@acl.local', system_role='mentor' if name in ('prof', 'legacymentor') else 'user')
            u.set_password('password123')
            db.session.add(u)
            db.session.flush()
            ids[name] = u.id
        db.session.add_all([WorkspaceMember(user_id=ids['owner'], pid=PID, role='owner'),
                            WorkspaceMember(user_id=ids['editor'], pid=PID, role='editor'),
                            WorkspaceMember(user_id=ids['legacymentor'], pid=PID, role='viewer'),
                            WorkspaceMember(user_id=ids['outsider'], pid=OTHER, role='owner'),
                            MentorLink(mentor_id=ids['legacymentor'], mentee_id=ids['owner']),
                            Paper(paper_id=PAPER, pid=PID, title='Acl paper', interpretation_status=Paper.STATUS_GOLD_READY)])
        db.session.commit()
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        ManuscriptIO.save_block_version(PID, 'introduction', 'T', f"<p>{SENTINELS['manuscript_section']}</p>", updated_by='owner')
        from app.services.literature_library import LiteratureLibrary
        lib = LiteratureLibrary(str(ROOT))
        lib.merge_candidates(PID, [{'title': 'Acl paper', 'doi': '10.1/acl'}])
        eid = next(iter(lib.load(PID)['entries']))
        lib.update_entry(PID, eid, {'paper_id': PAPER, 'reading_note': SENTINELS['literature_reading_note']}, actor='owner', base_rev=0)
    owner = login(app, 'owner')
    invite(app, owner, 'prof', 'professor')
    invite(app, owner, 'teamviewer', 'team')
    fusion = ROOT / PID / 'literature' / 'papers' / PAPER / '05_interprets' / 'fusion'
    fusion.mkdir(parents=True, exist_ok=True)
    (fusion / 'full_text.json').write_text(json.dumps({'paper_id': PAPER, 'content': [{'page': 1, 'blocks': [
        {'type': 'body', 'content': 'The improvement was observed only at 300 K under one calibration protocol.'}]}]}), encoding='utf-8')
    owner.post('/api/study/notes/save', json={'pid': PID, 'notes': SENTINELS['study_free_note'], 'base_rev': 0})
    conv = owner.post('/api/study/conversations/create', json={'pid': PID, 'title': 't'}).json['conv_id']
    path = ROOT / PID / 'study' / 'conversations' / f'{conv}.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['messages'].append({'role': 'user', 'content': SENTINELS['study_conversation']})
    path.write_text(json.dumps(doc), encoding='utf-8')
    (ROOT / PID / 'study' / 'latest_matrix.json').write_text(json.dumps({'matrix_id': 'm1', 'rows': [SENTINELS['study_matrix']]}), encoding='utf-8')
    note = {'paper_id': PAPER, 'anchors': [{'quote': 'The improvement was observed only at 300 K under one calibration protocol.',
                                            'side': 'original', 'ref': 'p1-b1', 'source': 'flowa_fusion'}],
            'statements': [{'kind': 'human_hypothesis', 'text': SENTINELS['study_unshared_evidence']}]}
    r = owner.post('/api/study/evidence_notes/accept', json={'pid': PID, 'base_rev': 0, 'accepted': True, 'note': note})
    assert r.status_code == 200, r.get_data(as_text=True)
    for sub, prefix, key in (('literature', 'lit-chat_', 'literature_chat'), ('paq', 'paq-chat_', 'paq_chat')):
        chat = ROOT / PID / sub / 'chat'
        chat.mkdir(parents=True, exist_ok=True)
        (chat / f'{prefix}260101-000000.json').write_text(json.dumps([{'role': 'user', 'content': SENTINELS[key], 'ts': '2026-01-01'}]), encoding='utf-8')
    with open(LOG, 'a', encoding='utf-8') as fh:
        # 結構化出處格式（app/log_provenance.TAG_FORMAT）。
        fh.write(f"2026-10-26 10:00:00,000 [INFO] 1/MainThread x [pid=-]: pid={PID} {SENTINELS['runtime_log']}\n")
        # 另一個專案的 Flow B 日誌行：任何 ACLP1 成員都不該看到——包括訊息文字裡提到 ACLP1 的行（出處是別的專案）。
        fh.write(f"2026-10-26 10:00:00,000 [INFO] 1/MainThread x [pid=aclp2]: [Flow B] run_translation pid={OTHER} paper=secret {SENTINELS['other_project_log']}\n")
        fh.write(f"2026-10-26 10:00:00,000 [INFO] 1/MainThread x [pid=aclp2]: [Flow B] run_translation mentions {PID} {SENTINELS['other_project_log']}\n")
        # 未標記的舊格式行：不論內容都不給專案成員。
        fh.write(f"[2026-10-26] INFO [Flow B] run_translation pid={PID} legacy {SENTINELS['other_project_log']}\n")
    return ids, conv


def fill(rule, ids, conv):
    values = {'pid': PID, 'paper_id': PAPER, 'conv_id': conv, 'matrix_id': 'm1', 'mentee_id': str(ids['owner']),
              'seq_id': 'p1_body_1', 'filename': 'x.png', 'rid': '1', 'item_id': '1', 'task_id': '1', 'iid': '0' * 32,
              'user_id': str(ids['owner']), 'project_id': PID, 'section_key': 'introduction', 'version': 'V1'}
    def sub(m):
        return values.get(m.group(2), 'x')
    return re.sub(r'<(?:([a-z]+):)?([a-zA-Z_]+)>', sub, rule)


def run():
    app = build_app()
    ids, conv = seed(app)
    clients = {name: login(app, name) for name in ROLES}
    rules = sorted({r.rule for r in app.url_map.iter_rules() if 'GET' in r.methods
                    and not r.rule.startswith('/static') and r.endpoint not in ('auth.logout',)})
    rows = []
    for rule in rules:
        base = fill(rule, ids, conv)
        variants = [base] if '<pid>' in rule else [base, base + ('&' if '?' in base else '?') + f'pid={PID}&paper_id={PAPER}']
        for url in variants:
            row = {'rule': rule, 'url': url, 'roles': {}}
            for name, client in clients.items():
                try:
                    resp = client.get(url)
                    body = resp.get_data(as_text=True)
                    row['roles'][name] = {'status': resp.status_code,
                                          'leaks': sorted(k for k, v in SENTINELS.items() if v in body)}
                except Exception as exc:  # 路由在探測參數下崩潰也是發現
                    row['roles'][name] = {'status': 'exception', 'error': f'{type(exc).__name__}: {exc}'[:200], 'leaks': []}
            rows.append(row)
    findings = []
    for row in rows:
        for name, res in row['roles'].items():
            if not res['leaks']:
                continue
            if 'other_project_log' in res['leaks']:
                findings.append({'severity': 'cross-project', 'role': name, 'url': row['url'], 'leaks': res['leaks']})
            elif name == 'outsider':
                findings.append({'severity': 'cross-project', 'role': name, 'url': row['url'], 'leaks': res['leaks']})
            elif name == 'prof' and set(res['leaks']) - {'manuscript_section'}:
                findings.append({'severity': 'review-scope', 'role': name, 'url': row['url'], 'leaks': res['leaks']})
            elif name in ('teamviewer', 'legacymentor') and 'runtime_log' in res['leaks']:
                findings.append({'severity': 'non-project-data', 'role': name, 'url': row['url'], 'leaks': res['leaks']})
    return {'routes': len(rules), 'requests': len(rows) * len(ROLES), 'findings': findings, 'rows': rows}


if __name__ == '__main__':
    result = run()
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding='utf-8')
    else:
        print(text)
