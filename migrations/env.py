# Roothinks source maintenance contract
# 主要責任: 連接 Flask-Migrate metadata 與 Alembic context，分別執行 offline SQL 產生及 online transaction migration。
# 上下游: Alembic/啟動 migration runner 讀目前 schema，upgrade/downgrade 轉換 SQLite 後再由 ORM 使用。
# 驗證: python -m py_compile migrations/env.py
# 檔案路徑: migrations/env.py
# 產生時間: 2026-07-04 19:20 +08:00
# 版本: v0.1
# 模組定位:
#   Alembic environment for Roothinks SQLAlchemy metadata.
# 維護提醒:
#   - 本輪只 scaffold/baseline 與新增 evidence_segments；不要 autogenerate destructive diff。
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app import models  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.metadata


def _database_url() -> str:
    return os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
