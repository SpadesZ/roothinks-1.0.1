# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_listen.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 註冊 Literature 頁面與事件入口，將請求轉交 routes/processing services。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/core_proc/literature/Literature_listen.py) #版本 v1.0 #更版時間 20260206-1635
import os
import threading
import json
import shutil
import re
import time
import logging
import atexit
from datetime import datetime
from flask import request, jsonify, current_app
from werkzeug.exceptions import BadRequest
from app.models import db, Paper, Project
from app.security import safe_join_under, validate_id
from app.core_pro.storage_layout import resolve_literature_paper_dir

# [LAVA Module Imports]
# 引用核心處理模組，確保路徑與依賴正確
from .literature_cvpipeline import PaperCVPipeline
from .literature_translator import get_translator, TranslationContext  # [v2.0] 混合翻譯器
from app.llm_service.matching_tasks.task_4cv import SemanticCorrector
from app.llm_service.matching_tasks.task_5interpret import Interpreter

logger = logging.getLogger("LiteratureListen")
_PIPELINE_SHUTDOWN = threading.Event()


@atexit.register
def _shutdown_pipeline_worker():
    _PIPELINE_SHUTDOWN.set()

def register_routes(bp):
    
    @bp.route('/api/pipeline/execute', methods=['POST'])
    def execute_pipeline():
        """
        [LAVA Pipeline Execution v1.0] 
        核心職責：雙軌管線調度器 (Orchestrator)
        流程：Stage 1 -> Stage 6.5 -> Stage 9 -> Study Ready
        升級記錄：
        1. [Timer] 記錄 start/end 時間。
        2. [Bridge] 在 Stage 9 完成後，顯式更新 Paper.full_text_path，打通 Study 橋樑。
        """
        data = request.json
        target_ids = data.get('paper_ids', [])
        pid = (data.get('pid') or '').strip()
        if not pid:
            proj = Project.query.filter_by(status='formal').order_by(Project.created_at.desc()).first()
            pid = proj.project_id if proj else ''

        if not pid:
            return jsonify({"ok": False, "msg": "Missing project id (pid). Please select project in PAQ first."}), 400
        
        if not target_ids:
            return jsonify({"ok": False, "msg": "未選擇任何文件 (No papers selected)"}), 400
        
        # 獲取 Flask App Context 以便傳遞給背景執行緒
        app = current_app._get_current_object()

        # 定義背景工作流程
        def worker(p_ids):
            with app.app_context():
                logger.info(f">>> [LAVA Listener] Batch started for {len(p_ids)} papers...")
                
                # [Init] 初始化各階段處理引擎
                base_data_root = os.path.join(app.root_path, "..", "data")
                cv_pipe = PaperCVPipeline(base_data_path=base_data_root)
                corrector = SemanticCorrector()
                interpreter = Interpreter()
                translator = get_translator()  # [v2.0] 混合翻譯器 (NLLB + Gemini) 

                for paper_id in p_ids:
                    if _PIPELINE_SHUTDOWN.is_set():
                        logger.info("[LAVA Listener] Shutdown signal received. Stop remaining papers.")
                        break
                    # 使用 Context Manager 確保連線釋放
                    try:
                        paper = db.session.get(Paper, (paper_id, pid))
                        if not paper: continue
                        
                        if paper.process_status in ['paused', 'canceled']: 
                            logger.warning(f"[Skip] Paper {paper_id} is {paper.process_status}")
                            continue

                        # [Timer Start] 初始化計時器
                        try:
                            res_data = paper.get_results()
                            res_data['timer'] = {
                                'start': datetime.utcnow().isoformat(),
                                'end': None
                            }
                            paper.result_json = json.dumps(res_data)
                            paper.process_status = 'running'
                            paper.current_stage = 'processing' # UI Badge
                            db.session.commit()
                        except Exception as json_e:
                            logger.error(f"[Timer Init Error] {json_e}")

                        # --- 定義日誌更新函式 ---
                        def update_progress(stage_num, msg, status='running'):
                            try:
                                timestamp = time.strftime("%H:%M:%S")
                                prefix = f"[{timestamp}] [階段 {stage_num}/9]"
                                friendly_msg = f"{prefix} {msg}\n"
                                paper.process_log += friendly_msg
                                paper.process_status = status
                                db.session.commit()
                                logger.info(f"    -> {friendly_msg.strip()}")
                            except Exception as db_e:
                                logger.error(f"    ! Log Update Failed: {db_e}")

                        # --- 開始執行 LAVA 流程 ---
                        update_progress(1, "系統初始化：正在檢查檔案與載入 AI 模組...", 'running')

                        base_paper_path = resolve_literature_paper_dir(
                            base_data_root,
                            pid,
                            paper_id,
                            for_write=True,
                            migrate_legacy=True,
                        )
                        pdf_path = os.path.join(base_paper_path, "00_origins", "source.pdf")
                        
                        if not os.path.exists(pdf_path):
                            raise FileNotFoundError(f"原始 PDF 檔案遺失: {pdf_path}")

                        # --- Stage 2-6: CV Dual Pipeline ---
                        update_progress(2, "視覺感知啟動：正在執行 Stack A/B 雙軌辨識 (此步驟較耗時)...")
                        cv_pipe.run_pipeline(pid, paper_id, pdf_path)
                        
                        recog_dir = os.path.join(base_paper_path, "03_recognizes")
                        if not os.path.exists(recog_dir) or not os.listdir(recog_dir):
                            raise RuntimeError("CV 階段未產生任何識別結果 (Raw JSON missing)")

                        # --- Stage 6.5: Metadata Write-back ---
                        update_progress(6, "資訊提取：正在分析首頁以提取論文標題、作者與期刊資訊...")
                        try:
                            meta = corrector.extract_metadata(pid, paper_id, base_data_root)
                            if meta:
                                if meta.get("title") and meta["title"] != "Unknown": paper.title = meta["title"]
                                if meta.get("authors") and meta["authors"] != "Unknown": paper.authors = meta["authors"]
                                if meta.get("journal"): paper.journal = meta["journal"]
                                if meta.get("publish_date"): paper.publish_date = meta["publish_date"]
                                db.session.commit()
                                update_progress(6, f"資料更新成功：{paper.title[:30]}...")
                        except Exception as meta_e:
                            update_progress(6, "警告：資訊提取過程發生錯誤 (不影響後續流程)。")

                        # --- Stage 7: Context Guide ---
                        update_progress(7, "認知重構：正在閱讀全文以建立「全域術語指導書」...")
                        guide_path = corrector.generate_context_guide(pid, paper_id, base_data_root)
                        
                        # --- Stage 8: Semantic Fix ---
                        update_progress(8, "單頁精修：正在依據指導書修復 OCR 錯字與排版...")
                        files = sorted([f for f in os.listdir(recog_dir) if f.endswith('_raw.json')])
                        fix_count = 0
                        for fname in files:
                            raw_p = os.path.join(recog_dir, fname)
                            fixed_p = os.path.join(recog_dir, fname.replace('_raw.json', '_fixed.json'))
                            out = corrector.fix_content(raw_p, fixed_p, guide_path=guide_path)
                            if out: fix_count += 1
                        update_progress(8, f"完成 {fix_count}/{len(files)} 頁面的語義修復。")

                        # --- Stage 9: Fusion, Summary & Translation ---
                        update_progress(9, "最終階段：正在進行全文重組、摘要生成與多國語言翻譯...")
                        fusion_path = interpreter.run_fusion(pid, paper_id, base_data_root)
                        
                        if fusion_path and os.path.exists(fusion_path):
                            # Summary
                            try:
                                interpreter.run_summary(fusion_path)
                                update_progress(9, "摘要生成完畢。")
                            except Exception as sum_e:
                                update_progress(9, f"警告：摘要生成失敗 ({str(sum_e)})")

                            # Translation - [v2.0] 使用混合翻譯器
                            final_path = fusion_path
                            if translator:
                                try:
                                    trans_path = fusion_path.replace('.json', '_trans.json')
                                    translator.translate_file(fusion_path, trans_path, TranslationContext.LITERATURE_BATCH)
                                    final_path = trans_path
                                    update_progress(9, "多國語言翻譯完成 (Hybrid Mode)。")
                                except Exception as trans_e:
                                    update_progress(9, f"警告：翻譯過程發生錯誤 ({str(trans_e)})")
                            
                            # [Critical Bridge] 更新結果標記與路徑
                            final_res = paper.get_results()
                            final_res.update({"abstract": 1, "trans": 1, "summary": 1})
                            paper.result_json = json.dumps(final_res)
                            
                            # 寫入 Study 模組所需的路徑 (絕對路徑或相對路徑皆可，這裡存相對路徑較安全)
                            # 存: data/pid/paper_id/05_interprets/fusion/full_text_trans.json
                            rel_path = os.path.relpath(final_path, start=app.root_path) 
                            paper.full_text_path = final_path
                            paper.current_stage = 'study_ready' # Study 模組識別標誌
                            db.session.commit()
                        else:
                            raise RuntimeError("全文融合失敗 (Fusion Failed)")

                        update_progress(9, "恭喜！所有 LAVA 任務已成功執行完畢。", 'done')

                    except Exception as e:
                        logger.error("[Worker Error on %s] %s", paper_id, e, exc_info=True)
                        db.session.rollback()
                        try:
                            p_err = db.session.get(Paper, (paper_id, pid))
                            if p_err:
                                p_err.process_status = 'error'
                                p_err.current_stage = 'error'
                                p_err.process_log += f"\n[Critical Error] {str(e)}\n"
                                db.session.commit()
                        except Exception:
                            logger.warning("Failed to persist worker error state for %s", paper_id)
                    
                    finally:
                        # 清理暫存目錄，避免 temp 長期殘留
                        try:
                            temp_dir = safe_join_under(base_paper_path, "03_recognizes", "temp")
                            if os.path.isdir(temp_dir):
                                shutil.rmtree(temp_dir)
                        except Exception as cleanup_err:
                            logger.warning("Temp cleanup failed for %s/%s: %s", pid, paper_id, cleanup_err)

                        # [Timer End] 記錄結束時間
                        try:
                            p_final = db.session.get(Paper, (paper_id, pid))
                            if p_final:
                                res_data = p_final.get_results()
                                if 'timer' in res_data:
                                    res_data['timer']['end'] = datetime.utcnow().isoformat()
                                    p_final.result_json = json.dumps(res_data)
                                    db.session.commit()
                        except Exception as timer_e:
                            logger.error(f"[Timer End Error] {timer_e}")
                        
                        db.session.close()

        threading.Thread(target=worker, args=(target_ids,), daemon=True).start()
        return jsonify({"ok": True, "msg": "Batch started"})

    # --- Pipeline Status List ---
    @bp.route('/api/pipeline/list/<pid>', methods=['GET'])
    def list_pipeline_status(pid):
        try:
            papers = Paper.query.filter_by(pid=pid).order_by(Paper.paper_id.asc()).all()
            data = []
            
            for p in papers:
                res_json = p.get_results()
                timer_info = res_json.get('timer', {})
                
                data.append({
                    "paper_id": p.paper_id,
                    "title": p.title,
                    "journal": p.journal,
                    "authors": p.authors,
                    "status": p.process_status,
                    "log": p.process_log,
                    "results": {
                        "abstract": res_json.get('abstract'), 
                        "trans": res_json.get('trans'), 
                        "summary": res_json.get('summary'),
                        "timer": timer_info
                    }
                })
            return jsonify({"ok": True, "data": data})
        except Exception as e:
            logger.error("[pipeline_list] %s", e, exc_info=True)
            return jsonify({"ok": False, "msg": "Internal server error"}), 500

    # --- Pipeline Control ---
    @bp.route('/api/pipeline/control', methods=['POST'])
    def control_pipeline():
        try:
            data = request.json
            pid = validate_id(data.get('pid'), "project_id")
            paper_id = validate_id(data.get('paper_id'), "paper_id")
            action = data.get('action') 
            
            paper = db.session.get(Paper, (paper_id, pid))
            if not paper: return jsonify({"ok": False, "msg": "Not found"}), 404
            
            if action == 'delete':
                if paper.process_status == 'running': 
                    return jsonify({"ok": False, "msg": "Cannot delete running task"}), 400
                db.session.delete(paper)
                data_root = os.path.realpath(os.path.join(current_app.root_path, "..", "data"))
                raw_target_dir = resolve_literature_paper_dir(
                    data_root,
                    pid,
                    paper_id,
                    for_write=False,
                    migrate_legacy=True,
                )
                if os.path.lexists(raw_target_dir) and os.path.islink(raw_target_dir):
                    raise ValueError(f"Unsafe symlink target: {raw_target_dir}")
                target_dir = resolve_literature_paper_dir(
                    data_root,
                    pid,
                    paper_id,
                    for_write=False,
                    migrate_legacy=True,
                )
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                db.session.commit()
                return jsonify({"ok": True})
                
            elif action in ['pause', 'cancel']:
                paper.process_status = 'paused' if action == 'pause' else 'canceled'
                paper.process_log += f"\n[User] {action} command received."
                db.session.commit()
                return jsonify({"ok": True})
            
            return jsonify({"ok": False, "msg": "Invalid action"}), 400
            
        except BadRequest as e:
            db.session.rollback()
            return jsonify({"ok": False, "msg": e.description or "Bad request"}), 400
        except Exception as e:
            db.session.rollback()
            logger.error("[pipeline_control] %s", e, exc_info=True)
            return jsonify({"ok": False, "msg": "Internal server error"}), 500


