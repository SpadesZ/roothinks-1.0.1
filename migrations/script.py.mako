## Roothinks source maintenance contract
## 檔案路徑: migrations/script.py.mako
## 模組定位: 資料庫 schema migration 層；把既有正式資料安全帶到目前 ORM 契約。
## 主要責任: 產生 Alembic revision 骨架，保留 upgrade/downgrade 與 revision metadata 位置。
## 上下游: Alembic/啟動 migration runner 讀目前 schema，upgrade/downgrade 轉換 SQLite 後再由 ORM 使用。
## 維護邊界: 任何資料變更都需明確目標、備份、idempotency 與失敗回滾；預設不得碰正式 data 或輸出秘密。
## 驗證: python scripts/audit_source_contract.py
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from alembic import op
import sqlalchemy as sa

${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
