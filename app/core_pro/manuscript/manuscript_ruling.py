# 檔案路徑: app/core_pro/manuscript/manuscript_ruling.py
# 產生時間: 2026-07-04 19:10 +08:00
# 版本: v0.4
# 模組定位:
#   Manuscript 規則引擎與上下文管制中樞。
# 主要責任:
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
    ):
        """
        核心監聽與準備流程：
        1. 檢核 Title 條件
        2. 整合 context_inject, 匯入檔案, 與對話紀錄 (預留與實作)
        3. 準備最終交付給 LLM 的 context
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
        if pid and section:
            try:
                from app.core_pro.manuscript.context_audit import write_context_audit
                from app.core_pro.manuscript.context_inject import build_injected_context_block, retrieve_paragraph_context

                injected_context_items = retrieve_paragraph_context(
                    project_id=pid,
                    section_title=section,
                    # 自然語言指令才包含「以 SEB-ASR 為主／取 Method 段」等選材意圖；
                    # 舊版只查 2B 文字，因此使用者點名論文不會影響檢索。
                    paragraph_goal=user_prompt or current_context or "",
                    draft_text=retrieval_seed,
                    # 1200 tokens 配上「一頁一段」的粗索引等於幾乎沒有檢索：
                    # 每個 segment 是一整頁（4000～6500 字元，約 1500～2000 tokens），
                    # 就算 top_k 撈回 8 段，預算也只塞得下不到一段。
                    # 實測正式站 75 個 segment 全是頁層級，草稿因此永遠缺依據。
                    # 檢索是唯一能隨論文數量擴展的路（整包倒語料在
                    # GOOGLE_LLM_GLOBAL_TPM_LIMIT=25000 之下超過兩篇就必死），
                    # 所以這裡要給得起真正有用的量。
                    # 2026-08-09：整包倒語料停用後空出約 11000 tokens
                    # （原本 corpus 16000 上限 + 檢索 9000 ≈ 25000，剛好撞 TPM）。
                    # 把預算給檢索才划算：檢索的注入量不隨論文數成長，
                    # 而整包倒是「每篇取開頭 N 字元」，論文一多就只剩前言。
                    max_tokens=_read_int_env("MANUSCRIPT_RETRIEVAL_MAX_TOKENS", 18000),
                    top_k=_read_int_env("MANUSCRIPT_RETRIEVAL_TOP_K", 20),
                )
                injected_block = build_injected_context_block(injected_context_items)
                if injected_block:
                    context_audit_path = write_context_audit(
                        project_id=pid,
                        section_id=section,
                        context_items=injected_context_items,
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

            # PAQ Taxonomy
            paq_paths = [
                os.path.join(data_root, base_pid, "paq", "taxonomy_manual_update.json"),
                os.path.join(data_root, base_pid, "paq", "paq", "taxonomy_manual_update.json"),
            ]
            for p in paq_paths:
                if os.path.exists(p):
                    try:
                        with open(p, 'r', encoding='utf-8') as f:
                            t = json.load(f)
                        labels = t.get('axis_labels', {}) if isinstance(t, dict) else {}
                        tags = t.get('axis_tags', {}) if isinstance(t, dict) else {}
                        snippets.append(f"[PAQ Taxonomy]\nlabels={json.dumps(labels, ensure_ascii=False)}\ntags={json.dumps(tags, ensure_ascii=False)}")
                        break
                    except Exception:
                        pass

            # Literature Search 結果
            sr_path = os.path.join(data_root, base_pid, "search_results.json")
            if os.path.exists(sr_path):
                try:
                    with open(sr_path, 'r', encoding='utf-8') as f:
                        sr = json.load(f)
                    rs = sr.get('results', {}) if isinstance(sr, dict) else {}
                    keys = rs.get('keywords', []) if isinstance(rs, dict) else []
                    citations = rs.get('apa_citations', []) if isinstance(rs, dict) else []
                    snippets.append(f"[Literature Hints]\nkeywords={json.dumps(keys[:8], ensure_ascii=False)}\napa={json.dumps(citations[:3], ensure_ascii=False)}")
                except Exception:
                    pass

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
