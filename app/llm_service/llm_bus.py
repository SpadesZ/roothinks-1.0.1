# Roothinks source maintenance contract
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 檔案路徑: roothinks/app/llm_service/llm_bus.py
# 產生時間: 2026-07-04 23:35 +08:00
# 版本: v0.4（cancel_event passthrough）
# 更新時間: 2026-08-10 +08:00
# 模組定位:
#   LLM 服務匯流排 (Service Bus)。從 DB 讀取連線設定 -> 動態載入 Adapter ->
#   建立 Client 實例,對上提供統一 send_message 介面。
# 主要責任: 從 llm_match DB 載入 connection/model，動態建立 provider adapter 並原樣傳遞 prompt、usage 與 cancel_event。
#   1. load_from_db():依 connection id 載入 vendor/api_key/model 並初始化 Client。
#   2. _load_driver():依 vendor 名稱動態匯入 adapter 模組(llm_<vendor>.py)。
#   3. send_message():統一發送代理。
#   4. 原樣轉交可選 cancel_event；bus 不自行建立或取代 job cancellation identity。
# 維護提醒:
#   - adapter 模組必須恰好有一個以 Client 結尾、且類名含 vendor 前綴的類別,
#     否則 _load_driver 會載錯或載不到——新增 adapter 時務必遵守命名慣例。
#   - v0.3 起 load_from_db 會區分「API Key 未設定」與「解密失敗」兩種錯誤,
#     解密失敗多半是 FERNET_KEY 與加密當時不一致,錯誤訊息已註明。
#   - 新增 adapter 的 send_text_and_optional_images 必須接受 cancel_event=None。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_llm_dispatcher.py -q 應全綠。
# ------------------------------------------------------------------------------
import importlib
import inspect
import threading
from typing import Dict, List, Tuple
from app.llm_service.llm_model import LLMModel

_bus_singleton = None
_bus_lock = threading.Lock()

class LlmBus:
    """
    LLM 服務匯流排 (Service Bus)
    職責：從 DB 讀取連線設定 -> 動態載入 Adapter -> 建立 Client 實例
    """
    def __init__(self):
        self.MAX_BUS_SLOTS = 32
        self._client = None
        self._provider_name = None
        self._model_name = None

    def get_driver(self, bus_id: int):
        """向後相容：依 connection id 載入並回傳 driver 實例。"""
        ok, _ = self.load_from_db(bus_id)
        if ok:
            return self._client
        return None

    def _load_driver(self, vendor: str):
        """
        [v0.2 重構] 動態載入 Adapter 驅動，消除硬編碼。
        根據 vendor 名稱自動尋找對應的模組與 Client 類別。
        """
        vendor_key = vendor.lower()
        mod_path = f"app.llm_service.adapter.llm_{vendor_key}"
        
        try:
            # 動態匯入對應的模組 (例如 app.llm_service.adapter.llm_openai)
            mod = importlib.import_module(mod_path)
            
            # 尋找模組中以 Client 結尾的類別
            for name, obj in inspect.getmembers(mod, inspect.isclass):
                if name.endswith("Client"):
                    # 簡單過濾，確保抓到的類別名稱包含 vendor 關鍵字 (或特殊處理 openrouter)
                    if vendor_key[:3].lower() in name.lower() or vendor_key == "openrouter":
                        return obj, ""
                        
            return None, f"No valid Client class found in {mod_path}"
        except ImportError as e:
            return None, f"Adapter Import Error ({mod_path}): {e}"
        except Exception as e:
            return None, f"Dynamic Load Error for {vendor_key}: {e}"

    def load_from_db(self, conn_id: int) -> Tuple[bool, str]:
        """
        從 llm_connections 表載入設定並初始化 Client
        """
        try:
            # 使用 LLMModel 查詢 DB
            conn_data = LLMModel.execute_query(
                "SELECT * FROM llm_connections WHERE id = ?", 
                (conn_id,), 
                fetch_one=True
            )
            
            if not conn_data:
                return False, f"Connection ID {conn_id} not found in DB"
            
            vendor = conn_data['vendor']
            raw_key = conn_data.get('api_key')
            is_encrypted = bool(conn_data.get('is_encrypted'))
            # 傳入 conn_id 讓 legacy 明文列可自動升級加密。
            api_key = LLMModel.decrypt_api_key_value(raw_key, is_encrypted, conn_id=conn_id)
            model = conn_data['model_name']

            if not api_key:
                # 區分「未設定」與「解密失敗」:後者多為 FERNET_KEY 與加密時不一致。
                if raw_key and is_encrypted:
                    return False, (
                        f"API Key decrypt failed for connection {conn_id} "
                        "(FERNET_KEY 與加密當時不一致,請檢查 .env)"
                    )
                return False, f"API Key missing for connection {conn_id}"

            # 載入驅動
            ClientCls, err = self._load_driver(vendor)
            if not ClientCls:
                return False, err
            
            # 初始化 Client
            try:
                self._client = ClientCls(api_key=api_key, model=model)
                self._provider_name = vendor
                self._model_name = model
                return True, "Provider loaded successfully"
            except Exception as e:
                self._client = None
                return False, f"Client Init Error: {e}"

        except Exception as e:
            return False, f"DB Load Error: {e}"

    def send_message(
        self, text: str, images: List[str] = None, cancel_event=None
    ) -> Tuple[bool, Dict, str]:
        """
        統一發送介面，代理至底層 Client
        """
        if not self._client:
            return False, {}, "Bus not initialized (No Provider)"
        
        if hasattr(self._client, 'send_text_and_optional_images'):
            # NOTE(NOTE-002): event identity 必須從 Socket job 一路保持到 adapter。
            return self._client.send_text_and_optional_images(
                text=text, filepaths=images, cancel_event=cancel_event
            )
        else:
            return False, {}, "Driver missing 'send_text_and_optional_images' method"

    def dispatch_task(self, task_id: str, text: str, priority: int = 5, images: List[str] = None, max_retries: int = 1):
        """
        向後相容介面：供 legacy matching tasks 直接以 task_id 呼叫。
        回傳格式與舊版一致: (success, reply_text)
        """
        from app.llm_service.llm_dispatcher import dispatch_task as disp_task
        res = disp_task(task_id, text, priority, images, max_retries)
        return res.get("ok", False), res.get("text", res.get("msg", ""))


def get_bus() -> LlmBus:
    """全域 Bus 實例 (lazy singleton)。"""
    global _bus_singleton
    if _bus_singleton is None:
        with _bus_lock:
            if _bus_singleton is None:
                _bus_singleton = LlmBus()
    return _bus_singleton
