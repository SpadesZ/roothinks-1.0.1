# Roothinks source maintenance contract
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/core_pro/manuscript/manuscript_ruling.py
# 產生時間: 2026-07-04 19:10 +08:00
# 版本: v0.4
# 模組定位:
#   Manuscript 規則引擎與上下文管制中樞。
# 主要責任: 解析並套用 Manuscript 章節輸入/輸出規則，驗證 editable/hypothetical section 邊界。
#   1. 攔截缺 title 的生成請求。
#   2. 以研究筆記優先，彙整 history / upstream / paragraph-level context。
#   3. 寫入 context audit sidecar 以保留 provenance。
# 呼叫來源:
#   manuscript_routes.py Task8Drafter.process_request 前置攔截。
# 輸入輸出契約:
#   - 所有路徑讀取均透過 _get_data_root() 取絕對路徑，防相對路徑繞過。
#   - _read_local_file / _load_upstream_context 失敗只 return None/""（不 crash）。
# 安全邊界:
#   - 禁止 os.path.join('data', ...) 相對路徑組合。
#   - 所有路徑先用 _get_data_root() 取絕對 data 根目錄再組合。
# 維護提醒:
#   - v0.3 [Batch C] 改用 _get_data_root() 絕對路徑，
#     消除原 L101-181 多處 os.path.join('data', ...) 繞過點。
#   - 本檔只接最小 context injection，不改 Manuscript 生成主流程。
#   - v0.5 _load_upstream_context **不再讀 PAQ 與 Literature**：
#     兩者已移到 coc_producers（NOTE-018 / NOTE-019）。這裡只剩
#     Project 標題／背景與 Study 研究筆記。**不要把它們加回來** ——
#     兩個 reader 並存時一邊擋掉、另一邊照送，而且沒有任何外顯症狀。
#   - coc_source_items 的 fingerprint / segment_id 必須原樣帶過來，不得寫死 None：
#     那會讓 audit 看起來有 provenance、實際上查不回任何來源版本。
# 尚未處理（給下一輪）:
#   - [Project Background] 1200、Study notes 2400、merged 6000 三處仍是硬截，
#     被裁掉的部分沒有進 notes → audit（靜默遺失）。統一 packer 尚未接管。
# -----------------------------------------------------------------------------

import os
import json
import glob
import logging

from app.core_pro.manuscript.manuscript_io import _get_data_root

logger = logging.getLogger(__name__)


def _read_int_env(key: str, default_val: int) -> int:
    """壞值一律回預設。檢索預算被設成 0 或負數會讓草稿靜默失去依據，
    那種故障沒有任何外顯症狀，比直接報錯難查得多。"""
    try:
        val = int(str(os.environ.get(key, default_val)).strip())
        return val if val > 0 else default_val
    except Exception:
        return default_val


