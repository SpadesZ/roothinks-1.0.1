# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/literature_batch_routes.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 註冊 Literature 批次處理與狀態 API，把耗時工作交給 runner 而非阻塞 route。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/core_pro/literature/literature_batch_routes.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. 註冊 upload/run_batch/run_translation 路由。
#2. 處理檔案上傳與 Flow A 批次背景任務排程。
#3. 組裝 Flow B 依賴並委派 run_translation_impl 執行。

import hashlib
import os
import re
import threading
import time
import tempfile

from flask import current_app, jsonify, request
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename
from filelock import FileLock
from app.security import build_lock_path
from werkzeug.exceptions import Conflict


def register_batch_routes(literature_bp, deps):
    def register_original(pid, paper_id, entry_id='', base_rev=None):
        """原件不可變；恢復只補登記。完成後重試不得重設作者已選的新 PDF。"""
        save_dir = deps.safe_join_under(deps._get_literature_paper_dir(pid, paper_id, for_write=False), '00_origins')
        source_path = deps.safe_join_under(save_dir,'source.pdf')
        metadata_path = deps.safe_join_under(save_dir,'metadata.json')
        meta = deps.load_json_locked(metadata_path,{},strict=True)
        if not meta.get('sha256') or not os.path.isfile(source_path):
            raise Conflict('缺少完整原件或來源指紋，無法自動恢復。')
        with open(source_path,'rb') as source:
            digest = hashlib.file_digest(source,'sha256').hexdigest()
        if digest != meta['sha256']:
            raise Conflict('原件指紋已變更；停止登記並保留現況。')
        library = deps.get_literature_library()
        if meta.get('registration') == 'complete':
            if entry_id and entry_id != meta.get('linked_entry_id'):
                raise Conflict('此次登記已完成；變更文獻連結請使用文獻清單。')
            return meta['linked_entry_id']
        if not deps.Paper.query.filter_by(paper_id=paper_id,pid=pid).first():
            deps.db.session.add(deps.Paper(paper_id=paper_id,pid=pid,title=meta['original_filename'][:500],
                has_source=True,interpretation_status=deps.Paper.STATUS_PENDING))
            deps.db.session.commit()
        entry = library.register_upload(pid,paper_id,meta['original_filename'],source_sha256=digest,
                                        actor=meta.get('uploaded_by',''))
        if entry_id:
            if base_rev is None and current_app.config.get('AUTH_MODE') == 'session':
                raise ValueError('連結文獻需要載入的版號，原件已保留。')
            entry = library.update_entry(pid,entry_id,{'paper_id':paper_id},actor=deps.current_screening_actor(),base_rev=base_rev)
        meta.update(registration='complete',linked_entry_id=entry['entry_id'])
        deps.write_json_locked(metadata_path,meta)
        return entry['entry_id']

    @literature_bp.route('/api/literature/recover_upload', methods=['POST'])
    def recover_upload():
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        if not pid:
            return jsonify(status='error',message='Invalid pid'),404
        paper_id = deps._safe_paper_id(data.get('paper_id'))
        lock_target = deps.safe_join_under(deps.DATA_ROOT,pid,'literature','upload')
        try:
            with FileLock(build_lock_path(lock_target),timeout=10):
                entry_id = register_original(pid,paper_id,str(data.get('entry_id') or ''),data.get('base_rev'))
            return jsonify(status='success',paper_id=paper_id,linked_entry_id=entry_id)
        except Conflict as exc:
            deps.db.session.rollback()
            return jsonify(status='error',paper_id=paper_id,message=exc.description),409
        except (ValueError,TypeError) as exc:
            deps.db.session.rollback()
            return jsonify(status='error',paper_id=paper_id,message=str(exc)),400
        except Exception:
            deps.db.session.rollback()
            deps.logger.exception('Upload recovery failed')
            return jsonify(status='error',paper_id=paper_id,message='補登記未完成；原件維持原狀。'),503

    @literature_bp.route('/api/literature/upload', methods=['POST'])
    def upload_file():
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file part"}), 400
        file = request.files['file']
        pid = deps._normalize_literature_pid(request.form.get('pid'))
        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404

        if file.filename == '':
            return jsonify({"status": "error", "message": "No selected file"}), 400

        if file:
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            mimetype = (file.mimetype or '').lower()

            is_pdf = ext == '.pdf' or mimetype == 'application/pdf'
            is_image = mimetype.startswith('image/') or ext in {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}

            if not (is_pdf or is_image):
                return jsonify({"status": "error", "message": "Unsupported file type. Please upload PDF or image."}), 400

            raw_paper_id = os.path.splitext(filename)[0].replace(" ", "_")
            raw_paper_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_paper_id)[:110]
            paper_id = deps._safe_paper_id(raw_paper_id)

            # 單專案鎖包住 ID 分配及原件寫入，避免同名併發 TOCTOU。
            lock_target = deps.safe_join_under(deps.DATA_ROOT, pid, 'literature', 'upload')
            with FileLock(build_lock_path(lock_target), timeout=10):
                # 同名檔案處理：既有 paper 存在時預設配發新 id（_2, _3...），
                # overwrite 不能破壞既有原件；不同版本分配不同 paper_id。
                overwrite = str(request.form.get('overwrite') or '').strip().lower() in {'1', 'true', 'yes'}
                if not overwrite:
                    base_id = paper_id
                    suffix = 2
                    while True:
                        existing_row = deps.Paper.query.filter_by(paper_id=paper_id, pid=pid).first()
                        existing_pdf = os.path.exists(
                            deps.safe_join_under(
                                deps._get_literature_paper_dir(pid, paper_id, for_write=False),
                                "00_origins",
                                "source.pdf",
                            )
                        )
                        if not existing_row and not existing_pdf:
                            break
                        if suffix > 50:
                            return jsonify({"status": "error", "message": "Too many duplicate filenames"}), 409
                        paper_id = deps._safe_paper_id(f"{base_id}_{suffix}")
                        suffix += 1

                paper_dir = deps._get_literature_paper_dir(pid, paper_id, for_write=True)
                save_dir = deps.safe_join_under(paper_dir, "00_origins")
                os.makedirs(save_dir, exist_ok=True)

                save_path = deps.safe_join_under(save_dir, "source.pdf")
                if os.path.exists(save_path):
                    return jsonify({"status": "error", "message": "原件已存在，請以新版本上傳。", "paper_id": paper_id}), 409
                source_type = 'pdf'

                try:
                    # 同檔案系統先完成、fsync，再用 exclusive hard-link 發布，永不覆寫原件。
                    with tempfile.TemporaryDirectory(prefix='upload-',dir=save_dir) as staging:
                        staged = os.path.join(staging,'source.pdf')
                        incoming = staged if is_pdf else os.path.join(staging,'original')
                        with open(incoming,'xb') as target:
                            file.save(target)
                            target.flush()
                            os.fsync(target.fileno())
                        if not is_pdf:
                            original_path = deps.safe_join_under(save_dir,'uploaded_original'+(ext or '.image'))
                            os.link(incoming,original_path)
                            with Image.open(incoming) as raw:
                                img = ImageOps.exif_transpose(raw).convert('RGB')
                                with open(staged,'xb') as target:
                                    img.save(target,format='PDF',resolution=200.0)
                                    target.flush()
                                    os.fsync(target.fileno())
                            source_type = 'image_to_pdf'
                        with open(staged,'rb') as source:
                            digest = hashlib.file_digest(source,'sha256').hexdigest()
                        deps.write_json_locked(deps.safe_join_under(save_dir,'metadata.json'),{
                            'original_filename':filename,'upload_time':time.time(),'source_type':source_type,
                            'sha256':digest,'size_bytes':os.path.getsize(staged),
                            'uploaded_by':deps.current_screening_actor(),'registration':'pending',
                            'requested_entry_id':str(request.form.get('entry_id') or ''),
                        })
                        os.link(staged,save_path)
                except Exception:
                    deps.logger.exception('Original publication failed')
                    return jsonify(status='error',original_saved=os.path.isfile(save_path),paper_id=paper_id,
                                   message='原件寫入未完成；未登記為可處理 PDF。'),503

                entry_id = str(request.form.get('entry_id') or '').strip()
                try:
                    raw_rev = request.form.get('base_rev')
                    entry_id = register_original(pid,paper_id,entry_id,int(raw_rev) if raw_rev is not None else None)
                except Exception:
                    deps.logger.exception("Upload library registration/link failed")
                    return jsonify({"status": "error", "original_saved": True, "paper_id": paper_id,
                                    "message": "原件已保存；文獻登記或連結未完成，請保留此 ID 並核對文獻清單。"}), 503
                deps.logger.info(f"File uploaded: {save_path}")
                return jsonify({"status": "success", "paper_id": paper_id, "linked_entry_id": entry_id})

    @literature_bp.route('/api/literature/run_batch', methods=['POST'])
    def run_batch():
        """
        [Updated v3.3] 整合 Gold Data Bridge 的批次處理
        流程: CV Pipeline -> [Auto] Task 4 Fix -> [Auto] Task 5 Interpret
        """
        data = request.get_json(silent=True) or {}
        pid = deps._normalize_literature_pid(data.get('pid'))
        paper_ids = data.get('paper_ids', [])

        if not pid:
            return jsonify({"status": "error", "message": "Invalid pid"}), 404
        if not paper_ids:
            return jsonify({"status": "error", "message": "No papers selected"}), 400
        try:
            paper_ids = [deps._safe_paper_id(p) for p in paper_ids]
        except deps.BadRequest:
            return jsonify({"status": "error", "message": "Invalid paper_id in request"}), 400

        lock_root = os.environ.get("LOCK_ROOT", "/tmp/roothinks-locks")
        os.makedirs(lock_root, exist_ok=True)
        active_pid_lock = os.path.join(lock_root, f"literature_active_{pid}.lock")
        lock_timeout = float(os.environ.get("LITERATURE_LOCK_TIMEOUT_SEC", "3600"))
        lock_acquired = False

        try:
            os.mkdir(active_pid_lock)
            lock_acquired = True
        except FileExistsError:
            try:
                mtime = os.path.getmtime(active_pid_lock)
                if time.time() - mtime > lock_timeout:
                    deps.logger.warning(f"Found stale lock for {pid}, removing it.")
                    os.rmdir(active_pid_lock)
                    os.mkdir(active_pid_lock)
                    lock_acquired = True
            except Exception as e:
                deps.logger.warning(f"Error checking stale lock for {pid}: {e}")
                pass

        if not lock_acquired:
            return jsonify({"status": "error", "message": "Batch already running for this pid"}), 429

        for paper_id in paper_ids:
            deps._update_paper_status(pid, paper_id, deps.Paper.STATUS_T5_QUEUED, "Task 5 queued")

        app = current_app._get_current_object()

        def _pipeline_worker(target_pid, target_ids, app_obj):
            """薄殼：保證離開時清除 LLM 用量歸屬（見 _pipeline_worker_body）。"""
            try:
                return _pipeline_worker_body(target_pid, target_ids, app_obj)
            finally:
                try:
                    from app.llm_service.llm_usage import clear_context
                    clear_context()
                except Exception:
                    pass

        def _pipeline_worker_body(target_pid, target_ids, app_obj):
            """
            背景工作執行緒：依序執行 CV 與 Bridge
            [Updated] 智能重試機制：只執行未完成或失敗的步驟
            """
            try:
                with app_obj.app_context():
                    for pid_paper in target_ids:
                        # [usage] 標記接下來的 LLM 呼叫屬於哪一篇，dispatcher
                        # 記錄用量時才掛得上 pid/paper_id。逐篇覆寫即可——
                        # 這條 worker 是循序處理，且執行緒結束後 context 自動消失。
                        try:
                            from app.llm_service.llm_usage import set_context as _usage_ctx
                            _usage_ctx(target_pid, pid_paper)
                        except Exception:
                            deps.logger.warning("[usage] 設定 context 失敗（不影響流程）", exc_info=True)
                        try:
                            deps.logger.info(f"--- Starting Pipeline for {pid_paper} ---")

                            deps._update_paper_status(
                                target_pid,
                                pid_paper,
                                deps.Paper.STATUS_ANALYZING,
                                "Starting CV Pipeline...",
                            )

                            paper_dir = deps._get_literature_paper_dir(target_pid, pid_paper, for_write=True)
                            pdf_path = deps.safe_join_under(paper_dir, "00_origins", "source.pdf")

                            if not os.path.exists(pdf_path):
                                deps.logger.warning(f"[{pid_paper}] Source PDF not found: {pdf_path}")
                                deps._update_paper_status(target_pid, pid_paper, deps.Paper.STATUS_FAILED, "Source PDF not found")
                                continue

                            recognize_dir = os.path.join(paper_dir, "03_recognizes")
                            origin_pages = deps._collect_origin_pages(paper_dir)
                            try:
                                ocr_ready_ratio = float(os.environ.get("LITERATURE_OCR_READY_RATIO", "1.0"))
                            except Exception:
                                ocr_ready_ratio = 1.0
                            ocr_ready_ratio = min(1.0, max(0.1, ocr_ready_ratio))

                            has_raw_ocr = deps._has_valid_stage_files(
                                recognize_dir,
                                "_raw.json",
                                min_size=100,
                                min_ratio=ocr_ready_ratio,
                                expected_pages=origin_pages if origin_pages else None,
                            )

                            if not has_raw_ocr:
                                deps.logger.info(f"[{pid_paper}] Step 1: CV Pipeline Running...")
                                pipeline = deps.get_cv_pipeline()
                                if pipeline:
                                    pipeline.run_pipeline(target_pid, pid_paper, pdf_path)
                                else:
                                    deps.logger.error(f"[{pid_paper}] CV Pipeline not available")
                                    continue
                            else:
                                deps.logger.info(f"[{pid_paper}] Step 1: Skipped (OCR already done)")

                            deps._update_paper_status(target_pid, pid_paper, deps.Paper.STATUS_T5_RUNNING, "Task 5 running")
                            deps.logger.info(f"[{pid_paper}] Step 2: Triggering Gold Data Bridge...")
                            deps._trigger_gold_bridge(target_pid, pid_paper)

                            deps.logger.info(f"[{pid_paper}] All Processing Done.")

                        except Exception as e:
                            deps.logger.error(f"Pipeline error for {pid_paper}: {e}")
                            deps._update_paper_status(target_pid, pid_paper, deps.Paper.STATUS_FAILED, f"Pipeline error: {str(e)}")
                    try:
                        deps.db.session.remove()
                    except Exception:
                        deps.logger.warning("Failed to remove DB session in pipeline worker", exc_info=True)
            finally:
                try:
                    os.rmdir(
                        os.path.join(os.environ.get("LOCK_ROOT", "/tmp/roothinks-locks"), f"literature_active_{target_pid}.lock")
                    )
                except Exception:
                    pass

        use_direct_thread = deps._to_bool(
            os.environ.get("LITERATURE_USE_DIRECT_THREAD"),
            deps._to_bool(os.environ.get("FLASK_DEBUG"), True),
        )
        if use_direct_thread:
            t = threading.Thread(
                target=_pipeline_worker,
                args=(pid, paper_ids, app),
                daemon=True,
                name=f"literature-batch-{pid}-{int(time.time())}",
            )
            t.start()
        else:
            deps._get_batch_executor().submit(_pipeline_worker, pid, paper_ids, app)

        return jsonify(
            {
                "status": "success",
                "message": "Batch processing initiated (CV -> Fix -> Summary).",
                "details": [f"{p}: Queued" for p in paper_ids],
            }
        )

    @literature_bp.route('/api/literature/run_translation', methods=['POST'])
    def run_translation():
        flowb_deps = deps.FlowBRouteDeps(
            request=request,
            jsonify=jsonify,
            current_app=current_app,
            logger=deps.logger,
            db=deps.db,
            Paper=deps.Paper,
            BadRequest=deps.BadRequest,
            DATA_ROOT=deps.DATA_ROOT,
            FLOWB_SUBPROCESS_ENABLED=deps.FLOWB_SUBPROCESS_ENABLED,
            FLOWB_TIMEOUT_SEC=deps.FLOWB_TIMEOUT_SEC,
            cap_worker_count=deps.cap_worker_count,
            safe_join_under=deps.safe_join_under,
            load_json_locked=deps.load_json_locked,
            _normalize_literature_pid=deps._normalize_literature_pid,
            _safe_paper_id=deps._safe_paper_id,
            _dedupe_keep_order=deps._dedupe_keep_order,
            _new_run_id=deps._new_run_id,
            _write_job_config=deps._write_job_config,
            _append_flow_event=deps._append_flow_event,
            _update_paper_status=deps._update_paper_status,
            _flowb_read_bool_env=deps._flowb_read_bool_env,
            _flowb_compute_fusion_quality_metrics=deps._flowb_compute_fusion_quality_metrics,
            _generate_flowb_reflow_artifact=deps._generate_flowb_reflow_artifact,
            _flowb_is_ready_generation_mode=deps._flowb_is_ready_generation_mode,
            _flowb_read_ready_generation_modes=deps._flowb_read_ready_generation_modes,
            _run_flowb_subprocess=deps._run_flowb_subprocess,
            _to_bool=deps._to_bool,
            _get_flowb_executor=deps._get_flowb_executor,
            get_nllb_translator=deps.get_nllb_translator,
        )
        return deps.run_translation_impl(flowb_deps)
