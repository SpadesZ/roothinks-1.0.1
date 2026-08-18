# Roothinks source maintenance contract
# 主要責任: 驗收 Literature 對話式找文獻的意圖閘門、幻覺剔除與階段錯誤可見性。
# 上下游: pytest/monkeypatch -> task_3a_litchat / task_3bc_scout；LLM 與外部 API 全部替換成假的。
# 檔案路徑: test/unit/test_literature_chat_pipeline.py
# 建立時間: 2026-08-17 +08:00；版本: v1.0
# 模組定位: A/B/C 三腳流程的行為護欄。
# 驗證契約:
#   1. 意圖閘門：不需要文獻的輪次**不得**發動搜尋（那是 4 次 LLM 呼叫的成本）。
#   2. 幻覺剔除：回查不到的論文不得出現在結果，而且要出現在 dropped 裡有原因。
#   3. 顯示連結只走 doi.org / PubMed / arXiv / Scholar，出版社 landing page 一律退回 Scholar。
#   4. 任一階段失敗都要進 meta.stage_errors —— 半套結果不能長得像完整結果。
#   5. 回傳筆數受 LIT_CHAT_MAX_PAPERS 上限約束。
# 明確不負責:
#   - 不驗 LLM 回覆品質，也不驗真實 grounding 行為（那要真的打 Google，見 HANDOFF）。
#   - 不驗 HTTP 層與持久化，那是 test_literature_chat_persistence.py。
# 安全邊界: 不發任何真實 HTTP、不讀寫 data/。
# 執行: python -m pytest test/unit/test_literature_chat_pipeline.py -q
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json

import pytest


# --------------------------------------------------------------------------
# A：意圖閘門
# --------------------------------------------------------------------------
def _fake_a(monkeypatch, intent_payload, reply_text="好的"):
    from app.llm_service.matching_tasks import task_3a_litchat

    prompts = []

    def _dispatch(task_id, prompt, *_a, **_k):
        prompts.append(prompt)
        # 意圖判斷的 prompt 會要求輸出 should_search 這個欄位名。
        if "should_search" in prompt:
            return {"ok": True, "text": json.dumps(intent_payload, ensure_ascii=False)}
        return {"ok": True, "text": reply_text}

    monkeypatch.setattr(task_3a_litchat, "dispatch_task", _dispatch)
    return prompts


def test_non_search_turn_does_not_trigger_search(monkeypatch):
    from app.llm_service.matching_tasks import task_3a_litchat

    _fake_a(monkeypatch, {"should_search": False, "query": "", "reason": "只是追問"})
    calls = []

    turn = task_3a_litchat.run_chat_turn(
        [], "剛剛第 2 篇的方法能解釋一下嗎？",
        search_fn=lambda q: calls.append(q) or {"papers": []},
    )

    assert calls == [], "純追問竟然發動了搜尋"
    assert turn["papers"] == []
    assert turn["meta"]["should_search"] is False


def test_search_request_triggers_search_with_rewritten_query(monkeypatch):
    from app.llm_service.matching_tasks import task_3a_litchat

    _fake_a(
        monkeypatch,
        {"should_search": True, "query": "auditory ventral stream speech recognition", "reason": "要文獻"},
    )
    calls = []

    turn = task_3a_litchat.run_chat_turn(
        [], "幫我找聽覺腹側路徑與語音辨識的關鍵論文",
        search_fn=lambda q: calls.append(q) or {"papers": [], "dropped": [], "meta": {}},
    )

    assert calls == ["auditory ventral stream speech recognition"]
    assert turn["meta"]["should_search"] is True


def test_intent_fallback_is_labelled_not_silent(monkeypatch):
    """A 解析失敗時退回關鍵字，但必須標明 intent_source=keyword。"""
    from app.llm_service.matching_tasks import task_3a_litchat

    monkeypatch.setattr(
        task_3a_litchat, "dispatch_task", lambda *_a, **_k: {"ok": True, "text": "抱歉我壞了"}
    )

    intent = task_3a_litchat.classify_intent([], "幫我找一些相關論文")
    assert intent["should_search"] is True
    assert intent["intent_source"] == "keyword", "退化路徑不得偽裝成正常判斷"


