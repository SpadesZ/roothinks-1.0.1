#路徑(./app/project_portfolio/project_service.py) #版本 v1.7 #更版時間 20260312-1413
import os
import random
import string
import json
from flask import current_app
from app import db
from app.models import Project, MetadataIndex, PaqSurvey
from app.services.index_service import IndexService

import logging

logger = logging.getLogger("app.project_portfolio.project_service")

class ProjectService:
    """
    專案管理服務層 (Service Layer) v1.7
    負責處理專案的 CRUD、檔案系統操作與資料庫交易
    [v1.7 Update]
    1. 修復草案建立時過度展開生命週期資料夾的錯誤，確保僅建立 paq 及其子目錄。
    [v1.6 Update]
    1. 修復 data/<pid>/paq/paq/ 雙層目錄錯誤，精準於 data/<pid>/paq/ 下建立子目錄。
    [v1.4 Update] 
    1. 新增 _validate_members 私有方法，強制驗證成員資料格式與 PI 唯一性。
    2. 在 create_project 與 update_project 中整合驗證邏輯，防止髒數據寫入。
    """

    @staticmethod
    def generate_pid(length=6):
        """生成 6 碼隨機專案代號 (PID)"""
        chars = string.ascii_uppercase + string.digits
        while True:
            pid = ''.join(random.choices(chars, k=length))
            # 確保 PID 唯一
            if not Project.query.filter_by(project_id=pid).first():
                return pid

    @staticmethod
    def create_project_structure(pid):
        """建立專案實體目錄結構"""
        # 取得資料庫 URI 的相對路徑並轉為絕對路徑，以定位 data 目錄
        # 注意: 這裡假設 db path 類似 sqlite:///.../data/roothinks.db
        base_data_path = os.path.dirname(current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''))
        
        # 若路徑不是絕對路徑，則嘗試基於 root_path 修正
        if not os.path.isabs(base_data_path):
             base_data_path = os.path.join(current_app.root_path, '../data')
        
        # 定義專案根目錄 data/<pid>
        project_path = os.path.join(base_data_path, pid)
        
        # [v1.7 Fix] 草案階段僅需 PAQ 相關目錄。其餘模組目錄留待專案轉正時由 paq_routes 生成。
        # 原邏輯: subfolders = ['paq', 'literature', 'study', 'manuscript', 'submit']
        subfolders = ['paq']
        
        # [v1.6 Fix] 預先定義 PAQ 專屬子目錄，避免後續模組因路徑不存在而引發雙層目錄重疊
        paq_subfolders = ['paq_taxonomy', 'paq_matrix', 'paq_chat']
        paq_path = os.path.join(project_path, 'paq')
        
        try:
            os.makedirs(project_path, exist_ok=True)
            for folder in subfolders:
                os.makedirs(os.path.join(project_path, folder), exist_ok=True)
            
            # [v1.6 Fix] 精確寫入 data/<pid>/paq/ 目錄下
            for sub in paq_subfolders:
                os.makedirs(os.path.join(paq_path, sub), exist_ok=True)
                
            return True, project_path
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _validate_members(members):
        """
        [v1.4 New] 驗證成員列表格式
        :param members: list of dict
        :return: (bool, error_message)
        """
        if not isinstance(members, list):
            return False, "Members must be a list."
        
        pi_count = 0
        for m in members:
            if not isinstance(m, dict):
                return False, "Member item must be a dictionary."
            if not m.get('name'):
                return False, "Member name is required."
            if m.get('role', '').startswith('主持人'):
                pi_count += 1
        
        if pi_count != 1:
            return False, f"Exactly one Principal Investigator (PI) is required. Found {pi_count}."
            
        return True, ""

    @staticmethod
    def create_project(data):
        """
        建立新專案
        Flow: Validate -> Generate PID -> DB Insert -> Create Folder -> Index Context
        """
        # 1. 驗證必要欄位
        if not data.get('name'):
            return None, "Project Name is required"

        # [v1.4] 驗證成員資料
        members = data.get('members', [])
        is_valid, err_msg = ProjectService._validate_members(members)
        if not is_valid:
            return None, f"Member Validation Error: {err_msg}"

        pid = ProjectService.generate_pid()

        try:
            # 2. 建立資料庫紀錄
            new_project = Project(
                project_id=pid,
                name=data['name'],
                abbreviation=data.get('abbreviation'),
                classification=data.get('classification'),
                keywords=data.get('keywords'),
                context_background=data.get('context'), # Mapping 'context' to 'context_background'
                members=members
            )
            
            db.session.add(new_project)
            db.session.flush()

            # 3. 初始化 PAQ Survey Table（與 Project 同一 transaction）
            paq_survey = PaqSurvey(project_ref_id=new_project.id)
            db.session.add(paq_survey)
            db.session.commit()

            # 4. 建立實體目錄
            ok, path_msg = ProjectService.create_project_structure(pid)
            if not ok:
                logger.error(f"Warning: Failed to create folder for {pid}: {path_msg}")

            # 5. 註冊 Context 到 CoC 索引 (Background -> Index)
            bg_text = data.get('context', '')
            if bg_text:
                # 截取前 50 字作為摘要
                summary = bg_text[:50].replace('\n', ' ') + "..."
                IndexService.register_index(
                    pid, 'project', 'init', 'project_info.json', 
                    f"專案初始化背景: {summary}"
                )

            return new_project, "Created"

        except Exception as e:
            db.session.rollback()
            return None, str(e)

    @staticmethod
    def update_project(pid, data):
        """更新專案資訊"""
        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return False, "Project not found"
        
        try:
            if 'name' in data: project.name = data['name']
            if 'abbreviation' in data: project.abbreviation = data['abbreviation']
            if 'classification' in data: project.classification = data['classification']
            if 'keywords' in data: project.keywords = data['keywords']
            if 'context' in data: project.context_background = data['context']
            
            # [v1.4] 更新成員並驗證
            if 'members' in data:
                is_valid, err_msg = ProjectService._validate_members(data['members'])
                if not is_valid:
                    return False, err_msg
                project.members = data['members']

            db.session.commit()
            return True, "Updated"
        except Exception as e:
            db.session.rollback()
            # 若發生 OperationalError，代表 Schema 可能仍未更新 (雖然有 fix_db_schema)
            if "no such column" in str(e):
                return False, "Database Schema Error: Please restart the system to fix missing columns."
            return False, str(e)

    @staticmethod
    def get_projects(status=None):
        """取得專案列表"""
        query = Project.query
        if status:
            query = query.filter_by(status=status)
        
        # 依建立時間倒序排列
        projects = query.order_by(Project.created_at.desc()).all()
        return [p.to_dict() for p in projects]

    @staticmethod
    def delete_project(pid):
        """
        刪除專案 (Cascade Delete Logic)
        """
        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return False, "Not Found"
            
        try:
            # 刪除關聯資料 (手動 Cascade 以防 DB 層級未設定)
            MetadataIndex.query.filter_by(project_ref_id=project.id).delete()
            PaqSurvey.query.filter_by(project_ref_id=project.id).delete()
            
            # 刪除實體檔案 (Optional: 視需求決定是否保留檔案)
            # import shutil
            # shutil.rmtree(...) 
            
            # 刪除專案本體
            db.session.delete(project)
            db.session.commit()
            
            return True, "Deleted"
        except Exception as e:
            db.session.rollback()
            return False, str(e)
