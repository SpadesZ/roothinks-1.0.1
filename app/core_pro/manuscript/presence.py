# Roothinks source maintenance contract
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/core_pro/manuscript/presence.py
# 產生時間: 2026-07-27 +08:00
# 版本: v1.0
# 模組定位:
#   Manuscript 專案「目前有誰在線上」的行程內註冊表。
# 背景:
#   權限模型允許同一章節有多個寫入者——總編輯與 owner 依定義可寫所有章節，
#   同一章節也可以指派給多位限定編輯。實測確認同一章節可同時有 5 人有寫入權，
#   最常見的情境是指導教授在學生撰寫某章的當下進去修改那一章。
#   撞在一起不會壞（草稿每人一份、版本各自保留），但雙方互不知情，
#   最後會各自存出不同版本再人工合併。在線提示讓雙方至少知道對方也在。
# 主要責任:
#   1. 記錄 sid -> (pid, user_id, name)。
#   2. 查詢某專案目前有誰在線（依 user_id 去重）。
#   3. 連線結束時清除。
# 呼叫來源:
#   manuscript_routes.py 的 connect / disconnect handler。
# 輸入輸出契約:
#   純記憶體結構，不碰資料庫、不依賴 Flask context，可直接單元測試。
# 安全邊界:
#   - 刻意**只記錄專案層級**，不記章節、不記誰在改哪裡。
#     章節層級的在線狀態會把「某人被指派了哪一章」洩漏給看不到該章的限定編輯，
#     而且產品上也不需要——使用者要的資訊是「還有人在」而不是「誰在改哪」。
#   - 送出對象限定同專案成員（connect 已驗過 workspace role 才會 join room）。
#   - 名稱由呼叫端提供，輸出到前端時仍需跳脫（前端 _esc）。
# 維護提醒:
#   - 這是**單一行程**的記憶體狀態。目前部署是單一容器單一 process，成立。
#     若日後改成多 worker（gunicorn -w N）或多台機器，在線清單會各看各的，
#     屆時要換成 flask-socketio 的 message_queue（Redis）。
#   - 註冊表以 sid 為鍵：同一人開兩個分頁算兩筆，查詢時依 user_id 去重。

import threading
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
# sid -> {"pid", "user_id", "name"}
_BY_SID: Dict[str, Dict[str, Any]] = {}


def enter(sid: str, pid: str, user_id: Any, name: str) -> None:
    """記錄一條連線進入某專案。"""
    if not sid or not pid:
        return
    with _LOCK:
        _BY_SID[sid] = {
            "pid": str(pid),
            "user_id": user_id,
            "name": name or "協作者",
        }


def drop(sid: str) -> Optional[str]:
    """連線結束。回傳它離開前所在的 pid，供呼叫端廣播；未註冊回 None。"""
    with _LOCK:
        info = _BY_SID.pop(sid, None)
        return info["pid"] if info else None


def online(pid: str) -> List[Dict[str, Any]]:
    """某專案目前在線的人（依 user_id 去重，同一人開多分頁只算一筆）。"""
    merged: Dict[Any, Dict[str, Any]] = {}
    with _LOCK:
        rows = [v for v in _BY_SID.values() if v["pid"] == str(pid)]
    for r in rows:
        merged.setdefault(r["user_id"], {"user_id": r["user_id"], "name": r["name"]})
    return sorted(merged.values(), key=lambda x: str(x["name"]))


def reset() -> None:
    """僅供測試使用。"""
    with _LOCK:
        _BY_SID.clear()
