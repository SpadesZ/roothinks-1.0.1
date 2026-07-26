# 檔案路徑: test/unit/test_email_auth.py
# 產生時間: 2026-07-26 00:50 +08:00
# 版本: v1.0
# 模組定位:
#   email 唯一登入識別改造的單元測試。
# 主要責任:
#   1. 驗證 email 格式錯誤時回傳產品指定字串「請使用mail格式註冊」。
#   2. 驗證 email 正規化（小寫）與登入不區分大小寫。
#   3. 驗證 username 由 email 前綴自動產生，且撞名時自動加尾碼。
#   4. 驗證 system_role 預設為 'user'。
#   5. 驗證 fix_db_schema 對既有資料庫的升級（NOT NULL + 補值 + 去重 + 冪等）。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   使用 in-memory SQLite；每個 fixture 獨立，不碰真實 data/roothinks.db。
# 安全邊界:
#   - schema 測試在 tmp_path 建立臨時 DB，絕不指向 repo 內的 data/ 目錄。
# 維護提醒:
#   - EMAIL_FORMAT_ERROR 是產品指定文案，測試以字面值斷言；
#     若產品要改字串，前端 login.html / register.html 的 MSG 也必須同步。
# 驗證方式:
#   python -m pytest test/unit/test_email_auth.py -q
# ------------------------------------------------------------------------------
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

