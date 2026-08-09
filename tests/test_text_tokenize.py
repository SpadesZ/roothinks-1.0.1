# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 text tokenize 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_text_tokenize.py -q
# 檔案路徑: tests/test_text_tokenize.py
# 產生時間: 2026-07-19
# 維護提醒: 共用 tokenizer 的 CJK bigram 與拉丁詞行為契約。

from app.services.text_tokenize import tokenize


def test_latin_tokens_kept_whole():
    assert tokenize("Diabetes insulin-resistance 2024") == ["diabetes", "insulin", "resistance", "2024"]


def test_cjk_run_becomes_bigrams():
    assert tokenize("深度學習") == ["深度", "度學", "學習"]


def test_cjk_substring_query_overlaps_document():
    doc = set(tokenize("本文提出多閾值信心設定框架"))
    query = set(tokenize("多閾值"))
    assert query & doc, "中文子字串查詢必須與文件 token 有交集"


def test_mixed_latin_and_cjk():
    tokens = tokenize("COVID疫苗 efficacy")
    assert "covid" in tokens
    assert "疫苗" in tokens
    assert "efficacy" in tokens


def test_single_cjk_char_kept():
    assert tokenize("肝") == ["肝"]


def test_empty_and_none():
    assert tokenize("") == []
    assert tokenize(None) == []
