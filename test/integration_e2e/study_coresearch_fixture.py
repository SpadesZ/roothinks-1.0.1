# Roothinks source maintenance contract
# 目的：Study 共研切片的真瀏覽器離線合成站。只綁 localhost；SQLite、論文、鎖都在新 tmp。
# 內容：一篇自寫的合成論文（3 頁、含譯文與 seq_id）、研究者（owner）、隊友（editor）、教授（viewer＋MentorLink）。
# 模型替身：Tutor 不連外；結構化請求回傳一個真實引文與一個不存在的引文，用來驗證系統會擋下假引文。
# 驗證：python test/integration_e2e/study_coresearch_fixture.py；不得作正式服務使用。
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
for key in ('DATABASE_URL', 'SECRET_KEY', 'FERNET_KEY', 'APP_ENV', 'AUTH_MODE', 'SOCKETIO_MESSAGE_QUEUE',
            'API_BEARER_TOKEN', 'API_BEARER_TOKENS', 'OPENAI_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY'):
    os.environ.pop(key, None)
PORT = int(os.environ.get('STUDY_FIXTURE_PORT', '8894'))
root = Path(tempfile.mkdtemp(prefix='roothinks-study-browser-'))
os.environ.update(FLASK_ENV='development', PYTHON_DOTENV_DISABLED='1', SOCKETIO_ASYNC_MODE='threading',
                  API_AUTH_ENABLED='0', LOCK_ROOT=str(root / 'locks'), LECTURE_SYSTEM_LOG_FILE=str(root / 'runtime.log'),
                  HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', CORS_ALLOWED_ORIGINS=f'http://127.0.0.1:{PORT}')

from app import create_app, db, socketio
from app.models import User, Project, WorkspaceMember, MentorLink, Paper, ProjectInvitation
from app.llm_service.llm_model import LLMModel
LLMModel.get_db_path = staticmethod(lambda: str(root / 'llm.db'))
app = create_app({'TESTING': True, 'AUTH_MODE': 'session', 'API_AUTH_ENABLED': False, 'WTF_CSRF_ENABLED': True,
                  'RATELIMIT_ENABLED': False, 'SECRET_KEY': 'synthetic-browser-only',
                  'SQLALCHEMY_DATABASE_URI': f'sqlite:///{root / "app.db"}',
                  'SQLALCHEMY_BINDS': {'manuscript': f'sqlite:///{root / "manu.db"}'}})
from app.core_pro.literature import literature_routes
literature_routes.DATA_ROOT = str(root)
from app.core_pro.study import study_routes

PID = 'STUDYCO-p'
PAPER = 'synthetic_calibration'
PAGES = [
    (1, [('title', 'Calibrated Thermal Response in a Synthetic Sample Set', ''),
         ('body', 'We report a bounded measurement of thermal response for sample A. The improvement was observed only at 300 K under one calibration protocol.',
          '我們報告樣本 A 熱響應的有界量測。改善只在 300 K、單一校正流程下觀察到。'),
         ('body', 'No claim is made about samples B or C, which were not measured in this study.',
          '本研究未量測樣本 B 或 C，因此不對它們做任何主張。')]),
    (2, [('title', 'Methods', ''),
         ('body', 'All measurements used the same reference plane. The calibration drift was below 0.2 percent across three repeated runs.',
          '所有量測使用同一參考平面。三次重複量測的校正漂移低於 0.2%。'),
         ('body', 'Sample A was prepared with a fixed annealing time of 40 minutes; annealing time was not varied.',
          '樣本 A 以固定 40 分鐘退火製備；退火時間沒有改變。')]),
    (3, [('title', 'Results', ''),
         ('body', 'Sample A improved by 12 percent at 300 K relative to the uncalibrated baseline. The effect was not tested at other temperatures.',
          '相對於未校正基線，樣本 A 在 300 K 改善 12%。其他溫度沒有測試。'),
         ('body', 'The result may depend on the annealing time, which this study held constant.',
          '結果可能取決於退火時間，而本研究把退火時間固定。')]),
]


def _write_paper():
    paper_dir = root / PID / 'literature' / 'papers' / PAPER
    content = []
    for page, blocks in PAGES:
        rows = []
        for idx, (kind, text, zh) in enumerate(blocks, start=1):
            row = {'type': kind, 'content': text, 'seq_id': f'p{page}_{kind}_{idx}'}
            if zh:
                row['content_zh'] = zh
            rows.append(row)
        content.append({'page': page, 'blocks': rows})
    fusion = paper_dir / '05_interprets' / 'fusion'
    fusion.mkdir(parents=True)
    plain = [{'page': p['page'], 'blocks': [{k: v for k, v in b.items() if k != 'content_zh'} for b in p['blocks']]}
             for p in content]
    meta = {'paper_id': PAPER, 'metadata': {'title': PAGES[0][1][0][1]}}
    (fusion / 'full_text.json').write_text(json.dumps({**meta, 'content': plain}, ensure_ascii=False), encoding='utf-8')
    (paper_dir / '05_interprets' / 'full_text_trans.json').write_text(
        json.dumps({**meta, 'content': content}, ensure_ascii=False), encoding='utf-8')
    (paper_dir / '05_interprets' / 'summary.json').write_text(json.dumps({
        'abstract_zh': '合成論文：樣本 A 在 300 K、單一校正流程下改善；其他樣本與溫度未測。',
        'key_findings': ['只量測樣本 A', '只在 300 K']}, ensure_ascii=False), encoding='utf-8')


class _StubBus:
    """模型替身：舊流程回傳純文字；結構化流程回傳一真一假兩個引文。"""
    calls = []

    def dispatch_task(self, task_name, prompt, **kwargs):
        self.calls.append(task_name)
        if 'ROOTHINKS_ANCHORED_JSON' not in prompt:
            return True, '根據目前文獻資料：樣本 A 只在 300 K 量測。（合成模型替身）'
        critique = 'MODE: critique' in prompt
        return True, json.dumps({
            'interpretation': ('這段只支持樣本 A、300 K、單一校正流程下的改善；不能外推到其他樣本或溫度。'
                               if not critique else '主要限制：只有一個溫度與固定退火時間，替代解釋未排除。'),
            'points': [
                {'text': '改善的範圍被作者明確限制在 300 K。',
                 'evidence': [{'ref': 'E1', 'quote': 'The improvement was observed only at 300 K under one calibration protocol.'}]},
                {'text': '作者聲稱已在多個實驗室重現。',
                 'evidence': [{'ref': 'E1', 'quote': 'replicated in three independent laboratories'}]},
            ],
            'uncertainty': {'level': 'medium', 'reasons': ['只有單一溫度', '退火時間沒有改變']},
            'open_questions': ['在其他溫度是否仍有改善？'],
        }, ensure_ascii=False)


class _NoChain:
    data_root = None

    def query_hybrid_kg_rag(self, **kwargs):
        return {'compact_context': {}}


study_routes.tutor_engine.bus = _StubBus()
study_routes.get_context_chain_service = lambda: _NoChain()

with app.app_context():
    db.create_all()
    db.session.add(Project(project_id=PID, name='Study 共研合成專案', research_title='Synthetic calibration', status='formal'))
    users = {}
    for name, role, system_role in [('researcher', 'owner', 'user'), ('teammate', 'editor', 'user'),
                                    ('professor', 'viewer', 'mentor'), ('outsider', None, 'user')]:
        user = User(username=name, email=f'{name}@example.invalid', system_role=system_role)
        user.set_password('Synthetic123!')
        db.session.add(user)
        db.session.flush()
        users[name] = user.id
        if role:
            db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
    db.session.add(MentorLink(mentor_id=users['professor'], mentee_id=users['researcher']))
    # 與真實流程相同：教授經「教授邀請」加入（viewer、只授予審閱權）→ Study R2 的審閱身分。
    from datetime import datetime, timedelta, timezone
    db.session.add(ProjectInvitation(id='0' * 32, pid=PID, invited_email='professor@example.invalid', created_by=users['researcher'],
                                     accepted_by=users['professor'], kind='professor', role='viewer', sections_json='[]',
                                     token_hash='0' * 64, status='accepted', expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    db.session.add(Paper(paper_id=PAPER, pid=PID, title=PAGES[0][1][0][1], interpretation_status=Paper.STATUS_GOLD_READY))
    db.session.commit()
    _write_paper()
    # R4 段落出處：已保存一版的 Introduction（兩段），供 Manuscript「段落出處」旅程使用。
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    ManuscriptIO.save_block_version(PID, 'introduction', 'Introduction',
                                    '<p>本研究只在 300 K 觀察到樣本 A 的改善，結論不能外推到其他溫度或樣本。</p>'
                                    '<p>我們推測退火時間可能影響結果，這是研究者自己的推論。</p>', updated_by='researcher')
print(json.dumps({'url': f'http://127.0.0.1:{PORT}', 'pid': PID, 'paper_id': PAPER, 'root': str(root), 'users': users}),
      flush=True)
socketio.run(app, host='127.0.0.1', port=PORT, allow_unsafe_werkzeug=True, use_reloader=False, log_output=False)
