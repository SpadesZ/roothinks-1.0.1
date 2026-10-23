# Roothinks source maintenance contract
# 目的：只新增 ChapterComment 回覆事件表；不重建、不改寫既有研究資料。
# 驗證：SAI 合成 SQLite 舊表升級檢查；正式資料庫未執行。
from alembic import op
from app.models import ChapterReviewEvent

revision = '0003_chapter_review_events'
down_revision = '0002_create_evidence_segments'
branch_labels = None
depends_on = None


def upgrade():
    ChapterReviewEvent.__table__.create(bind=op.get_bind(),checkfirst=True)


def downgrade():
    raise RuntimeError('審閱歷史不可自動刪除；請先另存並由擁有者確認復原方案。')
