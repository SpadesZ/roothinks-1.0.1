# Roothinks source maintenance contract
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 檔案路徑: roothinks/test/unit/test_translator_fallback.py
# 產生時間: 2026-07-05 06:00 +08:00
# 版本: v1.0
# 模組定位:
#   literature_translator v1.2「中英夾雜靜默殘留」修復的單元測試。
# 主要責任: 重現並驗收 translator fallback 的成功、失敗與回歸邊界。
#   1. 全引擎失敗的段落帶 [未翻譯] 標記,不再無聲保留英文。
#   2. quick-google 失敗後會嘗試 Gemini 逐段重試。
#   3. _is_mostly_chinese 品質閘判定正確。
# 維護提醒:
#   - monkeypatch 全部引擎,不觸網。
# 驗證方式:
#   - pytest test/unit/test_translator_fallback.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.literature.literature_translator import HybridTranslator, TranslationContext


def _mk_translator(monkeypatch):
    tr = HybridTranslator.__new__(HybridTranslator)  # 跳過 __init__(不載引擎)
    tr._quick_translator = None
    tr._nllb = None
    return tr


def test_failed_segment_gets_untranslated_mark(monkeypatch):
    tr = _mk_translator(monkeypatch)
    monkeypatch.setattr(tr, "_translate_quick_google", lambda seg, lang: (False, "err"))
    monkeypatch.setattr(tr, "translate", lambda seg, ctx: (False, "", "err"))
    monkeypatch.setattr(tr, "_translate_gemini", lambda seg, lang: (False, "err"))

    ok, merged = tr._translate_segments_small_batch(["This failed segment."], "zho_Hant")
    assert ok is True
    assert merged.startswith(HybridTranslator.UNTRANSLATED_MARK)
    assert "This failed segment." in merged


def test_gemini_per_segment_retry_used(monkeypatch):
    tr = _mk_translator(monkeypatch)
    calls = {"gemini": 0}
    monkeypatch.setattr(tr, "_translate_quick_google", lambda seg, lang: (False, "rate limited"))
    monkeypatch.setattr(tr, "translate", lambda seg, ctx: (False, "", "nllb unavailable"))

    def fake_gemini(seg, lang):
        calls["gemini"] += 1
        return True, "成功的中文翻譯結果"

    monkeypatch.setattr(tr, "_translate_gemini", fake_gemini)
    ok, merged = tr._translate_segments_small_batch(["Segment to translate."], "zho_Hant")
    assert calls["gemini"] == 1
    assert merged == "成功的中文翻譯結果"
    assert HybridTranslator.UNTRANSLATED_MARK not in merged


def test_successful_quick_path_untouched(monkeypatch):
    tr = _mk_translator(monkeypatch)
    monkeypatch.setattr(tr, "_translate_quick_google", lambda seg, lang: (True, "翻譯好的內容"))
    ok, merged = tr._translate_segments_small_batch(["Segment."], "zho_Hant")
    assert merged == "翻譯好的內容"


def test_is_mostly_chinese_gate():
    assert HybridTranslator._is_mostly_chinese("這是一段完全中文的翻譯內容沒有問題") is True
    assert HybridTranslator._is_mostly_chinese("This is entirely English text remains") is False
    # 32% 英文殘留案例:中文為主但過半 -> 仍過門檻;過多英文 -> 不過
    mixed_mostly_en = "短中文 " + "long english residue text " * 10
    assert HybridTranslator._is_mostly_chinese(mixed_mostly_en) is False
    assert HybridTranslator._is_mostly_chinese("") is False
