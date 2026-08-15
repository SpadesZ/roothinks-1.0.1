# Roothinks source maintenance contract
# 檔案路徑: scripts/verify_fresh_bootstrap.py
# 模組定位:
#   全新機器啟動的端對端驗收器。守 NOTE-024。
# 主要責任:
#   在一份**沒有 data/、沒有任何 SQLite 檔**的乾淨 checkout 上，實際跑 create_app()
#   四次，證明：首次啟動成功、二次啟動冪等、建立的資料重啟後仍在、三顆 DB
#   quick_check=ok。
# 為什麼需要這支而不是只寫 pytest:
#   fix_db_schema._resolve_db_path() 是**相對原始碼位置**解析的，不看設定。
#   在本 repo 內跑的測試無論怎麼 monkeypatch，都無法忠實模擬「這份 checkout
#   自己沒有 data/」的狀態 —— 一 monkeypatch 就變成在驗 mock。
#   所以「乾淨 checkout 能不能開機」這一項只能在真正乾淨的 checkout 上跑。
#   對應的單元層護欄在 test/unit/test_fresh_bootstrap.py。
# 上游呼叫者:
#   人工／CI。不被應用程式碼 import。
# 讀寫或持久化位置:
#   **只寫自己所在的那份 checkout**（cwd 推導，無任何寫死路徑）。
#   刻意設計成「必須在拋棄式 worktree 上跑」：它會在該 checkout 建出
#   data/roothinks.db 等檔案並塞入探針資料。
# ACL/安全邊界:
#   會建立測試帳號與測試專案。**絕對不要在正式 repo 或掛載正式 data/ 的環境跑。**
#   啟動時會自我保護：偵測到 data/roothinks.db 已存在就直接拒絕執行，
#   避免有人在正式 checkout 上誤觸而污染真實資料。
# 不變量:
#   - 探針 PID 固定為 BOOTP1-p（-p 結尾同時順帶驗證含連字號的 PID 可寫入）。
#   - 不刪除任何東西：清理由呼叫端丟棄整份 worktree 完成。
# 相關 NOTE:
#   NOTE-024（schema migration 只升級已初始化的 DB）。
# 驗證:
#   git worktree add --detach <tmp> HEAD && cd <tmp> && py -3.10 scripts/verify_fresh_bootstrap.py
#   期望：四項全 PASS，結束碼 0。
# ---------------------------------------------------------------------------
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

BOOT_SNIPPET = """
import sys
from app import create_app
create_app()
sys.stdout.write("BOOT_OK\\n")
"""

SEED_SNIPPET = """
import sys
from app import create_app, db
from app.models import User, Project
app = create_app()
with app.app_context():
    u = User(username="bootstrap_probe", email="bootstrap_probe@preview.local")
    if hasattr(u, "set_password"):
        u.set_password("bootstrap-probe-only")
    db.session.add(u)
    db.session.add(Project(project_id="BOOTP1-p",
                           name="bootstrap probe project",
                           status="formal"))
    db.session.commit()
sys.stdout.write("SEED_OK\\n")
"""

READBACK_SNIPPET = """
import sys
from app import create_app
from app.models import User, Project
app = create_app()
with app.app_context():
    u = User.query.filter_by(username="bootstrap_probe").first()
    p = Project.query.filter_by(project_id="BOOTP1-p").first()
    sys.stdout.write("READBACK user=%s project=%s\\n" % (bool(u), bool(p)))
"""


def _run(label, snippet):
    env = dict(os.environ)
    env.setdefault("FLASK_ENV", "development")
    env.setdefault("SOCKETIO_ASYNC_MODE", "threading")
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    marker = " ".join(
        l for l in proc.stdout.splitlines() if "_OK" in l or "READBACK" in l
    )
    print(f"[{label}] rc={proc.returncode} {marker}")
    if proc.returncode != 0:
        print("       " + "\n       ".join(proc.stderr.strip().splitlines()[-6:]))
    return proc.returncode == 0, marker


def _table_count(path):
    if not os.path.exists(path):
        return None
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]


def _quick_check(path):
    if not os.path.exists(path):
        return "MISSING"
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA quick_check").fetchone()[0]


def main():
    main_db = os.path.join(ROOT, "data", "roothinks.db")

    # 自我保護：這支會建立測試帳號與測試專案，不得在有真實資料的 checkout 上跑。
    if os.path.exists(main_db):
        print(f"REFUSING: {main_db} 已存在。這支只能在乾淨的拋棄式 worktree 上跑。")
        return 2

    results = []

    ok1, _ = _run("boot-1 empty checkout", BOOT_SNIPPET)
    results.append(("1. 空目錄第一次啟動成功", ok1))

    tables_1 = _table_count(main_db)
    ok2, _ = _run("boot-2 idempotent", BOOT_SNIPPET)
    tables_2 = _table_count(main_db)
    print(f"       tables: boot1={tables_1} boot2={tables_2}")
    results.append(("2. 第二次啟動成功且 schema 不重複", ok2 and tables_1 == tables_2))

    ok_seed, _ = _run("seed user+project", SEED_SNIPPET)
    ok_read, line = _run("restart + readback", READBACK_SNIPPET)
    results.append((
        "3. 建帳號/建 project/重啟後資料仍在",
        ok_seed and ok_read and "user=True project=True" in line,
    ))

    dbs = {
        "roothinks.db": main_db,
        "manu_core.db": os.path.join(ROOT, "data", "manu_core.db"),
        "sys/llm_match.db": os.path.join(ROOT, "data", "sys", "llm_match.db"),
    }
    checks = {name: _quick_check(path) for name, path in dbs.items()}
    for name, value in checks.items():
        print(f"       quick_check {name}: {value}")
    results.append((
        "4. 三顆 SQLite quick_check=ok",
        all(v == "ok" for v in checks.values()),
    ))

    print("\n=== P0-5 fresh bootstrap ===")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
