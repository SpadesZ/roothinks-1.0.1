# 檔案路徑: patch_schema.py
# 模組定位: 歷史一次性 source patch 工具；把 papers composite-key migration 片段注入 fix_db_schema.py，不屬於啟動 migration。
# 主要責任: 以精確 old/new 字串替換補入 papers 表重建、資料搬移與 pid index 建立程式，供舊基線人工升級。
# 上下游: 維護者於 disposable checkout 明確執行 -> fix_db_schema.py -> 後續隔離 SQLite migration/restore drill。
# 維護邊界: 字串未唯一命中必須 fail closed；不得直接對正式 checkout 或 DB 執行，schema 變更須先備份並驗證 quick_check/資料 digest。
# 驗證: python -m py_compile patch_schema.py；在 disposable worktree 執行後檢查唯一 diff，再以舊 schema 複本跑 migration 測試。

with open('fix_db_schema.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_str = """        for column_name, column_type in column_defs.items():
            if column_name not in columns:
                LOGGER.info("Adding missing column: %s", column_name)
                cursor.execute(f"ALTER TABLE projects ADD COLUMN {column_name} {column_type}")
        conn.commit()"""

new_str = """        for column_name, column_type in column_defs.items():
            if column_name not in columns:
                LOGGER.info("Adding missing column: %s", column_name)
                cursor.execute(f"ALTER TABLE projects ADD COLUMN {column_name} {column_type}")
        
        # Check papers primary key
        cursor.execute("PRAGMA table_info(papers)")
        papers_cols = cursor.fetchall()
        pid_col = next((c for c in papers_cols if c[1] == 'pid'), None)
        
        # pk > 0 means it is part of primary key. If pid is not pk, upgrade
        if pid_col and pid_col[5] == 0:
            LOGGER.info("Migrating papers table to composite primary key")
            cols_names = ', '.join(c[1] for c in papers_cols)
            
            create_stmt = '''CREATE TABLE papers_new (
                paper_id VARCHAR(120),
                pid VARCHAR(50) NOT NULL,
                title VARCHAR(500),
                authors VARCHAR(200),
                journal VARCHAR(200),
                publish_date VARCHAR(50),
                process_status VARCHAR(20),
                process_log TEXT,
                result_json TEXT,
                interpretation_status VARCHAR(20),
                has_source BOOLEAN,
                full_text_path VARCHAR(500),
                clean_text_cache TEXT,
                study_time INTEGER,
                page_count INTEGER,
                size_str VARCHAR(20),
                created_at DATETIME,
                updated_at DATETIME,
                PRIMARY KEY (paper_id, pid)
            )'''
            cursor.execute(create_stmt)
            cursor.execute(f"INSERT INTO papers_new ({cols_names}) SELECT {cols_names} FROM papers")
            cursor.execute("DROP TABLE papers")
            cursor.execute("ALTER TABLE papers_new RENAME TO papers")
            cursor.execute("CREATE INDEX ix_papers_pid ON papers (pid)")
        
        conn.commit()"""
text = text.replace(old_str, new_str)
with open('fix_db_schema.py', 'w', encoding='utf-8') as f:
    f.write(text)
