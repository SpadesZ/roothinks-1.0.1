#路徑(./app/models.py) #版本 v1.4 #更版時間 20260225-1600
from app import db
from datetime import datetime, timezone
import json
import logging

LOGGER = logging.getLogger("models")

class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    
    # [Fix] 簡稱 (必備欄位，對應 fix_db_schema)
    abbreviation = db.Column(db.String(50), nullable=True)
    
    research_title = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), default='temp', nullable=False)
    
    classification = db.Column(db.String(50), nullable=True) 
    keywords = db.Column(db.String(200), nullable=True)
    
    context_background = db.Column(db.Text, nullable=True)
    
    # [v1.4 Update] 新增 AI 摘要欄位，用於正式研究的快速檢視
    ai_summary = db.Column(db.Text, nullable=True)
    
    # 人員組織資料 (JSON)
    members = db.Column(db.JSON, nullable=True)
    
    # 時間戳記
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        """將 Project 物件轉換為 Dictionary"""
        return {
            'id': self.id,
            'project_id': self.project_id,
            'name': self.name,
            'abbreviation': self.abbreviation,
            'research_title': self.research_title,
            'status': self.status,
            'classification': self.classification,
            'keywords': self.keywords,
            'background': self.context_background,
            'ai_summary': self.ai_summary, # [v1.4 Update]
            'members': self.members,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if hasattr(self, 'updated_at') and self.updated_at else None
        }

class PaqSurvey(db.Model):
    __tablename__ = 'paq_surveys'
    id = db.Column(db.Integer, primary_key=True)
    project_ref_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, unique=True)
    
    # [v1.3 Update] Renamed from axis_definitions to axis_labels
    axis_labels = db.Column(db.JSON, nullable=True)
    axis_tags = db.Column(db.JSON, nullable=True)
    cube_data = db.Column(db.JSON, nullable=True)
    
    # 對話紀錄 (可選)
    prompt_history = db.Column(db.Text, nullable=True)
    
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class MetadataIndex(db.Model):
    __tablename__ = 'metadata_index'
    id = db.Column(db.Integer, primary_key=True)
    project_ref_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    
    module_name = db.Column(db.String(50), nullable=False)   # 例如: 'paq', 'literature'
    folder_name = db.Column(db.String(50), nullable=False)   # 例如: 'paq_taxonomy'
    file_path = db.Column(db.String(255), nullable=False)    # 相對路徑: 'paq/paq_taxonomy/xxx.json'
    
    # [Critical] 限制為 15-20 字的精確摘要，供 LLM 快速檢索
    index_content = db.Column(db.String(255), nullable=False)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Paper(db.Model):
    __tablename__ = 'papers'

    STATUS_PENDING = 'pending'
    STATUS_ANALYZING = 'analyzing'
    STATUS_T5_QUEUED = 't5_queued'
    STATUS_T5_RUNNING = 't5_running'
    STATUS_GOLD_READY = 'gold_ready'
    STATUS_FAILED = 'failed'

    paper_id = db.Column(db.String(120), primary_key=True)
    pid = db.Column(db.String(50), primary_key=True, nullable=False, index=True)

    title = db.Column(db.String(500), nullable=True)
    authors = db.Column(db.String(200), nullable=True)
    journal = db.Column(db.String(200), nullable=True)
    publish_date = db.Column(db.String(50), nullable=True)

    process_status = db.Column(db.String(20), default='idle')
    process_log = db.Column(db.Text, default='')
    result_json = db.Column(db.Text, default='{}')
    interpretation_status = db.Column(db.String(20), default=STATUS_PENDING)

    has_source = db.Column(db.Boolean, default=True)
    full_text_path = db.Column(db.String(500), nullable=True)
    clean_text_cache = db.Column(db.Text, nullable=True)
    study_time = db.Column(db.Integer, default=0)
    page_count = db.Column(db.Integer, default=0)
    size_str = db.Column(db.String(20), default='0KB')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def get_results(self):
        try:
            return json.loads(self.result_json) if self.result_json else {}
        except Exception:
            LOGGER.exception("Failed to parse paper.result_json")
            return {}


class ConfigKV(db.Model):
    __tablename__ = 'config_kv'

    k = db.Column(db.String(100), primary_key=True)
    v = db.Column(db.Text)


def _get_kv(key, default=None):
    try:
        item = db.session.get(ConfigKV, key)
        return item.v if item else default
    except Exception:
        LOGGER.exception("ConfigKV read failed for key=%s", key)
        return default


def _set_kv(key, value):
    try:
        item = db.session.get(ConfigKV, key)
        if not item:
            item = ConfigKV(k=key)
            db.session.add(item)
        item.v = '' if value is None else str(value)
        db.session.commit()
    except Exception:
        db.session.rollback()
        LOGGER.exception("ConfigKV write failed for key=%s", key)
