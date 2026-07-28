#!/usr/bin/env python
# 檔案路徑: scripts/migrate_organization_v2.py
# 產生時間: 2026-07-27 +08:00
# 版本: v1.0
# 模組定位:
#   一次性資料搬遷：專案成員的「所屬單位」由舊三層改為新五層。
# 背景:
#   舊版三層的方向是「由大到小」——
#       l1 = Institution（最大）  l2 = Dept/Div  l3 = Lab/Unit（最小）
#   新版改為五層且方向相反，「由小到大」——
#       L1 Lab/Unit → L2 Dept./Div. → L3 Institution/Branch
#       → L4 Univ./Co. → L5 Nationality/Region
#   若只改前端標籤而不搬資料，既有那筆真資料會變成
#   「Lab/Unit = Hanyang University」，完全顛倒。
# 搬遷對應:
#   舊 l1（大學）→ 新 l4        舊 l2（系所）→ 新 l2
#   舊 l3（實驗室）→ 新 l1      新 l3 / l5 留空由使用者補
# 冪等性:
#   以「有沒有 l4/l5 鍵」判定是否已搬過。新格式一律寫滿五個鍵（含空字串），
#   所以看到 l4/l5 就代表這筆已是新格式，直接跳過。
#   這點很重要——重複執行會把資料轉到錯位置且無法還原。
# 使用方式:
#   python scripts/migrate_organization_v2.py --dry-run   # 先看會改什麼
#   python scripts/migrate_organization_v2.py             # 實際寫入（會先備份）
# 安全邊界:
#   - 寫入前一定先複製一份 <db>.bak-org-v2-<時間戳>。
#   - 只碰 projects.members 這個 JSON 欄位，不動其他資料表。

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

LEGACY_KEYS = ("l1", "l2", "l3")
NEW_KEYS = ("l1", "l2", "l3", "l4", "l5")


def default_db_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "data", "roothinks.db")


def is_legacy(org) -> bool:
    """舊格式＝有 l1~l3 其中之一，且完全沒有 l4/l5 鍵。"""
    if not isinstance(org, dict):
        return False
    if "l4" in org or "l5" in org:
        return False
    return any(k in org for k in LEGACY_KEYS)


def convert(org: dict) -> dict:
    return {
        "l1": (org.get("l3") or "").strip(),   # 舊最小層 → 新最小層
        "l2": (org.get("l2") or "").strip(),
        "l3": "",                               # Institution/Branch，舊資料沒有
        "l4": (org.get("l1") or "").strip(),   # 舊最大層 → 新 Univ./Co.
        "l5": "",                               # Nationality/Region，舊資料沒有
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=default_db_path())
    ap.add_argument("--dry-run", action="store_true", help="只顯示會改什麼，不寫入")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"找不到資料庫: {args.db}")
        return 1

    conn = sqlite3.connect(args.db)
    try:
        rows = conn.execute("select project_id, members from projects").fetchall()
    except sqlite3.OperationalError as e:
        print(f"讀取 projects.members 失敗: {e}")
        return 1

    planned = []       # (project_id, new_members_json, [(before, after), ...])
    skipped_new = 0

    for pid, raw in rows:
        if not raw:
            continue
        try:
            members = json.loads(raw)
        except (TypeError, ValueError):
            print(f"  [略過] {pid}: members 不是合法 JSON")
            continue
        if not isinstance(members, list):
            continue

        changes = []
        touched = False
        for m in members:
            if not isinstance(m, dict):
                continue
            org = m.get("organization")
            if not isinstance(org, dict):
                continue
            if not is_legacy(org):
                if any(org.get(k) for k in NEW_KEYS):
                    skipped_new += 1
                continue
            new_org = convert(org)
            changes.append((dict(org), dict(new_org)))
            m["organization"] = new_org
            touched = True

        if touched:
            planned.append((pid, json.dumps(members, ensure_ascii=False), changes))

    if not planned:
        print(f"沒有需要搬遷的資料（已是新格式的成員 {skipped_new} 筆）。")
        return 0

    print(f"{'【試跑】' if args.dry_run else '【實際寫入】'}"
          f"需要搬遷的專案 {len(planned)} 個：\n")
    for pid, _, changes in planned:
        print(f"  {pid}")
        for before, after in changes:
            print(f"    舊 {before}")
            print(f"    新 {after}")
    print()

    if args.dry_run:
        print("試跑結束，未寫入任何資料。確認無誤後拿掉 --dry-run 再跑一次。")
        return 0

    backup = f"{args.db}.bak-org-v2-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(args.db, backup)
    print(f"已備份原始資料庫至：{backup}")

    for pid, payload, _ in planned:
        conn.execute("update projects set members=? where project_id=?", (payload, pid))
    conn.commit()
    print(f"完成：更新 {len(planned)} 個專案。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
