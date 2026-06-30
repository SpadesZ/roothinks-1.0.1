#路徑(./app/services/index_service.py) #版本 v0.1 #更版時間 20260131-0245
from app import db
from app.models import Project, MetadataIndex
from sqlalchemy import desc

import logging

logger = logging.getLogger("app.services.index_service")

class IndexService:
    """
    CoC 索引服務 (Chain-of-Context Indexer)
    負責維護 metadata_index 表，提供寫入與檢索介面
    """

    @staticmethod
    def register_index(pid, module, folder, path, content):
        """
        註冊新的上下文索引
        對應 Source 251: register_index(pid, module, folder, path, content)
        """
        try:
            # 1. 查找專案 (需取得 PK ID)
            project = Project.query.filter_by(project_id=pid).first()
            if not project:
                return False, f"Project {pid} not found"

            # 2. 建立索引物件
            new_index = MetadataIndex(
                project_ref_id=project.id,
                module_name=module,
                folder_name=folder,
                file_path=path,
                index_content=content
            )

            # 3. 寫入資料庫
            db.session.add(new_index)
            db.session.commit() # Source 254: Commit Transaction
            
            return True, "Index registered"

        except Exception as e:
            db.session.rollback()
            return False, f"Index Error: {str(e)}"

    @staticmethod
    def get_latest_context(pid, limit=5):
        """
        取得最新的上下文摘要 (Chain-of-Context)
        對應 Source 255-256: 依 created_at 倒序回傳最近的 5 筆摘要
        供下一個 Task 的 Prompt 使用
        """
        try:
            # 1. 查找專案
            project = Project.query.filter_by(project_id=pid).first()
            if not project:
                return []

            # 2. 查詢索引表
            indices = MetadataIndex.query.filter_by(project_ref_id=project.id)\
                .order_by(desc(MetadataIndex.created_at))\
                .limit(limit)\
                .all()

            # 3. 格式化回傳 (供 Prompt 組合使用)
            # 例如: "[paq_taxonomy] 基於深度學習..."
            context_list = []
            for idx in indices:
                context_list.append(f"[{idx.folder_name}] {idx.index_content}")
            
            return context_list

        except Exception as e:
            # Log error strictly
            logger.error(f"[IndexService] Error fetching context: {e}")
            return []

    @staticmethod
    def search_indices_by_module(pid, module_name):
        """
        依模組篩選索引 (Source 44, 276)
        用於特定模組回顧 (如 Literature 模組查詢 PAQ 結論)
        """
        try:
            project = Project.query.filter_by(project_id=pid).first()
            if not project:
                return []

            indices = MetadataIndex.query.filter_by(
                project_ref_id=project.id,
                module_name=module_name
            ).order_by(desc(MetadataIndex.created_at)).all()

            return [idx.to_dict() for idx in indices]

        except Exception as e:
            logger.error(f"[IndexService] Search Error: {e}")
            return []