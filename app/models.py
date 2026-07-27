# 檔案路徑: app/models.py
# 產生時間: 2026-07-26 00:30 +08:00
# 版本: v1.9
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
#   6. [email-auth] User.email 升級為唯一登入識別（unique NOT NULL、小寫儲存）；
#      username 降為顯示名，由 email 前綴自動產生。
#      新增 system_role（user / mentor / admin），與專案層角色互相獨立。
# 維護提醒:
#   - Paper 複合主鍵是高風險 legacy schema，本輪只新增 migration plan，不重建。
#   - User.to_dict() 不得含 password_hash（安全邊界）。
#   - set_password 使用 werkzeug scrypt，check_password 提供常數時間比對。
#   - WorkspaceMember.pid 對應 projects.project_id（字串），非 projects.id（整數）。
#   - RevisionLog 寫入失敗只 log warning，不中斷主流程。
#   - email 一律經 User.normalize_email() 才寫入或查詢，否則大小寫會造成重複帳號。
#   - users 表的 email NOT NULL 由 fix_db_schema._upgrade_users_table 於啟動時保證；
#     db.create_all() 不會改既有表的欄位約束。
# 驗證方式:
#   "C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python" -m pytest test -q
# ------------------------------------------------------------------------------
from app import db
from datetime import datetime, timezone
import json
import logging
import re

from app.errors import AppError
from app.status import ProcessStatus
from app.utils.json_safe import safe_json_loads
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

LOGGER = logging.getLogger("models")


# =============================================================================
# [email-auth] 系統層身分 — 與專案層角色（WorkspaceMember.role）互相獨立
# =============================================================================
SYSTEM_ROLE_USER = "user"
SYSTEM_ROLE_MENTOR = "mentor"
SYSTEM_ROLE_ADMIN = "admin"

SYSTEM_ROLES = {SYSTEM_ROLE_USER, SYSTEM_ROLE_MENTOR, SYSTEM_ROLE_ADMIN}

# 顯示名允許的字元集，與 auth 表單驗證同源。
_USERNAME_ALLOWED_RE = re.compile(r"[^A-Za-z0-9_\-]")
_USERNAME_MAX_LEN = 32
_USERNAME_MIN_LEN = 3


