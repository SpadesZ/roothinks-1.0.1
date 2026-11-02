# Roothinks source maintenance contract
# 目的：R7 閱讀介面合成論文（test/integration_e2e/reading_ui_fixture_paper.py）的資料契約。
#       瀏覽器檢查 reading_ui_r7_browser.cjs 依賴這裡的每一種情境；情境被改掉時這裡先失敗，而不是瀏覽器檢查「全綠但沒測到」。
# 邊界：只寫 pytest tmp_path；不啟動應用程式、不連外。
# 驗證：python -m pytest test/unit/test_reading_ui_r7_fixture.py -q
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'test' / 'integration_e2e'))
import reading_ui_fixture_paper as paper  # noqa: E402

PID = 'STUDYCO-p'
# 簡體專用字（繁體不用）；譯文是臺灣繁中，不得出現。
SIMPLIFIED = re.compile('[这们为说时对发数据译显码页图无关闭选择简获载错误请点击记录题读写设输认证计网络软统应该过还进现个来没种样实验测论献结讨参态状资务专项师导处开启动体变节检宽长温]')


def _blocks():
    with_zh, _ = paper.build_content(PID)
    return [(page['page'], idx, block) for page in with_zh for idx, block in enumerate(page['blocks'], start=1)]


def test_paper_sorts_after_the_journey_paper():
    """既有旅程點「第一個閱讀全文」；清單依資料夾名排序，本篇必須排在 synthetic_calibration 之後。"""
    assert paper.PAPER_ID > 'synthetic_calibration'


def test_structure_covers_every_reader_case():
    blocks = _blocks()
    pages = {page for page, _, _ in blocks}
    kinds = {block['type'] for _, _, block in blocks}
    assert len(pages) >= 3
    assert {'MainTitle', 'Subtitle', 'SubSubtitle', 'Title', 'Body', 'Equation', 'Figure', 'Table', 'Caption',
            'Footnote', 'Reference', 'Header'} <= kinds
    seq_ids = [block['seq_id'] for _, _, block in blocks]
    assert len(seq_ids) == len(set(seq_ids))
    assert all(re.fullmatch(r'[A-Za-z0-9._-]{1,120}', s) for s in seq_ids)  # 與 study_routes._safe_seq_id 相同
    equations = [block for _, _, block in blocks if block['type'] == 'Equation']
    assert any(block.get('equation_failed') for block in equations)
    assert any('\\SI' in block.get('latex', '') for block in equations), 'KaTeX 不支援的指令'
    assert any(len(block.get('latex', '')) > 200 for block in equations), '窄螢幕會溢出的長公式'


def test_translation_cases():
    blocks = _blocks()
    text = [b for _, _, b in blocks if b['type'] in ('Body', 'Reference', 'Footnote', 'Caption')]
    assert [b for b in text if 'content_zh' not in b], '缺譯段落'
    # 中文每字資訊量約為英文字母的 2–3 倍；字數 1.5 倍以上的譯文在畫面上約高出原文 3 倍。
    assert any(len(b.get('content_zh', '')) > 1.5 * len(b['content']) for b in text), '譯文遠長於原文'
    assert any(len(b['content']) < 12 and b.get('content_zh') for b in text), '會被黏到上一列的短碎片'
    for _, _, block in blocks:
        assert not SIMPLIFIED.search(block.get('content_zh', '')), block['content_zh']


def test_symbols_citations_and_long_tokens_are_present_verbatim():
    joined = ' '.join(block['content'] for _, _, block in _blocks())
    for token in ('$\\kappa$', 'µm', '10⁻³', 'W m⁻¹ K⁻¹', '∂ω₂D/∂ε', '[12, 14]', '(Smith et al., 2020)',
                  paper.LONG_URL, paper.LONG_DOI, paper.LONG_FILE, 'https://doi.org/'):
        assert token in joined, token


def test_write_paper_matches_loader_layout_and_merges_vns(tmp_path):
    seg = tmp_path / PID / 'literature' / 'segmentation'
    seg.mkdir(parents=True)
    (seg / f'{PID}_vns.json').write_text(json.dumps({'papers': {'other_paper': {'records': [{'seq_id': 'keep'}]}}}),
                                         encoding='utf-8')
    paper_dir = paper.write_paper(tmp_path, PID)
    trans = json.loads((paper_dir / '05_interprets' / 'full_text_trans.json').read_text(encoding='utf-8'))
    plain = json.loads((paper_dir / '05_interprets' / 'fusion' / 'full_text.json').read_text(encoding='utf-8'))
    assert [p['page'] for p in trans['content']] == [p['page'] for p in plain['content']]
    assert not any('content_zh' in b for p in plain['content'] for b in p['blocks'])
    vns = json.loads((seg / f'{PID}_vns.json').read_text(encoding='utf-8'))
    assert vns['papers']['other_paper']['records'] == [{'seq_id': 'keep'}], '不可覆蓋別篇的切塊紀錄'
    records = vns['papers'][paper.PAPER_ID]['records']
    assert {r['type'] for r in records} == {'Figure', 'Table', 'Equation'}
    for record in records:
        png = Path(record['png_path'])
        assert png.is_file() and png.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
        assert str(png).startswith(str(tmp_path))
