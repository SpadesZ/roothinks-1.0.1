# 檔案路徑: fix_db_schema.py
# 產生時間: 2026-07-26 06:30 +08:00
# 版本: v1.1
# 模組定位:
#   SQLite schema 升級器。於每次應用啟動時由 create_app 的 _run_schema_fix 呼叫，
#   負責把既有資料庫補齊到目前 ORM 模型所需的結構。
# 主要責任:
#   1. projects 表補上數值主鍵與缺漏欄位（legacy schema 相容）。
#   2. papers 表升級為 (paper_id, pid) 複合主鍵。
#   3. [email-auth] users.email 升級為 NOT NULL UNIQUE 並新增 system_role。
#   4. [comment-versioning] chapter_comments 補上 scope / s_ver / g_ver，
#      讓留言綁定「當時被評論的版本」（見 docs/NOTES.md NOTE-008、NOTE-009）。
# 呼叫來源:
#   app/__init__.py 的 _run_schema_fix()；亦可人工執行 python fix_db_schema.py。
# 輸入輸出契約:
#   無參數；資料庫路徑由 _resolve_db_path() 於 data/ 或 app/data/ 下尋得。
#   失敗時拋例外，create_app 會據此中止啟動（寧可開不起來也不要帶著壞 schema 跑）。
#   例外（NOTE-024）：「DB 尚未初始化」不算失敗。DB 檔不存在、或存在但沒有
#   projects 表時，fix_schema() 記 log 後直接 return，把建置交給 db.create_all()。
#   全新機器上那是正常狀態，不是錯誤 —— 升級器沒有 legacy schema 可升。
# 安全邊界:
#   - 破壞性操作（DROP TABLE / 重建表）。以檔案鎖 _schema_lock 序列化，
#     避免多個 worker 同時啟動時互相踩踏。
#   - 對既有資料採防禦性補值而非直接失敗：此函式在每次啟動時執行，
#     一拋例外整個服務就開不起來。
# 維護提醒:
#   - SQLite 無法直接修改欄位約束，只能「建新表→搬資料→改名」。
#     沿用本檔既有的重建樣式，不要試圖 ALTER COLUMN。
#   - 新增欄位若帶 NOT NULL，必須同時給 DEFAULT，否則 ALTER TABLE 會失敗。
#   - db.create_all() 只會建「不存在的表」，不會改既有表的欄位，
#     因此所有欄位變更都必須寫在這裡。
# 驗證方式:
#   python -m pytest test/unit/test_email_auth.py -q   （含 schema 升級測試）
#   python -m pytest test/unit/test_comment_versioning.py -q  （留言版本欄位）
#   注意：_resolve_db_path() 不看 SQLALCHEMY_DATABASE_URI，永遠解析到
#   <repo>/data/roothinks.db；因此任何會 create_app() 的測試都會對真實 dev DB
#   套用本檔的升級。新增欄位前務必確認該升級是純增量且可回退。
# ------------------------------------------------------------------------------
import contextlib
import logging
import os
import sqlite3
import time

LOGGER = logging.getLogger("fix_db_schema")


def target_db_path() -> str:
    """
    這支 migration 會動到的那一顆 DB。

    公開它，是為了讓 create_app 在跑之前能先問一句「我要改的，是不是這次 ORM
    真的要用的那一顆」。路徑相對本檔位置寫死，**完全不看 SQLALCHEMY_DATABASE_URI**
    —— 不論呼叫端把 DB 指到哪，這裡永遠回傳 repo 的 data/roothinks.db。
    那道比對見 app/__init__.py 的 _should_run_schema_fix()。

    NOTE(NOTE-024): 用 must_exist=False —— 回傳的是「**將會**使用的路徑」，
    檔案還不存在也照樣回答。全新機器上該檔本來就不存在，若這裡抛例外，
    _should_run_schema_fix() 會退回 `return True` 而讓啟動直接失敗。
    「這顆 DB 是哪一顆」與「這顆 DB 存不存在」是兩個不同的問題。
    """
    return _resolve_db_path(must_exist=False)


