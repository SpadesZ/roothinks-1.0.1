# 檔案路徑: app/project_portfolio/project_service.py
# 產生時間: 2026-07-04 00:00 +08:00
# 版本: v1.8
# 模組定位:
#   專案管理服務層。負責 Project CRUD、目錄建立、成員 membership 寫入。
# 主要責任:
#   1. create_project — 建立 DB 記錄 + 目錄；若有 session user 自動加為 owner。
#   2. update_project — 更新欄位（需驗成員格式）。
#   3. get_projects — 回傳專案清單；AUTH_MODE=session 時僅回傳有 membership 的專案。
#   4. delete_project — 刪除 Project 及關聯，連帶清除 workspace_members 記錄。
# 呼叫來源:
#   app/project_portfolio/project_routes.py
#   app/core_pro/paq/paq_routes.py（promote 時複製 membership）
# 輸入輸出契約:
#   - create_project(data: dict) -> (Project | None, str)
#   - get_projects(status, user_id) -> list[dict]
#   - delete_project(pid) -> (bool, str)
# 安全邊界:
#   - 成員資料 members JSON 欄位僅允許 list of dict，PI 唯一性強制驗證。
#   - WorkspaceMember 的 role 只能是 'owner'|'editor'|'viewer'。
# 維護提醒:
#   - [Batch B] get_projects 新增 user_id 參數；AUTH_MODE=session 時過濾。
#   - promote 流程（paq_routes.py）需呼叫 copy_memberships(old_pid, new_pid)。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test -q
# ------------------------------------------------------------------------------
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
    def _is_academic_lead(member):
        """PI／Co-PI 是論文領導角色，系統權限最低必須能編輯全部章節。"""
        role = str((member or {}).get('role') or '').strip()
        return (
            role.startswith('主持人')
            or role.startswith('共同主持人')
            or 'co-pi' in role.casefold()
        )

    @staticmethod
    def _sync_members_access(pid, members):
        """
        [collab] 依人員組織的 email 同步系統權限（WorkspaceMember）。

        為什麼需要這個：人員組織（Table 2）與成員管理是兩套並存的介面，
        前者原本純粹是論文署名資料、完全不給權限，使用者很容易誤以為
        填了 email 就等於把人加進專案。現在讓 access_role 有欄位可填，
        並在此把它落實到真正的權限表。

        規則：
        - 一般成員 access_role 空白 → 僅列名，不動既有權限（也不移除）。
        - PI／Co-PI → 最低 editor（全章節讀寫）；既有 owner 不得被降級。
        - email 找不到已註冊帳號 → 略過並記錄，不中斷存檔
          （對方可能還沒註冊，之後可用「成員管理」邀請）。
        - 已存在的 membership 只更新角色，不重複建立。
        - **絕不在此移除 membership**：避免有人改了人員組織就無聲踢掉協作者；
          移除一律走「成員管理」明確操作。

        回傳 (授權筆數, 略過的 email 清單) 供呼叫端提示使用者。
        """
        from app.models import User, WorkspaceMember, ROLE_EDITOR, ROLE_ORDER

        granted, skipped = 0, []
        if not isinstance(members, list):
            return granted, skipped

        for m in members:
            if not isinstance(m, dict):
                continue
            access_role = str(m.get('access_role') or '').strip().lower()
            is_lead = ProjectService._is_academic_lead(m)
            if access_role and access_role not in ROLE_ORDER:
                logger.warning("[members-access] 未知角色 %s，略過", access_role)
                access_role = ''
            if is_lead and ROLE_ORDER.get(access_role, 0) < ROLE_ORDER[ROLE_EDITOR]:
                access_role = ROLE_EDITOR
            if not access_role:
                continue

            emails = m.get('emails') or []
            if isinstance(emails, str):
                emails = [emails]
            email = next((str(e).strip() for e in emails if str(e).strip()), '')
            if not email:
                continue

            user = User.find_by_email(email)
            if user is None:
                skipped.append(email)
                continue

            existing = WorkspaceMember.query.filter_by(user_id=user.id, pid=pid).first()
            if existing:
                desired_role = access_role
                if is_lead and ROLE_ORDER.get(existing.role or '', 0) > ROLE_ORDER[desired_role]:
                    desired_role = existing.role
                if existing.role != desired_role:
                    existing.role = desired_role
                    granted += 1
            else:
                db.session.add(
                    WorkspaceMember(user_id=user.id, pid=pid, role=access_role)
                )
                granted += 1

        if granted or skipped:
            db.session.commit()
        return granted, skipped

    @staticmethod
    def ensure_academic_lead_access():
        """補齊既有專案的 PI／Co-PI 全內容編輯權；可安全重複執行。"""
        granted = 0
        skipped = set()
        for project in Project.query.all():
            leads = [
                m for m in (project.members or [])
                if isinstance(m, dict) and ProjectService._is_academic_lead(m)
            ]
            if not leads:
                continue
            count, missing = ProjectService._sync_members_access(project.project_id, leads)
            granted += count
            skipped.update(missing)
        return granted, sorted(skipped)

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

            # [Batch B] 若有 session user，自動寫入 owner membership
            try:
                from flask_login import current_user
                from app.models import WorkspaceMember, ROLE_OWNER
                if current_user and current_user.is_authenticated:
                    membership = WorkspaceMember(
                        user_id=current_user.id,
                        pid=new_project.project_id,
                        role=ROLE_OWNER,
                    )
                    db.session.add(membership)
                    db.session.commit()
            except Exception:
                # 無 session context（如 CLI / dev 模式）靜默跳過，不中斷建立流程
                logger.debug("No session user for membership write; skipping.")

            # [collab] 建立時就把人員組織的 access_role 落實成權限。
            # 放在 owner membership 之後：建立者若同時列在人員組織中，
            # 其 access_role 會覆蓋自動給的 owner —— 這是刻意的，
            # 使用者明確填寫的值優先於系統預設。
            try:
                granted, skipped = ProjectService._sync_members_access(
                    new_project.project_id, members
                )
                if skipped:
                    logger.info(
                        "[members-access] 建立 %s：授權 %d 人，%d 個 Email 尚未註冊",
                        new_project.project_id, granted, len(skipped),
                    )
            except Exception:
                logger.warning(
                    "[members-access] 建立時同步權限失敗 pid=%s",
                    new_project.project_id, exc_info=True,
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
            if 'name' in data:
                project.name = data['name']
                # 專案名稱即研究題目，兩欄不得分岔。
                # research_title 才是 dashboard 正式研究卡片與所有 LLM task
                # （SWOT／cubegen／2A chat／手稿初始標題／文獻 context）實際讀的欄位，
                # 它們一律是 `research_title or name`。這裡不同步的話，轉正之後
                # 改題目就只改得到 name，畫面與 AI 模組會永遠停在轉正當下那一版。
                project.research_title = data['name']
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

            # [collab] 存檔後把人員組織的 access_role 落實成真正的權限。
            if 'members' in data:
                granted, skipped = ProjectService._sync_members_access(
                    project.project_id, data['members']
                )
                if skipped:
                    return True, ("Updated（已授權 %d 人；下列 Email 尚未註冊而略過：%s）"
                                  % (granted, ", ".join(skipped)))
            return True, "Updated"
        except Exception as e:
            db.session.rollback()
            # 若發生 OperationalError，代表 Schema 可能仍未更新 (雖然有 fix_db_schema)
            if "no such column" in str(e):
                return False, "Database Schema Error: Please restart the system to fix missing columns."
            return False, str(e)

    @staticmethod
    def get_projects(status=None, user_id=None):
        """
        取得專案列表。
        [Batch B] AUTH_MODE=session 時（user_id 有值）只回傳該 user 有 membership 的專案。
        dev 模式（user_id=None）照舊全回。
        """
        from flask import current_app as _app
        auth_mode = str(_app.config.get("AUTH_MODE", "none")).strip().lower()

        query = Project.query
        if status:
            query = query.filter_by(status=status)

        if auth_mode == "session" and user_id is not None:
            # join WorkspaceMember 過濾有 membership 的專案
            from app.models import WorkspaceMember
            query = query.join(
                WorkspaceMember,
                WorkspaceMember.pid == Project.project_id,
            ).filter(WorkspaceMember.user_id == user_id)

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

            # [Batch B] 連帶刪除 workspace_members 記錄
            try:
                from app.models import WorkspaceMember
                WorkspaceMember.query.filter_by(pid=project.project_id).delete()
            except Exception:
                logger.warning("Failed to delete workspace_members for pid=%s", project.project_id, exc_info=True)

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
