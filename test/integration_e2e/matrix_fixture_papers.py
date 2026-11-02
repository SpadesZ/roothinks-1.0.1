# Roothinks source maintenance contract
# 目的：R7 多篇比較矩陣的合成資料與比較器替身；只由 study_coresearch_fixture.py 合成站匯入（兩行）。
# 內容：另一個合成專案 STUDYMX-p（研究者是 owner），四篇自寫的合成論文、一筆舊版單一比較記錄與一筆 papers_comp 舊檔。
#       四篇論文的資料夾名稱刻意與「研究者勾選順序」不同，用來驗證欄位順序來自勾選而不是清單排序。
# 模型替身：比較器不連外。新版（ROOTHINKS_MATRIX_PAIR/2）回傳含真引文、捏造引文、太短引文、錯段落引文、
#           缺資料卻排名、條件不同卻比大小的回覆，用來驗證伺服器會擋下或標示；舊版提示詞回傳舊格式。
# 邊界：只寫入合成站的 tmp 根目錄；不得作正式服務使用。
import json
import re
import threading
import time
from pathlib import Path

MX_PID = 'STUDYMX-p'
BASELINE = 'mx_c_calibration'
# 研究者在畫面上的勾選順序（第一篇是基準）；資料夾排序是 a、b、c、d，兩者刻意不同。
SELECTION_ORDER = ['mx_c_calibration', 'mx_b_annealing', 'mx_a_lowtemp', 'mx_d_position']
FAKE_QUOTE = 'The improvement was replicated across five independent sample batches.'
FAKE_ONLY_QUOTE = 'The authors acknowledge that no experiments were performed in this paper.'

_INTRO = [
    'Thermal response measurements in synthetic samples are sensitive to how the reference plane is defined, how the '
    'instrument is calibrated, and how the sample is prepared before measurement. Earlier synthetic studies reported '
    'single-condition improvements, but the preparation history of each sample was rarely varied in a controlled way. '
    'This makes it difficult for readers to tell whether an improvement belongs to the calibration protocol or to the '
    'way the sample was annealed before it reached the instrument.',
    'A second difficulty is that published improvements are often stated relative to different baselines. Some studies '
    'use an uncalibrated instrument as the reference, others use a previously calibrated run, and a few compare against '
    'a simulated response. When the reference changes, a larger percentage does not necessarily mean a larger physical '
    'effect, and readers must reconstruct the comparison from the methods section rather than from the abstract.',
    'Temperature is a third source of variation. Many measurements are performed near room temperature for practical '
    'reasons, while cryogenic measurements are reported in absolute units rather than as relative changes. Combining '
    'results from both regimes in one summary table can therefore suggest a trend that no single study has tested. '
    'In this synthetic study we keep the temperature fixed so that only one preparation variable changes at a time.',
    'We also keep the calibration protocol identical to an earlier synthetic baseline. The protocol fixes the '
    'reference plane, repeats each measurement three times, and reports the drift of the calibration across the '
    'repeated runs. Keeping the protocol constant means that any difference we observe can be attributed to the '
    'preparation step under study, within the limits of a single sample batch, which we discuss at the end.',
    'The remainder of this paper is organised as follows. The methods section describes the annealing conditions and '
    'the measurement sequence. The results section reports the relative improvement for each condition, and the '
    'limitations section lists what this design cannot show. No measurement outside the stated conditions is reported '
    'or implied, and readers should not extend the results to other temperatures or to other sample batches.',
]

