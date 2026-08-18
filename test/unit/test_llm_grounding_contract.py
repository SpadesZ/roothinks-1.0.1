# Roothinks source maintenance contract
# 主要責任: 驗收 grounding 請求在 bus/dispatcher 之間的傳遞契約與不降級保證。
# 上下游: pytest/monkeypatch -> app.llm_service.llm_bus / llm_dispatcher；不觸網、不讀 DB 以外的東西。
# 檔案路徑: test/unit/test_llm_grounding_contract.py
# 建立時間: 2026-08-17 +08:00；版本: v1.0
# 模組定位: 「B/C 真的有上網」這件事的最小護欄。
# 驗證契約:
#   1. provider 不支援 grounding 時**報錯**，不得默默改走一般路徑。
#      （沒有這條，B 綁到 OpenAI 時使用者會拿到憑記憶編的文獻清單卻以為經過搜尋。）
#   2. grounding=True 一律跳過 response cache。
#   3. adapter 回的 grounding metadata 要一路帶到 dispatch_task 的回傳。
# 安全邊界: 全程 fake client，不送出任何 prompt 或 API key，不發真實 HTTP。
# 執行: python -m pytest test/unit/test_llm_grounding_contract.py -q
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _PlainClient:
    """沒有 grounding 能力的 provider（OpenAI/OpenRouter 就是這樣）。"""

    def __init__(self):
        self.plain_calls = 0

    def send_text_and_optional_images(self, text, filepaths=None, cancel_event=None):
        self.plain_calls += 1
        return True, {"text": "從記憶編出來的清單", "usage": {}}, ""


class _GroundedClient(_PlainClient):
    def __init__(self):
        super().__init__()
        self.grounded_calls = 0

    def send_text_with_search_grounding(self, text, cancel_event=None):
        self.grounded_calls += 1
        return (
            True,
            {
                "text": '{"papers":[]}',
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "grounding": {"queries": ["auditory ventral stream"], "domains": ["pubmed.ncbi.nlm.nih.gov"], "chunk_count": 3},
            },
            "",
        )


def _bus_with(client, provider="openai"):
    from app.llm_service.llm_bus import LlmBus

    bus = LlmBus()
    bus._client = client
    bus._provider_name = provider
    bus._model_name = "fake-model"
    return bus


def test_unsupported_provider_reports_error_instead_of_degrading():
    client = _PlainClient()
    ok, res, err = _bus_with(client).send_message("找文獻", grounding=True)

    assert ok is False
    assert "does not support Google Search grounding" in err
    # 關鍵：**不可以**偷偷改用一般路徑產生一份沒搜尋過的答案。
    assert client.plain_calls == 0, "grounding 不支援時竟然退回一般生成路徑"


def test_grounding_false_still_uses_plain_path():
    client = _GroundedClient()
    ok, _res, _err = _bus_with(client, provider="google").send_message("一般問答")

    assert ok is True
    assert client.plain_calls == 1
    assert client.grounded_calls == 0, "沒要求 grounding 卻去上網搜尋"


def test_grounded_path_returns_metadata():
    client = _GroundedClient()
    ok, res, _err = _bus_with(client, provider="google").send_message("找文獻", grounding=True)

    assert ok is True
    assert client.grounded_calls == 1
    assert client.plain_calls == 0
    assert res["grounding"]["chunk_count"] == 3


def _patch_dispatcher(monkeypatch, client, provider="google"):
    from app.llm_service import llm_dispatcher

    monkeypatch.setattr(
        llm_dispatcher.LLMModel,
        "execute_query",
        staticmethod(lambda *_a, **_k: {"connection_id": 7}),
    )
    monkeypatch.setattr(llm_dispatcher, "LlmBus", lambda: _StubBus(client, provider))
    return llm_dispatcher


class _StubBus:
    def __init__(self, client, provider):
        self._client = client
        self._provider_name = provider
        self._model_name = "fake-model"

    def load_from_db(self, _conn_id):
        return True, "ok"

    def send_message(self, text, images=None, cancel_event=None, grounding=False):
        from app.llm_service.llm_bus import LlmBus

        bus = LlmBus()
        bus._client = self._client
        bus._provider_name = self._provider_name
        bus._model_name = self._model_name
        return bus.send_message(text, images, cancel_event=cancel_event, grounding=grounding)


def test_grounded_request_never_touches_response_cache(monkeypatch):
    client = _GroundedClient()
    llm_dispatcher = _patch_dispatcher(monkeypatch, client)

    consulted = []
    monkeypatch.setattr(llm_dispatcher, "is_llm_cache_enabled", lambda: True)
    monkeypatch.setattr(
        llm_dispatcher, "load_cached_response", lambda key: consulted.append(key) or None
    )
    monkeypatch.setattr(llm_dispatcher, "save_cached_response", lambda *a, **k: consulted.append("save"))

    ok, _res, _msg = llm_dispatcher.LlmDispatcher().execute(
        "task_3b_scout", "找文獻", grounding=True
    )

    assert ok is True
    assert consulted == [], "grounded 請求進了 response cache，會拿舊答案冒充搜尋結果"


def test_dispatch_task_passes_grounding_through(monkeypatch):
    client = _GroundedClient()
    llm_dispatcher = _patch_dispatcher(monkeypatch, client)
    monkeypatch.setattr(llm_dispatcher, "is_llm_cache_enabled", lambda: False)

    result = llm_dispatcher.dispatch_task("task_3b_scout", "找文獻", grounding=True)

    assert result["ok"] is True
    assert client.grounded_calls == 1
    assert result["grounding"]["domains"] == ["pubmed.ncbi.nlm.nih.gov"]


def test_unsupported_grounding_is_fatal_and_not_retried(monkeypatch):
    client = _PlainClient()
    llm_dispatcher = _patch_dispatcher(monkeypatch, client, provider="openai")
    monkeypatch.setattr(llm_dispatcher, "is_llm_cache_enabled", lambda: False)

    result = llm_dispatcher.dispatch_task(
        "task_3b_scout", "找文獻", max_retries=3, grounding=True
    )

    assert result["ok"] is False
    assert "does not support Google Search grounding" in result["msg"]
    assert client.plain_calls == 0
