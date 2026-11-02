# Roothinks source maintenance contract
# 目的：資料庫遷移演練（R7）。在一次性的代表性資料上，從 main 的 schema 升級到目前分支，證明：
#   1. 既有資料表與資料列完全不變（逐表雜湊）；新資料表存在；alembic 版本正確。
#   2. 升級後的應用程式可以啟動，所有只帶 pid 的 GET 路由沒有 5xx；新功能（教授邀請）可以寫入。
#   3. 冪等：再跑一次啟動升級與 alembic upgrade，資料不變。
#   4. 回滾：main 的程式可以在升級後的資料庫上啟動（多出的表被忽略）；downgrade 依設計拒絕執行；從備份還原後雜湊與升級前相同。
#   5. 沒有 alembic_version 的資料庫（只靠啟動時 create_all 建表）直接跑 `alembic upgrade head` 會失敗；正確步驟是先 stamp。
# 邊界：只在新的暫存目錄建立與修改資料庫；不讀、不寫任何正式資料；不連網；不呼叫模型。
#       「代表性」是指每一張 main 的資料表都有合成資料列（含外鍵關係），不是正式資料的副本。
# 用法：python scripts/repro/migration_rehearsal.py --main-tree <main 的 checkout> --out <報告.json>
#       （子命令 seed／startup／smoke／write 由主程序以子程序呼叫，各自載入指定 checkout 的程式碼）
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve()
THIS_TREE = HERE.parents[2]
PID, PID2 = "REHEARSE-p", "REHEARSE2-p"
PASSWORD = "Rehearsal123!"
USERS = [("owner", "user"), ("editor", "user"), ("viewer", "user"), ("coauthor", "user"), ("prof", "mentor"), ("admin", "admin")]
_ENV_DROP = ("DATABASE_URL", "SECRET_KEY", "FERNET_KEY", "APP_ENV", "AUTH_MODE", "SOCKETIO_MESSAGE_QUEUE", "API_BEARER_TOKEN",
             "API_BEARER_TOKENS", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "NLTK_DATA")


