#路徑(./app/core_proc/manuscript/model_section.py)
#版本 v0.1
#更版時間 20260318-1230

from app import db
from datetime import datetime

class ManuSectionConfig(db.Model):
    """
    論文段落設定表
    實作 2A/2B 視窗同步的動態章節管理 (包含固定與可自訂排序)
    """
    __tablename__ = 'manu_section_configs'
    __bind_key__ = 'manuscript'

    id = db.Column(db.Integer, primary_key=True)
    pid = db.Column(db.String(50), nullable=False, index=True) # 專案代號
    
    section_key = db.Column(db.String(50), nullable=False) # 系統內部鍵值 (如 'intro')
    section_name = db.Column(db.String(100), nullable=False) # 顯示名稱 (如 'Introduction')
    
    order_index = db.Column(db.Integer, nullable=False, default=0) # 排序權重
    is_fixed = db.Column(db.Boolean, default=False) # 是否為鎖定章節 (Title ~ Keyword)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def init_default_sections(cls, pid):
        """
        初始化專案的預設章節
        Title 到 Keyword 設為 is_fixed=True (不可編輯順序與名稱)
        後續章節設為 is_fixed=False (可編輯、可排序)
        """
        # 預設章節清單：(內部鍵值, 顯示名稱, 排序權重, 是否鎖定)
        defaults = [
            # 固定的前置章節 (不可改名、不可更動順序)
            ('title', 'Title', 10, True),
            ('author', 'Author', 20, True),
            ('abstract', 'Abstract', 30, True),
            ('keyword', 'Keyword', 40, True),
            
            # 可編輯/排序的後續章節
            ('introduction', 'Introduction', 50, False),
            ('method', 'Method', 60, False),
            ('results', 'Results', 70, False),
            ('discussion', 'Discussion', 80, False),
            ('conclusion', 'Conclusion', 90, False),
            ('acknowledgements', 'Acknowledgements', 100, False),
            ('competing_interest', 'Declaration of Competing Interest', 110, False),
            ('reference', 'Reference', 120, False),
            ('appendix', 'Supplementary Materials / Appendices', 130, False)
        ]
        
        sections = []
        for key, name, order, fixed in defaults:
            sec = cls(pid=pid, section_key=key, section_name=name, order_index=order, is_fixed=fixed)
            db.session.add(sec)
            sections.append(sec)
        
        db.session.commit()
        return sections