def _resolve_db_path(must_exist: bool = True) -> str:
    """
    找出這支 migration 的目標 DB 路徑。

    must_exist=True（人工執行 `python fix_db_schema.py`）維持原行為：找不到就抛
    FileNotFoundError。那條路徑是人明確要求「升級某顆既有 DB」，找不到就是真的
    錯了，不該安靜地無事發生。

    must_exist=False 用於啟動流程：回傳第一順位候選路徑，讓呼叫端自己決定
    「不存在」代表什麼（見 upgrade_database() 的未初始化分支）。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "data", "roothinks.db"),
        os.path.join(base_dir, "app", "data", "roothinks.db"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    if must_exist:
        raise FileNotFoundError(f"Database file not found in: {candidates}")
    return candidates[0]


def _database_is_initialised(db_path: str) -> bool:
    """
    這顆 DB 是否已經被 `db.create_all()` 建立過。

    NOTE(NOTE-024): 判斷依據刻意是「有沒有 projects 表」，而不是「檔案在不在」
    或「檔案多大」。`sqlite3.connect()` 對不存在的路徑會**直接建出一個 0 byte
    的檔**，所以「檔案存在」完全不代表裡面有 schema —— 那正是本缺陷第二層的
    形狀（補上空 DB 檔之後，改成在 projects 表那一行爆）。
    """
    if not os.path.exists(db_path):
        return False
    try:
        with contextlib.closing(sqlite3.connect(db_path, timeout=30)) as conn:
            return _table_exists(conn.cursor(), "projects")
    except sqlite3.DatabaseError:
        # 檔案存在但不是合法 sqlite（例如被截斷）。這裡回 False 只是讓 migration
        # 讓路；真正的錯誤會在 create_all/首次查詢時如實爆出來。由那一層報告，
        # 比在 migration 裡把它改寫成別的訊息誠實。
        return False


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


def _table_exists(cursor, name: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _upgrade_users_table(cursor):
    """
    將 users.email 從 nullable 升級為 NOT NULL UNIQUE，並新增 system_role 欄位。

    email 成為唯一登入識別後，nullable 會讓「用 email 登入」失去硬保證，
    因此必須在 DB 層鎖死。SQLite 無法直接修改欄位約束，只能重建資料表。

    對既有 NULL/空 email 的帳號採防禦性補值而非直接失敗：此函式在每次應用啟動時
    執行，若拋例外會導致整個服務無法開機。正常流程應先跑
    scripts/cleanup_legacy_users.py 清掉這些帳號。
    """
    if not _table_exists(cursor, "users"):
        # 全新資料庫；db.create_all() 會直接建出正確 schema。
        return

    cursor.execute("PRAGMA table_info(users)")
    cols_info = cursor.fetchall()
    if not cols_info:
        return
    cols = {c[1]: c for c in cols_info}

    if "system_role" not in cols:
        LOGGER.info("Adding users.system_role column")
        cursor.execute(
            "ALTER TABLE users ADD COLUMN system_role VARCHAR(20) NOT NULL DEFAULT 'user'"
        )

    email_col = cols.get("email")
    if email_col is None:
        return
    # PRAGMA table_info 欄位順序: (cid, name, type, notnull, dflt_value, pk)
    if email_col[3] == 1:
        # 已是 NOT NULL，代表先前已升級過；保持冪等。
        return

    LOGGER.info("Migrating users table: email -> NOT NULL UNIQUE (lowercased)")

    # email 一律正規化為小寫，讓登入比對不區分大小寫。
    #
    # 正規化結果只在記憶體中計算，寫入時直接落到新表 —— 不可在舊表上就地 UPDATE：
    # 舊表本身帶 UNIQUE(email)，把 'A@x.com' 小寫成 'a@x.com' 時會撞到既有的
    # 'a@x.com' 那一列而拋 IntegrityError。
    rows = cursor.execute("SELECT id, username, email FROM users").fetchall()

    # 衝突時的取捨順序：原本就有真實 email 的帳號優先保住該 email，
    # 其次才輪到補值帳號；同組內由 id 小者（較早註冊）勝出。
    def _sort_key(row):
        uid, _uname, mail = row
        is_placeholder = 0 if (mail or "").strip() else 1
        return (is_placeholder, uid)

    resolved: dict[int, str] = {}
    seen: dict[str, int] = {}
    for uid, uname, mail in sorted(rows, key=_sort_key):
        original = mail or ""
        norm = original.strip().lower()
        if not norm:
            fallback = (str(uname or "").strip().lower() or f"user{uid}")
            norm = f"{fallback}@invalid.local"
            LOGGER.warning(
                "users.id=%s 無 email，補為佔位值 %s；該帳號需重新設定 email 才能登入",
                uid, norm,
            )
        if norm in seen:
            conflict_id = seen[norm]
            norm = f"dup{uid}.{norm}"
            LOGGER.warning(
                "users.id=%s email 與 id=%s 衝突，改寫為 %s", uid, conflict_id, norm
            )
        seen[norm] = uid
        resolved[uid] = norm

    cursor.execute(
        """
        CREATE TABLE users_new (
            id INTEGER NOT NULL,
            username VARCHAR(32) NOT NULL,
            email VARCHAR(254) NOT NULL,
            password_hash VARCHAR(256) NOT NULL,
            is_active BOOLEAN NOT NULL,
            system_role VARCHAR(20) NOT NULL DEFAULT 'user',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    # 逐列搬移，email 用上面算好的正規化值覆蓋原值。
    source = cursor.execute(
        "SELECT id, username, password_hash, is_active, "
        "       COALESCE(system_role, 'user'), created_at, updated_at "
        "FROM users"
    ).fetchall()
    cursor.executemany(
        "INSERT INTO users_new "
        "(id, username, email, password_hash, is_active, system_role, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (uid, uname, resolved[uid], pw, active, srole, created, updated)
            for (uid, uname, pw, active, srole, created, updated) in source
        ],
    )
    cursor.execute("DROP TABLE users")
    cursor.execute("ALTER TABLE users_new RENAME TO users")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    LOGGER.info("users table migration finished (%s rows)", len(rows))


