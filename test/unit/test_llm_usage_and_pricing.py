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


def test_gemini_models_are_priced():
    """task_5interpret / task_5b_reflow 走 Gemini。

    這一系列原本整組沒單價，於是文獻列表只能顯示「≥ $X（部分模型未設定單價）」，
    給不出一個完整的美金金額。
    """
    cost, note = llm_pricing.estimate_cost_usd("google", "gemini-2.5-flash",
                                               1_000_000, 1_000_000)
    assert cost == pytest.approx(2.80)      # 0.30 in + 2.50 out
    assert note is not None


def test_tiered_model_charges_higher_rate_over_threshold():
    """Gemini pro 系列 >200k prompt 走高階費率。

    只登記低價那段會系統性低估長 prompt 的花費——使用者會照著一個偏小的
    數字決定要不要繼續花錢，這個方向的錯比高估危險。
    """
    below, _ = llm_pricing.estimate_cost_usd("google", "gemini-2.5-pro", 200_000, 0)
    above, _ = llm_pricing.estimate_cost_usd("google", "gemini-2.5-pro", 200_001, 0)
    assert below == pytest.approx(200_000 / 1_000_000 * 1.25)
    assert above == pytest.approx(200_001 / 1_000_000 * 2.50)
    assert above > below * 1.9


def test_flat_priced_model_is_unaffected_by_tier_logic():
    """沒有分段設定的模型，費率不因 prompt 長度改變。"""
    small, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1_000, 0)
    large, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1_000_000, 0)
    assert large == pytest.approx(small * 1000)


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


def test_existing_usage_table_is_upgraded_without_losing_rows(usage_db):
    """父版本已建過舊表時，要原地加欄位，不能讓後續記帳靜默失效。"""
    conn = sqlite3.connect(usage_db)
    try:
        conn.execute("""
            CREATE TABLE llm_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                pid TEXT,
                paper_id TEXT,
                task_id TEXT NOT NULL,
                vendor TEXT,
                model_name TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL,
                price_note TEXT,
                cache_hit INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO llm_usage_log "
            "(created_at, pid, paper_id, task_id, input_tokens, total_tokens) "
            "VALUES ('old', 'P', 'old-paper', 'old-task', 7, 7)"
        )
        conn.commit()
    finally:
        conn.close()

    with llm_usage.usage_context("P", "new-paper"):
        llm_usage.record(
            "new-task", "openai", "gpt-4.1",
            {"input_tokens": 100, "cached_input_tokens": 40},
        )

    conn = sqlite3.connect(usage_db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_usage_log)")}
        rows = conn.execute(
            "SELECT paper_id, cached_input_tokens FROM llm_usage_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert "cached_input_tokens" in columns
    assert rows == [("old-paper", 0), ("new-paper", 40)]
    assert llm_usage.summarize_project("P")["new-paper"]["calls"] == 1


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
    # 未計價的「量」也要給：只有布林值的話，UI 只能一律標記整筆金額不可信，
    # 一次小呼叫就會讓一筆大金額看起來完全不能用。
    assert s["unpriced_tokens"] == 6000
    assert s["total_tokens"] - s["unpriced_tokens"] == 100


def test_unpriced_tokens_is_zero_when_everything_priced(usage_db):
    with llm_usage.usage_context("P-p", "allpriced"):
        llm_usage.record("task_4cv", "openai", "gpt-4.1",
                         {"prompt_tokens": 100, "completion_tokens": 10})
    s = llm_usage.summarize_paper("P-p", "allpriced")
    assert s["has_unpriced"] is False
    assert s["unpriced_tokens"] == 0


def test_project_summary_also_reports_unpriced_tokens(usage_db):
    """列表走的是 summarize_project（一次撈全專案），兩邊欄位要一致，
    否則畫面吃不到這個欄位會靜默退化成 undefined。"""
    with llm_usage.usage_context("P-p", "paper3"):
        llm_usage.record("task_4cv", "openai", "gpt-4.1",
                         {"prompt_tokens": 100, "completion_tokens": 0})
        llm_usage.record("task_5interpret", "google", "gemini-flash-latest",
                         {"prompt_token_count": 900, "candidates_token_count": 100})
    s = llm_usage.summarize_project("P-p")["paper3"]
    assert s["has_unpriced"] is True
    assert s["unpriced_tokens"] == 1000


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


def test_context_propagates_into_new_thread(usage_db):
    """[P1-1 迴歸] contextvars 不會自動跨執行緒。

    翻譯與 LaTeX OCR 都是「外層設好歸屬、內層另開 ThreadPoolExecutor 呼叫」
    的結構。沒有 propagate() 的話，新執行緒拿到空 context，token 會以
    pid/paper_id=NULL 落帳——該篇的摘要顯示 0 次呼叫，錢花了卻查不到是誰花的。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _call():
        llm_usage.record("task_5b_reflow", "openai", "gpt-4.1",
                         {"prompt_tokens": 110, "completion_tokens": 0})
        return llm_usage.current_context()

    # 沒包 propagate：歸屬會掉
    with llm_usage.usage_context("P-p", "crossthread"):
        with ThreadPoolExecutor(max_workers=1) as ex:
            naive = ex.submit(_call).result()
    assert naive["paper_id"] is None, "前提不成立則本測試無意義"
    assert llm_usage.summarize_paper("P-p", "crossthread")["calls"] == 0

    # 包了 propagate：歸屬跟著進去
    with llm_usage.usage_context("P-p", "crossthread"):
        with ThreadPoolExecutor(max_workers=1) as ex:
            fixed = ex.submit(llm_usage.propagate(_call)).result()
    assert fixed["paper_id"] == "crossthread"
    s = llm_usage.summarize_paper("P-p", "crossthread")
    assert s["calls"] == 1 and s["input_tokens"] == 110


def test_cached_input_is_billed_at_lower_rate(usage_db):
    """[P2-4 迴歸] 廠商回報的 input 已含 cached 部分。

    不拆開的話會把全部輸入按原價算，長 prompt 反覆呼叫時系統性高估。
    """
    full, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1_000_000, 0, 0)
    half, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1_000_000, 0, 500_000)
    assert full == pytest.approx(2.0)
    # 一半命中快取：500k @ $2 + 500k @ $0.50
    assert half == pytest.approx(1.25)
    assert half < full


