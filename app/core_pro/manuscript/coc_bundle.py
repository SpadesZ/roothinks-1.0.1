# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/coc_bundle.py
# 子系統定位:
#   Manuscript Drafter 的「人類決策鏈（Chain of Context）」組裝層。
#   位於 socket handler 與 ManuscriptRuling 之間：handler 負責解析身分與權限，
#   本模組負責依伺服器端真相把人類脈絡讀出來、排序、套預算，交給 Ruling 併入 prompt。
# 主要責任:
#   1. 依伺服器解析的真實 S.Ver 讀取目前章節正文（不接受呼叫端宣告版本）。
#   2. 讀回 2A 對話歷史，讓上一輪的人類指令與取捨進入下一輪 prompt。
#   3. 帶入其他章節與 2C 全文的最新有效版本，供跨章敘事連貫。
#   4. 帶入綁定版本的 reviewer/mentor 留言（人類審閱決策）。
#   5. 帶入前置模組的人類決策：PAQ 問題空間與人工 included 的文獻
#      （實際讀取委派給 coc_producers，本檔只決定預算與擺放順序）。
#   6. 依優先序套用 token 預算，並回報每一段的來源與被裁掉的部分。
# 明確不負責:
#   - 不「計算」權限，但一定「執行」呼叫端算好的權限。三個 ACL 參數
#     （readable_sections / can_read_current_section / include_paper）刻意設成
#     必填關鍵字，沒有預設值：第一版把它們設成寬鬆預設，結果新接的 socket 路徑
#     一個都沒傳，限定編輯經由 prompt 讀得到整篇 2C。沒有預設值 = 忘了帶會
#     TypeError 當場爆掉，而不是靜默外洩。
#   - 不做論文證據檢索：那是 context_inject.retrieve_paragraph_context 的責任，
#     納入/排除過濾見 NOTE-013。
#   - 不寫檔、不改版本、不發 socket 事件。
# 上游呼叫者:
#   app/core_pro/manuscript/manuscript_routes.py 的 chat_message handler。
# 下游服務:
#   ManuscriptIO（版本清單與版本內容）、chat_<section>.json（2A 歷史）、
#   app.models.ChapterComment（版本綁定留言）。
# 讀寫或持久化位置:
#   唯讀。<DATA_ROOT>/<formal_pid>/manuscript/block/<section>/V*.json、
#   <DATA_ROOT>/<formal_pid>/manuscript/paper/V*.json、
#   <DATA_ROOT>/<formal_pid>/manuscript/chat/chat_<section>.json、
#   以及 chapter_comments 資料表。
# 不變量:
#   - 版本真相只能來自伺服器：呼叫端送來的 s_ver 一律不採信（NOTE-012）。
#   - 只組本專案資料；跨專案取材視為缺陷，pid 由呼叫端解析後傳入。
#   - prompt 是一條讀取管道，與 cmd_load_block 等值：任何經由本模組進入 prompt
#     的正文，呼叫端都必須先確認該使用者本來就讀得到。2A 歷史與本章正文共用
#     同一道判定 —— 讀不到本章的人，也不該讀到本章的對話紀錄。
#   - 進 prompt 的正文來自版本檔的 content 欄位，不是瀏覽器 DOM 文字，
#     因此不會混入章節 badge 等 UI 文字（NOTE-012）。
#   - 預算不足時只能整段捨棄或截斷並記錄，不得靜默丟失（notes 會列出）。
#   - `items` 與 `blocks` 是**不同粒度**，不可假設一對一：blocks 是 packer 去重用的
#     顯示單位，items 是 audit 用的逐筆 provenance。一段「已納入文獻」對應 N 筆 item。
#   - PAQ／Literature 是專案層資料，不受章節 ACL 管轄（呼叫端已確認專案成員身分），
#     因此刻意不放在 can_read_current_section 底下。
# 相關 NOTE:
#   NOTE-012（伺服器端組裝、前端不得指定版本真相）。
#   NOTE-015（prompt 是讀取管道，套用與 cmd_load_block 同一套章節 ACL）。
#   NOTE-018（PAQ canonical reader）、NOTE-019（未 screening 的搜尋結果不得進寫作路徑）。
# 驗證:
#   python -m pytest test/unit/test_coc_bundle.py -q
# ---------------------------------------------------------------------------
from __future__ import annotations

import html as html_lib
import logging
import os
import re
from typing import Any

from app.core_pro.manuscript.manuscript_io import ManuscriptIO, _get_data_root
from app.core_pro.manuscript.source_context import _estimate_tokens, _sanitize, _truncate_to_budget
from app.security import load_json_locked, safe_join_under

