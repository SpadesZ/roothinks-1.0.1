# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_schema_fix_scope.py
# 子系統定位:
#   啟動期 schema migration 的作用範圍護欄。
# 主要責任:
#   1. create_app() 只在「設定的 DB」就是「migration 的目標 DB」時才跑 migration。
#   2. **有自行指定 DB 的測試**不得碰到 repo 的正式 data/roothinks.db。
# 範圍限制（不要誤讀成「跑測試絕不碰正式 DB」）:
#   test/integration_smoke/* 是 create_app({"TESTING": True})，完全沒有覆寫
#   SQLALCHEMY_DATABASE_URI 與 BINDS，因此它們**設定的就是正式 DB**，本判斷會
#   正確地放行 migration。那 26 個測試仍會連上 data/roothinks.db、manu_core.db
#   與 data/sys/llm_match.db。要不要改是產品決策：把它們指向空的 tmp DB 之後
#   路由可能照樣回 200，變成「通過但覆蓋範圍縮水」。見 docs/HANDOFF.md §3.10。
# 明確不負責:
#   - 不驗 migration 本身改了哪些欄位（見 fix_db_schema.py 與其既有測試）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只在 tmp_path 建立假 DB 檔。**本檔會讀取（不寫入）真實 data/roothinks.db 的 mtime**，
#   那正是 test_real_db_is_untouched_by_create_app 要保護的對象。
# 不變量:
#   - 這是一組 A/B：目標相符時必須跑、不相符時必須不跑。少了任何一邊，
#     這個測試都可能因為錯誤的理由是綠的（例如 migration 根本沒被接上）。
# 相關 NOTE:
#   NOTE-016（schema migration 只在目標 DB 就是本次要用的 DB 時執行，含範圍限制）。
# 相關背景:
#   fix_db_schema.target_db_path() 是相對該檔位置寫死的 repo/data/roothinks.db，
#   完全不看 SQLALCHEMY_DATABASE_URI。在加上這道判斷之前，每一個呼叫 create_app()
#   的測試都會對正式資料庫跑一次含 DROP TABLE／重建表的 migration
#   ——全套跑一次 700 多次。
# 驗證:
#   python -m pytest test/unit/test_schema_fix_scope.py -q
# ---------------------------------------------------------------------------
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

REAL_DB = PROJECT_ROOT / "data" / "roothinks.db"


@pytest.fixture(autouse=True)
def _dev_env(monkeypatch, tmp_path):
    """與其他測試相同的啟動環境；非 development 會要求 SECRET_KEY。"""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))


def _make_app(tmp_path, db_file):
    from app import create_app

    return create_app({
        "TESTING": True,
        "AUTH_MODE": "none",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'sfs_manu.db'}"},
        "SERVER_NAME": None,
    })


@pytest.fixture()
def spy(monkeypatch):
    """攔住真正的 migration，只記錄它有沒有被呼叫。"""
    import fix_db_schema

    calls = []
    monkeypatch.setattr(fix_db_schema, "fix_schema", lambda *a, **k: calls.append(1))
    return calls


class TestSchemaFixScope:
    def test_runs_when_configured_db_is_the_migration_target(self, tmp_path, monkeypatch, spy):
        """
        對照組。少了它，下面那個「不該跑」的測試可能只是因為 migration
        根本沒被接上 create_app 而通過 —— 那樣就完全測不到範圍判斷。
        """
        import fix_db_schema

        fake_target = tmp_path / "roothinks.db"
        fake_target.write_bytes(b"")
        monkeypatch.setattr(fix_db_schema, "target_db_path", lambda: str(fake_target))

        _make_app(tmp_path, fake_target)
        assert spy, "設定的 DB 就是 migration 目標，卻沒有跑 migration"

    def test_skipped_when_configured_db_differs(self, tmp_path, monkeypatch, spy):
        """主斷言：測試把 DB 指到 tmp 時，不得對別的資料庫動手。"""
        import fix_db_schema

        fake_target = tmp_path / "roothinks.db"
        fake_target.write_bytes(b"")
        monkeypatch.setattr(fix_db_schema, "target_db_path", lambda: str(fake_target))

        _make_app(tmp_path, tmp_path / "somewhere_else.db")
        assert not spy, "設定的 DB 不是 migration 目標，卻仍然跑了破壞性 migration"

    def test_still_runs_when_target_cannot_be_resolved(self, tmp_path, monkeypatch, spy):
        """
        target_db_path() 抛例外時維持原行為（照跑，讓既有的錯誤處理接手）。

        這道範圍判斷的目的是「不要動到別人的資料庫」，不是「順便把找不到 DB
        的啟動失敗吞掉」—— 那條路徑刻意要保持會爆。
        """
        import fix_db_schema

        def _boom():
            raise FileNotFoundError("no db")

        monkeypatch.setattr(fix_db_schema, "target_db_path", _boom)
        _make_app(tmp_path, tmp_path / "any.db")
        assert spy, "解析不到目標時不該靜默跳過 migration"


class TestRealDatabaseIsNotTouched:
    @pytest.mark.skipif(not REAL_DB.exists(), reason="這台機器沒有正式 data/roothinks.db")
    def test_real_db_is_untouched_by_create_app(self, tmp_path):
        """
        牙齒測試：直接看正式資料庫的 mtime。

        上面兩個測試驗的是判斷邏輯，這一個驗的是使用者真正在意的結果 ——
        「跑測試不會動到我的資料庫」。刻意不 mock 任何東西。
        """
        before = REAL_DB.stat().st_mtime_ns
        _make_app(tmp_path, tmp_path / "isolated.db")
        after = REAL_DB.stat().st_mtime_ns

        assert before == after, (
            f"create_app() 動到了正式資料庫 {REAL_DB} —— "
            "migration 的範圍判斷失效了"
        )
