# Path: scripts/prepare_test_runtime.py
# Purpose: create the disposable schema-fix database required before isolated tests.

import os
import sqlite3
import sys
from pathlib import Path


def prepare_schema_fix_db() -> None:
    db_path = Path("/app/data/roothinks.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                project_id VARCHAR(20) NOT NULL UNIQUE,
                name VARCHAR(100) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'temp'
            )
            """
        )
        conn.commit()


if __name__ == "__main__":
    prepare_schema_fix_db()
    if len(sys.argv) < 2:
        raise SystemExit("missing test command")
    os.execvp(sys.argv[1], sys.argv[1:])
