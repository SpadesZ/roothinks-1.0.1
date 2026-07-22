# 檔案路徑: app/core_pro/literature/literature_library_routes.py
# 產生時間: 2026-07-19
# 版本: v0.1
# 模組定位:
#   持久文獻庫 API：list / update / import / export / rebuild_evidence_index。
# 主要責任:
#   1. Library entry 狀態管理（screening 與 reading 分開）與 Paper 連結驗證。
#   2. 外部 normalized / CSL JSON 批次匯入。
#   3. Reference export（BibTeX/RIS/CSL JSON，缺欄位省略、不捏造）。
#   4. Evidence index backfill / 重建（冪等）。
# 維護提醒:
#   - deps 為 literature_routes module（與其他 register_* 相同慣例）。
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

        try:
            entry = deps.get_literature_library().update_entry(pid, entry_id, patch)
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