EXPECTED_FORMAT_ERROR = "請使用mail格式註冊"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_session(monkeypatch):
    """AUTH_MODE=session app fixture；CSRF 關閉方便測試。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)

    db_uri = "sqlite:///:memory:"
    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "AUTH_MODE": "session",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": db_uri,
            "SQLALCHEMY_BINDS": {"manuscript": db_uri},
            "SERVER_NAME": None,
        }
    )
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

    with app.app_context():
        from app import db
        db.create_all()
        yield app


@pytest.fixture
def client(app_session):
    return app_session.test_client()


def _register(client, email, password="password123"):
    return client.post(
        "/auth/register",
        data={"email": email, "password": password, "confirm_password": password},
        follow_redirects=True,
    )


def _login_form(client, email, password="password123"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def _login_api(client, email, password="password123"):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        content_type="application/json",
    )


def _logout(client):
    return client.post("/api/auth/logout", content_type="application/json")


# ---------------------------------------------------------------------------
# Test: email 格式驗證
# ---------------------------------------------------------------------------


class TestEmailFormatValidation:
    # 涵蓋使用者最可能誤打的形態：純帳號、缺 TLD、缺 local、多個 @、含空白。
    BAD_EMAILS = [
        "notanemail",
        "rickie",
        "rickie@",
        "@example.com",
        "rickie@example",
        "rickie@@example.com",
        "rickie kuo@example.com",
        "",
        "   ",
    ]

    @pytest.mark.parametrize("bad_email", BAD_EMAILS)
    def test_register_rejects_bad_format(self, client, bad_email):
        """註冊時 email 格式不符 → 頁面顯示產品指定字串。"""
        resp = _register(client, bad_email)
        assert resp.status_code == 200
        assert EXPECTED_FORMAT_ERROR in resp.data.decode("utf-8")

    @pytest.mark.parametrize("bad_email", BAD_EMAILS)
    def test_login_rejects_bad_format(self, client, bad_email):
        """登入時 email 格式不符 → 同一句提示。"""
        resp = _login_form(client, bad_email)
        assert resp.status_code == 200
        assert EXPECTED_FORMAT_ERROR in resp.data.decode("utf-8")

    @pytest.mark.parametrize("bad_email", ["notanemail", "rickie@", "@example.com"])
    def test_api_login_rejects_bad_format(self, client, bad_email):
        """JSON API 格式不符回 400 且 message 為產品指定字串。"""
        resp = _login_api(client, bad_email)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_email_format"
        assert data["message"] == EXPECTED_FORMAT_ERROR

    @pytest.mark.parametrize(
        "good_email",
        [
            "rickiekuo1203@gmail.com",
            "first.last@sub.domain.co.uk",
            "user+tag@example.org",
            "a_b-c%d@example.io",
        ],
    )
    def test_register_accepts_valid_format(self, client, good_email):
        """合法 email 應可成功註冊（不出現格式錯誤）。"""
        resp = _register(client, good_email)
        assert resp.status_code == 200
        assert EXPECTED_FORMAT_ERROR not in resp.data.decode("utf-8")


# ---------------------------------------------------------------------------
# Test: email 正規化與大小寫
# ---------------------------------------------------------------------------


class TestEmailNormalization:
    def test_email_stored_lowercase(self, client, app_session):
        """大寫 email 註冊後應以小寫儲存。"""
        _register(client, "Rickie.KUO@Example.COM")
        with app_session.app_context():
            from app.models import User
            user = User.query.first()
            assert user is not None
            assert user.email == "rickie.kuo@example.com"

    def test_login_is_case_insensitive(self, client):
        """以不同大小寫登入應成功。"""
        _register(client, "rickie@example.com")
        _logout(client)

        resp = _login_api(client, "RICKIE@EXAMPLE.COM")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_duplicate_email_different_case_rejected(self, client, app_session):
        """同一 email 換大小寫再註冊應被擋，避免產生兩個帳號。"""
        _register(client, "dup@example.com")
        _logout(client)

        resp = _register(client, "DUP@Example.com")
        assert "已被使用" in resp.data.decode("utf-8")

        with app_session.app_context():
            from app.models import User
            assert User.query.count() == 1


# ---------------------------------------------------------------------------
# Test: username 自動產生
# ---------------------------------------------------------------------------


class TestUsernameDerivation:
    def test_username_from_email_local_part(self, client, app_session):
        """username 取 email 前綴。"""
        _register(client, "rickiekuo@example.com")
        with app_session.app_context():
            from app.models import User
            assert User.query.first().username == "rickiekuo"

    def test_username_collision_gets_suffix(self, client, app_session):
        """不同網域但相同前綴 → 第二個帳號的顯示名自動加尾碼。"""
        _register(client, "rickie@aaa.com")
        _logout(client)
        _register(client, "rickie@bbb.com")

        with app_session.app_context():
            from app.models import User
            names = sorted(u.username for u in User.query.all())
            assert names == ["rickie", "rickie2"]

    def test_username_strips_illegal_chars(self, client, app_session):
        """前綴中的非法字元（如 . 與 %）會被濾除。"""
        _register(client, "first.last%tag@example.com")
        with app_session.app_context():
            from app.models import User
            uname = User.query.first().username
            assert uname == "firstlasttag"

    def test_username_padded_when_too_short(self, client, app_session):
        """前綴過短時補足到最小長度，避免違反顯示名長度下限。"""
        _register(client, "a@example.com")
        with app_session.app_context():
            from app.models import User
            assert User.query.first().username == "auu"

    def test_username_truncated_to_max_len(self, client, app_session):
        """超長前綴截斷至 32 字。"""
        long_local = "x" * 60
        _register(client, f"{long_local}@example.com")
        with app_session.app_context():
            from app.models import User
            assert len(User.query.first().username) == 32

    def test_derive_username_all_illegal_local_part(self, app_session):
        """前綴全為非法字元時退回 'user' 基底，不得產生空 username。"""
        with app_session.app_context():
            from app.models import User
            assert User.derive_username("....@example.com") == "user"


# ---------------------------------------------------------------------------
# Test: system_role
# ---------------------------------------------------------------------------


class TestSystemRole:
    def test_default_system_role_is_user(self, client, app_session):
        _register(client, "plain@example.com")
        with app_session.app_context():
            from app.models import User
            user = User.query.first()
            assert user.system_role == "user"
            assert user.is_mentor is False

    def test_mentor_flag(self, app_session):
        with app_session.app_context():
            from app import db
            from app.models import User, SYSTEM_ROLE_MENTOR

            user = User(email="m@example.com", username="m", system_role=SYSTEM_ROLE_MENTOR)
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()
            assert user.is_mentor is True

    def test_to_dict_excludes_password_hash(self, client, app_session):
        _register(client, "safe@example.com")
        with app_session.app_context():
            from app.models import User
            payload = User.query.first().to_dict()
            assert "password_hash" not in payload
            assert payload["email"] == "safe@example.com"
            assert payload["system_role"] == "user"


# ---------------------------------------------------------------------------
# Test: fix_db_schema users 表升級
# ---------------------------------------------------------------------------


def _make_legacy_db(path: Path, rows):
    """建立一個 email 為 nullable 的舊版 users 表，並塞入 rows。"""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER NOT NULL,
            username VARCHAR(32) NOT NULL,
            email VARCHAR(254),
            password_hash VARCHAR(256) NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (email)
        )
        """
    )
    conn.executemany(
        "INSERT INTO users (id, username, email, password_hash, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, 'hash', 1, '2026-01-01', '2026-01-01')",
        rows,
    )
    conn.commit()
    conn.close()