# ----------------------------------------------------------------------------- 子程序：在指定 checkout 中執行
def _boot(code_root, work, app_db, manu_db, run_schema_fix):
    """載入 code_root 的程式碼並建立 app；所有可寫入的路徑都導向 work（暫存目錄）。"""
    sys.path.insert(0, str(code_root))
    os.environ.update(FLASK_ENV="development", PYTHON_DOTENV_DISABLED="1", SOCKETIO_ASYNC_MODE="threading",
                      API_AUTH_ENABLED="0", LOCK_ROOT=str(Path(work) / "locks"),
                      LECTURE_SYSTEM_LOG_FILE=str(Path(work) / "runtime.log"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    import fix_db_schema
    if run_schema_fix:
        # 與正式啟動相同：create_app 會對「它要用的那顆 DB」跑 fix_db_schema（正式路徑寫死在 data/roothinks.db，
        # 這裡把目標換成演練用的 DB，其餘流程不變）。
        fix_db_schema._resolve_db_path = lambda must_exist=True: str(app_db)
        fix_db_schema.target_db_path = lambda: str(app_db)
    from app.llm_service.llm_model import LLMModel
    LLMModel.get_db_path = staticmethod(lambda: str(Path(work) / "llm.db"))
    from app import create_app
    app = create_app({"TESTING": True, "AUTH_MODE": "session", "API_AUTH_ENABLED": False, "WTF_CSRF_ENABLED": False,
                      "RATELIMIT_ENABLED": False, "SECRET_KEY": "rehearsal-only",
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{app_db}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"}})
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    # 有些模組在匯入時就把資料根寫死成 <checkout>/data（例如 literature_routes.DATA_ROOT）：一律改到暫存目錄，
    # 演練不得在任何 checkout 裡建立 data/。
    repo_data = os.path.abspath(os.path.join(str(code_root), "data"))
    for mod in list(sys.modules.values()):
        for attr in ("DATA_ROOT", "DATA_DIR", "BASE_DATA_DIR"):
            value = getattr(mod, attr, None)
            if isinstance(value, str) and os.path.abspath(value).startswith(repo_data):
                setattr(mod, attr, str(Path(work) / "data"))
    return app


def _filler_value(col, table, i):
    import datetime
    t = type(col.type).__name__.lower()
    if t == "json":  # JSON 欄位：真實資料是物件或陣列
        return {"synthetic": i}
    if "int" in t:
        return i + 1
    if "bool" in t:
        return bool(i % 2)
    if "float" in t or "numeric" in t:
        return i + 0.5
    if "datetime" in t:
        return datetime.datetime(2026, 10, 1, 12, i % 60)
    if t == "date":
        return datetime.date(2026, 10, 1)
    length = getattr(col.type, "length", None)
    if length is None:  # Text／JSON 欄位：給合法的 JSON 字串（真實資料的 JSON 欄位不會是任意文字）
        return json.dumps({"synthetic": i, "table": table})
    return f"{table[:8]}_{col.name[:12]}_{i}"[:length]


def cmd_seed(args):
    """用 main 的模型建立代表性資料：有意義的核心資料（帳號、專案、成員…）＋其餘每張表各 3 列合成資料。"""
    app = _boot(args.code_root, args.work, args.app_db, args.manu_db, run_schema_fix=False)
    from app import db
    from app import models as m
    with app.app_context():
        db.create_all()
        ids = {}
        for name, system_role in USERS:
            u = m.User(username=name, email=f"{name}@rehearsal.invalid", system_role=system_role)
            u.set_password(PASSWORD)
            db.session.add(u)
            db.session.flush()
            ids[name] = u.id
        for pid in (PID, PID2):
            db.session.add(m.Project(project_id=pid, name=f"演練 {pid}", status="formal"))
        for name, role in (("owner", "owner"), ("editor", "editor"), ("viewer", "viewer"), ("coauthor", "coauthor"), ("prof", "viewer")):
            db.session.add(m.WorkspaceMember(user_id=ids[name], pid=PID, role=role))
        db.session.add(m.WorkspaceMember(user_id=ids["editor"], pid=PID2, role="owner"))
        db.session.add(m.ChapterAssignment(pid=PID, section_key="introduction", user_id=ids["coauthor"]))
        db.session.add(m.MentorLink(mentor_id=ids["prof"], mentee_id=ids["owner"]))
        db.session.add(m.Paper(paper_id="paper_a", pid=PID, title="演練論文 A"))
        db.session.add(m.Paper(paper_id="paper_b", pid=PID, title="演練論文 B"))
        db.session.commit()
        # Manuscript 資料庫：預設章節（與正式專案初始化相同）
        from app.core_pro.manuscript.model_section import ManuSectionConfig
        ManuSectionConfig.init_default_sections(PID)
        db.session.commit()
        # 其餘仍是空的資料表：依欄位型別各補 3 列（外鍵指向既有的列），讓「每張表都有資料」。
        filled = {}
        for bind in (None, "manuscript"):
            engine = db.engines[bind]
            existing = _tables_of(engine)
            metadata = (getattr(db, "metadatas", None) or {}).get(bind) or db.metadata  # 綁定 manuscript 的模型有自己的 metadata
            with engine.begin() as conn:
                for table in metadata.sorted_tables:
                    if table.name not in existing or conn.execute(table.select().limit(1)).first() is not None:
                        continue
                    rows = 0
                    for i in range(3):
                        values = {}
                        for col in table.columns:
                            if col.primary_key and not col.foreign_keys and "int" in type(col.type).__name__.lower():
                                continue  # 自動編號主鍵
                            if col.foreign_keys:
                                ref = next(iter(col.foreign_keys)).column
                                row = conn.execute(ref.table.select().limit(1)).first() if ref.table.name in existing else None
                                values[col.name] = row._mapping[ref.name] if row is not None else _filler_value(col, table.name, i)
                            else:
                                values[col.name] = _filler_value(col, table.name, i)
                        try:
                            conn.execute(table.insert().values(**values))
                            rows += 1
                        except Exception as exc:  # 個別約束（例如唯一鍵組合）失敗不影響其他表
                            filled.setdefault("_skipped", []).append(f"{table.name}: {type(exc).__name__}")
                            break
                    filled[table.name] = rows
    print(json.dumps({"ok": True, "users": ids, "filled": filled}))


def _tables_of(engine):
    import sqlalchemy as sa
    return set(sa.inspect(engine).get_table_names())


def cmd_startup(args):
    """模擬一次正式啟動：fix_db_schema（目標換成演練 DB）＋ create_app（create_all）。"""
    _boot(args.code_root, args.work, args.app_db, args.manu_db, run_schema_fix=True)
    print(json.dumps({"ok": True}))


def cmd_smoke(args):
    """登入 owner，呼叫所有只帶 <pid> 的 GET API 路由；回報 5xx。"""
    app = _boot(args.code_root, args.work, args.app_db, args.manu_db, run_schema_fix=False)
    client = app.test_client()
    login = client.post("/api/auth/login", json={"email": "owner@rehearsal.invalid", "password": PASSWORD})
    rules = sorted({r.rule for r in app.url_map.iter_rules() if "GET" in r.methods and set(r.arguments) <= {"pid"}
                    and (r.rule.startswith("/api/") or r.rule.startswith("/manuscript/api/")) and "debug" not in r.rule})
    errors, statuses = [], {}
    for rule in rules:
        url = rule.replace("<pid>", PID) + ("&" if "?" in rule else "?") + f"pid={PID}"
        try:
            status = client.get(url).status_code
        except Exception as exc:
            status = f"exception:{type(exc).__name__}"
        statuses[rule] = status
        if not isinstance(status, int) or status >= 500:
            errors.append({"rule": rule, "status": status})
    print(json.dumps({"ok": login.status_code == 200 and not errors, "login": login.status_code, "routes": len(rules),
                      "errors": errors, "status_counts": _count(statuses)}))


def _count(statuses):
    out = {}
    for s in statuses.values():
        out[str(s)] = out.get(str(s), 0) + 1
    return out


def cmd_write(args):
    """升級後的新功能可以寫入：owner 建立教授邀請（寫入 project_invitations）。"""
    app = _boot(args.code_root, args.work, args.app_db, args.manu_db, run_schema_fix=False)
    client = app.test_client()
    client.post("/api/auth/login", json={"email": "owner@rehearsal.invalid", "password": PASSWORD})
    resp = client.post(f"/api/collaboration/project/{PID}/invitations",
                       json={"email": "newprof@rehearsal.invalid", "kind": "professor", "role": "viewer"})
    print(json.dumps({"ok": resp.status_code in (200, 201), "status": resp.status_code}))


# ----------------------------------------------------------------------------- 主程序
def fingerprint(path):
    """每張表：欄位、列數、所有列排序後的雜湊（不含 alembic_version 與 sqlite 內部表）。"""
    con = sqlite3.connect(path)
    try:
        out = {}
        for (name,) in con.execute("select name from sqlite_master where type='table' order by name"):
            if name.startswith("sqlite_") or name == "alembic_version":
                continue
            cols = [c[1] for c in con.execute(f'pragma table_info("{name}")')]
            rows = sorted(json.dumps(r, default=str) for r in con.execute(f'select * from "{name}"'))
            out[name] = {"columns": cols, "rows": len(rows), "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest()}
        return out
    finally:
        con.close()


def alembic_version(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("select version_num from alembic_version")]
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in _ENV_DROP}
    env.update(extra or {})
    return env


def run_sub(code_root, cmd, work, app_db, manu_db):
    proc = subprocess.run([sys.executable, str(HERE), cmd, "--code-root", str(code_root), "--work", str(work),
                           "--app-db", str(app_db), "--manu-db", str(manu_db)],
                          cwd=str(work), env=_env(), capture_output=True, text=True, timeout=900)
    last = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    result = json.loads(last[-1]) if last else {"ok": False}
    result["returncode"] = proc.returncode
    if proc.returncode != 0:
        result["stderr_tail"] = proc.stderr.strip().splitlines()[-5:]
    return result


def run_alembic(code_root, work, app_db, *args):
    proc = subprocess.run([sys.executable, "-m", "alembic", "-c", str(Path(code_root) / "alembic.ini"), *args],
                          cwd=str(code_root), env=_env({"DATABASE_URL": f"sqlite:///{app_db}",
                                                        "LECTURE_SYSTEM_LOG_FILE": str(Path(work) / "alembic-runtime.log"),
                                                        "FLASK_ENV": "development", "PYTHON_DOTENV_DISABLED": "1"}),
                          capture_output=True, text=True, timeout=600)
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "tail": tail}


def diff(before, after):
    """回傳 (被改變的既有表, 新增的表, 消失的表)。"""
    changed = sorted(t for t in before if t in after and before[t] != after[t])
    return changed, sorted(set(after) - set(before)), sorted(set(before) - set(after))


def orchestrate(args):
    main_tree, new_tree = Path(args.main_tree).resolve(), THIS_TREE
    work = Path(tempfile.mkdtemp(prefix="roothinks-migration-rehearsal-"))
    report = {"main_tree_sha": _sha(main_tree), "new_tree_sha": _sha(new_tree), "work_dir": "<暫存目錄>", "scenarios": {}}

    def fresh(name, stamp):
        d = work / name
        d.mkdir()
        app_db, manu_db = d / "roothinks.db", d / "manu_core.db"
        seed = run_sub(main_tree, "seed", d, app_db, manu_db)
        st = run_alembic(main_tree, d, app_db, "stamp", "head") if stamp else None
        return d, app_db, manu_db, seed, st

    # A：alembic 已在 main 的 head（0002）。依正式順序：部署新程式（啟動）→ alembic upgrade head。
    d, app_db, manu_db, seed, st = fresh("A_stamped", stamp=True)
    base_app, base_manu = fingerprint(app_db), fingerprint(manu_db)
    backup = d / "backup"
    backup.mkdir()
    shutil.copy2(app_db, backup / app_db.name)
    shutil.copy2(manu_db, backup / manu_db.name)
    a = {"seed": seed, "main_alembic_stamp": st, "alembic_before": alembic_version(app_db),
         "tables_with_rows": sum(1 for v in base_app.values() if v["rows"]), "tables": len(base_app),
         "manuscript_tables_with_rows": sum(1 for v in base_manu.values() if v["rows"]), "manuscript_tables": len(base_manu)}
    a["startup_new"] = run_sub(new_tree, "startup", d, app_db, manu_db)
    a["alembic_upgrade"] = run_alembic(new_tree, d, app_db, "upgrade", "head")
    a["alembic_after"] = alembic_version(app_db)
    up_app, up_manu = fingerprint(app_db), fingerprint(manu_db)
    a["changed_existing_tables"], a["new_tables"], a["dropped_tables"] = diff(base_app, up_app)
    a["manuscript_changed"], a["manuscript_new"], a["manuscript_dropped"] = diff(base_manu, up_manu)
    # 冪等：再啟動一次、再 upgrade 一次
    run_sub(new_tree, "startup", d, app_db, manu_db)
    a["alembic_upgrade_again"] = run_alembic(new_tree, d, app_db, "upgrade", "head")
    a["idempotent"] = fingerprint(app_db) == up_app and fingerprint(manu_db) == up_manu
    a["smoke_new"] = run_sub(new_tree, "smoke", d, app_db, manu_db)
    a["write_new_feature"] = run_sub(new_tree, "write", d, app_db, manu_db)
    # 回滾一：不還原資料庫，直接換回 main 的程式
    a["rollback_code_only_startup_main"] = run_sub(main_tree, "startup", d, app_db, manu_db)
    a["rollback_code_only_smoke_main"] = run_sub(main_tree, "smoke", d, app_db, manu_db)
    # 回滾二：alembic downgrade 依設計拒絕；從備份還原
    a["alembic_downgrade"] = run_alembic(new_tree, d, app_db, "downgrade", "-1")
    shutil.copy2(backup / app_db.name, app_db)
    shutil.copy2(backup / manu_db.name, manu_db)
    a["restore_matches_seed"] = fingerprint(app_db) == base_app and fingerprint(manu_db) == base_manu
    report["scenarios"]["A_alembic_at_main_head"] = a

    # B：資料庫從沒用過 alembic（只靠啟動時 create_all 建表）。
    d, app_db, manu_db, seed, _ = fresh("B_unstamped", stamp=False)
    base_app = fingerprint(app_db)
    b = {"seed": seed, "alembic_before": alembic_version(app_db)}
    b["startup_new"] = run_sub(new_tree, "startup", d, app_db, manu_db)
    b["alembic_upgrade_without_stamp"] = run_alembic(new_tree, d, app_db, "upgrade", "head")
    b["after_failed_upgrade_unchanged"] = all(fingerprint(app_db).get(t) == v for t, v in base_app.items())
    b["alembic_stamp_main_head"] = run_alembic(new_tree, d, app_db, "stamp", "0002_create_evidence_segments")
    b["alembic_upgrade_after_stamp"] = run_alembic(new_tree, d, app_db, "upgrade", "head")
    b["alembic_after"] = alembic_version(app_db)
    up = fingerprint(app_db)
    b["changed_existing_tables"], b["new_tables"], b["dropped_tables"] = diff(base_app, up)
    b["smoke_new"] = run_sub(new_tree, "smoke", d, app_db, manu_db)
    report["scenarios"]["B_never_stamped"] = b

    # C：先 alembic upgrade、後啟動新程式（順序相反也要安全）。
    d, app_db, manu_db, seed, st = fresh("C_alembic_first", stamp=True)
    base_app = fingerprint(app_db)
    c = {"seed": seed, "alembic_upgrade": run_alembic(new_tree, d, app_db, "upgrade", "head")}
    c["startup_new"] = run_sub(new_tree, "startup", d, app_db, manu_db)
    c["alembic_after"] = alembic_version(app_db)
    c["changed_existing_tables"], c["new_tables"], c["dropped_tables"] = diff(base_app, fingerprint(app_db))
    c["smoke_new"] = run_sub(new_tree, "smoke", d, app_db, manu_db)
    report["scenarios"]["C_alembic_before_startup"] = c

    # 提醒：alembic.ini 的預設網址不是應用程式的資料庫
    ini = (new_tree / "alembic.ini").read_text(encoding="utf-8")
    report["alembic_ini_default_url"] = next((l.split("=", 1)[1].strip() for l in ini.splitlines()
                                              if l.strip().startswith("sqlalchemy.url")), None)
    report["app_default_db"] = "data/roothinks.db（或 DATABASE_URL）"
    report["verdict"] = _verdict(report)
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        report["work_dir"] = str(work)
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


def _verdict(r):
    a, b, c = (r["scenarios"][k] for k in ("A_alembic_at_main_head", "B_never_stamped", "C_alembic_before_startup"))
    checks = {
        "A 既有資料表不變": not a["changed_existing_tables"] and not a["dropped_tables"],
        "A 新增 3 張表": set(a["new_tables"]) >= {"academic_profiles", "project_invitations", "chapter_review_events"},
        "A alembic 到 0004": a["alembic_after"] == ["0004_project_invitations"],
        "A 冪等": a["idempotent"],
        "A 新程式無 5xx": bool(a["smoke_new"].get("ok")),
        "A 新功能可寫入": bool(a["write_new_feature"].get("ok")),
        "A 只換回 main 程式也能啟動且無 5xx": bool(a["rollback_code_only_startup_main"].get("ok"))
                                         and bool(a["rollback_code_only_smoke_main"].get("ok")),
        "A downgrade 依設計拒絕": not a["alembic_downgrade"]["ok"],
        "A 備份還原後與升級前相同": a["restore_matches_seed"],
        "B 未 stamp 直接 upgrade 會失敗（已知，需先 stamp）": not b["alembic_upgrade_without_stamp"]["ok"],
        "B 失敗後既有資料不變": b["after_failed_upgrade_unchanged"],
        "B stamp 0002 後 upgrade 成功且資料不變": b["alembic_upgrade_after_stamp"]["ok"] and not b["changed_existing_tables"],
        "C 先 alembic 後啟動也安全": c["alembic_upgrade"]["ok"] and not c["changed_existing_tables"] and bool(c["smoke_new"].get("ok")),
    }
    return {"checks": checks, "passed": sum(checks.values()), "total": len(checks)}


def _sha(tree):
    try:
        return subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="orchestrate", choices=["orchestrate", "seed", "startup", "smoke", "write"])
    parser.add_argument("--main-tree")
    parser.add_argument("--out")
    parser.add_argument("--keep", action="store_true", help="保留暫存目錄以便除錯")
    parser.add_argument("--code-root")
    parser.add_argument("--work")
    parser.add_argument("--app-db")
    parser.add_argument("--manu-db")
    args = parser.parse_args()
    if args.command == "orchestrate":
        if not args.main_tree:
            parser.error("--main-tree 必填")
        return orchestrate(args)
    {"seed": cmd_seed, "startup": cmd_startup, "smoke": cmd_smoke, "write": cmd_write}[args.command](args)


if __name__ == "__main__":
    main()
