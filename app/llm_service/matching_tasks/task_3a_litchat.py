# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/task_3a_litchat.py
# 模組定位: LLM task 業務層；Literature 對話式找文獻的 A 腳（對話 + 意圖判斷 + 回覆組合）。
# 主要責任: 判斷這一句要不要發動搜尋、把 B/C 驗證後的清單組成回覆，不自行產生文獻。
# 上下游: literature_chat_routes -> 本檔 -> dispatcher(task_3a_litchat) 與 task_3bc_scout。
# 維護邊界:
#   - **A 不得產生任何文獻**。compose_reply 只能引用傳入的 verified 清單；
#     prompt 明令禁止新增，回覆中出現清單外的論文即為契約破損。
#   - 關鍵字快篩只是給 A 的提示，不是判斷本身：語意決定要不要搜尋
#     （「不用再找了」含「找」但不該觸發搜尋）。
#   - 意圖判斷失敗時退回關鍵字結果，但必須在 meta.intent_source 標明是退化路徑，
#     不得看起來像正常判斷。
# 驗證: python -m pytest test/unit/test_literature_chat_pipeline.py -q
#路徑(./app/llm_service/matching_tasks/task_3a_litchat.py) #版本 v0.1 #更版時間 20260817-1200
import json
import re
from typing import Any, Callable, Dict, List

from app.llm_service.llm_dispatcher import dispatch_task
from app.llm_service.matching_tasks import task_3bc_scout

TASK_ID = "task_3a_litchat"

# 關鍵字快篩：命中只代表「這句話有搜尋的味道」，最終仍由 A 的語意判斷決定。
_SEARCH_HINT_PATTERN = re.compile(
    r"(搜尋|搜索|查找|找一?些?|尋找|推薦|建議.*文獻|要.*論文|需要|給我|列出|有沒有|"
    r"文獻|論文|paper|papers|article|reference|citation|literature|study|studies|"
    r"search|find|recommend|suggest)",
    re.IGNORECASE,
)

_HISTORY_TURNS = 6


def keyword_hint(user_input: str) -> bool:
    return bool(_SEARCH_HINT_PATTERN.search(str(user_input or "")))


