# 目的：只新增自行填寫的學術背景及專案邀請表；不推導權限、不重建既有研究資料。
# 邊界：事件重用 RevisionLog，舊 MentorLink／membership 不自動改寫；downgrade 不刪歷史。
# 驗證：合成 SQLite 的 Table.create(checkfirst=True) 舊庫與可重入檢查。
from alembic import op
from app.models import AcademicProfile, ProjectInvitation

revision = '0004_project_invitations'
down_revision = '0003_chapter_review_events'
branch_labels = None
depends_on = None


def upgrade():
    for model in (AcademicProfile,ProjectInvitation):
        model.__table__.create(bind=op.get_bind(),checkfirst=True)


def downgrade():
    raise RuntimeError('邀請與學術背景歷史不可自動刪除；請先保存並確認復原方案。')