PAPERS = {
    'mx_b_annealing': {
        'title': 'Annealing Time Dependence of Thermal Response in Synthetic Samples',
        'pages': [
            (1, [('title', 'Annealing Time Dependence of Thermal Response in Synthetic Samples'),
                 ('body', 'We examine whether annealing time changes the thermal response of synthetic sample A. '
                          'Three annealing times were compared under a single calibration protocol at 300 K.'),
                 ('title', 'Introduction'),
                 ('body', _INTRO[0]), ('body', _INTRO[1]), ('body', _INTRO[2])]),
            (2, [('body', _INTRO[3]), ('body', _INTRO[4]),
                 ('title', 'Methods'),
                 ('body', 'Samples were annealed for 20, 40, and 60 minutes before measurement. All measurements were '
                          'taken at 300 K using the calibration protocol of the baseline study.'),
                 ('body', 'Each annealing condition was measured in three repeated runs on a single sample batch.')]),
            (3, [('title', 'Results'),
                 ('body', 'At 300 K, the 60-minute anneal improved thermal response by 18 percent relative to the '
                          'uncalibrated baseline, while the 20-minute anneal improved it by 9 percent.'),
                 ('body', 'The improvement increased monotonically with annealing time across the three tested conditions.'),
                 ('title', 'Limitations'),
                 ('body', 'Only one sample batch was used, so batch-to-batch variation was not assessed. '
                          'Temperatures other than 300 K were not tested.')]),
        ],
    },
    'mx_a_lowtemp': {
        'title': 'Thermal Response of Sample A at Cryogenic Temperature',
        'pages': [
            (1, [('title', 'Thermal Response of Sample A at Cryogenic Temperature'),
                 ('body', 'This study reports the thermal response of sample A measured at 77 K. '
                          'The work does not attempt to reproduce room-temperature conditions.')]),
            (2, [('title', 'Methods'),
                 ('body', 'Measurements were performed at 77 K in a liquid-nitrogen cryostat with a two-point calibration.'),
                 ('title', 'Results'),
                 ('body', 'The thermal response increased by 0.8 mW/K after calibration at 77 K.')]),
        ],
    },
    'mx_d_position': {
        'title': 'Reporting Calibration Conditions: A Position Paper',
        'pages': [
            (1, [('title', 'Reporting Calibration Conditions: A Position Paper'),
                 ('body', 'We argue that thermal response studies should report calibration protocol, temperature and '
                          'annealing time together, because results obtained under different conditions cannot be '
                          'compared directly.')]),
            (2, [('title', 'Recommendations'),
                 ('body', 'Authors should state the measurement temperature and the units of every reported change.')]),
        ],
    },
}

