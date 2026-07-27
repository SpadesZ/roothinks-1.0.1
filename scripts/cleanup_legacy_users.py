# 檔案路徑: scripts/cleanup_legacy_users.py
# 產生時間: 2026-07-26 00:20 +08:00
# 版本: v1.0
# 模組定位:
#   一次性資料清理腳本。為「email 成為唯一登入識別」的 schema 改造做前置準備。
# 主要責任:
#   1. 找出 users 表中 email 為 NULL 或空字串的帳號（早期 username-only 註冊留下的）。
#   2. 連帶清除這些帳號的 workspace_members 授權記錄。
#   3. 將這些帳號在 revision_logs 的 user_id 改寫為 NULL（保留審計軌，只脫鉤身分）。
#   4. 預設 dry-run 只報告不寫入；必須顯式加 --apply 才會真的刪除。
# 呼叫來源:
#   人工執行：python scripts/cleanup_legacy_users.py --apply
#   不被應用程式碼 import，不在啟動流程中執行。
# 輸入輸出契約:
#   輸入：--db <path> 指定資料庫（預設 data/roothinks.db）、--apply 啟用寫入。
#   輸出：stdout 報告；exit code 0 成功、1 失敗。
# 安全邊界:
#   - 破壞性操作。執行前必須自行備份 data/roothinks.db。
#   - 只刪 email 為空的帳號；有 email 的帳號一律不動（含唯一保留帳號）。
#   - 若清理後會一個帳號都不剩，直接中止並回非零 exit code，避免把系統清成空殼。
#   - revision_logs 採 SET NULL 而非刪除：審計紀錄不應因帳號清理而消失。
# 維護提醒:
#   - SQLite 預設不強制 FK，因此關聯資料必須在此手動處理，不能倚賴 ON DELETE。
#   - 這是一次性腳本；跑完即完成階段性任務，保留於 repo 僅供追溯。
# 驗證方式:
#   python scripts/cleanup_legacy_users.py            # dry-run，確認名單
#   python scripts/cleanup_legacy_users.py --apply    # 實際執行
# ------------------------------------------------------------------------------
import argparse
import os
import sqlite3
import sys


def _default_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data", "roothinks.db")


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def cleanup(db_path: str, apply_changes: bool) -> int:
    if not os.path.exists(db_path):
        print(f"[ERROR] 資料庫不存在: {db_path}")
        return 1

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        cursor = conn.cursor()

        if not _table_exists(cursor, "users"):
            print("[SKIP] users 表不存在，無需清理。")
            return 0

        doomed = cursor.execute(
            "SELECT id, username FROM users "
            "WHERE email IS NULL OR TRIM(email) = ''"
        ).fetchall()
        survivors = cursor.execute(
            "SELECT id, username, email FROM users "
            "WHERE email IS NOT NULL AND TRIM(email) <> ''"
        ).fetchall()

        print(f"資料庫: {db_path}")
        print(f"保留 {len(survivors)} 個帳號（有 email）:")
        for uid, uname, mail in survivors:
            print(f"  - id={uid} username={uname} email={mail}")

        print(f"待刪除 {len(doomed)} 個帳號（無 email）:")
        for uid, uname in doomed:
            print(f"  - id={uid} username={uname}")

        if not doomed:
            print("[OK] 沒有需要清理的帳號。")
            return 0

        # 防呆：清理後不能一個帳號都不剩，否則系統會變成無法登入的空殼。
        if not survivors:
            print("[ABORT] 清理後將沒有任何帳號存活，拒絕執行。")
            return 1

        doomed_ids = [row[0] for row in doomed]
        placeholders = ",".join("?" for _ in doomed_ids)

        wm_count = 0
        if _table_exists(cursor, "workspace_members"):
            wm_count = cursor.execute(
                f"SELECT COUNT(*) FROM workspace_members WHERE user_id IN ({placeholders})",
                doomed_ids,
            ).fetchone()[0]

        rl_count = 0
        if _table_exists(cursor, "revision_logs"):
            rl_count = cursor.execute(
                f"SELECT COUNT(*) FROM revision_logs WHERE user_id IN ({placeholders})",
                doomed_ids,
            ).fetchone()[0]

        print(f"連帶處理：workspace_members 刪除 {wm_count} 筆、"
              f"revision_logs 脫鉤 {rl_count} 筆（user_id 改 NULL）。")

        if not apply_changes:
            print("\n[DRY-RUN] 未寫入任何變更。確認名單無誤後加 --apply 重跑。")
            return 0

        if _table_exists(cursor, "workspace_members"):
            cursor.execute(
                f"DELETE FROM workspace_members WHERE user_id IN ({placeholders})",
                doomed_ids,
            )
        if _table_exists(cursor, "revision_logs"):
            cursor.execute(
                f"UPDATE revision_logs SET user_id = NULL WHERE user_id IN ({placeholders})",
                doomed_ids,
            )
        cursor.execute(f"DELETE FROM users WHERE id IN ({placeholders})", doomed_ids)
        conn.commit()

        remaining = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print(f"\n[APPLIED] 已刪除 {len(doomed_ids)} 個帳號，users 剩餘 {remaining} 筆。")
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清除無 email 的舊帳號，為 email 唯一登入識別做準備。"
    )
    parser.add_argument("--db", default=_default_db_path(), help="SQLite 資料庫路徑")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際寫入變更（未指定時只做 dry-run 報告）",
    )
    args = parser.parse_args()
    return cleanup(args.db, args.apply)


if __name__ == "__main__":
    sys.exit(main())
