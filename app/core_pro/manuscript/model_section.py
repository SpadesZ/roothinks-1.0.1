# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/model_section.py
# 模組定位: Manuscript 核心層；位於 HTTP/Socket 工作台、章節協作與持久化之間。
# 主要責任: 定義章節順序、名稱與可編輯規則的 canonical configuration，供 API 與前端共同使用。
# 上下游: manuscript_routes 與前端 workspace 呼叫本層，經 ManuscriptIO/DB 寫入 data/<pid>/manuscript 並回送 HTTP/Socket 事件。
# 維護邊界: 所有讀寫保留 PID、user 與 section scope；草稿/版本/快取不得跨使用者、跨章或以舊非同步回應覆蓋新狀態。
# 驗證: python -m pytest test/unit tests -q
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


class ManuSectionProgress(db.Model):
    """章節完成度（0～100 的整數百分比）。

    NOTE(NOTE-036) 為什麼**不是** ManuSectionConfig 上的一個欄位：
    `POST /manuscript/api/sections/<pid>` 的實作是

        ManuSectionConfig.query.filter_by(pid=pid).delete()   # 全量覆蓋

    也就是使用者每按一次「章節管理 → 儲存」（改名或調順序），整張設定表就會被
    刪掉重建。完成度如果放在那張表上，那個動作會把**所有章節的進度靜默歸零**，
    畫面上還不會有任何錯誤。分成兩張表之後，「設定重寫不得清空進度」是結構上
    成立的，不必靠人記得在覆蓋時搬移欄位。

    「沒有這一列」代表**還沒填**，與「填了 0%」是不同的事，因此不設預設值列。
    """

    __tablename__ = 'manu_section_progress'
    __bind_key__ = 'manuscript'
    __table_args__ = (
        db.UniqueConstraint('pid', 'section_key', name='uq_manu_section_progress'),
    )

    id = db.Column(db.Integer, primary_key=True)
    pid = db.Column(db.String(50), nullable=False, index=True)
    section_key = db.Column(db.String(50), nullable=False)

    # 0~100。伺服器負責 clamp 與型別檢查；前端的 min/max 只是體驗優化。
    progress_percent = db.Column(db.Integer, nullable=False, default=0)

    # 誰最後改的。進度是要拿去回報的數字，「誰說的」必須可歸屬。
    updated_by = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @staticmethod
    def clamp(value):
        """把任意輸入收斂成 0~100 的整數；無法解讀時回 None。

        接受 int / float / 數字字串（含 "80%" 這種使用者會直接貼進來的形式）。
        回 None 代表「這不是一個百分比」，呼叫端應該拒絕而不是當成 0 ——
        把看不懂的輸入寫成 0 會讓使用者以為自己填的被記錄了。
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        text = str(value).strip().rstrip('%').strip()
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float('inf'), float('-inf')):
            return None
        return max(0, min(100, int(round(number))))

    @classmethod
    def map_for_pid(cls, pid):
        """回傳 { section_key: progress_percent }。未填的章節不會出現在裡面。"""
        return {
            row.section_key: int(row.progress_percent)
            for row in cls.query.filter_by(pid=pid).all()
        }
