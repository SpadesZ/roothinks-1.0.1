# 檔案路徑: app/llm_service/llm_dispatcher.py
# 產生時間: 2026-07-04 19:10 +08:00
# 版本: v0.5
# 模組定位:
#   LLM task dispatcher：Task ID -> binding -> bus -> provider。
# 主要責任:
#   1. 保留既有 tuple/dict 回傳格式。
#   2. 新增結構化 error_code，避免只靠中文訊息判斷 fatal。
#   3. 以 opt-in file cache 保守快取成功 response。
# 維護提醒:
#   - cache 預設關閉；不可保存 API key 或 connection raw row。
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

class LlmDispatcher:
    """
    任務分派器 (Dispatcher)
    職責：解耦「業務邏輯(Task)」與「執行單元(Bus)」。
    流程：Task ID -> 查表(Bindings) -> 取得 Connection ID -> 初始化 Bus -> 執行
    """
    
    def execute(self, task_id: str, text: str, images: list = None, max_retries: int = 1):
        """
        執行指定的 AI 任務
        :param task_id: 例如 'task_1paqswot'
        :param text: Prompt 文本
        :param images: 圖片路徑列表 (可選)
        :param max_retries: 重試次數
        :return: (Success: bool, Result: dict, Message: str)
        """
        try:
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
            if is_llm_cache_enabled() and not images:
                cache_key = build_llm_cache_key(
                    task_type=task_id,
                    prompt=text,
                    provider=getattr(bus, "_provider_name", "") or "",
                    model=getattr(bus, "_model_name", "") or "",
                    params={"max_retries": max_retries},
                )
                cached = load_cached_response(cache_key)
                if cached:
                    response = dict(cached.get("response") or {})
                    response["cache_hit"] = True
                    response["cache_key"] = cache_key
                    return True, response, "Success"
            
            # 3. 執行生成
            # 調用 Bus 的統一介面發送請求
            ok, res, err = bus.send_message(text, images)
            
            if ok:
                if cache_key:
                    save_cached_response(cache_key, {"ok": True, "text": res.get("text", "")})
                return True, res, "Success"
            else:
                app_err = AppError(ErrorCode.LLM_PROVIDER_ERROR, f"Provider Error: {err}")
                return False, {"error_code": app_err.code.value}, str(app_err)

        except Exception as e:
            app_err = AppError(ErrorCode.LLM_PROVIDER_ERROR, "Dispatcher system error", ErrorSeverity.RECOVERABLE, e)
            return False, {"error_code": app_err.code.value}, str(app_err)

# [Critical Fix] 實例化全域物件，供 Task 腳本 Import 使用
dispatcher = LlmDispatcher()


def dispatch_task(task_id, prompt, priority=5, images=None, max_retries=None):
    """
    向後相容函式：回傳舊版 dict 結構，供 task_3~task_7直接使用。
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
        ok, res, msg = dispatcher.execute(task_id, prompt, images)
        if ok:
            out = {"ok": True, "text": res.get("text", "")}
            if res.get("cache_hit"):
                out["cache_hit"] = True
                out["cache_key"] = res.get("cache_key")
            return out

        last_msg = msg
        # 避免在安全阻擋或設定錯誤時做無效重試
        fatal_error_codes = {ErrorCode.LLM_NOT_BOUND.value, ErrorCode.SECRET_CONFIG_ERROR.value}
        fatal_keywords = ["Unknown vendor", "API Key missing", "Blocked by safety filters"]
        if (res or {}).get("error_code") in fatal_error_codes or any(k in (msg or "") for k in fatal_keywords):
            break

        if i < attempts - 1:
            time.sleep(0.6 * (i + 1))

    return {"ok": False, "msg": last_msg}
