# Roothinks source maintenance contract
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 檔案路徑: app/llm_service/llm_cancellation.py
# 版本: v1.0；建立時間: 2026-08-10 +08:00
# 模組定位: 同步 Flask worker 與 async provider transport 之間的取消橋接層。
# 主要責任: 提供跨同步/async provider 的共同取消例外、狀態判斷與 coroutine 終止橋接。
#   1. 定義可被 dispatcher 辨識、不得轉成一般 provider failure 的取消例外。
#   2. 在獨立 event loop 執行單一 async request，輪詢 threading.Event 與 timeout。
#   3. 取消勝出時 cancel 並 await provider task，讓 HTTP 連線確實離開 worker。
# 輸入輸出: coro_factory 必須回傳新的 coroutine；成功回傳 provider response，
#   取消拋 LLMRequestCancelled，逾時拋 TimeoutError。
# 維護邊界: 本模組不保存 prompt、API key 或 response；不要把取消吞成成功或 retry。
# 驗證: python -m pytest test/unit/test_llm_cancellation.py -q

import asyncio
import time


class LLMRequestCancelled(Exception):
    """The caller explicitly aborted an in-flight provider request."""


def is_cancelled(cancel_event) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def run_cancellable_async(coro_factory, cancel_event, timeout_sec):
    """Run one async provider request and close it promptly when cancellation wins."""
    # NOTE(NOTE-002): 必須 cancel + await；只從外層 return 會留下 provider request。
    async def _run():
        task = asyncio.create_task(coro_factory())
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        try:
            while True:
                if is_cancelled(cancel_event):
                    raise LLMRequestCancelled("LLM request cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LLM request timeout after {timeout_sec}s")
                done, _pending = await asyncio.wait(
                    {task}, timeout=min(0.1, remaining)
                )
                if task in done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return asyncio.run(_run())
