# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_quick_google_translate.py
# 目的: 自有的 Google 翻譯客戶端必須與 deep-translator 1.11.4 用同一協定，每次請求都有逾時，
#       找不到譯文時明確失敗（不回傳原文冒充譯文），且產品不再依賴 deep-translator。
# 邊界: 全部用假 session，不連網。與 deep-translator 的逐字差異測試在交付證據中（2,000 個隨機 HTML）。
# 驗證: python -m pytest test/unit/test_quick_google_translate.py -q
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from app.core_pro.literature import literature_quick_google as quick

ROOT = Path(__file__).resolve().parents[2]
TEXT = 'The measured improvement applies only to sample A.'


class FakeSession:
    def __init__(self, body='', status=200):
        self.body, self.status, self.calls, self.closed = body, status, [], 0

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append(dict(url=url, params=dict(params), timeout=timeout, extra=kwargs))
        return SimpleNamespace(status_code=self.status, text=self.body, close=self._close)

    def _close(self):
        self.closed += 1


def translate(body, text=TEXT, status=200):
    session = FakeSession(body, status)
    return quick.QuickGoogleTranslator(session=session).translate(text), session


def test_same_protocol_as_deep_translator_and_always_a_timeout():
    result, session = translate('<html><body><div class="result-container">量測到的改善只適用於樣本 A。</div></body></html>')
    assert result == '量測到的改善只適用於樣本 A。'
    assert session.calls == [dict(url='https://translate.google.com/m', params={'tl': 'zh-TW', 'sl': 'en', 'q': TEXT},
                                  timeout=(5, 30), extra={})]
    assert session.closed == 1


@pytest.mark.parametrize('body,expected', [
    ('<div class="result-container">後備</div><div class="t0">優先</div>', '優先'),
    ('<div class="x result-container y">多 class</div>', '多 class'),
    ('<div class="result-container">A &amp; B<br>第二行 <span> x </span>&#39;q&#39;</div>', "A & B第二行x'q'"),
    ('<div class="result-container"><div>inner</div> tail <div><div>deep</div></div></div><div>outside</div>', 'innertaildeep'),
    ('<script>var x = "<div class=\'result-container\'>fake</div>";</script><div class="result-container">real</div>', 'real'),
    ('<DIV CLASS="result-container">大寫</DIV>', '大寫'),
])
def test_extraction_matches_beautifulsoup_get_text_strip(body, expected):
    assert translate(body)[0] == expected


@pytest.mark.parametrize('body,status', [
    ('<p>沒有譯文</p>', 200),
    ('<div class="result-container"></div>', 200),
    ('<div class="result-container">ok</div>', 429),
    ('<div class="result-container">ok</div>', 500),
    ('<div class="result-container">ok</div>', 301),
])
def test_failures_raise_instead_of_returning_the_source_text(body, status):
    session = FakeSession(body, status)
    with pytest.raises(quick.QuickTranslateError):
        quick.QuickGoogleTranslator(session=session).translate(TEXT)
    assert session.closed == 1


def test_length_limit_and_blank_input_send_nothing():
    session = FakeSession('<div class="result-container">ok</div>')
    translator = quick.QuickGoogleTranslator(session=session)
    assert translator.translate('a' * 4999) == 'ok'
    with pytest.raises(quick.QuickTranslateError):
        translator.translate('a' * 5000)
    assert translator.translate('   \n ') == ''
    assert len(session.calls) == 1


def test_unchanged_translation_is_returned_as_is():
    assert translate(f'<div class="result-container">{TEXT}</div>')[0] == TEXT


def test_hybrid_translator_uses_the_in_repo_client(monkeypatch):
    from app.core_pro.literature import literature_nllb, literature_translator
    monkeypatch.setattr(literature_nllb.NLLBTranslator, '__new__', lambda cls: SimpleNamespace(ready=False))
    hybrid = literature_translator.HybridTranslator()
    assert isinstance(hybrid._quick_translator, quick.QuickGoogleTranslator)
    assert hybrid.engine_available(literature_translator.ENGINE_GOOGLE) == (True, '')
    session = FakeSession('<div class="result-container">譯文</div>')
    hybrid._quick_translator._session = session
    assert hybrid._translate_quick_google(TEXT, 'zho_Hant') == (True, '譯文')
    session.body = '<p>blocked</p>'
    ok, message = hybrid._translate_quick_google(TEXT, 'zho_Hant')
    assert ok is False and message != TEXT


def test_product_no_longer_depends_on_deep_translator():
    sources = [p for p in (ROOT / 'app').rglob('*.py')]
    assert not [p for p in sources if re.search(r'^\s*(from|import)\s+deep_translator', p.read_text(encoding='utf-8'), re.M)]
    lock = (ROOT / 'requirements-py312.lock').read_text(encoding='utf-8')
    for name in ('deep-translator', 'beautifulsoup4', 'soupsieve'):
        assert not re.search(rf'^{name}==', lock, re.M)
    assert not re.search(r'^deep-translator', (ROOT / 'requirements.txt').read_text(encoding='utf-8'), re.M)


class PerTextSession(FakeSession):
    """每段文字各自決定回應；用來模擬批次中某一段沒有譯文。"""

    def __init__(self, answers):
        super().__init__()
        self.answers = answers

    def get(self, url, params=None, timeout=None, **kwargs):
        self.body = self.answers[params['q']]
        return super().get(url, params=params, timeout=timeout, **kwargs)


def test_translate_batch_is_sequential_and_fails_as_a_whole():
    session = PerTextSession({'one': '<div class="result-container">一</div>', 'two': '<div class="result-container">二</div>',
                              'bad': '<div class="result-container"></div>'})
    translator = quick.QuickGoogleTranslator(session=session)
    assert translator.translate_batch(['one', 'two']) == ['一', '二']
    assert [call['params']['q'] for call in session.calls] == ['one', 'two']
    with pytest.raises(quick.QuickTranslateError):
        translator.translate_batch(['one', 'bad'])
    with pytest.raises(quick.QuickTranslateError):
        translator.translate_batch([])


def test_empty_google_result_in_a_batch_is_marked_untranslated_not_silently_empty(monkeypatch):
    """deep-translator 回傳 '' 時，批次路徑會把空字串當譯文；現在必須落到逐段補救並標記未翻譯。"""
    from app.core_pro.literature.literature_translator import HybridTranslator
    hybrid = HybridTranslator.__new__(HybridTranslator)
    hybrid._nllb, hybrid._gemini_available = None, False
    hybrid._quick_translator = quick.QuickGoogleTranslator(session=PerTextSession({
        'Good sentence.': '<div class="result-container">好句子。</div>',
        'Blocked sentence.': '<div class="result-container"></div>'}))
    monkeypatch.setattr(hybrid, 'translate', lambda seg, ctx: (False, '', 'error'))
    monkeypatch.setattr(hybrid, '_translate_gemini', lambda seg, lang: (False, 'no llm'))
    ok, merged = hybrid._translate_segments_small_batch(['Good sentence.', 'Blocked sentence.'], 'zho_Hant')
    lines = merged.split('\n')
    assert lines[0] == '好句子。'
    assert lines[1].startswith(HybridTranslator.UNTRANSLATED_MARK) and 'Blocked sentence.' in lines[1]
