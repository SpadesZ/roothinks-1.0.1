#路徑(./app/core_pro/paq/paq_core.py) #版本 v0.6 #更版時間 20260223-1530
import os
import json
import time
import glob
from datetime import datetime, timedelta
from flask import current_app
from app import db
from app.models import Project, PaqSurvey
from app.services.index_service import IndexService
from app.security import safe_join_under, validate_id

import logging

logger = logging.getLogger("app.core_pro.paq.paq_core")

# 引入 LAVA Task 模組
from app.llm_service.matching_tasks import task_1paqswot
from app.llm_service.matching_tasks import task_2cubegen
from app.llm_service.matching_tasks import task2A_paqchat 

class PaqCore:
    """
    PAQ 模組核心調度器 (Orchestrator)
    負責 LAVA 任務調度、檔案 IO 與 CoC 索引註冊
    [v0.6 Update]:
    1. 在 task_2a_chat 中，自動查詢 PaqSurvey 資料表，提取最新 Taxonomy 與 Cube 資料傳入 LLM。
    [v0.5 Update]:
    1. 新增 _save_chat_record 私有方法，支援 Chat 對話實體存檔。
    2. 實作 UTC+8 台灣時間換算 (yymmdd-hhmmss)。
    3. 實作 10 分鐘 (600秒) 斷點續存邏輯 (Append or Create new)。
    """

    @staticmethod
    def run_paq_task(pid, task_id, input_data):
        """
        執行 PAQ 任務主流程
        Flow: Call LLM Task -> Get Result -> Save to DB/File -> Register Index
        """
        try:
            # 1. 取得專案 Context
            project = Project.query.filter_by(project_id=pid).first()
            if not project:
                return False, "Project not found"
            
            project_context = project.to_dict()
            result_data = {}
            success = False
            msg = ""
            target_folder = "paq_misc"
            
            # 2. 根據 Task ID 分派任務
            # ==========================================
            # Task 1: Taxonomy
            # ==========================================
            if task_id == 'task_1paqswot':
                # [v0.4] 支援 fresh/refine 雙模式
                mode = input_data.get('mode', 'fresh')
                user_edits = input_data.get('user_edits', {})
                success, result_data = task_1paqswot.execute_paq_taxonomy(
                    project_context, mode=mode, user_edits=user_edits
                )
                target_folder = "paq_taxonomy"
                if success:
                    PaqCore._update_survey_data(project.id, taxonomy=result_data)

            # ==========================================
            # Task 2: Cube Generation
            # ==========================================
            elif task_id == 'task_2cubegen':
                # 需先讀取現有的 Taxonomy
                survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
                if not survey or not survey.axis_labels:
                    return False, "Missing Taxonomy Data. Please run Task 1 first."
                
                taxonomy_data = {
                    'axis_labels': survey.axis_labels,
                    'axis_tags': survey.axis_tags
                }
                
                success, result_data = task_2cubegen.execute_cube_gen(project_context, taxonomy_data)
                target_folder = "paq_matrix"
                if success:
                    PaqCore._update_survey_data(project.id, cube=result_data)

            # ==========================================
            # Task 2A: PAQ Chat (Fix for Chat Linkage)
            # ==========================================
            elif task_id == 'task_2a_chat':
                user_input = input_data.get('user_input', '') or input_data.get('message', '')
                chat_history = input_data.get('chat_history', []) or input_data.get('history', [])
                
                # [v0.6 New] 提取最新 Taxonomy 與 Cube 資料作為 Chat Prompt 參數
                survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
                taxonomy_data = {}
                cube_data = []
                if survey:
                    taxonomy_data = {
                        'axis_labels': survey.axis_labels or {},
                        'axis_tags': survey.axis_tags or {}
                    }
                    cube_data = survey.cube_data or []
                
                # 執行 Chat 邏輯
                success, response_text = task2A_paqchat.execute_paq_chat(
                    project_context, 
                    chat_history, 
                    user_input,
                    taxonomy_data, # [v0.6 傳遞最新 Taxonomy]
                    cube_data      # [v0.6 傳遞最新 Cube]
                )
                
                target_folder = "paq_chat"
                if success:
                    result_data = {'response': response_text}
                    # Chat 不一定要存入 PaqSurvey，但需建立索引
                    # [v0.5 New] 寫入實體 Chat 紀錄 (10分鐘間隔邏輯)
                    PaqCore._save_chat_record(pid, user_input, response_text)
                else:
                    return False, response_text

            else:
                return False, f"Unknown Task ID: {task_id}"

            if not success:
                return False, f"Task Execution Failed: {result_data}"

            # 3. 處理 Chat 特殊回傳結構 (因為 Chat 不存實體檔案)
            if task_id == 'task_2a_chat':
                # 註冊對話摘要索引
                IndexService.register_index(
                    pid=pid,
                    module='paq',
                    folder='paq_chat',
                    path='chat_session', # 虛擬路徑
                    content=f"Chat: {str(result_data)[:20]}..."
                )
                return True, result_data

            # 4. 標準任務存檔與索引 (Task 1 & 2)
            summary = result_data.get('summary', 'Generated by Roothinks')
            filename = f"{task_id}_v{datetime.now().strftime('%Y%m%d')}.json"
            
            # 儲存 JSON 檔案
            file_path = PaqCore._save_json(pid, target_folder, filename, result_data)
            
            # 註冊 CoC 索引
            rel_path = f"paq/{target_folder}/{filename}"
            IndexService.register_index(
                pid=pid,
                module='paq',
                folder=target_folder,
                path=rel_path,
                content=summary
            )
            
            return True, {
                "file": rel_path,
                "summary": summary,
                "data": result_data
            }

        except Exception as e:
            return False, str(e)

    @staticmethod
    def _update_survey_data(proj_db_id, taxonomy=None, cube=None):
        """更新 PaqSurvey 資料表"""
        survey = PaqSurvey.query.filter_by(project_ref_id=proj_db_id).first()
        if not survey:
            survey = PaqSurvey(project_ref_id=proj_db_id)
            db.session.add(survey)
        
        if taxonomy:
            survey.axis_labels = taxonomy.get('axis_labels')
            survey.axis_tags = taxonomy.get('axis_tags')
        
        if cube:
            # [v0.4] 防呆: cube 可能是 dict {'voxels': [...]} 或直接是 list
            if isinstance(cube, dict):
                survey.cube_data = cube.get('voxels', [])
            elif isinstance(cube, list):
                survey.cube_data = cube
            else:
                survey.cube_data = []
            
        db.session.commit()

    @staticmethod
    def _save_json(pid, folder, filename, data):
        """輔助: 將 JSON 寫入檔案系統"""
        safe_pid = validate_id(pid, "project_id")
        # 取得專案 data 根目錄
        base_data_path = os.path.dirname(current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''))
        if not os.path.isabs(base_data_path):
             base_data_path = os.path.join(current_app.root_path, '../data')
        
        dir_path = safe_join_under(base_data_path, safe_pid, 'paq', folder)
        os.makedirs(dir_path, exist_ok=True)
        
        full_path = safe_join_under(dir_path, filename)
        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        return full_path

    @staticmethod
    def _save_chat_record(pid, user_msg, ai_reply):
        """
        [v0.5 New] 輔助: 將 Chat 對話紀錄實體寫入檔案系統
        邏輯：若距離最新一份 chat 檔案修改時間小於 10 分鐘，則 Append 該檔；
              否則以當前時間 (UTC+8) 建立新檔 (paq-chat_yymmdd-hhmmss.json)。
        """
        safe_pid = validate_id(pid, "project_id")
        base_data_path = os.path.dirname(current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''))
        if not os.path.isabs(base_data_path):
             base_data_path = os.path.join(current_app.root_path, '../data')
             
        chat_dir = safe_join_under(base_data_path, safe_pid, 'paq', 'chat')
        os.makedirs(chat_dir, exist_ok=True)
        
        # 換算台灣當地時間 (UTC+8) 作為紀錄與檔名依據
        tw_time = datetime.utcnow() + timedelta(hours=8)
        time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')
        
        # 尋找目錄下最新的對話檔案
        list_of_files = glob.glob(os.path.join(chat_dir, 'paq-chat_*.json'))
        latest_file = None
        if list_of_files:
            latest_file = max(list_of_files, key=os.path.getmtime)
            
        current_epoch = time.time()
        
        # 準備要寫入的單次對話紀錄結構
        record = {
            "time": time_str,
            "user": user_msg,
            "ai": ai_reply
        }
        
        # 判斷是否在 10 分鐘 (600 秒) 的對話 Session 內
        if latest_file and (current_epoch - os.path.getmtime(latest_file) <= 600):
            try:
                with open(latest_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data.append(record)
                with open(latest_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return latest_file
            except Exception as e:
                logger.error(f"[Chat Save Error] Failed to append, creating new. Error: {e}")
                # 發生例外時，自動落入建立新檔邏輯
        
        # 超過 10 分鐘或檔案損毀/不存在，建立全新 Session 檔案
        new_filename = f"paq-chat_{tw_time.strftime('%y%m%d-%H%M%S')}.json"
        new_filepath = os.path.join(chat_dir, new_filename)
        with open(new_filepath, 'w', encoding='utf-8') as f:
            json.dump([record], f, ensure_ascii=False, indent=2)
            
        return new_filepath