# --------------------------------------------------------------------------
# B/C：搜尋、互檢與回查
# --------------------------------------------------------------------------
REAL_PAPER = {
    "title": "Cortical processing of the auditory ventral stream",
    "authors": ["Rauschecker, J."],
    "year": 2009,
    "venue": "Nature Neuroscience",
    "doi": "10.1038/nn.2331",
    "url": "https://doi.org/10.1038/nn.2331",
    "abstract": "",
    "citations": 1200,
    "source": "crossref",
}

FAKE_PAPER = {
    "title": "A totally invented paper about LAVASR pipelines",
    "authors": ["Nobody, N."],
    "year": 2024,
    "venue": "Journal of Nothing",
    "doi": "",
    "url": "",
    "why": "看起來很相關",
}


def _scout_payload(papers):
    return json.dumps({"papers": papers}, ensure_ascii=False)


@pytest.fixture()
def scout_env(monkeypatch):
    """把 B/C 的 LLM 與回查全部換成可控的假物件。"""
    from app.llm_service.matching_tasks import task_3bc_scout

    state = {
        "B": _scout_payload([dict(REAL_PAPER, why="機制證據")]),
        "C": _scout_payload([FAKE_PAPER]),
        "reviews": {},
        "resolvable": {REAL_PAPER["title"]: dict(REAL_PAPER)},
        "scout_error": {},
        # groundingChunks 數量：0 代表模型根本沒搜尋（實測會發生，見 NOTE-039）。
        "chunks": {},
    }

    def _dispatch(task_id, prompt, *_a, **_k):
        role = "B" if task_id == "task_3b_scout" else "C"
        if "待查核清單" in prompt:
            return {"ok": True, "text": json.dumps({"reviews": state["reviews"].get(role, [])})}
        if _k.get("grounding"):
            # 第一段：grounded 散文檢索。
            if role in state["scout_error"]:
                return {"ok": False, "msg": state["scout_error"][role]}
            return {
                "ok": True,
                "text": f"survey for {role}",
                "grounding": {
                    "chunk_count": state["chunks"].get(role, 4),
                    "domains": ["pubmed.ncbi.nlm.nih.gov"],
                    "queries": [],
                },
            }
        # 第二段：不帶 tools 的結構化抽取。
        return {"ok": True, "text": state[role]}

    def _fake_resolve(_self, title="", authors=None, year=None, doi=""):
        hit = state["resolvable"].get(title)
        if not hit:
            return {}
        resolved = dict(hit)
        resolved["match"] = "title"
        resolved["match_score"] = 1.0
        resolved["canonical_url"] = resolved.get("url", "")
        resolved["apa"] = f"{title}."
        return resolved

    monkeypatch.setattr(task_3bc_scout, "dispatch_task", _dispatch)
    # 只換掉「會發真實 HTTP」的那一個方法；normalize_doi / canonical_url 等
    # 純邏輯仍走真品，否則測試會繞過真正要保護的行為。
    monkeypatch.setattr(
        task_3bc_scout.ContextSearcher, "resolve_reference", _fake_resolve, raising=True
    )
    return state


def test_unresolvable_paper_is_dropped_as_hallucination(scout_env):
    from app.llm_service.matching_tasks import task_3bc_scout

    out = task_3bc_scout.run_debate_search("auditory ventral stream")

    titles = [p["title"] for p in out["papers"]]
    assert REAL_PAPER["title"] in titles
    assert FAKE_PAPER["title"] not in titles, "查無此文的論文竟然出現在結果裡"

    dropped_titles = [d["title"] for d in out["dropped"]]
    assert FAKE_PAPER["title"] in dropped_titles
    reason = next(d["reason"] for d in out["dropped"] if d["title"] == FAKE_PAPER["title"])
    assert "查無" in reason, f"剔除原因看不出發生什麼事: {reason}"


