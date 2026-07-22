#路徑(app/core_pro/literature/literature_context_routes.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. 註冊 bootstrap/context/search/search-results 路由。
#2. 管理專案 context history 與 Task3 搜尋結果持久化。
#3. 維持與前端既有 JSON 契約相容。

import json
import os
import time

from flask import jsonify, request


def register_context_routes(literature_bp, deps):
    @literature_bp.route('/api/literature/bootstrap', methods=['GET'])
    def get_literature_bootstrap():
        """
        初始化 Literature 頁面所需資料：
        1. 正式專案列表
        2. Active Project
        3. 來自 PAQ 的 Manual Context 種子資料
        """
        req_pid = deps._normalize_literature_pid(request.args.get('pid'))

        try:
            # [Security Fix 20260722] session 模式下，bootstrap 僅能回傳「當前登入者
            # 有 WorkspaceMember membership」的正式專案。原本用 raw Project.query 撈
            # 全部 formal 專案，導致零權限帳號（例：全新註冊）開 /literature（URL 無
            # pid，繞過 blueprint 的 pid ACL）時，被回傳他人專案清單與第一個專案的
            # manual_context = 跨租戶越權讀取。與 ProjectService.get_projects 同一套
            # 過濾邏輯保持一致。dev/none 模式（無 AUTH_MODE=session）維持全回不變。
            from flask import current_app as _app
            _auth_mode = str(_app.config.get("AUTH_MODE", "none")).strip().lower()

            _q = deps.Project.query.filter_by(status='formal')
            if _auth_mode == "session":
                from flask_login import current_user
                if not current_user.is_authenticated:
                    return jsonify(
                        {
                            'status': 'success',
                            'active_project': None,
                            'manual_context': '',
                            'research_title': '',
                            'formal_projects': [],
                        }
                    )
                from app.models import WorkspaceMember
                _q = _q.join(
                    WorkspaceMember,
                    WorkspaceMember.pid == deps.Project.project_id,
                ).filter(WorkspaceMember.user_id == current_user.id)

            formal_projects = _q.order_by(deps.Project.created_at.desc()).all()
            for proj in formal_projects:
                try:
                    deps.ensure_formal_project_records(proj)
                except Exception as sync_err:
                    deps.logger.warning(f"Bootstrap seed sync failed for {proj.project_id}: {sync_err}")

            project_list = [
                {
                    'pid': p.project_id,
                    'research_title': p.research_title or p.name,
                    'name': p.name,
                }
                for p in formal_projects
            ]

            if not project_list:
                return jsonify(
                    {
                        'status': 'success',
                        'active_project': None,
                        'manual_context': '',
                        'research_title': '',
                        'formal_projects': [],
                    }
                )

            active_project = next((p for p in formal_projects if p.project_id == req_pid), None)
            if active_project is None:
                active_project = formal_projects[0]

            seed = deps.ensure_formal_project_records(active_project)
            seed = seed or deps._load_seed_manual_context(active_project.project_id) or {}

            manual_context = (
                seed.get('manual_context')
                or active_project.context_background
                or active_project.research_title
                or active_project.name
                or ''
            )

            return jsonify(
                {
                    'status': 'success',
                    'active_project': active_project.project_id,
                    'manual_context': manual_context,
                    'research_title': active_project.research_title or active_project.name,
                    'formal_projects': project_list,
                }
            )
        except Exception as e:
            return deps._internal_error("get_literature_bootstrap", e)

    @literature_bp.route('/api/literature/save_context', methods=['POST'])
    def save_context():
        """
        儲存研究主題 (Context Definition)
        功能:
        1. 接收前端輸入的 Context。
        2. 讀取現有 history，將新資料插入最前方 (Latest first)。
        3. 寫回 context_history.json。
        """
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        context_text = data.get('context')

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not context_text:
            return jsonify({"status": "error", "message": "Missing context"}), 400

        try:
            p_dir = deps._get_project_dir(pid)
            os.makedirs(p_dir, exist_ok=True)

            ctx_file = os.path.join(p_dir, "context_history.json")
            history = []

            if os.path.exists(ctx_file):
                try:
                    history = deps.load_json_locked(ctx_file, [])
                except Exception as e:
                    deps.logger.warning(f"Failed to load context history: {e}")
                    history = []

            new_entry = {
                "timestamp": time.time(),
                "date_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                "text": context_text,
            }
            history.insert(0, new_entry)
            history = history[:50]

            deps.write_json_locked(ctx_file, history)

            proj = deps.Project.query.filter_by(project_id=pid).first()
            if proj and proj.status == 'formal':
                deps.ensure_formal_project_records(proj, manual_context=context_text)

            return jsonify({"status": "success", "message": "Context saved.", "entry": new_entry})
        except Exception as e:
            return deps._internal_error("save_context", e)

    @literature_bp.route('/api/literature/get_context_history', methods=['GET'])
    def get_context_history():
        """
        獲取 Context 歷史
        """
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        if not pid:
            return jsonify([])

        ctx_file = os.path.join(deps._get_project_dir(pid), "context_history.json")
        if os.path.exists(ctx_file):
            try:
                return jsonify(deps.load_json_locked(ctx_file, []))
            except Exception as e:
                deps.logger.warning("[get_context_history] failed to load %s: %s", ctx_file, e)
                return jsonify([])
        return jsonify([])

    @literature_bp.route('/api/literature/search', methods=['POST'])
    def execute_search():
        """
        執行 Task 3 搜尋建議 (主動式導航)
        """
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        context = data.get('context')
        include_reasoning = deps._to_bool(data.get('include_reasoning', True), True)
        tenant_id, access_level = deps.get_acl_context(default_access='internal')

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not context:
            return jsonify({"status": "error", "message": "Context required"}), 400

        try:
            task_3search = deps.get_task_3search()
            if task_3search is None:
                return jsonify({"status": "error", "message": "Task 3 module not available"}), 503

            searcher = task_3search.ContextSearcher()
            advice = searcher.generate_suggestions(pid, context, include_reasoning=include_reasoning)

            # 持久文獻庫：把本輪全量候選 merge 進 library（只補不覆寫），
            # 失敗只記 warning，不影響搜尋結果。pool 不回傳給前端。
            library_pool = advice.pop("library_pool", None)
            try:
                library = deps.get_literature_library()
                merge_stat = library.merge_candidates(
                    pid,
                    library_pool if library_pool else (advice.get("papers") or []),
                    topic=context,
                    default_source="task3_search",
                )
                deps.logger.info(
                    "[Library] merge for %s: +%s new, %s updated, total=%s",
                    pid, merge_stat.get("added"), merge_stat.get("updated"), merge_stat.get("total"),
                )
            except Exception as lib_err:
                deps.logger.warning(f"[Library] merge failed: {lib_err}")

            try:
                ccs = deps.get_context_chain_service()
                ccs.update_from_task3_search(
                    pid=pid,
                    topic=context,
                    task3_output=advice,
                    include_reasoning=include_reasoning,
                    tenant_id=tenant_id,
                    access_level=access_level,
                )
            except Exception as chain_err:
                deps.logger.warning(f"[ContextChain] Update failed: {chain_err}")

            return jsonify({"status": "success", "results": advice})
        except Exception as e:
            return deps._internal_error("execute_search", e)

    @literature_bp.route('/api/literature/save_search_results', methods=['POST'])
    def save_search_results():
        """
        保存搜尋結果到文件 (持久化)
        防止每次刷新都重新執行 Task 3
        """
        deps.logger.info("[save_search_results] Endpoint called, received payload")

        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        results = data.get('results')
        deps.logger.info(
            "[save_search_results] pid=%s, results type=%s, results keys=%s",
            pid,
            type(results).__name__,
            list(results.keys()) if isinstance(results, dict) else 'N/A',
        )

        if not pid:
            deps.logger.warning(f"[save_search_results] Invalid pid: {data.get('pid')}")
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not results:
            deps.logger.warning(f"[save_search_results] Missing data: pid={pid}, results={bool(results)}")
            return jsonify({"status": "error", "message": "Missing results"}), 400

        try:
            p_dir = deps._get_project_dir(pid)
            deps.logger.info(f"[save_search_results] Project directory: {p_dir}")
            deps.logger.info(f"[save_search_results] Directory exists: {os.path.exists(p_dir)}")

            os.makedirs(p_dir, exist_ok=True)
            deps.logger.info(f"[save_search_results] Directory created/verified: {os.path.exists(p_dir)}")

            results_file = deps.safe_join_under(p_dir, "search_results.json")
            deps.logger.info(f"[save_search_results] Target file: {results_file}")

            results_with_timestamp = {
                "timestamp": time.time(),
                "date_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                "results": results,
            }

            json_str = json.dumps(results_with_timestamp, ensure_ascii=False, indent=4)
            deps.logger.info(f"[save_search_results] JSON size: {len(json_str)} bytes")

            deps.write_json_locked(results_file, results_with_timestamp)

            if os.path.exists(results_file):
                file_size = os.path.getsize(results_file)
                deps.logger.info(
                    f"[save_search_results] ✓ File created successfully: {results_file}, size={file_size} bytes"
                )
                return jsonify({"status": "success", "message": "Search results saved"})
            deps.logger.error(f"[save_search_results] ✗ File not found after write: {results_file}")
            return jsonify({"status": "error", "message": "File not created"}), 500

        except Exception as e:
            return deps._internal_error("save_search_results", e)

    @literature_bp.route('/api/literature/get_search_results', methods=['GET'])
    def get_search_results():
        """
        獲取已保存的搜尋結果
        如果存在則返回，客戶端無需重新執行 Task 3
        """
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        deps.logger.info(f"[get_search_results] Endpoint called for pid={pid}")

        if not pid:
            deps.logger.warning("[get_search_results] Invalid pid parameter")
            return jsonify({"status": "error", "message": "Invalid pid"}), 404

        try:
            p_dir = deps._get_project_dir(pid)
            results_file = deps.safe_join_under(p_dir, "search_results.json")

            deps.logger.info(f"[get_search_results] Project dir: {p_dir}")
            deps.logger.info(f"[get_search_results] Results file path: {results_file}")
            deps.logger.info(f"[get_search_results] Directory exists: {os.path.exists(p_dir)}")
            deps.logger.info(f"[get_search_results] File exists: {os.path.exists(results_file)}")

            if os.path.exists(results_file):
                file_size = os.path.getsize(results_file)
                deps.logger.info(f"[get_search_results] ✓ Found results file, size={file_size} bytes")

                data = deps.load_json_locked(results_file, {})
                deps.logger.info(
                    f"[get_search_results] Loaded data with keys: {list(data.keys()) if isinstance(data, dict) else []}"
                )
                return jsonify({"status": "success", "results": (data or {}).get("results", {})})
            deps.logger.info("[get_search_results] ✓ No results file found (new session or cleared)")
            return jsonify({"status": "success", "results": None})
        except Exception as e:
            return deps._internal_error("get_search_results", e)

    @literature_bp.route('/api/literature/clear_search_results', methods=['POST'])
    def clear_search_results():
        """
        清除已保存的搜尋結果
        用戶可以手動清除以進行新的搜尋
        """
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404

        try:
            results_file = deps.safe_join_under(deps._get_project_dir(pid), "search_results.json")

            if os.path.exists(results_file):
                os.remove(results_file)

            return jsonify({"status": "success", "message": "Search results cleared"})
        except Exception as e:
            return deps._internal_error("clear_search_results", e)
