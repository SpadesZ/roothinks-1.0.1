# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_context_chain_routes.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 註冊 context chain 建立、讀取與更新 API，維持節點順序、來源和專案隔離。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/core_pro/literature/literature_context_chain_routes.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. 註冊 context_chain/brief/topk/hybrid/override 路由。
#2. 封裝 ContextChainService 查詢與 ACL 參數傳遞。
#3. 統一回傳 context-chain 相關 API 結果格式。

from flask import jsonify, request


def register_context_chain_routes(literature_bp, deps):
    @literature_bp.route('/api/literature/context_chain/brief', methods=['GET'])
    def get_context_chain_brief():
        """
        讀取專案層 L3 摘要
        """
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        tenant_id, requester_access_level = deps.get_acl_context(default_access='internal')
        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404

        try:
            ccs = deps.get_context_chain_service()
            brief = ccs.get_project_brief(
                pid=pid,
                tenant_id=tenant_id,
                requester_access_level=requester_access_level,
            )
            return jsonify({"status": "success", "results": brief})
        except Exception as e:
            return deps._internal_error("get_context_chain_brief", e)

    @literature_bp.route('/api/literature/context_chain/topk', methods=['GET'])
    def get_context_chain_topk():
        """
        以向量相似度從 L2 packets 檢索 Top-K（含 ACL pre-filter）
        """
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        query = request.args.get('query', '')
        tenant_id, requester_access_level = deps.get_acl_context(default_access='internal')
        k_raw = request.args.get('k', '5')
        try:
            k = min(max(int(k_raw), 1), 100)
        except Exception:
            k = 5

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404

        try:
            ccs = deps.get_context_chain_service()
            res = ccs.query_topk_packets(
                pid=pid,
                query=query,
                k=k,
                tenant_id=tenant_id,
                requester_access_level=requester_access_level,
            )
            return jsonify({"status": "success", "results": res})
        except Exception as e:
            return deps._internal_error("get_context_chain_topk", e)

    @literature_bp.route('/api/literature/context_chain/hybrid_query', methods=['GET'])
    def get_context_chain_hybrid_query():
        """
        輕量 Hybrid KG-RAG 查詢：Vector Top-K + KG triples
        """
        pid = deps._normalize_literature_pid(request.args.get('pid'))
        query = request.args.get('query', '')
        tenant_id, requester_access_level = deps.get_acl_context(default_access='internal')
        kv_raw = request.args.get('k_vector', '5')
        kt_raw = request.args.get('k_triple', '5')

        try:
            k_vector = int(kv_raw)
        except Exception:
            k_vector = 5
        try:
            k_triple = int(kt_raw)
        except Exception:
            k_triple = 5

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404

        try:
            ccs = deps.get_context_chain_service()
            res = ccs.query_hybrid_kg_rag(
                pid=pid,
                query=query,
                k_vector=k_vector,
                k_triple=k_triple,
                tenant_id=tenant_id,
                requester_access_level=requester_access_level,
            )
            return jsonify({"status": "success", "results": res})
        except Exception as e:
            return deps._internal_error("get_context_chain_hybrid_query", e)

    @literature_bp.route('/api/literature/context_chain/override_claim', methods=['POST'])
    def override_context_chain_claim():
        """
        手動覆寫 L1 claim 並標記 L2/L3 stale
        """
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        claim_id = data.get('claim_id')
        patch = data.get('patch') or {}
        editor = data.get('editor', 'user')
        tenant_id, requester_access_level = deps.get_acl_context(default_access='internal')

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not claim_id or not isinstance(patch, dict) or not patch:
            return jsonify({"status": "error", "message": "Missing claim_id/patch"}), 400

        try:
            ccs = deps.get_context_chain_service()
            chain = ccs.apply_manual_override(
                pid=pid,
                claim_id=claim_id,
                patch=patch,
                editor=editor,
                tenant_id=tenant_id,
                requester_access_level=requester_access_level,
            )
            return jsonify(
                {
                    "status": "success",
                    "message": "Claim overridden. L2/L3 marked stale.",
                    "version": chain.get("version", 0),
                }
            )
        except PermissionError:
            return jsonify({"status": "error", "message": "Forbidden"}), 403
        except Exception as e:
            return deps._internal_error("override_context_chain_claim", e)
