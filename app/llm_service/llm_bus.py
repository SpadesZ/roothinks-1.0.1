#路徑(./app/llm_service/llm_bus.py) #版本 v0.2 #更版時間 20260419-1530
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
            api_key = LLMModel.decrypt_api_key_value(
                conn_data.get('api_key'),
                bool(conn_data.get('is_encrypted'))
            )
            model = conn_data['model_name']
            
            if not api_key:
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

    def send_message(self, text: str, images: List[str] = None) -> Tuple[bool, Dict, str]:
        """
        統一發送介面，代理至底層 Client
        """
        if not self._client:
            return False, {}, "Bus not initialized (No Provider)"
        
        if hasattr(self._client, 'send_text_and_optional_images'):
            return self._client.send_text_and_optional_images(text=text, filepaths=images)
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