def test_suspect_without_second_source_is_dropped(scout_env):
    """互檢標可疑、且只有一隻腳找到 -> 剔除；不能靜靜地留下來。"""
    from app.llm_service.matching_tasks import task_3bc_scout

    # C 找到的那篇改成回查得到（排除機械驗證的影響），但被 B 標成 suspect。
    scout_env["resolvable"][FAKE_PAPER["title"]] = dict(
        FAKE_PAPER, doi="10.9999/fake", url="https://doi.org/10.9999/fake", source="crossref", citations=0
    )
    scout_env["reviews"]["B"] = [{"index": 0, "verdict": "suspect", "reason": "與主題無關"}]

    out = task_3bc_scout.run_debate_search("auditory ventral stream")

    titles = [p["title"] for p in out["papers"]]
    assert FAKE_PAPER["title"] not in titles
    assert any("互檢" in d["reason"] for d in out["dropped"])


def test_zero_grounding_chunks_is_rejected_not_accepted(scout_env):
    """
    NOTE(NOTE-039)：這條是實機打真的 Gemini 才發現的失敗模式，補在這裡當回歸護欄。

    grounded 請求可以「成功」（HTTP 200、有文字、沒有錯誤）卻**完全沒有搜尋** ——
    groundingChunks 為 0，模型憑記憶編出 title/DOI。實測觸發條件是在 grounded
    prompt 裡要求「只輸出 JSON」。這種回應絕對不能當成「這次剛好沒找到」而放行，
    否則整個功能會靜靜地退化成兩個模型互相幻想。
    """
    from app.llm_service.matching_tasks import task_3bc_scout

    scout_env["chunks"] = {"B": 0, "C": 0}
    out = task_3bc_scout.run_debate_search("auditory ventral stream")

    assert out["papers"] == [], "沒有實際搜尋卻回傳了論文"
    for role in ("B", "C"):
        err = out["meta"]["stage_errors"].get(f"scout_{role}", "")
        assert "grounding" in err, f"scout_{role} 沒有標明 grounding 未生效: {err!r}"


def test_grounded_search_prompt_must_not_demand_json(scout_env):
    """
    NOTE(NOTE-039)：要求 JSON 會讓 Gemini 跳過搜尋，所以檢索那一段的 prompt
    不得出現 JSON 指示。結構化是第二段（不帶 tools）的工作。
    """
    from app.llm_service.matching_tasks import task_3bc_scout

    prompt = task_3bc_scout._build_scout_prompt("auditory ventral stream", "B", 10)
    low = prompt.lower()
    assert "json" not in low or "do not output json" in low, (
        "grounded 檢索 prompt 要求了 JSON，會導致模型不搜尋"
    )
    assert "google search" in low, "檢索 prompt 沒有要求使用 Google 搜尋"


def test_extraction_stage_runs_without_grounding(scout_env):
    """第二段抽取不得再開 grounding：那是多花一次搜尋額度做純文字轉換。"""
    from app.llm_service.matching_tasks import task_3bc_scout

    seen = []
    original = task_3bc_scout.dispatch_task

    def _spy(task_id, prompt, *a, **k):
        seen.append({"grounding": bool(k.get("grounding")), "is_extract": "回顧全文" in prompt})
        return original(task_id, prompt, *a, **k)

    task_3bc_scout.dispatch_task = _spy
    try:
        task_3bc_scout.run_debate_search("auditory ventral stream")
    finally:
        task_3bc_scout.dispatch_task = original

    extracts = [c for c in seen if c["is_extract"]]
    assert extracts, "沒有跑抽取階段"
    assert all(not c["grounding"] for c in extracts), "抽取階段不該開 grounding"
    assert any(c["grounding"] for c in seen), "沒有任何 grounded 檢索呼叫"


def test_scout_failure_lands_in_stage_errors(scout_env):
    from app.llm_service.matching_tasks import task_3bc_scout

    scout_env["scout_error"]["C"] = "Provider 'openai' does not support Google Search grounding"
    out = task_3bc_scout.run_debate_search("auditory ventral stream")

    assert "scout_C" in out["meta"]["stage_errors"], "搜尋腳掛掉卻沒有留下任何痕跡"
    assert "grounding" in out["meta"]["stage_errors"]["scout_C"]
    # B 仍然有結果，但使用者必須看得到這是半套的。
    assert out["meta"]["searched_by"] == ["B"]


