# Roothinks source maintenance contract
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 檔案路徑: app/core_pro/literature/literature_chat_routes.py
# 產生時間: 2026-08-17
# 版本: v0.1
# 模組定位:
#   Literature 對話式找文獻 API：chat / history / clear / adopt。
# 主要責任: 接住對話請求，串起 A（意圖與回覆）與 B/C（搜尋與互檢），並落地對話與搜尋快取。
#   1. 一次 POST 走完 A -> (B/C 搜尋 -> 互檢 -> 回查驗證) -> A 組稿；
#      不觸發搜尋的輪次只花一次 LLM 呼叫。
#   2. 搜尋結果先查 persistent cache（TTL 見 literature_chat_store）再決定是否上網。
#   3. adopt 只把論文併成 candidate，走既有 LiteratureLibrary.merge_candidates，
#      **不得**自動 included —— included 是作者的學術判斷（NOTE-013 / NOTE-020）。
# 安全邊界:
#   - ACL 由 literature_routes 的 before_request 統一套用（POST->editor、GET->viewer），
#     本層不得自行放寬。
#   - 對話存檔失敗要在回應明說 persisted=false，不得靜默（同 PAQ 2A，NOTE-022）。
#   - meta.stage_errors 一律原樣回前端：搜尋只跑完一半卻看起來像完整結果，
#     比直接報錯更糟。
# 維護提醒:
#   - deps 為 literature_routes module（與其他 register_* 相同慣例）。
# -----------------------------------------------------------------------------

import os

from flask import jsonify, request

_MESSAGE_MAX_CHARS = 2000
_ADOPT_MAX_ITEMS = 50
_HISTORY_MAX = 40


def register_chat_routes(literature_bp, deps):
    def _project_context(pid: str) -> dict:
        """
        給 A 當對話背景。查不到專案不是錯誤——新專案本來就可能還沒設主題，
        A 的 prompt 會顯示「（未設定）」而不是硬掰一個。
        """
        ctx = {"research_title": "", "manual_context": ""}
        try:
            proj = deps.Project.query.filter_by(project_id=pid).first()
            if proj:
                ctx["research_title"] = proj.research_title or proj.name or ""
                ctx["manual_context"] = getattr(proj, "context_background", "") or ""
        except Exception as e:
            deps.logger.warning("[lit_chat] 讀取專案背景失敗 pid=%s: %s", pid, e)

        # 2.1「研究主題定義」剛存下的那句最貼近使用者現在在想什麼，優先採用。
        try:
            ctx_file = os.path.join(deps._get_project_dir(pid), "context_history.json")
            if os.path.exists(ctx_file):
                history = deps.load_json_locked(ctx_file, [])
                if isinstance(history, list) and history:
                    latest = history[0]
                    if isinstance(latest, dict) and latest.get("text"):
                        ctx["manual_context"] = str(latest["text"])
        except Exception as e:
            deps.logger.warning("[lit_chat] 讀取 context_history 失敗 pid=%s: %s", pid, e)
        return ctx

    @literature_bp.route('/api/literature/chat', methods=['POST'])
    def literature_chat():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        message = str(data.get('message') or '').strip()

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not message:
            return jsonify({"status": "error", "message": "Missing message"}), 400
        if len(message) > _MESSAGE_MAX_CHARS:
            return jsonify(
                {"status": "error", "message": f"Message too long (max {_MESSAGE_MAX_CHARS})"}
            ), 400

        try:
            litchat = deps.get_task_3a_litchat()
            if litchat is None:
                return jsonify({"status": "error", "message": "Chat task module not available"}), 503

            store = deps.get_literature_chat_store()
            history = store.load_records(pid, limit=_HISTORY_MAX)

            def _search(query: str) -> dict:
                cached = store.load_cached_search(pid, query)
                if cached:
                    result = dict(cached)
                    meta = dict(result.get("meta") or {})
                    meta["cache_hit"] = True
                    result["meta"] = meta
                    return result

                scout = deps.get_task_3bc_scout()
                if scout is None:
                    return {
                        "papers": [],
                        "dropped": [],
                        "meta": {"stage_errors": {"scout": "Scout task module not available"}},
                    }
                fresh = scout.run_debate_search(query)
                store.save_cached_search(pid, query, fresh)
                return fresh

            turn = litchat.run_chat_turn(
                history, message, _project_context(pid), search_fn=_search
            )
            papers = turn.get("papers") or []
            meta = turn.get("meta") or {}

            persisted = True
            try:
                store.append_record(
                    pid,
                    user_message=message,
                    ai_reply=turn.get("reply", ""),
                    papers=papers,
                    meta=meta,
                    actor=deps.current_screening_actor(),
                )
            except Exception as save_err:
                # 存檔失敗不擋回覆，但必須讓使用者知道這一輪不會留下來。
                persisted = False
                deps.logger.warning("[lit_chat] 對話存檔失敗 pid=%s: %s", pid, save_err)

            return jsonify(
                {
                    "status": "success",
                    "reply": turn.get("reply", ""),
                    "papers": papers,
                    "meta": meta,
                    "persisted": persisted,
                }
            )
        except Exception as e:
            return deps._internal_error("literature_chat", e)

    @literature_bp.route('/api/literature/chat/history', methods=['GET'])
    def literature_chat_history():
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        try:
            records = deps.get_literature_chat_store().load_records(pid, limit=_HISTORY_MAX)
            return jsonify({"status": "success", "count": len(records), "records": records})
        except Exception as e:
            return deps._internal_error("literature_chat_history", e)

    @literature_bp.route('/api/literature/chat/clear', methods=['POST'])
    def literature_chat_clear():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        try:
            removed = deps.get_literature_chat_store().clear(pid)
            return jsonify({"status": "success", "removed": removed})
        except Exception as e:
            return deps._internal_error("literature_chat_clear", e)

    @literature_bp.route('/api/literature/chat/adopt', methods=['POST'])
    def literature_chat_adopt():
        """把對話裡挑中的論文併入 2.2B 文獻庫（一律 candidate）。"""
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        papers = data.get('papers')
        topic = str(data.get('topic') or '').strip()

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not isinstance(papers, list) or not papers:
            return jsonify({"status": "error", "message": "Missing papers list"}), 400
        if len(papers) > _ADOPT_MAX_ITEMS:
            return jsonify(
                {"status": "error", "message": f"Too many papers (max {_ADOPT_MAX_ITEMS})"}
            ), 400

        # 只收 metadata 欄位。前端傳來的東西不是授權來源，screening 狀態尤其
        # 不能由請求體帶入（會變成「有人替 PI 做了納入決定」）。
        cleaned = []
        for raw in papers:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get('title') or '').strip()
            doi = str(raw.get('doi') or '').strip()
            if not title and not doi:
                continue
            cleaned.append(
                {
                    "title": title,
                    "authors": raw.get('authors') or [],
                    "year": raw.get('year'),
                    "venue": str(raw.get('venue') or '').strip(),
                    "doi": doi,
                    "url": str(raw.get('url') or '').strip(),
                    "abstract": str(raw.get('abstract') or '')[:4000],
                    "citations": raw.get('citations') or 0,
                    "source": str(raw.get('source') or '').strip(),
                }
            )

        if not cleaned:
            return jsonify({"status": "error", "message": "No usable paper metadata"}), 400

        try:
            stat = deps.get_literature_library().merge_candidates(
                pid, cleaned, topic=topic, default_source="lit_chat"
            )
            return jsonify({"status": "success", **stat})
        except Exception as e:
            return deps._internal_error("literature_chat_adopt", e)