class TestSchemaUpgrade:
    def test_upgrade_makes_email_not_null_and_backfills(self, tmp_path):
        """NULL / 空 email 補佔位值；大小寫重複自動去重；不得拋例外。"""
        import fix_db_schema

        db_path = tmp_path / "legacy.db"
        _make_legacy_db(
            db_path,
            [
                (1, "real", "Real@Example.com"),
                (2, "nomail", None),
                (3, "emptymail", ""),
                (4, "dupmail", "real@example.com"),
            ],
        )

        conn = sqlite3.connect(db_path)
        try:
            fix_db_schema._upgrade_users_table(conn.cursor())
            conn.commit()

            rows = dict(conn.execute("SELECT id, email FROM users").fetchall())
            # 全部 4 筆都存活，沒有任何帳號因升級而消失。
            assert len(rows) == 4
            # id=1 原本就有真實 email，正規化為小寫並保住該位址。
            assert rows[1] == "real@example.com"
            # 無 email 者補 <username>@invalid.local。
            assert rows[2] == "nomail@invalid.local"
            assert rows[3] == "emptymail@invalid.local"
            # 與 id=1 衝突者加 dup 前綴，確保 UNIQUE 成立。
            assert rows[4] == "dup4.real@example.com"

            email_col = [c for c in conn.execute("PRAGMA table_info(users)") if c[1] == "email"][0]
            assert email_col[3] == 1, "email 欄位應為 NOT NULL"

            cols = {c[1] for c in conn.execute("PRAGMA table_info(users)")}
            assert "system_role" in cols
        finally:
            conn.close()

    def test_upgrade_is_idempotent(self, tmp_path):
        """重複執行不應再改動資料（每次應用啟動都會跑）。"""
        import fix_db_schema

        db_path = tmp_path / "legacy2.db"
        _make_legacy_db(db_path, [(1, "solo", "solo@example.com")])

        conn = sqlite3.connect(db_path)
        try:
            fix_db_schema._upgrade_users_table(conn.cursor())
            conn.commit()
            first = conn.execute("SELECT id, username, email FROM users").fetchall()

            fix_db_schema._upgrade_users_table(conn.cursor())
            conn.commit()
            second = conn.execute("SELECT id, username, email FROM users").fetchall()

            assert first == second
        finally:
            conn.close()

    def test_upgrade_skips_when_table_missing(self, tmp_path):
        """全新資料庫沒有 users 表時應安靜跳過，交給 db.create_all()。"""
        import fix_db_schema

        db_path = tmp_path / "fresh.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE placeholder (id INTEGER)")
            conn.commit()
            fix_db_schema._upgrade_users_table(conn.cursor())  # 不應拋例外
        finally:
            conn.close()
