# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/adapter/llm_stub.py
# 模組定位:
#   LLM provider adapter（vendor="stub"）。**驗證專用**，不是離線模式、
#   不是降級 provider，任何情況下都不得用它產生真實研究內容。
# 主要責任:
#   1. 提供決定性、零成本、不需金鑰的 provider 回覆，讓「送出 → 存檔 → 重整
#      → 讀回 → 下一輪帶前文」這條鏈能在真瀏覽器上走完。
#   2. 把**實際收到的 prompt** 落檔，作為「第 N 輪確實帶上了前 N-1 輪」的直接證據。
# 上游呼叫者:
#   app/llm_service/llm_bus.py 的 _load_driver()，經 llm_connections.vendor='stub'
#   動態載入（模組名 llm_<vendor>，類名須以 Client 結尾且含 vendor 前三字）。
# 下游服務:
#   無。不發出任何網路請求。
# 輸入輸出契約:
#   send_text_and_optional_images(text, filepaths=None, history=None, cancel_event=None)
#     -> (ok: bool, {"text": str, "usage": dict}, msg: str)
#   回覆一律以 STUB_MARKER 開頭。
# 讀寫或持久化位置:
#   <data_root>/_stub_llm/prompt_<epoch_ms>_<seq>.json。data_root 由
#   SQLALCHEMY_DATABASE_URI 所在目錄推導，無 app context 時退回 cwd/data。
#   **不得新增原始碼相對的寫死路徑。**
# ACL/安全邊界:
#   - NOTE(NOTE-028) 雙重上鎖：未設 ROOTHINKS_ALLOW_STUB_LLM=1 時 __init__ 直接
#     raise；回覆強制帶 [STUB] 前綴且不可由呼叫端關閉。
#   - 落檔內容含完整 prompt，可能包含專案內容。因此只在 stub 啟用時寫，
#     且與其他 data 一樣受既有的 data root 保護；不得送往任何外部服務。
# 不變量:
#   1. 沒有 env 就不能有實例（擋「跑得起來」）。
#   2. 每則回覆都看得出是假的（擋「看起來像真的」）。
#   3. 不做任何網路 I/O。
# 相關 NOTE:
#   NOTE-028（本檔存在的理由與兩道鎖）。
# 驗證:
#   python -m pytest test/unit/test_stub_llm_gate.py -q
# ------------------------------------------------------------------------------
import json
import os
import threading
import time
from typing import Dict, List, Tuple

ENV_FLAG = "ROOTHINKS_ALLOW_STUB_LLM"

# NOTE(NOTE-028): 刻意是模組常數而非建構參數 —— 可以由呼叫端關掉的標記等於沒有標記。
STUB_MARKER = "[STUB]"

_seq_lock = threading.Lock()
_seq = 0


def stub_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip() == "1"


def _data_root() -> str:
    """
    落檔位置由設定推導，不使用原始碼相對路徑。

    與 manuscript_io._get_data_root() 同一套規則（刻意各自實作，避免 adapter
    反向依賴 manuscript 模組）：有 app context 就用 DB URI 所在目錄，
    沒有就退回 cwd/data。
    """
    try:
        from flask import current_app

        uri = str(current_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
        prefix = "sqlite:///"
        if uri.startswith(prefix):
            return os.path.dirname(os.path.abspath(uri[len(prefix):]))
    except Exception:
        pass
    return os.path.join(os.getcwd(), "data")


class StubClient:
    """
    決定性的假 provider。

    回覆內容刻意包含「收到的 prompt 長度」與「history 輪數」：驗收時光看畫面
    就能分辨「有帶前文」與「沒帶前文」，不必每次都去翻落檔。
    """

    def __init__(self, api_key: str = "", model: str = "stub-1"):
        if not stub_enabled():
            # NOTE(NOTE-028) 第一道鎖。訊息刻意寫清楚，避免有人在正式站
            # 建了 vendor='stub' 的連線之後，只看到一句無來由的載入失敗。
            raise RuntimeError(
                "Stub LLM adapter is disabled. "
                f"Set {ENV_FLAG}=1 only in an isolated verification environment. "
                "This adapter returns fabricated text and must never serve real work."
            )
        self.api_key = api_key
        self.model = model or "stub-1"

    # -- prompt 落檔（第三輪含前兩輪的直接證據） ---------------------------------

    def _record_prompt(self, text: str) -> str:
        global _seq
        with _seq_lock:
            _seq += 1
            seq = _seq

        out_dir = os.path.join(_data_root(), "_stub_llm")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"prompt_{int(time.time() * 1000)}_{seq:04d}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"seq": seq, "model": self.model, "prompt": text},
                fh, ensure_ascii=False, indent=2,
            )
        return path

    # -- provider 介面 ---------------------------------------------------------

    def send_text_and_optional_images(
        self, text: str, filepaths: List[str] = None, history=None, cancel_event=None
    ) -> Tuple[bool, Dict, str]:
        prompt = text or ""
        try:
            self._record_prompt(prompt)
        except Exception:
            # 落檔失敗不得讓驗證環境整個停擺；證據少一份，回覆仍然要給。
            pass

        # 回覆內容是 prompt 的可觀測摘要。用「User:」出現次數當輪數，
        # 因為 task2A_paqchat 就是用那個前綴組 Conversation History。
        turns = prompt.count("User:")
        reply = (
            f"{STUB_MARKER} 收到 {len(prompt)} 字元的 prompt，"
            f"其中對話輪次標記 {turns} 次。"
        )
        return True, {
            "text": reply,
            "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 16},
        }, "Success"

    # 部分呼叫端只用 send_message/generate；保持同一份實作避免行為分岔。
    def send_message(self, text: str, images=None, cancel_event=None):
        return self.send_text_and_optional_images(
            text, filepaths=images, cancel_event=cancel_event
        )