def _format_history(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "（無）"
    lines = []
    for msg in history[-_HISTORY_TURNS:]:
        user = str(msg.get("user") or "").strip()
        ai = str(msg.get("ai") or "").strip()
        if user:
            lines.append(f"User: {user}")
        if ai:
            lines.append(f"AI: {ai[:400]}")
    return "\n".join(lines) or "（無）"


def _parse_json_object(raw_text: str) -> Dict[str, Any]:
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(re.sub(r",\s*([\]}])", r"\1", text[start : end + 1]))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def classify_intent(
    history: List[Dict[str, Any]],
    user_input: str,
    project_context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    判斷這一輪要不要發動搜尋。
    :return: {"should_search": bool, "query": str, "reason": str, "intent_source": "llm"|"keyword"}
    """
    user_input = str(user_input or "").strip()
    hint = keyword_hint(user_input)
    context = project_context or {}
    topic = str(context.get("research_title") or context.get("manual_context") or "").strip()

    prompt = (
        "你是文獻搜尋助理的意圖判斷模組。判斷使用者這一句是否需要**發動一次學術文獻搜尋**。\n\n"
        f"研究專案主題：{topic or '（未設定）'}\n"
        f"對話歷史：\n{_format_history(history)}\n\n"
        f"使用者這一句：{user_input}\n"
        f"關鍵字快篩結果（僅供參考，不是答案）：{'命中' if hint else '未命中'}\n\n"
        "判斷原則：\n"
        "- 只有在使用者**想要取得論文清單**時才 true。\n"
        "- 對既有結果的追問、閒聊、請你解釋概念、或明確表示不用再找，一律 false。\n"
        "- true 時，把使用者的需求改寫成一句適合學術檢索的英文查詢字串放進 query，\n"
        "  補上專案主題的脈絡，但不要加入使用者沒表達的限制。\n\n"
        "只輸出 JSON，不要任何說明文字：\n"
        '{"should_search":false,"query":"","reason":""}'
    )

    result = dispatch_task(TASK_ID, prompt)
    parsed = _parse_json_object(result.get("text", "")) if result.get("ok") else {}

    if not parsed:
        # 退化路徑：A 判不出來就先信關鍵字，但要讓上層看得到這是退化的。
        return {
            "should_search": hint,
            "query": user_input if hint else "",
            "reason": "意圖判斷模型無法解析，暫以關鍵字快篩結果代替",
            "intent_source": "keyword",
        }

    should_search = bool(parsed.get("should_search"))
    query = str(parsed.get("query") or "").strip()
    if should_search and not query:
        query = user_input
    return {
        "should_search": should_search,
        "query": query,
        "reason": str(parsed.get("reason") or "").strip()[:200],
        "intent_source": "llm",
    }


def compose_reply(
    history: List[Dict[str, Any]],
    user_input: str,
    papers: List[Dict[str, Any]],
    meta: Dict[str, Any] = None,
    project_context: Dict[str, Any] = None,
) -> str:
    """
    用已驗證的清單組出回覆。papers 為空時也要老實說沒找到，不得改用模型記憶補位。
    """
    meta = meta or {}
    context = project_context or {}
    topic = str(context.get("research_title") or context.get("manual_context") or "").strip()

    compact = [
        {
            "n": idx + 1,
            "title": p.get("title", ""),
            "year": p.get("year"),
            "venue": p.get("venue", ""),
            "source": p.get("source", ""),
            "consensus": p.get("consensus", ""),
            "why": p.get("why", ""),
        }
        for idx, p in enumerate(papers[:20])
    ]

    prompt = (
        "你是研究助理，正在協助使用者釐清並蒐集文獻。\n\n"
        f"研究專案主題：{topic or '（未設定）'}\n"
        f"對話歷史：\n{_format_history(history)}\n"
        f"使用者這一句：{user_input}\n\n"
        f"已通過驗證的文獻清單（共 {len(papers)} 篇）：\n"
        f"{json.dumps(compact, ensure_ascii=False)}\n"
        f"被剔除的筆數：{len(meta.get('dropped') or [])}\n\n"
        "撰寫規則：\n"
        "1. 用繁體中文，200 字以內。\n"
        "2. **只能提到上面清單裡的論文**，用編號指涉（例如「第 1、3 篇」）。\n"
        "   嚴禁補充清單以外的任何論文、作者或年份，即使你認為那篇很重要。\n"
        "3. 說明這批文獻怎麼分群、以及建議先讀哪幾篇。\n"
        "4. 清單為空時直接說明沒有找到通過驗證的文獻，並建議如何調整檢索方向。\n"
        "5. 不要重複列出標題（清單會顯示在你的回覆下方），只寫判讀與建議。\n"
        "只輸出純文字。"
    )

    result = dispatch_task(TASK_ID, prompt)
    if result.get("ok") and str(result.get("text") or "").strip():
        return str(result["text"]).strip()

    # A 掛掉時給的是「清單本身仍然有效」的中性說明，不假裝有判讀。
    if papers:
        return (
            f"已找到 {len(papers)} 篇通過來源驗證的文獻（清單如下）。"
            "本輪的判讀說明生成失敗，請直接檢視清單。"
        )
    return "本輪沒有找到通過來源驗證的文獻，建議換個關鍵詞或放寬年份範圍再試一次。"


def chat_only_reply(
    history: List[Dict[str, Any]],
    user_input: str,
    project_context: Dict[str, Any] = None,
) -> str:
    """不觸發搜尋時的一般對話回覆（單次 LLM 呼叫）。"""
    context = project_context or {}
    topic = str(context.get("research_title") or context.get("manual_context") or "").strip()
    prompt = (
        "你是研究助理，正在協助使用者釐清研究主題與文獻檢索方向。\n\n"
        f"研究專案主題：{topic or '（未設定）'}\n"
        f"對話歷史：\n{_format_history(history)}\n"
        f"使用者這一句：{user_input}\n\n"
        "規則：\n"
        "1. 用繁體中文，200 字以內，語氣專業。\n"
        "2. **不要列出任何具體論文**（標題、作者、年份）——本輪沒有經過搜尋驗證，"
        "列出來的都是未經查證的。需要文獻時請引導使用者說出想找的方向，由系統發動搜尋。\n"
        "只輸出純文字。"
    )
    result = dispatch_task(TASK_ID, prompt)
    if result.get("ok") and str(result.get("text") or "").strip():
        return str(result["text"]).strip()
    return f"目前無法連線到對話模型（{result.get('msg') or '未知錯誤'}），請稍後再試。"


def run_chat_turn(
    history: List[Dict[str, Any]],
    user_input: str,
    project_context: Dict[str, Any] = None,
    search_fn: Callable[[str], Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    一輪完整對話。

    :param search_fn: 注入的搜尋執行器（route 會包一層 persistent cache）。
                      預設直接跑 task_3bc_scout.run_debate_search。
    :return: {"reply": str, "papers": [...], "meta": {...}}
    """
    user_input = str(user_input or "").strip()
    if not user_input:
        return {"reply": "", "papers": [], "meta": {"error": "empty input"}}

    intent = classify_intent(history, user_input, project_context)
    meta: Dict[str, Any] = {
        "should_search": intent["should_search"],
        "query": intent["query"],
        "intent_reason": intent["reason"],
        "intent_source": intent["intent_source"],
        "keyword_hint": keyword_hint(user_input),
    }

    if not intent["should_search"]:
        return {
            "reply": chat_only_reply(history, user_input, project_context),
            "papers": [],
            "meta": meta,
        }

    runner = search_fn or task_3bc_scout.run_debate_search
    search_result = runner(intent["query"]) or {}
    papers = search_result.get("papers") or []
    meta.update(search_result.get("meta") or {})
    meta["dropped"] = search_result.get("dropped") or []

    return {
        "reply": compose_reply(history, user_input, papers, meta, project_context),
        "papers": papers,
        "meta": meta,
    }
