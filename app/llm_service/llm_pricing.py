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
#   - 依 prompt 長度分段收費的模型（Gemini pro 系列）要填 tier_over_input_tokens
#     與 tier_* 費率；只填低價那段會系統性低估長 prompt 的花費。
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
    # cached_input：重複送出的 prompt 前綴會以快取價計費（約為 input 的 1/4）。
    # 不分開算的話，長 prompt 反覆呼叫的情境會被系統性高估。
    "openai/gpt-4.1": {
        "input": 2.00, "cached_input": 0.50, "output": 8.00,
        "checked": "2026-07-28", "source": "OpenAI 公開價目",
    },
    "openai/gpt-4.1-mini": {
        "input": 0.40, "cached_input": 0.10, "output": 1.60,
        "checked": "2026-07-28", "source": "OpenAI 公開價目",
    },

    # --- Google Gemini ---
    # 文獻解析的 task_5interpret / task_5b_reflow 走 Gemini，但這一整個系列
    # 原本都沒有單價，於是文獻列表的金額只能顯示「≥ $X（部分模型未設定單價）」。
    # 使用者要的是一個可以直接拿來決定「要不要繼續跑翻譯」的完整美金數字，
    # 補上單價才給得出來。
    "google/gemini-2.5-flash": {
        "input": 0.30, "cached_input": 0.03, "output": 2.50,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-2.5-flash-lite": {
        "input": 0.10, "cached_input": 0.01, "output": 0.40,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    # tier_over_input_tokens / tier_*：pro 系列依 prompt 長度分兩段收費。
    # 只登記低價那段的話，長 prompt 會被系統性低估——而低估比高估危險：
    # 使用者會照著一個偏小的數字決定要不要繼續花錢。
    "google/gemini-2.5-pro": {
        "input": 1.25, "cached_input": 0.125, "output": 10.00,
        "tier_over_input_tokens": 200_000,
        "tier_input": 2.50, "tier_cached_input": 0.25, "tier_output": 15.00,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-3-flash-preview": {
        "input": 0.50, "cached_input": 0.05, "output": 3.00,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-3.1-flash-lite": {
        "input": 0.25, "cached_input": 0.025, "output": 1.50,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-3.1-pro-preview": {
        "input": 2.00, "cached_input": 0.20, "output": 12.00,
        "tier_over_input_tokens": 200_000,
        "tier_input": 4.00, "tier_cached_input": 0.40, "tier_output": 18.00,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-3.5-flash": {
        "input": 1.50, "cached_input": 0.15, "output": 9.00,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-3.5-flash-lite": {
        "input": 0.30, "cached_input": 0.03, "output": 2.50,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-3.6-flash": {
        "input": 1.50, "cached_input": 0.15, "output": 7.50,
        "checked": "2026-08-03", "source": "Google Gemini API 公開價目",
    },

    # Robotics ER 系列：具身推理模型，**不適合拿來做文字任務**。
    # 會登記在這裡不是推薦使用，而是因為正式站曾經誤綁在 task_4cv 上跑過
    # （下拉選單只過濾「名字含 gemini」，把 robotics/TTS/image 全列了出來）。
    # 那些呼叫的花費是真的，不給單價的話歷史帳就永遠是「未設定」。
    # 定價與推薦與否是兩件事——記帳要誠實，選型的建議寫在文件裡。
    "google/gemini-robotics-er-1.6-preview": {
        "input": 1.00, "output": 5.00,
        "checked": "2026-08-04", "source": "Google Gemini API 公開價目",
    },
    "google/gemini-robotics-er-2-preview": {
        "input": 2.00, "output": 10.00,
        "checked": "2026-08-04", "source": "Google Gemini API 公開價目",
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


def _effective_rates(price: Dict[str, object], input_tokens: int):
    """依這次呼叫的 prompt 長度取出實際適用的費率。

    Gemini pro 系列以 prompt 長度分兩段收費（>200k tokens 走高階費率），
    門檻用 input_tokens 判定，與廠商計費方式一致。
    沒有 tier_over_input_tokens 的模型維持單一費率，行為不變。
    """
    threshold = price.get("tier_over_input_tokens")
    if threshold and input_tokens > int(threshold):  # type: ignore[arg-type]
        return (float(price["tier_input"]),
                price.get("tier_cached_input"),
                float(price["tier_output"]))
    return float(price["input"]), price.get("cached_input"), float(price["output"])


def estimate_cost_usd(
    vendor: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
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
        c = max(0, int(cached_input_tokens or 0))
    except (TypeError, ValueError):
        return None, None

    # 廠商回報的 input_tokens **已包含** cached 部分。要把它拆出來以較低的
    # 快取單價計，否則長 prompt 反覆呼叫的情境會被系統性高估。
    # c 夾在 [0, i]：回報異常時不能讓費用變成負的。
    c = min(c, i)
    fresh = i - c
    in_rate, cached_rate, out_rate = _effective_rates(price, i)

    cost = (fresh / 1_000_000.0) * in_rate + (o / 1_000_000.0) * out_rate
    if c:
        rate = float(cached_rate) if cached_rate is not None else in_rate
        cost += (c / 1_000_000.0) * rate

    note = (f"in ${in_rate}/1M"
            + (f", cached ${cached_rate}/1M" if cached_rate is not None else "")
            + f", out ${out_rate}/1M ({price['checked']} 核對)")
    return round(cost, 6), note


def known_models() -> Dict[str, Dict[str, object]]:
    """供設定畫面/診斷用：目前有單價的模型。"""
    return {k: dict(v) for k, v in _PRICES.items()}
