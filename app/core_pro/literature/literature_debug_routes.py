# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_debug_routes.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 註冊 Literature 診斷 API，回傳可遮罩的 pipeline 狀態與 artifact 檢查結果。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/core_pro/literature/literature_debug_routes.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. 註冊 debug/runtime_log_tail、flowb_probe、recover_flowb 路由。
#2. 提供 Flow B 執行態診斷與手動恢復能力。
#3. 以 deps 介面重用主模組既有工具與資料存取。

from datetime import datetime, timezone

from flask import jsonify, request


def register_debug_routes(literature_bp, deps):
    @literature_bp.route('/api/literature/debug/runtime_log_tail', methods=['GET'])
    def debug_runtime_log_tail():
        lines_raw = request.args.get("lines", "120")
        keyword = str(request.args.get("keyword", "")).strip().lower()
        try:
            lines = max(20, min(int(lines_raw), 400))
        except Exception:
            lines = 120

        log_path = deps._runtime_log_file_path()
        rows = deps._tail_text_file(log_path, lines=lines, max_bytes=512000)
        if keyword:
            rows = [r for r in rows if keyword in r.lower()]

        return jsonify(
            {
                "status": "success",
                "log_file": log_path,
                "lines": rows,
            }
        )

    @literature_bp.route('/api/literature/debug/flowb_probe', methods=['GET'])
    def debug_flowb_probe():
        pid = deps._normalize_literature_pid(request.args.get("pid"))
        paper_id = request.args.get("paper_id")
        if not pid or not paper_id:
            return jsonify({"status": "error", "message": "Missing/invalid pid or paper_id"}), 400
        try:
            paper_id = deps._safe_paper_id(paper_id)
        except deps.BadRequest:
            return jsonify({"status": "error", "message": "Invalid paper_id"}), 400

        lines_raw = request.args.get("lines", "160")
        try:
            lines = max(20, min(int(lines_raw), 400))
        except Exception:
            lines = 160

        paper = deps.Paper.query.filter_by(pid=pid, paper_id=paper_id).first()
        if paper is None:
            return jsonify({"status": "error", "message": "Paper not found"}), 404

        snap = deps._flowb_debug_snapshot(pid, paper_id, log_lines=lines)
        return jsonify({"status": "success", "debug": snap})

    @literature_bp.route('/api/literature/debug/recover_flowb', methods=['POST'])
    def debug_recover_flowb():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get("pid"))
        paper_id = data.get("paper_id")
        force = bool(data.get("force", False))
        min_age_raw = data.get("min_age_sec", 300)

        if not pid or not paper_id:
            return jsonify({"status": "error", "message": "Missing/invalid pid or paper_id"}), 400
        try:
            paper_id = deps._safe_paper_id(paper_id)
        except deps.BadRequest:
            return jsonify({"status": "error", "message": "Invalid paper_id"}), 400
        try:
            min_age_sec = max(30, int(min_age_raw))
        except Exception:
            min_age_sec = 300

        paper = deps.Paper.query.filter_by(pid=pid, paper_id=paper_id).first()
        if paper is None:
            return jsonify({"status": "error", "message": "Paper not found"}), 404

        status = str(paper.interpretation_status or "").lower()
        if status not in {"processing_b", "translating_local", "judging_llm"}:
            return jsonify(
                {
                    "status": "error",
                    "message": f"Paper status is not Flow B busy: {paper.interpretation_status}",
                }
            ), 409

        updated = paper.updated_at
        age_sec = None
        if isinstance(updated, datetime):
            ts = updated
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            age_sec = max(0.0, (datetime.utcnow() - ts).total_seconds())

        if (not force) and (age_sec is not None) and (age_sec < min_age_sec):
            return jsonify(
                {
                    "status": "error",
                    "message": f"Flow B not stale yet (age={round(age_sec, 3)}s, min_age_sec={min_age_sec})",
                }
            ), 409

        reason = f"Flow B recovered from stuck state (age_sec={round(age_sec or 0.0, 3)})"
        deps._append_flow_event(
            pid,
            paper_id,
            flow="flow_b",
            event="manual_recover",
            status=deps.Paper.STATUS_FAILED,
            message=reason,
            extra={"force": force, "min_age_sec": min_age_sec},
        )
        deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_FAILED, reason)

        snap = deps._flowb_debug_snapshot(pid, paper_id, log_lines=120)
        return jsonify({"status": "success", "message": "Flow B recovered to failed/need-retry", "debug": snap})