# 每個維度關鍵字 → 每篇論文的替身回覆。status／quotes 依合成論文內容寫；刻意放入應被伺服器擋下的內容。
_CELLS = {
    BASELINE: {
        '問題': ('reported', '量測樣本 A 在單一校正流程下的熱響應，範圍只限 300 K。',
                 [('p1-b2', 'We report a bounded measurement of thermal response for sample A.')]),
        '方法': ('reported', '固定參考平面，三次重複量測並報告校正漂移。',
                 [('p2-b2', 'All measurements used the same reference plane.')]),
        '資料': ('reported', '只有樣本 A，退火時間固定 40 分鐘。',
                 [('p2-b3', 'Sample A was prepared with a fixed annealing time of 40 minutes; annealing time was not varied.')]),
        '結果': ('reported', '300 K 下相對未校正基線改善 12%；其他溫度未測。',
                 [('p3-b2', 'Sample A improved by 12 percent at 300 K relative to the uncalibrated baseline.')]),
        # 引文其實在 p3-b3；故意標錯段落，伺服器應改正並註明。
        '限制': ('reported', '結果可能取決於被固定的退火時間。',
                 [('p1-b2', 'The result may depend on the annealing time, which this study held constant.')]),
    },
    'mx_b_annealing': {
        '問題': ('reported', '退火時間是否改變樣本 A 的熱響應。',
                 [('p1-b2', 'We examine whether annealing time changes the thermal response of synthetic sample A.')]),
        # 長文字：驗證畫面不靜默截斷（伺服器存全文、畫面折疊並提供「展開」）。
        '方法': ('reported', '比較 20、40、60 分鐘三種退火時間，沿用基準論文的校正流程與參考平面；每個條件三次重複量測，'
                 '量測溫度固定在 300 K，所以退火時間是唯一刻意改變的製備變因。作者說明這樣設計是為了把校正流程與樣本製備的效果分開，'
                 '但只用一批樣本，無法排除批次差異；退火後到量測前的等待時間沒有說明。這段文字刻意超過畫面折疊長度。',
                 [('p2-b4', 'Samples were annealed for 20, 40, and 60 minutes before measurement.')]),
        '資料': ('reported', '每個條件三次重複量測，只有單一批樣本。',
                 [('p2-b5', 'Each annealing condition was measured in three repeated runs on a single sample batch.')]),
        '結果': ('reported', '300 K 下 60 分鐘退火改善 18%，20 分鐘改善 9%；作者宣稱已在五批樣本重現。',
                 [('p3-b2', 'the 60-minute anneal improved thermal response by 18 percent relative to the uncalibrated baseline'),
                  ('p3-b3', FAKE_QUOTE)]),
        '限制': ('reported', '只用一批樣本，未評估批間差異；只測 300 K。',
                 [('p3-b5', 'Only one sample batch was used, so batch-to-batch variation was not assessed.')]),
    },
    'mx_a_lowtemp': {
        '問題': ('reported', '樣本 A 在 77 K 的熱響應。',
                 [('p1-b2', 'This study reports the thermal response of sample A measured at 77 K.')]),
        '方法': ('reported', '77 K 液氮恆溫器、兩點校正。',
                 [('p2-b2', 'Measurements were performed at 77 K in a liquid-nitrogen cryostat with a two-point calibration.'),
                  ('p2-b2', '77 K')]),
        '資料': ('uncertain', '樣本數與重複次數沒有說明。', []),
        '結果': ('reported', '77 K 下熱響應增加 0.8 mW/K。',
                 [('p2-b4', 'The thermal response increased by 0.8 mW/K after calibration at 77 K.')]),
        '限制': ('not_reported', '', []),
    },
    'mx_d_position': {
        '問題': ('reported', '主張校正流程、溫度與退火時間應一起報告。',
                 [('p1-b2', 'We argue that thermal response studies should report calibration protocol, temperature and '
                            'annealing time together')]),
        '方法': ('not_reported', '', []),
        '資料': ('not_reported', '', []),
        '結果': ('not_reported', '', []),
        # 模型說「有報告」，但唯一引文是捏造的；伺服器應降為不確定。
        '限制': ('reported', '作者承認本文沒有做任何實驗。', [('p2-b2', FAKE_ONLY_QUOTE)]),
    },
}
# 配對層級：可比性與 AI 描述的差異；其中幾則故意在缺資料或條件不同時「排名」，伺服器必須擋下。
_PAIR = {
    'mx_b_annealing': {'結果': ('comparable', '', '兩篇都在 300 K、以相對未校正基線的百分比報告；對照論文另外改變退火時間。')},
    'mx_a_lowtemp': {
        '結果': ('not_comparable', '量測溫度不同（77 K 對 300 K），而且單位不同（mW/K 對百分比），不能直接比較大小。',
                 '對照論文的改善幅度明顯較小，基準論文較優。'),
        '限制': ('unclear', '', '基準論文較嚴謹，對照論文沒有限制。'),
    },
    'mx_d_position': {
        '資料': ('unclear', '', '對照論文的資料較少，證據較弱。'),
        '結果': ('unclear', '', '基準論文的結果明顯優於對照論文。'),
    },
}
_KEYS = ('問題', '方法', '資料', '結果', '限制')


def _key_for(label):
    return next((k for k in _KEYS if k in str(label)), None)


def _side(paper_id, key):
    status, text, quotes = _CELLS.get(paper_id, {}).get(key, ('uncertain', '合成替身沒有這個維度的資料。', []))
    return {'status': status, 'text': text, 'quotes': [{'ref': r, 'quote': q} for r, q in quotes],
            'uncertain_reason': (text or '合成替身沒有這個維度的資料。') if status == 'uncertain' else ''}


