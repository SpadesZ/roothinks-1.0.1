# Roothinks source maintenance contract
# 主要責任: 建立可供既有 Roothinks ORM 啟動的 baseline schema，並提供對稱 downgrade。
# 上下游: Alembic/啟動 migration runner 讀目前 schema，upgrade/downgrade 轉換 SQLite 後再由 ORM 使用。
# 檔案路徑: migrations/versions/0001_baseline_schema.py
# 產生時間: 2026-07-04 19:20 +08:00
# 版本: v0.1
# 模組定位:
#   Alembic baseline marker for existing Roothinks schema.
# 維護提醒:
#   - No-op baseline. Existing DBs should be stamped after manual verification.
# -----------------------------------------------------------------------------

from __future__ import annotations


revision = "0001_baseline_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