def test_result_is_capped_by_max_papers(scout_env, monkeypatch):
    from app.llm_service.matching_tasks import task_3bc_scout

    monkeypatch.setenv("LIT_CHAT_MAX_PAPERS", "3")
    monkeypatch.setenv("LIT_CHAT_SCOUT_LIMIT", "10")

    many = []
    for i in range(10):
        title = f"Paper number {i} on auditory cortex"
        many.append({"title": title, "authors": ["A"], "year": 2020, "doi": f"10.1/{i}", "url": ""})
        scout_env["resolvable"][title] = {
            "title": title, "authors": ["A"], "year": 2020, "venue": "J",
            "doi": f"10.1/{i}", "url": f"https://doi.org/10.1/{i}",
            "abstract": "", "citations": i, "source": "crossref",
        }
    scout_env["B"] = _scout_payload(many)
    scout_env["C"] = _scout_payload([])

    out = task_3bc_scout.run_debate_search("auditory cortex")
    assert len(out["papers"]) == 3


def test_publisher_landing_page_falls_back_to_scholar(scout_env):
    """沒有 DOI、來源給的是出版社網址時，不得直接貼出去。"""
    from app.llm_service.matching_tasks import task_3bc_scout

    title = "Speech pathways without a DOI"
    scout_env["B"] = _scout_payload([{"title": title, "authors": ["X"], "year": 2021}])
    scout_env["C"] = _scout_payload([])
    scout_env["resolvable"][title] = {
        "title": title, "authors": ["X"], "year": 2021, "venue": "Elsevier",
        "doi": "", "url": "https://www.sciencedirect.com/science/article/pii/S123",
        "abstract": "", "citations": 3, "source": "openalex",
    }

    out = task_3bc_scout.run_debate_search("speech pathways")
    assert len(out["papers"]) == 1
    paper = out["papers"][0]
    assert paper["link_kind"] == "scholar"
    assert paper["url"].startswith("https://scholar.google.com/scholar?q=")
    assert "sciencedirect" not in paper["url"]


def test_doi_link_is_used_when_available(scout_env):
    from app.llm_service.matching_tasks import task_3bc_scout

    out = task_3bc_scout.run_debate_search("auditory ventral stream")
    paper = next(p for p in out["papers"] if p["title"] == REAL_PAPER["title"])
    assert paper["link_kind"] == "doi"
    assert paper["url"] == "https://doi.org/10.1038/nn.2331"


# --------------------------------------------------------------------------
# resolve_reference 本身的門檻
# --------------------------------------------------------------------------
def test_resolve_reference_rejects_near_miss_titles(monkeypatch):
    """標題只是同領域而非同一篇時必須回空，否則等於用另一篇論文冒充。"""
    from app.llm_service.matching_tasks.task_3search import ContextSearcher

    searcher = ContextSearcher()
    monkeypatch.setattr(searcher, "_search_crossref", lambda *_a, **_k: [
        {"title": "Auditory cortex and music perception", "year": 2009, "doi": "10.1/x", "source": "crossref"}
    ])
    monkeypatch.setattr(searcher, "_search_openalex", lambda *_a, **_k: [])
    monkeypatch.setattr(searcher, "_search_pubmed", lambda *_a, **_k: [])
    monkeypatch.setattr(searcher, "_search_arxiv", lambda *_a, **_k: [])

    assert searcher.resolve_reference("Cortical processing of the auditory ventral stream") == {}


def test_resolve_reference_rejects_year_mismatch(monkeypatch):
    from app.llm_service.matching_tasks.task_3search import ContextSearcher

    searcher = ContextSearcher()
    monkeypatch.setattr(searcher, "_search_crossref", lambda *_a, **_k: [
        {"title": "Cortical processing of the auditory ventral stream", "year": 1998,
         "doi": "10.1/x", "source": "crossref"}
    ])
    monkeypatch.setattr(searcher, "_search_openalex", lambda *_a, **_k: [])
    monkeypatch.setattr(searcher, "_search_pubmed", lambda *_a, **_k: [])
    monkeypatch.setattr(searcher, "_search_arxiv", lambda *_a, **_k: [])

    assert searcher.resolve_reference(
        "Cortical processing of the auditory ventral stream", year=2009
    ) == {}
