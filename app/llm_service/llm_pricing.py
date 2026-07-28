# 檔案路徑: app/llm_service/llm_pricing.py
# 產生時間: 2026-07-28 +08:00
# 版本: v1.0
# 模組定位:
#   LLM 單價表與費用換算。純函數，不碰資料庫、不依賴 Flask context。
# 主要責任:
#   1. 依 vendor + model 查出每百萬 token 的輸入/輸出單價。
#   2. 由 token 用量換算成金額（USD）。
# 安全邊界:
#   - **單價是會過期的外部事實**，不是程式邏輯。廠商調價、模型改名、
#     推出新層級，這張表就會失準。因此：
#       a) 查不到單價時回傳 None，呼叫端必須顯示「單價未設定」而不是 0，
#          絕不可讓使用者以為「這次不用錢」。
#       b) 每筆單價都標註來源與查核日期，維護時一眼看得出多久沒更新。
#   - 換算結果僅供參考，實際帳單以廠商為準。UI 上必須寫明這點。
# 維護提醒:
#   - 新增模型請一併補上 checked 日期。
#   - 模型名稱比對採「前綴 + 正規化」：gpt-4.1-2025-04-14 這種帶日期後綴的
#     會落到 gpt-4.1；但 gemini-flash-latest 這類 alias 指向的實際模型會變，
#     刻意不給預設價，逼使用者明確設定。

from typing import Dict, Optional, Tuple

# 單價單位：USD / 1M tokens。(input, output)
#
# checked 欄位＝最後一次人工核對廠商公開價目的日期。
# 超過半年沒核對就該當成不可信——這裡不做自動過期判定，因為悄悄把價格
# 歸零比顯示舊價更危險，但維護者看到日期就知道該去查了。
_PRICES: Dict[str, Dict[str, object]] = {
    "openai/gpt-4.1": {
        "input": 2.00, "output": 8.00,
        "checked": "2026-07-28", "source": "OpenAI 公開價目",
    },
    "openai/gpt-4.1-mini": {
        "input": 0.40, "output": 1.60,
        "checked": "2026-07-28", "source": "OpenAI 公開價目",
    },
}

# 這些是 alias（指向的實際模型會隨時間改變），刻意不給預設單價：
# 給了就等於猜，而猜錯的金額比「未設定」更誤導。
_ALIAS_NO_PRICE = {
    "google/gemini-flash-latest",
    "google/gemini-pro-latest",
    "openrouter/auto",
}


def normalize_model(vendor: str, model: str) -> str:
    """把 vendor+model 正規化成查表用的鍵。"""
    v = str(vendor or "").strip().lower()
    m = str(model or "").strip().lower()
    return f"{v}/{m}"


def _strip_date_suffix(key: str) -> str:
    """gpt-4.1-2025-04-14 -> gpt-4.1（只剝日期後綴，不亂剝版本號）。"""
    import re
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", key)


def lookup(vendor: str, model: str) -> Optional[Dict[str, object]]:
    """查單價。查不到回 None——呼叫端必須據此顯示『單價未設定』。"""
    key = normalize_model(vendor, model)
    if key in _ALIAS_NO_PRICE:
        return None
    if key in _PRICES:
        return dict(_PRICES[key])
    stripped = _strip_date_suffix(key)
    if stripped in _PRICES:
        return dict(_PRICES[stripped])
    return None


def estimate_cost_usd(
    vendor: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Tuple[Optional[float], Optional[str]]:
    """換算費用。

    回傳 (金額, 單價註記)；查不到單價時回 (None, None)，
    呼叫端據此顯示「單價未設定」而非 0。
    """
    price = lookup(vendor, model)
    if not price:
        return None, None
    try:
        i = max(0, int(input_tokens or 0))
        o = max(0, int(output_tokens or 0))
    except (TypeError, ValueError):
        return None, None
    cost = (i / 1_000_000.0) * float(price["input"]) + \
           (o / 1_000_000.0) * float(price["output"])
    note = (f"in ${price['input']}/1M, out ${price['output']}/1M "
            f"({price['checked']} 核對)")
    return round(cost, 6), note


def known_models() -> Dict[str, Dict[str, object]]:
    """供設定畫面/診斷用：目前有單價的模型。"""
    return {k: dict(v) for k, v in _PRICES.items()}
