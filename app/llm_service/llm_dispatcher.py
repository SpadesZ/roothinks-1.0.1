# 檔案路徑: app/llm_service/llm_dispatcher.py
# 產生時間: 2026-07-04 19:10 +08:00
# 版本: v0.7（grounding passthrough）
# 更新時間: 2026-08-17 +08:00
# 模組定位:
#   LLM task dispatcher：Task ID -> binding -> bus -> provider。
# 主要責任:
#   1. 保留既有 tuple/dict 回傳格式。
#   2. 新增結構化 error_code，避免只靠中文訊息判斷 fatal。
#   3. 以 opt-in file cache 保守快取成功 response。
#   4. 將 Manuscript cancel_event 傳到 bus，取消時禁止 cache 與 retry；若 provider
#      已回傳，usage 仍照實記錄已消耗的 token。
# 上下游:
#   matching task -> dispatch_task -> LlmDispatcher -> LlmBus -> provider adapter。
# 維護提醒:
#   - cache 預設關閉；不可保存 API key 或 connection raw row。
#   - cancellation 是終止狀態，不得包成一般 provider error 後重試。
#   - grounding=True 一律略過 cache，且 provider 不支援時是 fatal（不重試、不降級）。
# 驗證方式:
#   python -m pytest test/unit/test_llm_cancellation.py test/unit/test_llm_usage_and_pricing.py -q
# -----------------------------------------------------------------------------
from app.llm_service.llm_model import LLMModel
from app.llm_service.llm_bus import LlmBus
from app.errors import AppError, ErrorCode, ErrorSeverity
from app.services.llm_response_cache import (
    build_llm_cache_key,
    is_llm_cache_enabled,
    load_cached_response,
    save_cached_response,
)
import time
import logging
from app.llm_service.llm_cancellation import LLMRequestCancelled, is_cancelled

_LOGGER = logging.getLogger("LLMDispatcher")

class LlmDispatcher:
    """
    任務分派器 (Dispatcher)
    職責：解耦「業務邏輯(Task)」與「執行單元(Bus)」。
    流程：Task ID -> 查表(Bindings) -> 取得 Connection ID -> 初始化 Bus -> 執行
    """
    
    def execute(
        self, task_id: str, text: str, images: list = None, max_retries: int = 1,
        cancel_event=None, grounding: bool = False,
    ):
        """
        執行指定的 AI 任務
        :param task_id: 例如 'task_1paqswot'
        :param text: Prompt 文本
        :param images: 圖片路徑列表 (可選)
        :param max_retries: 重試次數
        :param grounding: 要求 provider 開 Google Search grounding（只有 Google adapter 支援）
        :return: (Success: bool, Result: dict, Message: str)
        """
        try:
            if is_cancelled(cancel_event):
                return False, {"cancelled": True}, "LLM request cancelled"
            # 1. 查詢綁定表 (Task Bindings)
            # 從資料庫獲取該任務 ID 綁定的 Connection ID
            binding = LLMModel.execute_query(
                "SELECT connection_id FROM task_bindings WHERE task_id = ?", 
                (task_id,), 
                fetch_one=True
            )
            
            # 防呆：檢查是否已綁定
            if not binding or not binding.get('connection_id'):
                err = AppError(
                    ErrorCode.LLM_NOT_BOUND,
                    f"Task '{task_id}' has no LLM binding.",
                    ErrorSeverity.USER_ACTION_REQUIRED,
                )
                return False, {"error_code": err.code.value}, str(err)
            
            conn_id = binding['connection_id']

            # 2. 初始化 Service Bus
            # 根據 Connection ID 載入對應的 Adapter (如 GoogleClient)
            bus = LlmBus()
            success, msg = bus.load_from_db(conn_id)
            
            if not success:
                err = AppError(ErrorCode.LLM_PROVIDER_ERROR, f"Bus Load Error: {msg}")
                return False, {"error_code": err.code.value}, str(err)

            cache_key = ""
            # NOTE(NOTE-039): grounded 請求刻意不進 cache——它的價值就是「現在上網查到的」，
            # 命中舊 cache 等於拿一份沒搜尋過的舊答案冒充搜尋結果，而且從回應看不出來。
            if is_llm_cache_enabled() and not images and not grounding:
                cache_key = build_llm_cache_key(
                    task_type=task_id,
                    prompt=text,
                    provider=getattr(bus, "_provider_name", "") or "",
                    model=getattr(bus, "_model_name", "") or "",
                    params={"max_retries": max_retries},
                )
                cached = load_cached_response(cache_key)
                if cached:
                    if is_cancelled(cancel_event):
                        return False, {"cancelled": True}, "LLM request cancelled"
                    response = dict(cached.get("response") or {})
                    response["cache_hit"] = True
                    response["cache_key"] = cache_key
                    return True, response, "Success"
            
            # 3. 執行生成
            # 調用 Bus 的統一介面發送請求
            ok, res, err = bus.send_message(
                text, images, cancel_event=cancel_event, grounding=grounding
            )

            if ok:
                # [usage] 記在這裡而不是各個 task 類別裡：這是唯一同時知道
                # task_id、vendor、model 與 usage 的地方，改一處就全流程涵蓋。
                # 記錄失敗只寫 log，絕不影響回傳——使用者要的是結果不是記帳。
                try:
                    from app.llm_service.llm_usage import record as _record_usage
                    _record_usage(
                        task_id=task_id,
                        vendor=getattr(bus, "_provider_name", "") or "",
                        model_name=getattr(bus, "_model_name", "") or "",
                        usage=(res or {}).get("usage"),
                    )
                except Exception:
                    _LOGGER.warning("[dispatcher] usage 記錄失敗（已忽略）", exc_info=True)

                if cache_key:
                    save_cached_response(cache_key, {"ok": True, "text": res.get("text", "")})
                return True, res, "Success"
            else:
                app_err = AppError(ErrorCode.LLM_PROVIDER_ERROR, f"Provider Error: {err}")
                return False, {"error_code": app_err.code.value}, str(app_err)

        except LLMRequestCancelled:
            return False, {"cancelled": True}, "LLM request cancelled"
        except Exception as e:
            app_err = AppError(ErrorCode.LLM_PROVIDER_ERROR, "Dispatcher system error", ErrorSeverity.RECOVERABLE, e)
            return False, {"error_code": app_err.code.value}, str(app_err)

