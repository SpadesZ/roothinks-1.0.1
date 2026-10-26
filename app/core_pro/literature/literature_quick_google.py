# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_quick_google.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 使用者明確選「Google 翻譯」時，以 Google 翻譯行動版頁面翻一段文字；取代 deep-translator 的 GoogleTranslator。
# 上下游: literature_translator.HybridTranslator 呼叫本層 -> translate.google.com/m（HTTPS）-> 回傳譯文或明確錯誤。
# 維護邊界: 只送出呼叫端給的單段文字；每次請求都有逾時；回應找不到譯文就報錯，不回傳原文冒充成功。
# 驗證: python -m pytest test/unit/test_quick_google_translate.py -q
#路徑(./app/core_pro/literature/literature_quick_google.py) #版本 v0.1 #更版時間 20261026
#功能概要:
#1. 協定與 deep-translator 1.11.4 的 GoogleTranslator 相同：GET /m，參數 tl、sl、q，取 div.t0，否則 div.result-container。
#2. 為什麼不用 deep-translator：PyPI 的 1.11.4 被 PYSEC-2022-252 掃描標為受影響（通報只列已下架的 1.8.5，
#   但範圍沒有結束版本），而且它的請求沒有逾時，連線卡住時翻譯工作會一直等。只用到它約 40 行的功能。
#3. 刻意的差異：deep-translator 把空譯文當成功回傳 ''（批次路徑會把空字串、甚至 'None' 當譯文）；本檔改為報錯，
#   讓呼叫端走既有的逐段補救與「未翻譯」標記。
import re
from html.parser import HTMLParser

import requests

ENDPOINT = 'https://translate.google.com/m'
# 與 deep-translator 相同：輸入長度必須 < 5000；超過就明確失敗，不靜默截斷。
MAX_CHARS = 5000
# 與 deep-translator 相同的查找順序。
RESULT_CLASSES = ('t0', 'result-container')
DEFAULT_TIMEOUT = (5, 30)


class QuickTranslateError(RuntimeError):
    """Google 翻譯沒有給出可用譯文；呼叫端據此回報失敗，不把原文當譯文。"""


class _DivText(HTMLParser):
    """取第一個 class 含 wanted 的 <div> 內所有文字；等同 BeautifulSoup 的 find().get_text(strip=True)。"""

    def __init__(self, wanted):
        super().__init__(convert_charrefs=True)
        self.wanted, self.depth, self.found, self.closed, self.parts = wanted, 0, False, False, []

    def handle_starttag(self, tag, attrs):
        if self.closed or tag != 'div':
            return
        if self.depth:
            self.depth += 1
        elif self.wanted in (dict(attrs).get('class') or '').split():
            self.depth, self.found = 1, True

    def handle_endtag(self, tag):
        if self.depth and tag == 'div':
            self.depth -= 1
            self.closed = self.depth == 0

    def handle_data(self, data):
        if self.depth and not self.closed:
            self.parts.append(data)

    def text(self):
        return ''.join(part.strip() for part in self.parts if part.strip())


def extract_translation(body):
    """回傳頁面中的譯文；兩種 class 都找不到時回傳 None。"""
    for wanted in RESULT_CLASSES:
        parser = _DivText(wanted)
        parser.feed(body)
        parser.close()
        if parser.found:
            return parser.text()
    return None


class QuickGoogleTranslator:
    def __init__(self, source='en', target='zh-TW', timeout=DEFAULT_TIMEOUT, session=None):
        self.source, self.target, self.timeout = source, target, timeout
        self._session = session or requests

    def translate(self, text):
        if not isinstance(text, str):
            raise TypeError('text must be str')
        if len(text) >= MAX_CHARS:
            raise QuickTranslateError(f'單段超過 {MAX_CHARS - 1} 字元，請先切段')
        text = text.strip()
        if not text:
            return text
        response = self._session.get(ENDPOINT, params={'tl': self.target, 'sl': self.source, 'q': text},
                                     timeout=self.timeout)
        try:
            if response.status_code == 429:
                raise QuickTranslateError('Google 翻譯回應 429：請求過多')
            if not 200 <= response.status_code <= 299:
                raise QuickTranslateError(f'Google 翻譯回應 HTTP {response.status_code}')
            body = response.text
        finally:
            response.close()
        translated = extract_translation(body)
        if translated is None:
            raise QuickTranslateError('回應中找不到譯文')
        if not translated and re.search(r'\w', text):
            raise QuickTranslateError('譯文是空的')
        return translated

    def translate_batch(self, batch):
        """與 deep-translator 相同：逐段呼叫 translate，任一段失敗就整批失敗，交給呼叫端逐段補救。"""
        if not batch:
            raise QuickTranslateError('沒有要翻譯的段落')
        return [self.translate(text) for text in batch]