logger = logging.getLogger("app.manuscript.coc_bundle")

# 每一層的預算比例。順序即優先序（擁有者拍板）：
#   本輪指令與目前章節 > 本章人類決策 > 其他章節與 2C > 審閱意見
# 論文證據不在這裡，走既有檢索（見 context_inject）。
_TIER_RATIO = {
    "current_section": 0.30,
    "chat_history": 0.26,
    "other_sections": 0.20,
    "review": 0.10,
    # 前置模組（PAQ 問題空間、人工 included 文獻）。比例低於 Manuscript 自身的
    # 段落是刻意的：它們是**選材與定位**的依據，不是要被大段複製的正文。
    "paq": 0.06,
    "literature": 0.08,
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t　]+")
_BLANK_RE = re.compile(r"\n{3,}")


def html_to_text(raw: Any) -> str:
    """
    版本檔存的是 HTML（`.card-content` 的 innerHTML），這裡轉成純文字。

    區塊標籤先換成換行再剝標籤，否則 `</p><p>` 相鄰的兩段會黏成一行，
    LLM 讀到的段落結構會與作者寫的不同。
    """
    text = str(raw or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return _sanitize(text)


def resolve_section_version(pid: str, section: str) -> tuple[str | None, str | None]:
    """
    伺服器端解析某章目前的最新版本，回傳 (ver, filename)。

    NOTE(NOTE-012): 版本必須在這裡解析，不得採用請求體帶來的 s_ver。
    前端長期送死值 '0.1'，導致歷史內容永遠讀到 V0.1、audit 也綁到錯的版本。
    """
    try:
        versions = ManuscriptIO.list_block_versions(pid, section) or []
    except Exception:
        logger.warning("[coc] 讀取章節版本失敗 pid=%s section=%s", pid, section, exc_info=True)
        return None, None
    for item in versions:                      # 清單為新版在前
        if isinstance(item, dict) and item.get("filename"):
            return str(item.get("ver") or ""), str(item.get("filename"))
    return None, None


def _load_section_text(pid: str, section: str) -> tuple[str, str | None]:
    """回傳 (該章最新版純文字, 版本號)。沒有版本就回空字串。"""
    ver, filename = resolve_section_version(pid, section)
    if not filename:
        return "", None
    try:
        block = ManuscriptIO.load_block(pid, section, filename) or {}
    except Exception:
        logger.warning("[coc] 讀取章節內容失敗 pid=%s section=%s", pid, section, exc_info=True)
        return "", ver
    return html_to_text(block.get("content")), ver


def _load_latest_paper(pid: str) -> tuple[str, str | None]:
    """2C 全文最新版 (純文字, G.Ver)。"""
    try:
        versions = ManuscriptIO.list_paper_versions(pid) or []
        if not versions:
            return "", None
        g_ver = versions[0].get("g_ver") or versions[0].get("ver")
        doc = ManuscriptIO.load_paper_version(pid, g_ver) or {}
        return html_to_text(doc.get("content")), (str(g_ver) if g_ver else None)
    except Exception:
        logger.warning("[coc] 讀取 2C 全文失敗 pid=%s", pid, exc_info=True)
        return "", None


def load_chat_history(pid: str, section: str, formal_pid: str) -> list[dict]:
    """
    讀回 2A 對話歷史。

    NOTE(NOTE-012): 這是本次修復的核心缺口 —— save_chat_history() 一直有寫，
    但生成下一輪時沒有任何程式碼讀回來，於是同一章聊十輪、每輪都近似重新開始。
    """
    if not formal_pid:
        return []
    try:
        safe_section = re.sub(r"[^A-Za-z0-9._-]+", "_", str(section or "general")) or "general"
        path = safe_join_under(
            _get_data_root(), formal_pid, "manuscript", "chat", f"chat_{safe_section}.json"
        )
        if not os.path.exists(path):
            return []
        history = load_json_locked(path, [])
        return history if isinstance(history, list) else []
    except Exception:
        logger.warning("[coc] 讀取 2A 歷史失敗 pid=%s section=%s", pid, section, exc_info=True)
        return []


def _format_history(history: list[dict], budget_tokens: int) -> str:
    """
    把對話歷史整理成 prompt 片段。

    由新到舊取用：越近的澄清與取捨越能代表作者現在的想法，預算不足時該犧牲最舊的。
    組完再依時間順序排回去，讓 LLM 讀到的是正常的對話流。
    """
    if not history or budget_tokens <= 0:
        return ""
    picked: list[str] = []
    used = 0
    for turn in reversed(history):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        content = _sanitize(turn.get("content"))
        if not content:
            continue
        speaker = "作者" if role == "user" else "AI"
        line = f"[{speaker}] {content}"
        cost = _estimate_tokens(line)
        if used + cost > budget_tokens:
            break
        picked.append(line)
        used += cost
    picked.reverse()
    return "\n".join(picked)


def build_coc_bundle(
    pid: str,
    section: str,
    *,
    readable_sections: list[str] | None,
    can_read_current_section: bool,
    include_paper: bool,
    formal_pid: str = "",
    review_comments: list[dict] | None = None,
    budget_tokens: int = 12000,
) -> dict[str, Any]:
    """
    組出送進 Drafter 的人類決策鏈。

    三個 ACL 參數都必須由呼叫端在 request context 內算好（本模組跑在 worker thread，
    那裡沒有 current_user 可判權限）。刻意不給預設值，理由見檔頭「明確不負責」。

      readable_sections         其他章節的可讀清單。None 代表呼叫端已確認可讀全部章節。
      can_read_current_section  是否可讀 section 本身（含其正文、2A 歷史、審閱意見）。
                                不從 readable_sections 推導 —— 章節可能還沒進 sections
                                資料表（新章節、'general' 這種預設值），用「不在清單裡」
                                當作「不可讀」會把合法請求的正文靜默丟掉。
      include_paper             是否可讀 2C 全篇。限定編輯一律不可（見 _socket_can_read_paper）：
                                2C 是所有章節組裝出來的成品，放行等於繞過章節層讀取限制。

    回傳:
      text        併好的 context 區塊（可直接前置到 prompt）
      blocks      同樣的內容，但保留分段結構：
                    [{"tier": ..., "body": 純內容, "text": 含標頭的整段, "item": 來源摘要}]
                  下游（manuscript_routes 的全域 packer）要拿「目前章節那一段」做去重，
                  必須從這裡取。**不要用正規表示式去 text 裡撈標頭字串** ——
                  那會讓下游耦合到本檔的顯示格式，改個標籤文字就會讓去重靜默失效，
                  而手工組字串的測試完全看不出來。
      s_ver       伺服器解析出的目前章節版本，供 audit 與留言綁定
      g_ver       2C 最新版本
      items       每一段的來源摘要，寫進 context_audit 用
      notes       被裁掉或跳過的部分，不讓遺失變成靜默（含被權限擋下的段落）
    """
    section = str(section or "general")
    notes: list[str] = []
    blocks: list[dict] = []
    s_ver: str | None = None
    g_ver: str | None = None

    def budget_of(tier: str) -> int:
        return max(0, int(budget_tokens * _TIER_RATIO.get(tier, 0)))

    def add(tier: str, header: str, body: str, items: list[dict]) -> None:
        """
        登記一段。text 是進 prompt 的樣子，body 是不含標頭的純內容（供去重比對）。

        `items` 是**清單**而非單筆：一個 block 可以對應 N 筆來源
        （「已納入文獻」那一段就是 N 篇論文）。這裡曾經是單筆 `item`，
        多來源時只好另外合成一筆 `literature:Nitems` 彙總 —— 那筆彙總
        **沒有 fingerprint、也不對應任何真實來源**，等於在 audit 裡憑空多一筆
        查不回去的證據。而且下游 `_strip_coc_section` 只認 `b["item"]`，
        去重一觸發就把逐篇來源整批丟掉，只留下那筆假的。
        改成清單之後 blocks 是唯一的來源真相，不需要側channel。
        """
        blocks.append({
            "tier": tier,
            "body": body,
            "text": f"{header}\n{body}",
            "items": list(items),
            # 單筆相容欄位：舊消費端（測試、外部呼叫）仍讀 b["item"]。
            # 只在剛好一筆時提供，避免再度出現「彙總假來源」。
            "item": items[0] if len(items) == 1 else None,
        })

    # 1. 目前章節最新版正文（真實 S.Ver，來自伺服器）
    if can_read_current_section:
        cur_text, s_ver = _load_section_text(pid, section)
        if cur_text:
            clipped = _truncate_to_budget(cur_text, budget_of("current_section"))
            if len(clipped) < len(cur_text):
                notes.append(f"current_section:{section} 依預算截斷")
            add("current_section",
                f"[目前章節 {section} · S.Ver {s_ver or '未存檔'}]",
                clipped,
                [{"source_type": "manuscript_section", "source_id": f"{section}:{s_ver}"}])
    else:
        notes.append(f"current_section:{section} 因讀取權限不足未納入")

    # 2. 本章的人類決策與理由（2A 歷史）
    #    NOTE(NOTE-015): 與正文共用同一道權限 —— 對話紀錄裡就是這一章的內容與
    #    作者取捨，讀不到正文卻讀得到對話，等於留了一條同等的外洩管道。
    if can_read_current_section:
        history = load_chat_history(pid, section, formal_pid)
        hist_text = _format_history(history, budget_of("chat_history"))
        if hist_text:
            add("chat_history",
                "[本章先前對話 · 作者與 AI 的往返]",
                hist_text,
                [{"source_type": "drafter_chat", "source_id": f"{section}:{len(history)}turns"}])
        elif history:
            notes.append("chat_history 因預算為 0 而未納入")
    else:
        notes.append("chat_history 因讀取權限不足未納入")

    # 3. 其他章節與 2C 全文的最新有效版本
    other_budget = budget_of("other_sections")
    if other_budget > 0:
        if include_paper:
            paper_text, g_ver = _load_latest_paper(pid)
            if paper_text:
                half = other_budget // 2
                clipped = _truncate_to_budget(paper_text, half)
                add("paper_2c",
                    f"[2C 全文 · G.Ver {g_ver or '未存檔'}]",
                    clipped,
                    [{"source_type": "manuscript_paper", "source_id": str(g_ver)}])
                other_budget -= _estimate_tokens(clipped)
        else:
            notes.append("paper_2c 因讀取權限不足未納入")

        for other in (readable_sections or []):
            if other == section or other_budget <= 0:
                continue
            text, ver = _load_section_text(pid, other)
            if not text:
                continue
            clipped = _truncate_to_budget(text, min(other_budget, other_budget // 2 or other_budget))
            if not clipped:
                continue
            add("other_section",
                f"[其他章節 {other} · S.Ver {ver}]",
                clipped,
                [{"source_type": "manuscript_section", "source_id": f"{other}:{ver}"}])
            other_budget -= _estimate_tokens(clipped)

    # 3.5 前置模組的人類決策：PAQ 問題空間與人工 included 的文獻。
    #     這兩段是**專案層**資料，不受章節 ACL 管轄（呼叫端已確認請求者是專案成員），
    #     因此不放在 can_read_current_section 底下。
    #     NOTE(NOTE-018) / NOTE(NOTE-019): 讀取一律走 coc_producers，
    #     不得在此另拼路徑或改讀 search_results.json。
    try:
        from app.core_pro.manuscript import coc_producers

        data_root = _get_data_root()
        for tier, loader in (
            ("paq", coc_producers.load_paq_context),
            ("literature", coc_producers.load_literature_context),
        ):
            text, items, tier_notes = loader(
                pid, data_root=data_root, budget_tokens=budget_of(tier)
            )
            notes.extend(tier_notes)
            if text and items:
                header = ("[PAQ 問題空間 · 作者標定的研究座標]" if tier == "paq"
                          else "[已由作者人工納入的文獻 · 含採用理由]")
                # items 直接沿用 producer 回傳的那一份（每筆都帶 fingerprint），
                # 不在這裡重組或合成彙總 —— 合成出來的那筆對應不到任何真實來源。
                add(tier, header, text, items)
    except Exception:
        logger.warning("[coc] 前置模組（PAQ/Literature）讀取失敗 pid=%s", pid, exc_info=True)
        notes.append("upstream_producers:讀取失敗，本次未納入")

    # 4. 版本綁定的審閱意見（人類審閱決策）
    #    這些留言是對「本章」的評論，同樣受本章讀取權限管轄。
    if review_comments and can_read_current_section:
        lines = []
        used = 0
        cap = budget_of("review")
        for c in review_comments:
            body = _sanitize(c.get("body"))
            if not body:
                continue
            anchor = c.get("s_ver") or c.get("g_ver") or "未標版本"
            line = f"[{c.get('author') or '審閱者'} @ {anchor}] {body}"
            cost = _estimate_tokens(line)
            if used + cost > cap:
                notes.append("review 依預算截斷")
                break
            lines.append(line)
            used += cost
        if lines:
            add("review",
                "[審閱意見 · 需在改寫時處理]",
                "\n".join(lines),
                [{"source_type": "review_comment", "source_id": f"{section}:{len(lines)}"}])

    return {
        "text": "\n\n".join(b["text"] for b in blocks),
        "blocks": blocks,
        "s_ver": s_ver,
        "g_ver": g_ver,
        # 由 blocks 攤平，blocks 是唯一的來源真相。
        # 不要改回「每個 block 一筆」：多來源的 block 會被迫合成一筆沒有
        # fingerprint 的彙總，那筆在 audit 裡對應不到任何東西。
        "items": [i for b in blocks for i in (b.get("items") or [])],
        "notes": notes,
    }