class ManuscriptRuling:
    """
    Manuscript 規則引擎與上下文管制中樞
    負責攔截不合法生成請求 (如缺乏 Title)，並統整各路徑的上下文數據。
    """

    @classmethod
    def validate_and_prepare(
        cls, pid, title, section, s_ver, current_context, attachment, import_type,
        user_prompt="",
        coc_items: list | None = None,
        coc_notes: list | None = None,
        remaining_budget: int | None = None,
    ):
        """
        核心監聽與準備流程：
        1. 檢核 Title 條件
        2. 整合 context_inject, 匯入檔案, 與對話紀錄 (預留與實作)
        3. 準備最終交付給 LLM 的 context

        remaining_budget 是**全域預算的最後一段**：呼叫端（socket handler 的
        packer）扣掉本輪指令、未存草稿與 COC 之後剩下的 token 數。本函式要在
        這個額度內同時容納 upstream context 與檢索結果 —— 兩者都在這裡才被加入
        final_context，若不一起收斂，「全域預算」這個名字就是假的。

        `None` 代表呼叫端沒有給預算（舊路徑／直接呼叫的測試），退回環境變數。
        **`0` 是有效值**，意思是「已經用盡，一個 token 都不能再加」，
        不是「沒給」—— 這裡曾經用 `> 0` 判斷，導致預算剛好用完時反而
        額外檢索 18000 tokens，正好是要防的那件事。
        """
        # --- 規則 1: 標題強制攔截檢核 (Title Block) ---
        invalid_titles = ['', 'untitled', 'untitled paper', 'untitled_paper']
        if not title or title.strip().lower() in invalid_titles:
            return {
                "ok": False,
                "reason": "missing_title",
                "sys_msg": "【系統強制攔截】\n草稿生成失敗！在進行任何章節 (Section) 的草稿生成前，必須先在 2C (Fusor) 視窗上方確立您的「論文標題 (Title)」。\n請輸入標題後再試一次。"
            }

        # --- 規則 2: 上下文數據整合 (Context Preparation) ---
        current_context = current_context if current_context else ""
        working_context = current_context
        original_len = len(current_context.strip())
        source_manifest = []
        if current_context.strip():
            source_manifest.append({"source_type": "current_draft", "source_id": section or "general"})

        # 讀取本機 Section 歷史數據 (從 Drafter 移植過來的邏輯)
        if pid and section and original_len < 50:
            history_content = cls._read_local_file(pid, section, s_ver)
            if history_content:
                working_context = f"[History Content from {section}_{s_ver}.json]:\n{history_content}\n\n" + working_context
                source_manifest.append({"source_type": "manuscript_history", "source_id": f"{section}:{s_ver}"})

        # 前置模組不是冷啟動備案，而是 Manuscript 的正式輸入。即使 2B 已有文字，
        # 研究筆記仍須保留，否則長一點的草稿會讓作者想法整段消失。
        upstream_ctx = cls._load_upstream_context(pid) if pid else ""

        # --- 全域預算的最後一段：upstream 與 retrieval 共用 remaining_budget ---
        # upstream 先扣，因為它是作者自己的研究筆記，優先序高於檢索到的論文段落。
        # 這一段必須排在下面的 marker 掃描**之前**：被預算裁掉的內容不能繼續
        # 出現在 source_manifest 裡，否則 manifest 會宣稱一份沒有進 prompt 的來源。
        budget_notes: list[str] = []
        # 兩條分支都一定會賦值。這個檔案有過 UnboundLocalError 的前科
        # （import 在 try 內、呼叫在 try 外），所以這裡刻意不留「可能未綁定」的形狀。
        effective_retrieval_budget: int
        if isinstance(remaining_budget, int) and remaining_budget >= 0:
            from app.core_pro.manuscript.source_context import _estimate_tokens, _truncate_to_budget

            left = max(0, remaining_budget)
            upstream_tokens = _estimate_tokens(upstream_ctx) if upstream_ctx else 0
            if upstream_tokens > left:
                clipped = _truncate_to_budget(upstream_ctx, left)
                budget_notes.append(
                    f"truncated:upstream_context:tokens={upstream_tokens}->{left} by global budget"
                )
                upstream_ctx = clipped
                upstream_tokens = _estimate_tokens(clipped) if clipped else 0
            effective_retrieval_budget = max(0, left - upstream_tokens)
            # 剩餘額度太小就整個跳過，不要硬塞。
            # 幾十個 token 的「證據」是一段被切斷的殘片，卻仍會在 audit 裡登記成
            # 一筆來源 —— 那比沒有證據更糟：provenance 宣稱有依據，內容卻不成句。
            # （這道下限是修好 _truncate_to_budget 之後才浮現的：截斷從前會超標，
            #   剛好把剩餘額度壓成 0，於是「額度用盡就跳過」看起來一直是對的。）
            min_useful = _read_int_env("MANUSCRIPT_RETRIEVAL_MIN_TOKENS", 200)
            if 0 < effective_retrieval_budget < min_useful:
                budget_notes.append(
                    f"retrieval:skipped:剩餘預算 {effective_retrieval_budget} 低於下限 {min_useful}"
                )
                effective_retrieval_budget = 0
            elif effective_retrieval_budget == 0:
                budget_notes.append("retrieval:skipped:全域預算已被前段內容用盡")
        else:
            effective_retrieval_budget = _read_int_env("MANUSCRIPT_RETRIEVAL_MAX_TOKENS", 18000)

        for marker, source_type in (
            ("[Study Notes]", "study_note"),
            ("[Project Background]", "project_background"),
            ("[PAQ Taxonomy]", "paq_note"),
        ):
            if marker in upstream_ctx:
                source_manifest.append({"source_type": source_type, "source_id": pid})

        retrieval_seed = "\n\n".join(x for x in (upstream_ctx, working_context) if x.strip())

        injected_context_items = []
        # injected_block 必須在這裡就有值。build_injected_context_block 的 import
        # 在下面的 try 內、try 又在 if 內，只要 pid/section 缺一或 import 失敗，
        # 名稱就未綁定 —— 之前在 if 之外無條件呼叫它，實測直接
        # UnboundLocalError，而且發生在 except 之外，下面那句
        # 「degrade, not crash」的防護等於是假的。
        injected_block = ""
        context_audit_path = ""

        # coc_items 是 handler 層傳下來的 COC 來源清單（任務 2）。
        # 轉成和 injected_context_items 相同的欄位格式，才能一起寫進 audit。
        #
        # fingerprint / segment_id 必須原樣帶過來，**不能寫死 None**。
        # 指紋是「反查來源」唯一的憑據：coc_producers 對 PAQ 與每一篇 included
        # 文獻都算了指紋，稽核時要能回答「這份草稿當時讀到的是哪一版 taxonomy、
        # 哪一筆 library entry」。寫死 None 會讓 audit 看起來有 provenance、
        # 實際上什麼都查不回來 —— 那比沒有 provenance 更糟（同 §3.10 的教訓）。
        coc_source_items = [
            {
                "source_type": item.get("source_type") or "coc",
                "source_id": item.get("source_id"),
                "paper_id": item.get("paper_id"),
                "segment_id": item.get("segment_id"),
                "fingerprint": item.get("fingerprint"),
                "score": None,
                "estimated_tokens": None,
            }
            for item in (coc_items or [])
            if isinstance(item, dict)
        ]

        if pid and section:
            try:
                from app.core_pro.manuscript.context_audit import write_context_audit
                from app.core_pro.manuscript.context_inject import build_injected_context_block, retrieve_paragraph_context

                # 預算用盡就完全不檢索。**不要改成把 0 傳下去**：
                # context_inject._apply_limits 曾經寫成 `max_tokens or 1200`，
                # 0 被當成「沒給」而放大成 1200。這裡明確跳過，就不必依賴
                # 下游每一層都正確處理 0 —— 那是三層都踩過的同一個陷阱。
                injected_context_items = [] if effective_retrieval_budget <= 0 else retrieve_paragraph_context(
                    project_id=pid,
                    section_title=section,
                    # 自然語言指令才包含「以 SEB-ASR 為主／取 Method 段」等選材意圖；
                    # 舊版只查 2B 文字，因此使用者點名論文不會影響檢索。
                    paragraph_goal=user_prompt or current_context or "",
                    draft_text=retrieval_seed,
                    # 任務 1：不再寫死 18000，改用 handler 算好的剩餘預算。
                    # 這確保 retrieval 不會把 COC + frontend context 之後的
                    # 剩餘空間超額使用。
                    max_tokens=effective_retrieval_budget,
                    top_k=_read_int_env("MANUSCRIPT_RETRIEVAL_TOP_K", 20),
                    # 不傳的話，ContextChain 的 fallback 會用「原始碼所在位置 /data」
                    # （context_inject 的 _project_root()），而不是本檔第 15 行宣告的
                    # _get_data_root()。正式站兩者剛好相同（工作目錄就是 repo 根），
                    # 所以一直沒被發現；但測試把 DB 指到 tmp 時，ContextChain 仍會去
                    # 讀寫真實 data/。同一個不變量在同一個函式裡要一致。
                    data_root=_get_data_root(),
                )
                injected_block = build_injected_context_block(injected_context_items)
                # 觸發條件不能是「檢索有結果」。原本寫成 `if injected_block:`，
                # 而目前全站沒有任何 library.json（screening 從未被使用），依 NOTE-013
                # 的 fail-closed，寫作路徑的檢索結果**恆為空** —— 於是這條 audit
                # 一次也不會被寫，COC provenance 在最需要它的狀態下完全消失。
                # 只要有任何東西值得記（檢索結果、COC 來源、降級紀錄）就要落地。
                if injected_block or coc_source_items or coc_notes or budget_notes:
                    # 任務 2：coc_items 一路接進 context_audit。
                    # write_context_audit 的 context_items 放 retrieval 結果；
                    # coc_source_items 另外記在 coc_sources，讓 audit 看得到
                    # 每一次生成的 COC 來源與降級情況。
                    context_audit_path = write_context_audit(
                        project_id=pid,
                        section_id=section,
                        context_items=injected_context_items,
                        coc_sources=coc_source_items,
                        # 預算層的降級（upstream 被截斷、檢索被跳過）與 COC 的降級
                        # 是同一件事的兩段，寫在同一個欄位才能一眼看完整。
                        coc_notes=list(coc_notes or []) + budget_notes,
                        # 不傳的話 write_context_audit 會用相對路徑 Path("data")，
                        # 也就是「process 的 cwd 底下的 data」——違反本檔第 15 行
                        # 自己宣告的不變量。實測後果：跑測試時 audit sidecar 寫進了
                        # repo 的真實 data/manuscript_context_audit/，而正式站則取決於
                        # 工作目錄，換個啟動方式 audit 就悄悄跑到別的地方去。
                        data_root=_get_data_root(),
                        prompt="\n\n".join(
                            x for x in (upstream_ctx, injected_block, working_context) if x.strip()
                        ),
                    )
            except Exception:
                # Context injection is value-add; generation should degrade, not crash.
                #
                # 但「降級」不等於「不留痕跡」：這個 except 原本完全靜默，檢索一壞
                # 使用者只會拿到沒有依據的草稿，而且沒有任何訊號指出檢索失敗過
                # ——先前那個 UnboundLocalError 正是躲在這個區塊裡。
                # 至少要讓日誌看得見，否則下一次同樣的故障還是查不到。
                logger.exception(
                    "[manuscript_ruling] 段落檢索失敗，本次草稿將缺少論文依據 "
                    "pid=%s section=%s", pid, section,
                )
                injected_context_items = []
                injected_block = ""

        final_context = "\n\n".join(
            x for x in (upstream_ctx, injected_block, working_context) if x.strip()
        )
        source_manifest.extend(
            {
                "source_type": item.get("source_type") or "context",
                "source_id": item.get("source_id"),
                "paper_id": item.get("paper_id"),
                "paper_title": item.get("paper_title"),
                "segment_id": item.get("segment_id"),
            }
            for item in injected_context_items
        )
        # COC 來源也是 source_manifest 的一員。少了這段，回傳給呼叫端的
        # context_sources 只會列出論文證據，看起來就像「這份草稿沒有用到
        # 作者的任何人類脈絡」。
        #
        # 這**不會**放寬下面的 has_grounding：它的允收清單裡沒有任何 COC 的
        # source_type（manuscript_section / drafter_chat / manuscript_paper /
        # review_comment 都不在內），所以「有沒有論文依據」的判準維持不變。
        # 要不要把作者自己的已存正文算成 grounding 是產品決策，不在這一刀的範圍。
        source_manifest.extend(
            {
                "source_type": item.get("source_type") or "coc",
                "source_id": item.get("source_id"),
                "paper_id": item.get("paper_id"),
                "paper_title": None,
                "segment_id": None,
            }
            for item in coc_source_items
        )

        # Title / Literature Hints 只負責定位，不能單獨被當成寫摘要的依據。
        has_grounding = bool(attachment) or any(
            item.get("source_type") in {
                "current_draft", "manuscript_history", "study_note",
                "project_background", "paq_note", "paper_segment",
                "context_chain_item", "manuscript_note",
            }
            for item in source_manifest
        )

        # --- 規則 3: 回傳放行狀態與準備完畢的數據 ---
        return {
            "ok": True,
            "context_text": final_context,
            "has_attachment": attachment is not None,
            "injected_context_ids": [x.get("source_id") for x in injected_context_items],
            "injected_context_fingerprints": [x.get("fingerprint") for x in injected_context_items],
            "context_audit_path": context_audit_path,
            "context_sources": source_manifest,
            "has_grounding": has_grounding,
        }

    @staticmethod
    def _read_local_file(pid, section, s_ver):
        try:
            # 優先讀取新結構: <DATA_ROOT>/<pid>-p/manuscript/block/<section>/*_Vx.y.json
            data_root = _get_data_root()
            root_pid = pid[:-2] if str(pid).endswith('-p') else str(pid)
            dir_candidates = [
                os.path.join(data_root, f"{root_pid}-p", "manuscript", "block", section),
                os.path.join(data_root, str(pid), f"man_{str(pid)}", section),
            ]

            def _extract_payload(obj):
                if isinstance(obj, dict):
                    if 'content' in obj:
                        return obj['content']
                    if 'blocks' in obj:
                        return json.dumps(obj['blocks'], ensure_ascii=False)
                return str(obj)

            for d in dir_candidates:
                if not os.path.isdir(d):
                    continue

                ver_str = str(s_ver).replace('v', '').replace('V', '')
                preferred = sorted(glob.glob(os.path.join(d, f"*_V{ver_str}.json")), reverse=True)
                candidates = preferred if preferred else sorted(glob.glob(os.path.join(d, "*.json")), reverse=True)

                if candidates:
                    with open(candidates[0], 'r', encoding='utf-8') as f:
                        return _extract_payload(json.load(f))
            return None
        except Exception:
            return None

    @staticmethod
    def _load_upstream_context(pid):
        """彙整前置模組可用摘要，供 Manuscript Drafter 快速冷啟動。"""
        try:
            data_root = _get_data_root()
            base_pid = pid[:-2] if str(pid).endswith('-p') else str(pid)
            snippets = []

            # Project 基本資訊
            try:
                from app.models import Project
                # 正式專案的題目可能已更新；精確 pid 必須優先於 readonly base 專案。
                proj = Project.query.filter_by(project_id=pid).first() or Project.query.filter_by(project_id=base_pid).first()
                if proj:
                    title = (proj.research_title or proj.name or '').strip()
                    if title:
                        snippets.append(f"[Project Title]\n{title}")
                    if proj.context_background:
                        snippets.append(f"[Project Background]\n{str(proj.context_background)[:1200]}")
            except Exception:
                pass

            # NOTE(NOTE-018): PAQ 的 canonical reader 已移到 coc_producers。
            # NOTE(NOTE-019): 未經 screening 的搜尋結果不得進入寫作路徑。
            # 這裡刻意不再讀，理由見下：
            #
            # 1. PAQ：本函式原本只找 `taxonomy_manual_update.json`，而 writer 寫的是
            #    `taxonomy_v1.json` / `cube_v1.json` / PaqSurvey。實測全站 0 個
            #    manual_update、3 個 taxonomy_v1 —— 讀的跟寫的從來不是同一個檔，
            #    而且只取 axis_labels/axis_tags 兩個 dict，cube 與 rag_context 全丟。
            #
            # 2. Literature：原本直接讀 `search_results.json` 的 keywords 與
            #    apa_citations 組成 `[Literature Hints]`。那是搜尋引擎的原始輸出，
            #    **完全沒有經過任何人的納入判斷** —— 等於在 NOTE-013 的 fail-closed
            #    旁邊開了一條旁路：作者標為 excluded 的論文擋得住檢索，卻擋不住這裡。
            #    測試 test_coc_producers.py 的 teeth test 實測哨兵確實會經此進入
            #    provider request。
            #
            # 兩者都改由 coc_bundle → coc_producers 供應，好處是同時取得
            # item（provenance）與 notes（被裁掉什麼），而不是像這裡併成一坨字串。
            # **不要把它們加回來**：兩個 reader 並存時，一邊擋掉、另一邊照送，
            # 而且沒有任何外顯症狀。

            # Study Notes (project-scoped JSON path + legacy fallback)
            note_json_path = os.path.join(data_root, f"{base_pid}-p", "study", f"{base_pid}-p_note.json")
            note_json_path_legacy = os.path.join(data_root, "note", f"{base_pid}_note.json")
            note_txt_path = os.path.join(data_root, base_pid, "study_notes.txt")
            notes = ""
            if os.path.exists(note_json_path):
                try:
                    with open(note_json_path, 'r', encoding='utf-8') as f:
                        note_obj = json.load(f)
                    if isinstance(note_obj, dict):
                        notes = str(note_obj.get("notes") or "").strip()
                    else:
                        notes = str(note_obj or "").strip()
                except Exception:
                    notes = ""
            elif os.path.exists(note_json_path_legacy):
                try:
                    with open(note_json_path_legacy, 'r', encoding='utf-8') as f:
                        note_obj = json.load(f)
                    if isinstance(note_obj, dict):
                        notes = str(note_obj.get("notes") or "").strip()
                    else:
                        notes = str(note_obj or "").strip()
                except Exception:
                    notes = ""
            elif os.path.exists(note_txt_path):
                try:
                    with open(note_txt_path, 'r', encoding='utf-8') as f:
                        notes = f.read().strip()
                except Exception:
                    notes = ""

            if notes:
                # 研究筆記是作者意圖，放在 context 最前面，避免 token 截斷時被尾端吃掉。
                snippets.insert(0, f"[Study Notes]\n{notes[:2400]}")

            if not snippets:
                return ""
            merged = "\n\n".join(snippets)
            return merged[:6000]
        except Exception:
            return ""