def test_cached_never_exceeds_input(usage_db):
    """cached 大於 input 是不合理的回報，不能讓費用變成負的。"""
    cost, _ = llm_pricing.estimate_cost_usd("openai", "gpt-4.1", 1000, 0, 999999)
    assert cost is not None and cost >= 0


def test_summarize_project_matches_per_paper(usage_db):
    """[P2-5 迴歸] 一次查完的結果要與逐篇查完全一致。"""
    with llm_usage.usage_context("PRJ", "p1"):
        llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 100, "completion_tokens": 10})
        llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 200, "completion_tokens": 20})
    with llm_usage.usage_context("PRJ", "p2"):
        llm_usage.record("t", "google", "gemini-flash-latest",
                         {"prompt_token_count": 300, "candidates_token_count": 30})

    bulk = llm_usage.summarize_project("PRJ")
    assert set(bulk) == {"p1", "p2"}
    for paper_id in ("p1", "p2"):
        one = llm_usage.summarize_paper("PRJ", paper_id)
        for key in ("input_tokens", "output_tokens", "total_tokens",
                    "cost_usd", "calls", "has_unpriced", "models"):
            assert bulk[paper_id][key] == one[key], f"{paper_id}.{key} 不一致"


def test_summarize_project_ignores_other_projects(usage_db):
    with llm_usage.usage_context("A", "x"):
        llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 1, "completion_tokens": 0})
    with llm_usage.usage_context("B", "y"):
        llm_usage.record("t", "openai", "gpt-4.1", {"prompt_tokens": 1, "completion_tokens": 0})
    assert set(llm_usage.summarize_project("A")) == {"x"}


def test_summarize_unknown_paper_is_empty(usage_db):
    s = llm_usage.summarize_paper("P-p", "never-seen")
    assert s["calls"] == 0 and s["cost_usd"] == 0.0