# =============================================================================
# [Batch A] User Model — 多人帳號登入
# [email-auth] email 升級為唯一登入識別；username 降為顯示名
# =============================================================================
class User(UserMixin, db.Model):
    """
    網站使用者帳號，支援 flask-login session 登入。

    email 是唯一登入識別（一律以小寫儲存，登入比對不區分大小寫）；
    username 僅作顯示名，於註冊時由 email 前綴自動產生。
    保留 username 的原因：成員管理 API、稿件署名（_updated_by）、RevisionLog
    與前端成員 modal 都以它為識別鍵，移除會擴散到多個模組。
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(32),
        unique=True,
        index=True,
        nullable=False,
    )
    # nullable=False：email 既是登入識別，DB 層必須鎖死，不能容忍空值。
    email = db.Column(db.String(254), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    system_role = db.Column(
        db.String(20),
        nullable=False,
        default=SYSTEM_ROLE_USER,
        server_default=SYSTEM_ROLE_USER,
    )
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

    # -- email / username 工具 ------------------------------------------------

    @staticmethod
    def normalize_email(raw: str) -> str:
        """正規化 email：去空白 + 轉小寫。寫入與查詢都必須經過此函式。"""
        return str(raw or "").strip().lower()

    @classmethod
    def find_by_email(cls, raw: str):
        """以 email 查帳號（不區分大小寫）。找不到回 None。"""
        normalized = cls.normalize_email(raw)
        if not normalized:
            return None
        return cls.query.filter_by(email=normalized).first()

    @classmethod
    def derive_username(cls, email: str) -> str:
        """
        由 email 前綴產生顯示名，並確保未與既有 username 衝突。

        規則：取 local-part → 濾掉不合法字元 → 不足 3 字補 'u' → 撞名時加數字尾碼
        （尾碼會擠掉字首多餘字元以維持 32 字上限）。
        """
        local_part = cls.normalize_email(email).split("@", 1)[0]
        base = _USERNAME_ALLOWED_RE.sub("", local_part)[:_USERNAME_MAX_LEN]
        if not base:
            base = "user"
        while len(base) < _USERNAME_MIN_LEN:
            base += "u"

        candidate = base
        suffix = 1
        while cls.query.filter_by(username=candidate).first() is not None:
            suffix += 1
            tail = str(suffix)
            candidate = f"{base[:_USERNAME_MAX_LEN - len(tail)]}{tail}"
        return candidate

    @property
    def is_mentor(self) -> bool:
        """是否具備 mentor 視角（admin 一併涵蓋）。mentor 同時保有一般 user 身分。"""
        return self.system_role in {SYSTEM_ROLE_MENTOR, SYSTEM_ROLE_ADMIN}

    def to_dict(self) -> dict:
        """轉為安全的 dict；不包含 password_hash。"""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "is_active": self.is_active,
            "system_role": self.system_role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# =============================================================================
# [Batch B] WorkspaceMember Model — 工作區成員與角色授權
# =============================================================================
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_COAUTHOR = "coauthor"
ROLE_VIEWER = "viewer"

# 角色排序：數值越大權限越高。用於 require_workspace_role 的 min_role 比較。
#
# coauthor 插在 viewer 與 editor 之間：
#   viewer   只能看
#   coauthor 能看全部、能對所有章節留言，但只能「寫被指派的章節」
#   editor   能寫所有章節
#   owner    再加上成員管理與刪除
# 章節層的細分不在這張表裡，由 security.can_write_section 綜合判斷。
ROLE_ORDER = {
    ROLE_VIEWER: 1,
    ROLE_COAUTHOR: 2,
    ROLE_EDITOR: 3,
    ROLE_OWNER: 4,
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


# =============================================================================
# [mentor] 師徒歸屬、活動統計與派工
# =============================================================================
# 無 heartbeat 超過這個秒數就視為離線，據此結算該段 session。
SESSION_IDLE_TIMEOUT_SEC = 300


class MentorLink(db.Model):
    """
    mentor → mentee 歸屬。與 WorkspaceMember 不同：那是「人↔專案」，這是「人↔人」。

    mentor 由自己輸入 mentee 的 email 建立歸屬（本輪不做 admin 介面）。
    """

    __tablename__ = "mentor_links"
    __table_args__ = (
        db.UniqueConstraint("mentor_id", "mentee_id", name="uq_mentor_link"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    mentor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mentee_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    mentor = db.relationship("User", foreign_keys=[mentor_id])
    mentee = db.relationship("User", foreign_keys=[mentee_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mentor_id": self.mentor_id,
            "mentee_id": self.mentee_id,
            "mentee_username": self.mentee.username if self.mentee else None,
            "mentee_email": self.mentee.email if self.mentee else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserSession(db.Model):
    """
    登入活動紀錄。上線次數＝session 筆數；停留時間＝duration_sec 總和。

    系統原本完全沒有這類埋點，mentor 要看的統計全部由這張表推導。
    ended_at 為 None 代表仍在線；超過 SESSION_IDLE_TIMEOUT_SEC 沒有 heartbeat
    的 session 會在讀取統計時被結算（避免額外跑背景排程）。
    """

    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    login_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    last_seen_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    ended_at = db.Column(db.DateTime, nullable=True)
    duration_sec = db.Column(db.Integer, nullable=False, default=0)

    def touch(self) -> None:
        """收到 heartbeat：更新最後活動時間並重算停留秒數。"""
        now = datetime.now(timezone.utc)
        self.last_seen_at = now
        self.duration_sec = max(int((now - _as_utc(self.login_at)).total_seconds()), 0)

    def close(self) -> None:
        """結算 session。停留時間以最後一次 heartbeat 為準，不含閒置尾巴。"""
        if self.ended_at is not None:
            return
        self.ended_at = self.last_seen_at
        self.duration_sec = max(
            int((_as_utc(self.last_seen_at) - _as_utc(self.login_at)).total_seconds()), 0
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "login_at": self.login_at.isoformat() if self.login_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_sec": self.duration_sec,
            "active": self.ended_at is None,
        }


class MentorTask(db.Model):
    """mentor 指派給 mentee 的工作項目。"""

    __tablename__ = "mentor_tasks"

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_DONE = "done"
    STATUSES = {STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_DONE}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    mentor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mentee_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 可選擇把工作綁到某個專案；不綁則為一般性任務。
    pid = db.Column(db.String(20), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.String(20), nullable=True)  # ISO date 字串，避免時區換算歧義
    status = db.Column(db.String(20), nullable=False, default=STATUS_OPEN)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    mentor = db.relationship("User", foreign_keys=[mentor_id])
    mentee = db.relationship("User", foreign_keys=[mentee_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mentor": self.mentor.username if self.mentor else None,
            "mentee_id": self.mentee_id,
            "mentee": self.mentee.username if self.mentee else None,
            "pid": self.pid,
            "title": self.title,
            "body": self.body,
            "due_date": self.due_date,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MentorComment(db.Model):
    """
    mentor 對 mentee 的評論。與 ChapterComment 分開：
    這是針對「人」的整體回饋，不綁定手稿章節。
    """

    __tablename__ = "mentor_comments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    mentor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mentee_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )

    mentor = db.relationship("User", foreign_keys=[mentor_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mentor": self.mentor.username if self.mentor else None,
            "mentee_id": self.mentee_id,
            "body": self.body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _as_utc(value: datetime) -> datetime:
    """
    把 DB 讀回的 naive datetime 視為 UTC。

    SQLite 不保存時區，寫入時是 timezone-aware、讀回來卻是 naive，
    直接相減會拋 TypeError。這個轉換是所有時間差計算的前置。
    """
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# =============================================================================
# [collab] 章節層授權與留言
# =============================================================================
class ChapterAssignment(db.Model):
    """
    章節撰寫指派。決定 coauthor 能寫哪些章節。

    只有「被指派」這個正面表列；沒有記錄就代表沒被指派。
    editor 以上不需要指派也能寫全部章節（見 security.can_write_section）。
    """

    __tablename__ = "chapter_assignments"
    __table_args__ = (
        db.UniqueConstraint(
            "pid", "section_key", "user_id", name="uq_chapter_assignment"
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 與 WorkspaceMember 同規：對應 projects.project_id（字串），不設 FK 保留彈性。
    pid = db.Column(db.String(20), nullable=False, index=True)
    # 對應 ManuSectionConfig.section_key，如 'introduction'。
    section_key = db.Column(db.String(50), nullable=False, index=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assigned_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user = db.relationship("User", foreign_keys=[user_id])
    assigner = db.relationship("User", foreign_keys=[assigned_by])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pid": self.pid,
            "section_key": self.section_key,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "email": self.user.email if self.user else None,
            "assigned_by": self.assigner.username if self.assigner else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ChapterComment(db.Model):
    """
    章節留言。viewer 以上皆可留言，含未被指派該章節的 coauthor。

    anchor 欄位預留給未來「錨定到選取文字」的顆粒度；目前一律 None，
    留言以整章一串呈現。
    """

    __tablename__ = "chapter_comments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    pid = db.Column(db.String(20), nullable=False, index=True)
    section_key = db.Column(db.String(50), nullable=False, index=True)
    author_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    body = db.Column(db.Text, nullable=False)
    anchor = db.Column(db.String(200), nullable=True)
    resolved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    author = db.relationship("User", foreign_keys=[author_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pid": self.pid,
            "section_key": self.section_key,
            "author_id": self.author_id,
            "author": self.author.username if self.author else None,
            "body": self.body,
            "anchor": self.anchor,
            "resolved": self.resolved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
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
