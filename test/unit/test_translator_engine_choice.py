# 檔案路徑: roothinks/test/unit/test_translator_engine_choice.py
# 產生時間: 2026-08-04 +08:00
# 版本: v1.0
# 模組定位:
#   翻譯引擎「三入口」（google / nllb / llm）的選擇與隔離測試。
# 背景:
#   原本引擎一律由 _decide_engine() 自動判斷，規則是「NLLB ready 就優先用」
#   （省 API 配額）。在 2 vCPU 的正式站上，這條規則會讓整篇論文翻到
#   Flow B 的 7200s timeout 被砍——省了配額卻永遠翻不完。
#   因此開放使用者指定引擎。
# 主要責任:
#   1. 指定引擎時**只用該引擎**，不得跨引擎降級。
#   2. auto 維持原本的自動判斷與降級鏈，行為不變。
#   3. normalize_engine 擋掉不認得的輸入（該值會進子行程 argv）。
# 安全邊界:
#   - 這組測試最關鍵的斷言是「指定 google 時 NLLB 一次都不能被呼叫」。
#     使用者選 google 幾乎都是為了避開跑不動的 NLLB，
#     偷偷降級回去等於把他要避開的問題又裝回來，而且會再 timeout 一次。
# 驗證方式:
#   - pytest test/unit/test_translator_engine_choice.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.literature.literature_translator import (  # noqa: E402
    ENGINE_AUTO,
    ENGINE_GOOGLE,
    ENGINE_LLM,
    ENGINE_NLLB,
    HybridTranslator,
    normalize_engine,
)


class _FakeNLLB:
    """假的 NLLB：ready=True，用來確認「指定其他引擎時它不該被碰到」。"""

    def __init__(self):
        self.ready = True
        self.calls = 0

    def translate_text(self, text, target_lang):
        self.calls += 1
        return "NLLB結果"

    def translate_texts(self, texts, target_lang):
        self.calls += 1
        return ["NLLB結果" for _ in texts]


class _FakeGoogle:
    def __init__(self):
        self.calls = 0

    def translate(self, text):
        self.calls += 1
        return "谷歌結果"

    def translate_batch(self, texts):
        self.calls += 1
        return ["谷歌結果" for _ in texts]


def _mk(nllb=None, google=None, gemini_available=True):
    tr = HybridTranslator.__new__(HybridTranslator)  # 跳過 __init__，不載真引擎
    tr._nllb = nllb
    tr._quick_translator = google
    tr._gemini_available = gemini_available
    return tr


# --- normalize -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("google", ENGINE_GOOGLE), ("NLLB", ENGINE_NLLB), (" llm ", ENGINE_LLM),
    ("gemini", ENGINE_LLM),                 # 舊稱相容
    ("auto", ENGINE_AUTO), ("", ENGINE_AUTO), (None, ENGINE_AUTO),
    ("bogus", ENGINE_AUTO), ("; rm -rf /", ENGINE_AUTO),
])
def test_normalize_engine(raw, expected):
    """不認得的一律當 auto——這個值會變成子行程的命令列參數。"""
    assert normalize_engine(raw) == expected


# --- 指定引擎時的隔離（本組最重要）------------------------------------------

def test_google_never_touches_nllb(monkeypatch):
    """就算 NLLB 是 ready 的，指定 google 也不能碰它。

    這正是使用者選 google 的目的：避開在這台機器上跑不完的本機模型。
    """
    nllb, google = _FakeNLLB(), _FakeGoogle()
    tr = _mk(nllb=nllb, google=google)
    ok, merged = tr._translate_segments_small_batch(
        ["Some text."], "zho_Hant", ENGINE_GOOGLE)
    assert ok is True
    assert "谷歌結果" in merged
    assert nllb.calls == 0, "指定 google 卻呼叫了 NLLB"


def test_nllb_never_touches_google():
    nllb, google = _FakeNLLB(), _FakeGoogle()
    tr = _mk(nllb=nllb, google=google)
    ok, merged = tr._translate_segments_small_batch(
        ["Some text."], "zho_Hant", ENGINE_NLLB)
    assert "NLLB結果" in merged
    assert google.calls == 0, "指定 nllb 卻呼叫了 Google"


def test_llm_never_touches_local_engines(monkeypatch):
    nllb, google = _FakeNLLB(), _FakeGoogle()
    tr = _mk(nllb=nllb, google=google)
    monkeypatch.setattr(tr, "_translate_gemini", lambda seg, lang: (True, "雲端結果"))
    ok, merged = tr._translate_segments_small_batch(
        ["Some text."], "zho_Hant", ENGINE_LLM)
    assert "雲端結果" in merged
    assert nllb.calls == 0 and google.calls == 0


def test_forced_engine_failure_marks_untranslated_instead_of_falling_back(monkeypatch):
    """指定引擎失敗時標記未翻譯，而不是偷偷換一個引擎。

    降級看似貼心，但會讓使用者以為自己選的引擎能用；
    真正的訊號是「這個引擎翻不動」，要看得見才有辦法處理。
    """
    nllb, google = _FakeNLLB(), _FakeGoogle()
    tr = _mk(nllb=nllb, google=google)
    monkeypatch.setattr(tr, "_translate_gemini", lambda seg, lang: (False, "quota"))
    ok, merged = tr._translate_segments_small_batch(
        ["Fails here."], "zho_Hant", ENGINE_LLM)
    assert ok is True
    assert HybridTranslator.UNTRANSLATED_MARK in merged
    assert "Fails here." in merged
    assert nllb.calls == 0 and google.calls == 0, "失敗後不得跨引擎降級"


# --- auto 行為不變 ----------------------------------------------------------

def test_auto_still_prefers_nllb_when_ready():
    """auto 維持原本規則（NLLB 優先），這次改動不能動到既有行為。"""
    nllb, google = _FakeNLLB(), _FakeGoogle()
    tr = _mk(nllb=nllb, google=google)
    ok, merged = tr._translate_segments_small_batch(
        ["Some text."], "zho_Hant", ENGINE_AUTO)
    assert "NLLB結果" in merged
    assert nllb.calls > 0


def test_default_argument_is_auto():
    """沒帶 engine 的舊呼叫端要維持原行為。"""
    nllb = _FakeNLLB()
    tr = _mk(nllb=nllb, google=None)
    ok, merged = tr._translate_segments_small_batch(["Some text."], "zho_Hant")
    assert "NLLB結果" in merged


# --- 可用性檢查 -------------------------------------------------------------

def test_engine_available_reports_reason():
    """開跑前就要能擋掉，不要讓使用者等兩小時才發現引擎沒裝起來。"""
    tr = _mk(nllb=None, google=None, gemini_available=False)
    for eng in (ENGINE_NLLB, ENGINE_GOOGLE, ENGINE_LLM):
        ok, why = tr.engine_available(eng)
        assert ok is False and why, f"{eng} 不可用時要給理由"

    tr2 = _mk(nllb=_FakeNLLB(), google=_FakeGoogle(), gemini_available=True)
    for eng in (ENGINE_NLLB, ENGINE_GOOGLE, ENGINE_LLM):
        ok, _ = tr2.engine_available(eng)
        assert ok is True
