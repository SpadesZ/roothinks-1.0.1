# 檔案路徑: app/core_pro/manuscript/manuscript_ruling.py
# 產生時間: 2026-07-04 19:10 +08:00
# 版本: v0.2
# 模組定位:
#   Manuscript 規則引擎與上下文管制中樞。
# 主要責任:
#   1. 攔截缺 title 的生成請求。
#   2. 彙整 history / upstream / paragraph-level injected context。
#   3. 寫入 context audit sidecar 以保留 provenance。
# 維護提醒:
#   - 本檔只接最小 context injection，不改 Manuscript 生成主流程。
# -----------------------------------------------------------------------------

import os
import json
import glob

class ManuscriptRuling:
    """
    Manuscript 規則引擎與上下文管制中樞
    負責攔截不合法生成請求 (如缺乏 Title)，並統整各路徑的上下文數據。
    """
    
    @classmethod
    def validate_and_prepare(cls, pid, title, section, s_ver, current_context, attachment, import_type):
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
        final_context = current_context if current_context else ""
        original_len = len(final_context.strip())
        
        # 讀取本機 Section 歷史數據 (從 Drafter 移植過來的邏輯)
        if pid and section and original_len < 50:
            history_content = cls._read_local_file(pid, section, s_ver)
            if history_content:
                final_context = f"[History Content from {section}_{s_ver}.json]:\n{history_content}\n\n" + final_context

        # [Sync Bridge] 在上下文不足時，注入前置模組摘要資料（PAQ/Literature/Study）
        if pid and len(final_context.strip()) < 300:
            upstream_ctx = cls._load_upstream_context(pid)
            if upstream_ctx:
                final_context = f"{upstream_ctx}\n\n{final_context}".strip()

        injected_context_items = []
        context_audit_path = ""
        if pid and section:
            try:
                from app.core_pro.manuscript.context_audit import write_context_audit
                from app.core_pro.manuscript.context_inject import build_injected_context_block, retrieve_paragraph_context

                injected_context_items = retrieve_paragraph_context(
                    project_id=pid,
                    section_title=section,
                    paragraph_goal=current_context or "",
                    draft_text=final_context,
                    max_tokens=1200,
                    top_k=8,
                )
                injected_block = build_injected_context_block(injected_context_items)
                if injected_block:
                    final_context = f"{injected_block}\n\n{final_context}".strip()
                    context_audit_path = write_context_audit(
                        project_id=pid,
                        section_id=section,
                        context_items=injected_context_items,
                        prompt=final_context,
                    )
            except Exception:
                # Context injection is value-add; generation should degrade, not crash.
                injected_context_items = []
        
        # --- 規則 3: 回傳放行狀態與準備完畢的數據 ---
        return {
            "ok": True,
            "context_text": final_context,
            "has_attachment": attachment is not None,
            "injected_context_ids": [x.get("source_id") for x in injected_context_items],
            "injected_context_fingerprints": [x.get("fingerprint") for x in injected_context_items],
            "context_audit_path": context_audit_path,
        }

    @staticmethod
    def _read_local_file(pid, section, s_ver):
        try:
            # 優先讀取新結構: data/<pid>-p/manuscript/block/<section>/*_Vx.y.json
            root_pid = pid[:-2] if str(pid).endswith('-p') else str(pid)
            dir_candidates = [
                os.path.join("data", f"{root_pid}-p", "manuscript", "block", section),
                os.path.join("data", str(pid), f"man_{str(pid)}", section),
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
        except Exception as e:
            return None

    @staticmethod
    def _load_upstream_context(pid):
        """彙整前置模組可用摘要，供 Manuscript Drafter 快速冷啟動。"""
        try:
            base_pid = pid[:-2] if str(pid).endswith('-p') else str(pid)
            snippets = []

            # Project 基本資訊
            try:
                from app.models import Project
                proj = Project.query.filter_by(project_id=base_pid).first() or Project.query.filter_by(project_id=pid).first()
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
                os.path.join("data", base_pid, "paq", "taxonomy_manual_update.json"),
                os.path.join("data", base_pid, "paq", "paq", "taxonomy_manual_update.json"),
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
            sr_path = os.path.join("data", base_pid, "search_results.json")
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
            note_json_path = os.path.join("data", f"{base_pid}-p", "study", f"{base_pid}-p_note.json")
            note_json_path_legacy = os.path.join("data", "note", f"{base_pid}_note.json")
            note_txt_path = os.path.join("data", base_pid, "study_notes.txt")
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
                snippets.append(f"[Study Notes]\n{notes[:1200]}")

            if not snippets:
                return ""
            merged = "\n\n".join(snippets)
            return merged[:5000]
        except Exception:
            return ""
