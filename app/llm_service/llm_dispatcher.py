#路徑(./app/llm_service/llm_dispatcher.py) #版本 v0.4 #更版時間 20260419-1530
from app.llm_service.llm_model import LLMModel
from app.llm_service.llm_bus import LlmBus
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
                return False, {}, f"Task '{task_id}' 未綁定任何 LLM 線路，請至 LAVA 設定頁進行綁定。"
            
            conn_id = binding['connection_id']

            # 2. 初始化 Service Bus
            # 根據 Connection ID 載入對應的 Adapter (如 GoogleClient)
            bus = LlmBus()
            success, msg = bus.load_from_db(conn_id)
            
            if not success:
                return False, {}, f"Bus Load Error: {msg}"
            
            # 3. 執行生成
            # 調用 Bus 的統一介面發送請求
            ok, res, err = bus.send_message(text, images)
            
            if ok:
                return True, res, "Success"
            else:
                return False, {}, f"Provider Error: {err}"

        except Exception as e:
            return False, {}, f"Dispatcher System Error: {str(e)}"

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
            return {"ok": True, "text": res.get("text", "")}

        last_msg = msg
        # 避免在安全阻擋或設定錯誤時做無效重試
        fatal_keywords = ["未綁定", "Unknown vendor", "API Key missing", "Blocked by safety filters"]
        if any(k in (msg or "") for k in fatal_keywords):
            break

        if i < attempts - 1:
            time.sleep(0.6 * (i + 1))

    return {"ok": False, "msg": last_msg}