# [Critical Fix] 實例化全域物件，供 Task 腳本 Import 使用
dispatcher = LlmDispatcher()


def dispatch_task(
    task_id, prompt, priority=5, images=None, max_retries=None, cancel_event=None,
    grounding=False,
):
    """
    向後相容函式：回傳舊版 dict 結構，供 task_3~task_7直接使用。

    grounding=True 時額外回 `grounding` 欄位（webSearchQueries / 命中網域 / chunk 數），
    呼叫端要靠 chunk_count 判斷這次是不是真的有搜尋。
    """
    retry_cnt = 0
    try:
        retry_cnt = int(max_retries) if max_retries is not None else 0
    except Exception:
        retry_cnt = 0
    retry_cnt = max(0, retry_cnt)

    attempts = retry_cnt + 1
    last_msg = ""
    for i in range(attempts):
        # NOTE(NOTE-002): 每次 provider call 與 retry 前都要先看同一個 event。
        if is_cancelled(cancel_event):
            return {"ok": False, "cancelled": True, "msg": "LLM request cancelled"}
        ok, res, msg = dispatcher.execute(
            task_id, prompt, images, cancel_event=cancel_event, grounding=grounding
        )
        if ok:
            out = {"ok": True, "text": res.get("text", "")}
            if res.get("cache_hit"):
                out["cache_hit"] = True
                out["cache_key"] = res.get("cache_key")
            if res.get("grounding") is not None:
                out["grounding"] = res.get("grounding")
            return out

        if (res or {}).get("cancelled") or is_cancelled(cancel_event):
            return {"ok": False, "cancelled": True, "msg": "LLM request cancelled"}

        last_msg = msg
        # 避免在安全阻擋或設定錯誤時做無效重試
        fatal_error_codes = {ErrorCode.LLM_NOT_BOUND.value, ErrorCode.SECRET_CONFIG_ERROR.value}
        fatal_keywords = [
            "Unknown vendor",
            "API Key missing",
            "Blocked by safety filters",
            # 綁錯 provider 不是暫時性故障，重試只是把同一個錯誤再撞三次。
            "does not support Google Search grounding",
        ]
        if (res or {}).get("error_code") in fatal_error_codes or any(k in (msg or "") for k in fatal_keywords):
            break

        if i < attempts - 1:
            delay = 0.6 * (i + 1)
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    return {"ok": False, "cancelled": True, "msg": "LLM request cancelled"}
            else:
                time.sleep(delay)

    return {"ok": False, "msg": last_msg}