def upgrade_database():
    db_path = _resolve_db_path(must_exist=False)

    # NOTE(NOTE-024): 全新安裝 —— 這顆 DB 還沒被 db.create_all() 建立過。
    # 升級器對它無事可做（沒有 legacy schema 需要被升級），把建置讓給
    # create_all()（app/__init__.py，本函式之後才執行）。
    # 這道判斷放在鎖**之內**：多個 gunicorn worker 同時開機時，
    # 「檢查」與「升級」必須是同一段臨界區，否則 B 可能對 A 正在建到一半的
    # schema 跑升級。
    if not _database_is_initialised(db_path):
        LOGGER.info(
            "Schema upgrade skipped: %s is not initialised yet; "
            "db.create_all() will bootstrap it",
            db_path,
        )
        return

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
                    name VARCHAR(300) NOT NULL,
                    abbreviation VARCHAR(50),
                    research_title VARCHAR(300),
                    status VARCHAR(20) NOT NULL DEFAULT 'temp',
                    classification VARCHAR(200),
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

        # 這裡的型別必須與上面 CREATE TABLE projects_new 及 app/models.py 的
        # Project 宣告三方一致。散在三處是既有設計，test_project_schema_lengths.py
        # 有一道測試會比對，改一處沒改另一處會紅。
        column_defs = {
            "ai_summary": "TEXT",
            "abbreviation": "VARCHAR(50)",
            "classification": "VARCHAR(200)",
            "research_title": "VARCHAR(300)",
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

        # [email-auth] users.email 升級為 NOT NULL UNIQUE + 新增 system_role。
        _upgrade_users_table(cursor)

        # [comment-versioning] 留言綁定版本。
        _upgrade_chapter_comments_table(cursor)

        conn.commit()

    LOGGER.info("Schema upgrade finished.")


def _upgrade_chapter_comments_table(cursor):
    """
    [comment-versioning] 讓留言帶上「當時被評論的版本」。

    NOTE(NOTE-009) 純增量：只 ADD COLUMN，不重建資料表，因此既有留言一列都不會動。
    舊留言的 s_ver / g_ver 保持 NULL —— 我們無從得知它當時針對哪一版，硬把它
    回填成目前版本等於偽造證據，讓使用者以為那句意見是在說現在這一版。
    NULL 由 API 標成 legacy_unversioned，前端另立「未標版本」區塊呈現。

    scope 有 DEFAULT 'section'：既有留言全部來自 2B 章節留言側欄，這個預設值
    對它們是正確的，不是隨便填的佔位。

    型別必須與 app/models.py 的 ChapterComment 宣告一致（同 projects 的三方一致
    要求，見上方 column_defs 附近的說明）。
    """
    cursor.execute("PRAGMA table_info(chapter_comments)")
    existing = {row[1] for row in cursor.fetchall()}
    if not existing:
        # 資料表還沒建立（全新環境）；db.create_all() 會依 models.py 直接建出新結構。
        return

    column_defs = {
        "scope": "VARCHAR(10) NOT NULL DEFAULT 'section'",
        "s_ver": "VARCHAR(20)",
        "g_ver": "VARCHAR(20)",
    }
    for column_name, column_type in column_defs.items():
        if column_name not in existing:
            LOGGER.info("Adding missing chapter_comments column: %s", column_name)
            cursor.execute(
                f"ALTER TABLE chapter_comments ADD COLUMN {column_name} {column_type}"
            )

    for index_sql in (
        "CREATE INDEX IF NOT EXISTS ix_chapter_comments_scope ON chapter_comments (scope)",
        "CREATE INDEX IF NOT EXISTS ix_chapter_comments_s_ver ON chapter_comments (s_ver)",
        "CREATE INDEX IF NOT EXISTS ix_chapter_comments_g_ver ON chapter_comments (g_ver)",
    ):
        cursor.execute(index_sql)


def fix_schema():
    """
    啟動期入口。既有 DB 走冪等升級；全新 DB 直接讓路（見 NOTE-024）。
    """
    db_path = _resolve_db_path(must_exist=False)

    # NOTE(NOTE-024): DB 檔根本不存在時提早回傳，連鎖都不取 —— `_schema_lock()`
    # 會 makedirs 鎖檔所在目錄，在這裡取鎖等於讓「只是檢查一下」產生副作用。
    if not os.path.exists(db_path):
        LOGGER.info(
            "Schema upgrade skipped: %s does not exist yet (fresh install)",
            db_path,
        )
        return

    lock_path = os.path.join(os.path.dirname(db_path), ".schema_fix.lock")
    with _schema_lock(lock_path):
        upgrade_database()


if __name__ == "__main__":
    # 人工執行：目標 DB 必須真的存在。這條路徑是人明確要求升級某顆既有 DB，
    # 找不到就是真的錯了 —— 不套用 NOTE-024 的「讓路」語意。
    logging.basicConfig(level=logging.INFO)
    _resolve_db_path(must_exist=True)
    fix_schema()
