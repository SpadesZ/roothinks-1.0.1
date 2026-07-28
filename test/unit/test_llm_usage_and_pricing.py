"""
路徑(./test/unit/test_llm_usage_and_pricing.py)
版本 v1.0
更版時間 20260728

模組定位：LLM token 用量記錄與費用換算的測試。
背景：文獻解析整條線都在打雲端 LLM（task_4cv 綁 gpt-4.1），但系統原本
      完全沒有記錄用量——adapter 拿得到 response.usage 卻直接丟掉，
      使用者無從得知跑一篇要多少錢。
重點：查不到單價時必須回 None 而不是 0。顯示 0 會讓使用者以為不用錢，
      比顯示「未設定」危險得多，所以這件事有獨立的斷言把關。
"""
import sqlite3

import pytest

from app.llm_service import llm_pricing, llm_usage


# --- 單價換算 -------------------------------------------------------------

def test_known_model_cost():
    cost, note = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1_000_000, 1_000_000)
    assert cost == pytest.approx(10.0)      # 2.00 in + 8.00 out
    assert "2026-07-28" in note


def test_date_suffixed_model_falls_back_to_base():
    """gpt-4.1-2025-04-14 這種帶日期的要能對到 gpt-4.1。"""
    a, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1-2025-04-14", 1000, 0)
    b, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1000, 0)
    assert a == b and a is not None


def test_unknown_model_returns_none_not_zero():
    """核心：不知道單價就要說不知道。回 0 會被當成「這次免費」。"""
    cost, note = llm_pricing.estimate_cost_usd("acme", "mystery-model", 999999, 999999)
    assert cost is None and note is None


def test_alias_models_have_no_price():
    """gemini-flash-latest 指向的實際模型會變，不能給預設價。"""
    for vendor, model in [("google", "gemini-flash-latest"),
                          ("openrouter", "openrouter/auto")]:
        assert llm_pricing.lookup(vendor, model) is None


def test_zero_tokens_costs_zero():
    cost, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 0, 0)
    assert cost == 0.0


# --- usage 正規化 ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, (10, 5, 15)),
    ({"prompt_token_count": 7, "candidates_token_count": 3, "total_token_count": 10}, (7, 3, 10)),
    ({"input_tokens": 1, "output_tokens": 2}, (1, 2, 3)),        # total 由加總補上
    ({}, (0, 0, 0)),
    (None, (0, 0, 0)),
    ("nonsense", (0, 0, 0)),
])
def test_normalize_usage_across_vendors(raw, expected):
    n = llm_usage.normalize_usage(raw)
    assert (n["input"], n["output"], n["total"]) == expected


# --- 記錄與彙總 -----------------------------------------------------------

@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    """把用量 DB 指向 tmp_path，完全不碰 repo 的 data/。"""
    db = tmp_path / "llm_match.db"
    monkeypatch.setattr(llm_usage, "get_db_path", lambda: str(db))
    monkeypatch.setattr(llm_usage, "_initialized", False, raising=False)
    yield db
    monkeypatch.setattr(llm_usage, "_initialized", False, raising=False)


def test_record_and_summarize(usage_db):
    with llm_usage.usage_context("P-p", "paper1"):
        llm_usage.record("task_4cv", "openai", "gpt-4.1",
                         {"prompt_tokens": 1000, "completion_tokens": 500})
        llm_usage.record("task_5interpret", "openai", "gpt-4.1",
                         {"prompt_tokens": 2000, "completion_tokens": 100})

    s = llm_usage.summarize_paper("P-p", "paper1")
    assert s["calls"] == 2
    assert s["input_tokens"] == 3000
    assert s["output_tokens"] == 600
    assert s["total_tokens"] == 3600
    assert s["has_unpriced"] is False
    assert s["cost_usd"] == pytest.approx(3000/1e6*2.0 + 600/1e6*8.0)


def test_unpriced_model_is_flagged(usage_db):
    """混到查不到單價的模型時，必須標記出來——
    否則使用者會把「已知部分的合計」當成全部花費。"""
    with llm_usage.usage_context("P-p", "paper2"):
        llm_usage.record("task_4cv", "openai", "gpt-4.1",
                         {"prompt_tokens": 100, "completion_tokens": 0})
        llm_usage.record("task_5b_reflow", "google", "gemini-flash-latest",
                         {"prompt_token_count": 5000, "candidates_token_count": 1000})

    s = llm_usage.summarize_paper("P-p", "paper2")
    assert s["calls"] == 2
    assert s["total_tokens"] == 6100      # token 數照樣算得出來
    assert s["has_unpriced"] is True      # 但金額不完整，要講清楚


def test_papers_are_isolated(usage_db):
    with llm_usage.usage_context("P-p", "a"):
        llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 10, "completion_tokens": 0})
    with llm_usage.usage_context("P-p", "b"):
        llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 999, "completion_tokens": 0})
    assert llm_usage.summarize_paper("P-p", "a")["input_tokens"] == 10
    assert llm_usage.summarize_paper("P-p", "b")["input_tokens"] == 999


def test_context_restores_after_exit(usage_db):
    with llm_usage.usage_context("P-p", "x"):
        assert llm_usage.current_context()["paper_id"] == "x"
    assert llm_usage.current_context()["paper_id"] is None


def test_empty_usage_is_not_recorded(usage_db):
    """沒拿到用量就別寫一筆全 0 的假資料，那會讓 calls 統計失真。"""
    with llm_usage.usage_context("P-p", "empty"):
        llm_usage.record("t", "openai", "gpt-4.1", {})
        llm_usage.record("t", "openai", "gpt-4.1", None)
    assert llm_usage.summarize_paper("P-p", "empty")["calls"] == 0


def test_recording_never_raises(monkeypatch, usage_db):
    """記帳壞掉不該讓解析整個失敗。"""
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk gone")
    monkeypatch.setattr(sqlite3, "connect", boom)
    llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 5, "completion_tokens": 5})
    # 沒有拋例外就是通過


def test_clear_context_prevents_thread_pool_leak(usage_db):
    """set_context() 不會自動還原，worker 收尾必須 clear_context()。

    這些 worker 跑在 ThreadPoolExecutor 的池化執行緒上，執行緒會被重複使用。
    不清的話，下一個工作的 LLM 呼叫會被算到上一篇的帳上——實測時就是靠
    「單獨跑會過、全套跑會失敗」抓到這個洩漏的。
    """
    llm_usage.set_context("P-p", "leaky")
    assert llm_usage.current_context()["paper_id"] == "leaky"

    llm_usage.clear_context()
    assert llm_usage.current_context() == {"pid": None, "paper_id": None}

    # 清乾淨之後的呼叫不該再掛到上一篇
    llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 50, "completion_tokens": 0})
    assert llm_usage.summarize_paper("P-p", "leaky")["calls"] == 0


def test_summarize_unknown_paper_is_empty(usage_db):
    s = llm_usage.summarize_paper("P-p", "never-seen")
    assert s["calls"] == 0 and s["cost_usd"] == 0.0
