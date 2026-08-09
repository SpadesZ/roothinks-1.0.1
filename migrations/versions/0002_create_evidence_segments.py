# Roothinks source maintenance contract
# 主要責任: 以 additive migration 建立 evidence_segments 與索引，不改寫既有 Paper 主鍵或研究內容。
# 上下游: Alembic/啟動 migration runner 讀目前 schema，upgrade/downgrade 轉換 SQLite 後再由 ORM 使用。
# 驗證: python -m py_compile migrations/versions/0002_create_evidence_segments.py
# 檔案路徑: migrations/versions/0002_create_evidence_segments.py
# 產生時間: 2026-07-04 19:20 +08:00
# 版本: v0.1
# 模組定位:
#   Additive migration for local Evidence Index.
# 維護提醒:
#   - Only creates evidence_segments and indexes. Does not alter Paper PK.
# -----------------------------------------------------------------------------

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_create_evidence_segments"
down_revision = "0001_baseline_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_segments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("paper_id", sa.String(length=120), nullable=True),
        sa.Column("segment_id", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_evidence_segments_project_id", "evidence_segments", ["project_id"])
    op.create_index("ix_evidence_segments_source_type", "evidence_segments", ["source_type"])
    op.create_index("ix_evidence_segments_source_id", "evidence_segments", ["source_id"])
    op.create_index("ix_evidence_segments_paper_id", "evidence_segments", ["paper_id"])
    op.create_index("ix_evidence_segments_segment_id", "evidence_segments", ["segment_id"])
    op.create_index("ix_evidence_segments_content_hash", "evidence_segments", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_evidence_segments_content_hash", table_name="evidence_segments")
    op.drop_index("ix_evidence_segments_segment_id", table_name="evidence_segments")
    op.drop_index("ix_evidence_segments_paper_id", table_name="evidence_segments")
    op.drop_index("ix_evidence_segments_source_id", table_name="evidence_segments")
    op.drop_index("ix_evidence_segments_source_type", table_name="evidence_segments")
    op.drop_index("ix_evidence_segments_project_id", table_name="evidence_segments")
    op.drop_table("evidence_segments")
