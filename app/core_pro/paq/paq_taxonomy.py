# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/paq/paq_taxonomy.py
# 模組定位: PAQ 核心層；管理研究題目、分類矩陣與 provisional/formal 專案銜接。
# 主要責任: 定義 PAQ taxonomy 的預設類別、正規化與持久化格式，讓 UI 與 LLM task 使用同一套分類。
# 上下游: Dashboard/PAQ 頁面 -> PAQ routes/core -> Project 與 data/<pid>/literature/paq_records。
# 維護邊界: 設定與共享狀態只能在既定初始化邊界改動；錯誤不得以表面成功掩蓋資料或授權不完整。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/core_pro/paq/paq_taxonomy.py) #版本 v1.0 #更版時間 20260220-1830
from app import db
from app.models import Project, PaqSurvey
from app.core_pro.paq.paq_core import PaqCore

import logging

logger = logging.getLogger("app.core_pro.paq.paq_taxonomy")

class PaqTaxonomy:
    """
    PAQ Taxonomy 專屬資料控制器 (人機協作專用)
    負責處理所有不需要經過 LLM 運算的純資料庫操作 (如：使用者手動修改標籤的覆寫)。
    """
    
    @staticmethod
    def save_manual_taxonomy(pid, labels, tags):
        """
        純儲存使用者手動修改的 Taxonomy (Labels & Tags)
        Bypass LLM，直接更新資料庫，確保使用者的智慧結晶被完整保留。
        """
        try:
            project = Project.query.filter_by(project_id=pid).first()
            if not project:
                return False, "Project not found"

            survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
            if not survey:
                # 萬一該專案完全沒有 survey 紀錄，則安全地建立一筆
                survey = PaqSurvey(project_ref_id=project.id)
                db.session.add(survey)

            # 強制覆寫為使用者傳入的最新陣列/物件
            survey.axis_labels = labels
            survey.axis_tags = tags
            
            db.session.commit()
            
            # 同步將手動修改的結果備份為實體 JSON 檔案 (留存修改軌跡)
            taxonomy_data = {
                "axis_labels": labels,
                "axis_tags": tags
            }
            PaqCore._save_json(pid, 'paq', 'taxonomy_manual_v1.json', taxonomy_data)
            
            return True, taxonomy_data
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"[PAQ Taxonomy] Manual Save Error: {str(e)}")
            return False, str(e)