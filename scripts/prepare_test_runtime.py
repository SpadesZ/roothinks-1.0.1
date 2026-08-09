# Roothinks source maintenance contract
# 檔案路徑: scripts/prepare_test_runtime.py
# 模組定位: 維運/資料處理 CLI 層；由人工或隔離測試明確執行，不是常駐 request path。
# 主要責任: 在隔離測試目錄建立 schema-fix 啟動所需的最小 SQLite，避免測試 bootstrap 觸碰正式資料。
# 上下游: 命令列參數/環境 -> 明確目標檔或 DB -> 可稽核輸出；不由一般 HTTP request 隱式觸發。
# 維護邊界: 任何資料變更都需明確目標、備份、idempotency 與失敗回滾；預設不得碰正式 data 或輸出秘密。
# 驗證: python -m py_compile scripts/prepare_test_runtime.py
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