class MatrixStubBus:
    """比較器替身：依提示詞版本回覆；每次呼叫記錄到 tmp 根目錄，供瀏覽器檢查核對模型呼叫次數與輸入涵蓋。"""

    def __init__(self, root, titles):
        self.root = Path(root)
        self.titles = titles
        self.calls = []
        self.lock = threading.Lock()

    def _log(self, entry):
        # 比較器以多執行緒並行呼叫；加鎖並以替換寫入，避免紀錄檔被同時寫壞。
        with self.lock:
            self.calls.append(entry)
            tmp = self.root / 'matrix_stub_calls.json.tmp'
            tmp.write_text(json.dumps(self.calls, ensure_ascii=False, indent=1), encoding='utf-8')
            tmp.replace(self.root / 'matrix_stub_calls.json')

    def dispatch_task(self, task_name, prompt, **kwargs):
        markers = {'annealing_methods': 'Samples were annealed for 20, 40, and 60 minutes' in prompt,
                   'annealing_results': '18 percent' in prompt,
                   'annealing_limitations': 'Only one sample batch was used' in prompt,
                   'baseline_results': 'Sample A improved by 12 percent' in prompt}
        if 'ROOTHINKS_MATRIX_PAIR/2' in prompt:
            target = re.search(r'TARGET_PAPER_ID: (\S+)', prompt).group(1)
            baseline = re.search(r'BASELINE_PAPER_ID: (\S+)', prompt).group(1)
            dims = json.loads(re.search(r'DIMENSIONS_JSON: (\[.*?\])\n', prompt).group(1))
            self._log({'t': time.time(), 'task': task_name, 'format': 'v2', 'target': target, 'prompt_chars': len(prompt),
                       'markers': markers})
            out = []
            for dim in dims:
                key = _key_for(dim['label'])
                comp, reason, relation = _PAIR.get(target, {}).get(key, ('comparable', '', '描述方式不同，細節請對照原文。'))
                out.append({'dimension_id': dim['id'], 'baseline': _side(baseline, key), 'target': _side(target, key),
                            'comparability': {'status': comp, 'reason': reason}, 'relation': relation,
                            'significance': 'High'})
            return True, json.dumps({'dimensions': out, 'overall_summary': '合成替身的比較摘要。'}, ensure_ascii=False)
        # 舊版提示詞（R6 以前）：回傳舊格式五個比較點；只用來重現改版前畫面。
        def _paper(label):
            found = re.search(r'\[Paper ' + label + r'\]:\s*Title: (.*)', prompt)
            name = found.group(1).strip() if found else ''
            return self.titles.get(name) or (name if name in self.titles.values() else '')  # 舊路由把 paper_id 當標題
        target, baseline = _paper(r'B \(Target\)'), _paper(r'A \(Baseline\)')
        self._log({'t': time.time(), 'task': task_name, 'format': 'legacy', 'target': target, 'prompt_chars': len(prompt),
                   'markers': markers})
        points = []
        for dim in ('研究問題與目標', '方法與系統設計', '資料與實驗設定', '結果與證據強度', '限制與風險'):
            key = _key_for(dim)
            base, tgt = _side(baseline, key), _side(target, key)
            relation = _PAIR.get(target, {}).get(key, ('', '', '描述方式不同，細節請對照原文。'))[2]
            points.append({'dimension': dim, 'baseline_view': base['text'] or 'N/A', 'target_view': tgt['text'] or 'N/A',
                           'synthesis': relation, 'significance': 'High'})
        return True, json.dumps({'comparison_points': points, 'overall_summary': '合成替身的比較摘要。'}, ensure_ascii=False)


def _write(paper_dir, paper_id, title, pages):
    content = [{'page': page, 'blocks': [{'type': kind, 'content': text, 'seq_id': f'{paper_id}_p{page}_{i}'}
                                         for i, (kind, text) in enumerate(blocks, start=1)]} for page, blocks in pages]
    fusion = paper_dir / '05_interprets' / 'fusion'
    fusion.mkdir(parents=True)
    (fusion / 'full_text.json').write_text(json.dumps({'paper_id': paper_id, 'metadata': {'title': title}, 'content': content},
                                                      ensure_ascii=False), encoding='utf-8')


