import contextlib
import logging
import os
import sqlite3
import time

LOGGER = logging.getLogger("fix_db_schema")


def _resolve_db_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "data", "roothinks.db"),
        os.path.join(base_dir, "app", "data", "roothinks.db"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"Database file not found in: {candidates}")


@contextlib.contextmanager
def _schema_lock(lock_path: str, timeout_sec: float = 30.0):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "a+b")
    # Windows msvcrt.locking operates on the current file pointer range.
    # Keep a stable 1-byte lock region at offset 0.
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        fh.write(b"0")
        fh.flush()

    acquired = False
    deadline = time.time() + timeout_sec
    try:
        while time.time() < deadline and not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                time.sleep(0.1)
        if not acquired:
            raise TimeoutError(f"Acquire schema lock timed out: {lock_path}")
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def upgrade_database():
    db_path = _resolve_db_path()
    LOGGER.info("Running schema upgrade on %s", db_path)

    with sqlite3.connect(db_path, timeout=30) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(projects)")
        project_cols_info = cursor.fetchall()
        columns = {col[1] for col in project_cols_info}
        if not columns:
            raise RuntimeError("Missing 'projects' table. Please initialize database first.")

        # Legacy DBs may only have project_id as PK and miss the numeric id column
        # required by current ORM relations (projects.id -> *_ref_id).
        if "id" not in columns:
            LOGGER.info("Migrating projects table to include numeric primary key and full model columns")
            cursor.execute("SELECT rowid, * FROM projects")
            rows = cursor.fetchall()
            old_names = [d[0] for d in cursor.description]

            cursor.execute(
                """
                CREATE TABLE projects_new (
                    id INTEGER PRIMARY KEY,
                    project_id VARCHAR(20) NOT NULL UNIQUE,
                    name VARCHAR(100) NOT NULL,
                    abbreviation VARCHAR(50),
                    research_title VARCHAR(200),
                    status VARCHAR(20) NOT NULL DEFAULT 'temp',
                    classification VARCHAR(50),
                    keywords VARCHAR(200),
                    context_background TEXT,
                    ai_summary TEXT,
                    members JSON,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )

            insert_sql = (
                "INSERT INTO projects_new "
                "(id, project_id, name, abbreviation, research_title, status, classification, "
                "keywords, context_background, ai_summary, members, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )

            for row in rows:
                item = dict(zip(old_names, row))
                project_id = str(item.get("project_id") or "").strip()
                if not project_id:
                    continue

                id_val = item.get("id")
                if id_val is None:
                    id_val = item.get("rowid")

                name = item.get("name")
                if name is None or str(name).strip() == "":
                    name = project_id

                cursor.execute(
                    insert_sql,
                    (
                        id_val,
                        project_id,
                        str(name),
                        item.get("abbreviation"),
                        item.get("research_title"),
                        item.get("status") or "temp",
                        item.get("classification"),
                        item.get("keywords"),
                        item.get("context_background"),
                        item.get("ai_summary"),
                        item.get("members"),
                        item.get("created_at"),
                        item.get("updated_at"),
                    ),
                )

            cursor.execute("DROP TABLE projects")
            cursor.execute("ALTER TABLE projects_new RENAME TO projects")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_projects_project_id ON projects (project_id)")

            cursor.execute("PRAGMA table_info(projects)")
            project_cols_info = cursor.fetchall()
            columns = {col[1] for col in project_cols_info}

        column_defs = {
            "ai_summary": "TEXT",
            "abbreviation": "VARCHAR(50)",
            "classification": "VARCHAR(50)",
            "research_title": "VARCHAR(200)",
            "keywords": "VARCHAR(200)",
            "context_background": "TEXT",
            "members": "JSON",
        }
        for column_name, column_type in column_defs.items():
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
        
        conn.commit()

    LOGGER.info("Schema upgrade finished.")


def fix_schema():
    db_path = _resolve_db_path()
    lock_path = os.path.join(os.path.dirname(db_path), ".schema_fix.lock")
    with _schema_lock(lock_path):
        upgrade_database()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fix_schema()
