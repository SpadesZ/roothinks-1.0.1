#路徑(./app/core_proc/manuscript/model_manu.py) 
#版本 v0.1 
#更版時間 20260120-0550
from datetime import datetime
from app import db

class ManuProject(db.Model):
    """
    專案主表 (Metadata Only)
    用途: 索引 JSON 資料夾位置，不儲存論文內容
    """
    __tablename__ = 'manu_projects'
    __bind_key__ = 'manuscript' # 指向 manu_core.db

    id = db.Column(db.Integer, primary_key=True)
    pid = db.Column(db.String(50), unique=True, nullable=False, index=True) # e.g. "mock2222"
    title = db.Column(db.String(200))
    
    # 指向當前最新的全域版本資料夾 (e.g. "v1.2") -> data/{pid}/man{pid}_v1.2/
    current_global_version = db.Column(db.String(20), default="v0.1")
    
    # 專案狀態: active, archived, deleted
    status = db.Column(db.String(20), default="active")
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 關聯
    locks = db.relationship('ManuSectionLock', backref='project', lazy=True, cascade="all, delete-orphan")

class ManuSectionLock(db.Model):
    """
    段落鎖定表 (Optimistic Locking State)
    用途: 防止多人同時編輯同一段落 (Abstract, Method...)
    """
    __tablename__ = 'manu_section_locks'
    __bind_key__ = 'manuscript'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('manu_projects.id'), nullable=False)
    
    # 段落鍵值: abstract, introduction, method, result...
    section_key = db.Column(db.String(50), nullable=False)
    
    # 鎖定者資訊
    locked_by_user = db.Column(db.String(50), nullable=False) # User ID or Socket Session ID
    locked_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 鎖定過期時間 (防止死鎖, e.g. +5 mins)
    expires_at = db.Column(db.DateTime, nullable=False)

    def is_expired(self):
        return datetime.utcnow() > self.expires_at

class ManuMember(db.Model):
    """
    成員權限表 (RBAC)
    """
    __tablename__ = 'manu_members'
    __bind_key__ = 'manuscript'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('manu_projects.id'), nullable=False)
    user_id = db.Column(db.String(50), nullable=False)
    
    # role: owner (可刪檔), editor (可編輯), viewer (僅檢視)
    role = db.Column(db.String(20), default="editor")