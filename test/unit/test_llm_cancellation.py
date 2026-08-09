# 檔案路徑: test/unit/test_llm_cancellation.py
# 建立時間: 2026-08-10 +08:00；版本: v1.0
# 模組定位: Manuscript 2A provider-aware cancellation 的最小回歸護欄。
# 驗證契約: router 取消後不啟動 drafting；OpenRouter HTTP coroutine確實被 cancel。
# 安全邊界: 使用 fake client 與無效 URL，不會送出 prompt、API key 或外部請求。
# 執行: python -m pytest test/unit/test_llm_cancellation.py -q

import threading


def test_drafter_stops_after_cancelled_router_call(monkeypatch):
    from app.llm_service.matching_tasks import task_8drafter

    cancel_event = threading.Event()
    calls = []

    monkeypatch.setattr(
        task_8drafter.ManuscriptRuling,
        "validate_and_prepare",
        lambda **_kwargs: {
            "ok": True,
            "context_text": "grounded context",
            "context_sources": [{"source_type": "study_note"}],
            "has_grounding": True,
        },
    )

    def _cancel_on_router(*_args, **_kwargs):
        calls.append("provider")
        cancel_event.set()
        return {"ok": False, "cancelled": True, "msg": "cancelled"}

    monkeypatch.setattr(task_8drafter, "dispatch_task", _cancel_on_router)
    result = task_8drafter.Task8Drafter().process_request(
        "請寫摘要",
        pid="CANCEL-p",
        title="Cancel",
        section="abstract",
        cancel_event=cancel_event,
    )

    assert calls == ["provider"], "cancelled router request incorrectly started drafting"
    assert result["content"] == "Request cancelled."


def test_openrouter_abort_closes_inflight_http_request(monkeypatch):
    from app.llm_service.adapter import llm_openrouter
    from app.llm_service.llm_cancellation import LLMRequestCancelled

    started = threading.Event()
    aborted = threading.Event()

    class _FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            import asyncio

            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                aborted.set()

    monkeypatch.setattr(llm_openrouter.httpx, "AsyncClient", _FakeAsyncClient)

    client = llm_openrouter.OpenRouterClient.__new__(llm_openrouter.OpenRouterClient)
    client.model = "fake/model"
    client.chat_endpoint = "https://example.invalid/chat/completions"
    client.timeout_sec = 60
    client.max_tokens = 128
    client.headers = {"Authorization": "Bearer hidden"}

    cancel_event = threading.Event()
    outcome = {}

    def _send():
        try:
            client.send_text_and_optional_images("draft", cancel_event=cancel_event)
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=_send)
    worker.start()
    assert started.wait(2)
    cancel_event.set()
    worker.join(2)

    assert not worker.is_alive()
    assert aborted.is_set(), "cancelling did not close the provider HTTP coroutine"
    assert isinstance(outcome.get("error"), LLMRequestCancelled)
