# Roothinks source maintenance contract
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 檔案路徑: app/core_pro/literature/literature_library_routes.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   持久文獻庫 API：list / update / import / export / rebuild_evidence_index。
# 主要責任: 註冊 Literature library 清單、匯入、screening 與 metadata 更新 API，逐次驗證 PID/paper scope。
#   1. Library entry 狀態管理（screening 與 reading 分開）與 Paper 連結驗證。
#   2. 外部 normalized / CSL JSON 批次匯入。
#   3. Reference export（BibTeX/RIS/CSL JSON，缺欄位省略、不捏造）。
#   4. Evidence index backfill / 重建（冪等）。
# 安全邊界:
#   - 改動 `screening_status` 需 workspace 角色 >= editor（owner=PI、editor=Co-PI；
#     coauthor 是章節限定編輯，一律不得改）。這道門原本**完全不存在** ——
#     任何打得到 API 的人都能把論文標成 included，而 included 直接決定
#     Drafter 的寫作依據（NOTE-013 / NOTE-020）。
#   - actor 由 deps.current_screening_actor() 從登入身分取，不得由請求體帶入。
# 維護提醒:
#   - deps 為 literature_routes module（與其他 register_* 相同慣例）。
#   - require_workspace_role 在 AUTH_MODE != session 時一律放行（全站慣例）；
#     service 層的 actor 必填是第二道，兩層各擋各的，缺一都會留下缺口。
# -----------------------------------------------------------------------------

from flask import Response, jsonify, request

_IMPORT_MAX_ITEMS = 500


def register_library_routes(literature_bp, deps):
    @literature_bp.route('/api/literature/library', methods=['GET'])
    def get_library():
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        try:
            entries = deps.get_literature_library().list_entries(
                pid,
                screening=str(request.args.get('screening') or '').strip(),
                reading=str(request.args.get('reading') or '').strip(),
                query=str(request.args.get('q') or '').strip(),
            )
            return jsonify({"status": "success", "count": len(entries), "entries": entries})
        except Exception as e:
            return deps._internal_error("get_library", e)

    @literature_bp.route('/api/literature/library/update', methods=['POST'])
    def update_library_entry():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        entry_id = str(data.get('entry_id') or '').strip()
        patch = data.get('patch') if isinstance(data.get('patch'), dict) else {}

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not entry_id or not patch:
            return jsonify({"status": "error", "message": "Missing entry_id/patch"}), 400

        # paper_id 連結必須指向真實 Paper（空字串 = 解除連結）
        if 'paper_id' in patch:
            link_target = str(patch.get('paper_id') or '').strip()
            if link_target:
                try:
                    link_target = deps._safe_paper_id(link_target)
                except deps.BadRequest:
                    return jsonify({"status": "error", "message": "Invalid paper_id"}), 400
                paper = deps.Paper.query.filter_by(paper_id=link_target, pid=pid).first()
                if paper is None:
                    return jsonify({"status": "error", "message": f"Paper not found: {link_target}"}), 404
            patch['paper_id'] = link_target

        # NOTE(NOTE-020): 納入／排除是 PI/Co-PI 的學術判斷。
        #
        # **這道檢查目前是重複的，而且我知道它是重複的**：`enforce_project_ownership`
        # 這個 before_request 守衛已經依 HTTP method 推 min_role，POST/PUT/PATCH
        # 一律要 editor（security.py 約 L620），所以 viewer 與 coauthor 本來就
        # 打不進來。實測拿掉本段之後 viewer 仍是 403。
        # 保留的理由只有一個：那道守衛的門檻是**從 method 推導**的，
        # 一旦有人加了 GET 帶參數的變更路徑、或改了 method 對應表，
        # 保護就會無聲消失；而 screening 決定的是稿件引用哪些文獻，
        # 屬於學術誠信控制，值得在真正做決定的那一行再確認一次。
        # 不要把它當成「原本沒有角色檢查所以補上」—— 那個描述是錯的。
        actor = ""
        if 'screening_status' in patch:
            from app.models import ROLE_EDITOR
            from app.security import require_workspace_role

            denied = require_workspace_role(pid, ROLE_EDITOR)
            if denied is not None:
                return denied
            actor = deps.current_screening_actor()

        try:
            entry = deps.get_literature_library().update_entry(
                pid, entry_id, patch,
                actor=actor,
                batch_id=str(data.get('batch_id') or '').strip(),
            )
            return jsonify({"status": "success", "entry": entry})
        except KeyError:
            return jsonify({"status": "error", "message": "Entry not found"}), 404
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400
        except Exception as e:
            return deps._internal_error("update_library_entry", e)

    @literature_bp.route('/api/literature/library/import', methods=['POST'])
    def import_library_items():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        items = data.get('items')

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not isinstance(items, list) or not items:
            return jsonify({"status": "error", "message": "Missing items list"}), 400
        if len(items) > _IMPORT_MAX_ITEMS:
            return jsonify({"status": "error", "message": f"Too many items (max {_IMPORT_MAX_ITEMS})"}), 400

        try:
            stat = deps.get_literature_library().import_items(pid, items)
            return jsonify({"status": "success", **stat})
        except Exception as e:
            return deps._internal_error("import_library_items", e)

    @literature_bp.route('/api/literature/library/export', methods=['GET'])
    def export_library_references():
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        fmt = str(request.args.get('format') or 'bibtex').strip().lower()
        scope = str(request.args.get('scope') or 'included').strip().lower()

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if fmt not in {'bibtex', 'ris', 'csl-json'}:
            return jsonify({"status": "error", "message": "format must be bibtex|ris|csl-json"}), 400

        try:
            from app.services.metadata_service import dumps_csl_json, export_bibtex, export_ris

            papers = deps.get_literature_library().entries_for_export(pid, scope=scope)
            if fmt == 'bibtex':
                body, mime, ext = export_bibtex(papers), "application/x-bibtex", "bib"
            elif fmt == 'ris':
                body, mime, ext = export_ris(papers), "application/x-research-info-systems", "ris"
            else:
                body, mime, ext = dumps_csl_json(papers), "application/json", "json"
            return Response(
                body,
                mimetype=mime,
                headers={"Content-Disposition": f"attachment; filename={pid}_references.{ext}"},
            )
        except Exception as e:
            return deps._internal_error("export_library_references", e)

    @literature_bp.route('/api/literature/rebuild_evidence_index', methods=['POST'])
    def rebuild_evidence_index():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        paper_ids = data.get('paper_ids')

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if paper_ids is not None:
            if not isinstance(paper_ids, list):
                return jsonify({"status": "error", "message": "paper_ids must be a list"}), 400
            try:
                paper_ids = [deps._safe_paper_id(p) for p in paper_ids]
            except deps.BadRequest:
                return jsonify({"status": "error", "message": "Invalid paper_id in request"}), 400

        try:
            from app.services.paper_evidence_sync import rebuild_project_evidence

            result = rebuild_project_evidence(pid, deps.DATA_ROOT, paper_ids)
            return jsonify({"status": "success", **result})
        except Exception as e:
            return deps._internal_error("rebuild_evidence_index", e)
