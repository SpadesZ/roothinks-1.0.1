# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_fresh_bootstrap.py
# 子系統定位:
#   全新機器啟動路徑的護欄。守 NOTE-024：schema migration 只升級「已初始化」
#   的 DB，尚未初始化的 DB 交給 db.create_all() 建置。
# 主要責任:
#   1. DB 檔不存在時 fix_schema() 讓路，且**不取鎖、不建目錄**（檢查不得有副作用）。
#   2. DB 檔存在但沒有 projects 表（0 byte 空檔）時同樣讓路，不再抛
#      RuntimeError("Missing 'projects' table")。
#   3. **對照組**：既有 legacy DB 仍然真的被升級。少了這一條，把 migration
#      整支關掉也會讓上面兩條全綠 —— 那正是「綠得沒有意義」。
# 明確不負責:
#   - 不驗 migration 改了哪些欄位的細節（見 fix_db_schema.py 既有測試）。
#   - 不驗「乾淨 checkout 直接 create_app() 會開機」。那需要一份沒有 data/ 的
#     真實 checkout，而 _resolve_db_path() 是相對原始碼位置解析的，在本 repo
#     內無法用 monkeypatch 忠實模擬。那一項由 scripts/verify_fresh_bootstrap.py
#     在 detached worktree 上實跑，證據記在 docs/HANDOFF.md。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只在 tmp_path 下建立 sqlite 檔與鎖檔。**不開啟、不建立真實 data/roothinks.db**
#   —— 每個會碰到路徑的測試都自行 monkeypatch _resolve_db_path。
# ACL/安全邊界:
#   本檔不涉及授權。但它守的是一條破壞性 migration 的觸發條件，
#   放寬判斷等於讓升級器對半建好的 schema 動手。
# 不變量:
#   - 「讓路」只能是 return，永遠不得變成建表、覆寫或清空。
#   - 判斷依據是「有沒有 projects 表」，不是「檔案在不在」或「檔案多大」：
#     sqlite3.connect() 對不存在的路徑會直接建出 0 byte 檔案。
# 相關 NOTE:
#   NOTE-024（本檔主題）、NOTE-016（migration 的範圍判斷，另有專屬測試）。
# 驗證:
#   python -m pytest test/unit/test_fresh_bootstrap.py -q
# ---------------------------------------------------------------------------
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

import fix_db_schema


def _legacy_projects_db(path: Path) -> None:
    """
    造一顆「已初始化但是舊 schema」的 DB：有 projects 表，但缺 ORM 需要的
    數值主鍵 id。upgrade_database() 對這種 DB 必須真的動手。
    """
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE projects ("
            "  project_id TEXT PRIMARY KEY,"
            "  name TEXT,"
            "  status TEXT"
            ")"
        )
        conn.execute(
            "INSERT INTO projects (project_id, name, status) VALUES (?, ?, ?)",
            ("LEGACY1-p", "legacy row", "formal"),
        )


class TestUninitialisedDatabaseIsNotAnError:
    def test_target_path_is_answerable_before_the_file_exists(self, monkeypatch):
        """
        target_db_path() 不得因為檔案還不存在就抛例外。

        它抛例外時，_should_run_schema_fix() 會退回 `return True`，
        於是全新機器必然走進「開不起來」那條路徑 —— 這是缺陷第一層的成因。
        """
        monkeypatch.setattr(fix_db_schema.os.path, "exists", lambda _p: False)

        resolved = fix_db_schema.target_db_path()

        assert resolved.endswith(os.path.join("data", "roothinks.db"))

    def test_must_exist_still_raises_for_manual_runs(self, monkeypatch):
        """
        對照組：人工執行 `python fix_db_schema.py` 那條路徑不受影響。
        少了它，上面那個測試無法分辨「放寬了正確的那一半」與「整個檢查被拿掉」。
        """
        monkeypatch.setattr(fix_db_schema.os.path, "exists", lambda _p: False)

        with pytest.raises(FileNotFoundError):
            fix_db_schema._resolve_db_path(must_exist=True)

    def test_absent_db_skips_without_taking_the_lock(self, tmp_path, monkeypatch):
        """
        DB 檔不存在 → 讓路，而且不得留下任何副作用。

        鎖檔是最好的探針：_schema_lock() 會 makedirs 鎖檔所在目錄並建立檔案，
        所以「鎖檔不存在」證明的是「我們根本沒走到取鎖那一步」，
        比只斷言「沒有抛例外」強。
        """
        db_path = tmp_path / "nested" / "roothinks.db"
        monkeypatch.setattr(
            fix_db_schema, "_resolve_db_path", lambda must_exist=True: str(db_path)
        )

        fix_db_schema.fix_schema()

        assert not db_path.exists(), "讓路的分支不得建出 DB 檔"
        assert not (tmp_path / "nested").exists(), (
            "只是檢查一下就建了目錄 —— 取鎖的副作用漏出來了"
        )

    def test_empty_db_file_does_not_raise_missing_projects(self, tmp_path, monkeypatch):
        """
        缺陷第二層：補上空 DB 檔之後改成在 projects 表那一行爆。

        sqlite3.connect() 對不存在的路徑會直接建出 0 byte 檔，所以這個狀態
        在真實部署裡非常容易出現（任何一次探測性連線都會造成）。
        """
        db_path = tmp_path / "roothinks.db"
        sqlite3.connect(db_path).close()
        assert db_path.exists()
        monkeypatch.setattr(
            fix_db_schema, "_resolve_db_path", lambda must_exist=True: str(db_path)
        )

        fix_db_schema.fix_schema()  # 不得抛 RuntimeError

        with sqlite3.connect(db_path) as conn:
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        assert tables == 0, "讓路的分支不得自己建表（建表只能有一個來源：models.py）"


class TestExistingDatabaseStillUpgraded:
    """
    對照組，比上面整組都重要。

    上面驗的全是「不再爆炸」。把 upgrade_database() 整支改成 `return` 也會讓
    它們全綠 —— 那等於把既有資料庫的升級靜默關掉，是遠比原缺陷嚴重的回歸。
    """

    def test_legacy_projects_table_is_migrated(self, tmp_path, monkeypatch):
        db_path = tmp_path / "roothinks.db"
        _legacy_projects_db(db_path)
        monkeypatch.setattr(
            fix_db_schema, "_resolve_db_path", lambda must_exist=True: str(db_path)
        )

        with sqlite3.connect(db_path) as conn:
            before = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        assert "id" not in before, "前提沒成立：這顆 DB 本來就有 id，測不到升級"

        fix_db_schema.fix_schema()

        with sqlite3.connect(db_path) as conn:
            after = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
            rows = conn.execute(
                "SELECT project_id, name FROM projects"
            ).fetchall()

        assert "id" in after, "既有 DB 沒有被升級 —— migration 被誤關掉了"
        assert ("LEGACY1-p", "legacy row") in rows, (
            "升級把既有資料弄丟了；本改動不得重建、覆寫或清空既有 DB"
        )

    def test_initialised_probe_matches_the_projects_table(self, tmp_path):
        """
        _database_is_initialised() 的判準本身。
        用檔案大小或存在與否當判準時，這兩個 assert 會有一個是紅的。
        """
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).close()
        legacy = tmp_path / "legacy.db"
        _legacy_projects_db(legacy)

        assert fix_db_schema._database_is_initialised(str(empty)) is False
        assert fix_db_schema._database_is_initialised(str(legacy)) is True
        assert fix_db_schema._database_is_initialised(str(tmp_path / "nope.db")) is False
