# 檔案路徑: app/models.py
# 產生時間: 2026-07-04 18:55 +08:00
# 版本: v1.8
# 模組定位:
#   Roothinks Flask-SQLAlchemy domain models。
# 主要責任:
#   1. 保存 Project / Paper / ConfigKV 等既有模型。
#   2. 新增 EvidenceSegment 作為本地 evidence index，不改 Paper 複合主鍵。
#   3. [Batch A] 新增 User model，支援 flask-login 多人登入系統。
#   4. [Batch B] 新增 WorkspaceMember model，實作多人角色授權內核。
#      角色階層：viewer < editor < owner（ROLE_ORDER 字典定義）。
#      UniqueConstraint(user_id, pid) 確保每人在每個專案只有一個角色。
#   5. [Batch C] 新增 RevisionLog model，版本紀錄骨架。
#      - 不存全文 diff，只記 entity_type / entity_ref / action / summary。
#      - payload_json 欄位 nullable，供未來擴充。
#      - dev 模式 user_id=None 也可寫入（hook 可用性確認）。
# 維護提醒:
#   - Paper 複合主鍵是高風險 legacy schema，本輪只新增 migration plan，不重建。
#   - User.to_dict() 不得含 password_hash（安全邊界）。
#   - set_password 使用 werkzeug scrypt，check_password 提供常數時間比對。
#   - WorkspaceMember.pid 對應 projects.project_id（字串），非 projects.id（整數）。
#   - RevisionLog 寫入失敗只 log warning，不中斷主流程。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test -q
# ------------------------------------------------------------------------------
from app import db
from datetime import datetime, timezone
import json
import logging

from app.errors import AppError
from app.status import ProcessStatus
from app.utils.json_safe import safe_json_loads
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

LOGGER = logging.getLogger("models")


# =============================================================================
# [Batch A] User Model — 多人帳號登入
# =============================================================================
class User(UserMixin, db.Model):
    """網站使用者帳號，支援 flask-login session 登入。"""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(32),
        unique=True,
        index=True,
        nullable=False,
    )
    email = db.Column(db.String(254), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def set_password(self, password: str) -> None:
        """設定密碼（werkzeug scrypt 雜湊）。"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """驗證密碼；使用 werkzeug 常數時間比對。"""
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        """轉為安全的 dict；不包含 password_hash。"""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# =============================================================================
# [Batch B] WorkspaceMember Model — 工作區成員與角色授權
# =============================================================================
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

# 角色排序：數值越大權限越高。用於 require_workspace_role 的 min_role 比較。
ROLE_ORDER = {
    ROLE_VIEWER: 1,
    ROLE_EDITOR: 2,
    ROLE_OWNER: 3,
}


class WorkspaceMember(db.Model):
    """工作區成員授權記錄。每筆記錄對應一位 User 在一個 Project 中的角色。"""

    __tablename__ = "workspace_members"
    __table_args__ = (
        db.UniqueConstraint("user_id", "pid", name="uq_workspace_member_user_pid"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # pid 對應 projects.project_id（字串），不用 FK 以保留彈性（如 promote 後 new_pid）。
    pid = db.Column(db.String(20), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False, default=ROLE_VIEWER)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # 關聯方便查詢 username
    user = db.relationship("User", backref=db.backref("workspace_memberships", lazy="dynamic"))

    def to_dict(self) -> dict:
        """轉為安全的 dict（供成員管理 API 回傳）。"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "pid": self.pid,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


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


class EvidenceSegment(db.Model):
    __tablename__ = 'evidence_segments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.String(50), index=True, nullable=False)
    source_type = db.Column(db.String(50), index=True, nullable=False)
    source_id = db.Column(db.String(255), index=True, nullable=False)
    paper_id = db.Column(db.String(120), index=True, nullable=True)
    segment_id = db.Column(db.String(120), index=True, nullable=True)
    title = db.Column(db.String(500), nullable=True)
    text = db.Column(db.Text, nullable=False)
    content_hash = db.Column(db.String(64), index=True, nullable=False)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


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

    process_status = db.Column(db.String(20), default=ProcessStatus.PENDING.value)
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

    def get_results(self, *, strict: bool = False):
        try:
            return safe_json_loads(
                self.result_json,
                default={},
                context=f"Paper.result_json pid={self.pid} paper_id={self.paper_id}",
            )
        except AppError as exc:
            LOGGER.exception("Failed to parse paper.result_json")
            if strict:
                raise
            return {
                "status": "invalid_json",
                "error_code": exc.code.value,
                "message": exc.message,
            }


# =============================================================================
# [Batch C] RevisionLog Model — 版本紀錄骨架
# =============================================================================
class RevisionLog(db.Model):
    """
    輕量版本紀錄，記錄 manuscript block/paper 的儲存與刪除動作。
    不存全文 diff；payload_json 欄位保留給未來擴充。
    dev 模式 user_id=None 亦可寫入（hook 可用性確認）。
    """

    __tablename__ = "revision_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    pid = db.Column(db.String(20), nullable=False, index=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_type = db.Column(db.String(30), nullable=False)   # 如 'manuscript_block'
    entity_ref = db.Column(db.String(200), nullable=False)   # 如 section/block id
    action = db.Column(db.String(20), nullable=False)        # 'save' | 'delete'
    summary = db.Column(db.String(300), nullable=True)       # 簡述，如字數變化
    payload_json = db.Column(db.Text, nullable=True)         # 未來擴充用
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # 關聯方便查詢 username（nullable，dev 模式無 user）
    user = db.relationship("User", backref=db.backref("revision_logs", lazy="dynamic"), foreign_keys=[user_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pid": self.pid,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "entity_type": self.entity_type,
            "entity_ref": self.entity_ref,
            "action": self.action,
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


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
