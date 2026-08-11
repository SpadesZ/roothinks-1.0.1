# Roothinks source maintenance contract
# 檔案路徑: app/services/index_service.py
# 子系統定位:
#   service 層。維護 `metadata_index` 資料表：登記「某專案的某模組產出了哪個檔案」
#   以及一段 255 字以內的摘要。
#   **這不是 Evidence Index。** Evidence Index 是 app/services/evidence_index_service.py
#   （`EvidenceSegment`，供檢索與 Drafter 取用）。本檔舊 header 宣稱自己負責
#   Evidence Index，與實作完全不符，已於 2026-08-10 依實際資料流改寫。
# 主要責任:
#   1. register_index()：寫入一筆 MetadataIndex（module_name / folder_name /
#      file_path / index_content），自行 commit。
#   2. get_latest_context()：依 created_at 倒序取最近 N 筆，格式化成
#      "[folder_name] index_content" 字串清單。
#   3. search_indices_by_module()：依模組名稱查詢。
# 明確不負責:
#   - 不做全文檢索、不做評分、不做 token 預算 —— 那是 evidence_index_service。
#   - 不讀寫實際檔案內容，只登記路徑與摘要。
#   - 不做權限判斷：呼叫端必須自行確認呼叫者可以看這個 pid。
# 上游呼叫者（production，2026-08-10 實查）:
#   app/core_pro/paq/paq_core.py:138、:156 與
#   app/project_portfolio/project_service.py:268 —— 三處都只呼叫 register_index()。
# 下游服務:
#   app.models.MetadataIndex / Project（SQLAlchemy）。
# 讀寫或持久化位置:
#   主資料庫的 `metadata_index` 表；register_index 內部 commit，不參與呼叫端交易。
# 不變量與已知限制（都是實查，不是推測）:
#   - **目前是 write-only**：全 app 沒有任何 get_latest_context() 或
#     search_indices_by_module() 的呼叫端。資料登記進去就沒有下一環讀取，
#     這正是 COC 斷鏈之一（見 docs/HANDOFF.md §3.9 第 13 條）。
#     要拿它接 COC 之前，先確認讀取端真的存在，不要假設「有寫就有人讀」。
#   - **search_indices_by_module() 目前必定回空清單**：它呼叫 `idx.to_dict()`，
#     但 MetadataIndex 並沒有定義 to_dict()，AttributeError 會被下面的
#     `except Exception` 吞掉。沒有呼叫端所以沒人發現。要啟用這條路徑就必須先補
#     to_dict()，或改成明確列欄位。
#   - `index_content` 是 String(255)：超過的摘要會在 DB 層被拒或截斷，
#     不適合塞入整段內容，只能放指標式摘要。
#   - 三個方法都以「回傳空值／(False, msg)」代替拋出例外。呼叫端若不檢查回傳值，
#     失敗會完全無聲。
# 相關 NOTE:
#   無。本檔目前只有既成事實與限制，沒有需要固定下來的長期決策；
#   要改變它在 COC 中的角色時再寫 NOTE。
# 驗證:
#   python -m pytest test/unit -q -k "index or paq"
#   確認 write-only 現況：
#   grep -rn "get_latest_context\|search_indices_by_module" --include=*.py app/
#   （只應出現在本檔的定義處；一旦出現呼叫端，本 header 的 write-only 敘述就要更新）
#路徑(./app/services/index_service.py) #版本 v0.2 #更版時間 20260810-2340
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