def _legacy_records(study_dir, titles):
    """R6 以前的兩種舊記錄：matrix_history 單一比較（欄位被截在 220 字）與更舊的 papers_comp。"""
    old_points = [{'dimension': d, 'baseline_view': b, 'target_view': t, 'synthesis': s, 'significance': 'Medium'}
                  for d, b, t, s in [('研究問題與目標', '量測樣本 A 的熱響應。', '退火時間是否改變熱響應。', '問題相近。'),
                                     ('結果與證據強度', '300 K 下改善 12%。', 'N/A', '資料不足，待補充比較證據。')]]
    comparison = {'target_paper_id': 'mx_b_annealing', 'target_paper_title': titles['mx_b_annealing'],
                  'comparison_points': old_points, 'overall_summary': '舊版比較', 'status': 'completed', 'quality_mode': 'llm',
                  'analysis': '【研究問題與目標】\n基準: 量測樣本 A 的熱響應。'}
    data = {'pid': MX_PID, 'criteria': 'General', 'baseline': {'id': BASELINE, 'title': titles[BASELINE]},
            'comparisons': [comparison], 'status': 'completed', 'success_count': 1, 'failure_count': 0}
    record = {'matrix_id': 'mx_1760000000_legacy01', 'pid': MX_PID, 'criteria': 'General',
              'paper_ids': [BASELINE, 'mx_b_annealing'], 'paper_titles': [titles[BASELINE], titles['mx_b_annealing']],
              'created_at': '2026-10-01 09:00:00', 'elapsed': 3.2, 'display_name': '舊版矩陣（R6）', 'is_legacy': False,
              'data': data}
    hist = study_dir / 'matrix_history'
    hist.mkdir(parents=True)
    (hist / 'mx_1760000000_legacy01.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    (hist / 'latest.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    (study_dir / 'latest_matrix.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    comp = study_dir / 'papers_comp'
    comp.mkdir()
    oldest = {'criteria': 'Methodology', 'baseline': {'id': BASELINE, 'title': titles[BASELINE]},
              'comparisons': [{'target_paper_id': 'mx_a_lowtemp', 'target_paper_title': titles['mx_a_lowtemp'],
                               'content': '舊版純文字比較：兩篇都量測樣本 A。'}]}
    (comp / 'v16_pair.json').write_text(json.dumps(oldest, ensure_ascii=False), encoding='utf-8')


def install_matrix_papers(root, base_pages, study_routes):
    """在 app context 內呼叫：建立 STUDYMX-p、四篇論文、舊記錄，並把比較器換成替身。"""
    from app import db
    from app.models import Paper, Project, User, WorkspaceMember
    root = Path(root)
    papers = dict(PAPERS)
    papers[BASELINE] = {'title': base_pages[0][1][0][1],
                        'pages': [(page, [(kind, text) for kind, text, _zh in blocks]) for page, blocks in base_pages]}
    db.session.add(Project(project_id=MX_PID, name='矩陣合成專案', research_title='Synthetic matrix', status='formal'))
    owner = User.query.filter_by(username='researcher').first()
    db.session.add(WorkspaceMember(user_id=owner.id, pid=MX_PID, role='owner'))
    for paper_id, spec in papers.items():
        db.session.add(Paper(paper_id=paper_id, pid=MX_PID, title=spec['title'], interpretation_status=Paper.STATUS_GOLD_READY))
        _write(root / MX_PID / 'literature' / 'papers' / paper_id, paper_id, spec['title'], spec['pages'])
    db.session.commit()
    titles = {pid: spec['title'] for pid, spec in papers.items()}
    _legacy_records(root / MX_PID / 'study', titles)
    study_routes.matrix_engine.comparator.bus = MatrixStubBus(root, {t: p for p, t in titles.items()})
