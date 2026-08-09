# Roothinks source maintenance contract
# 驗證: python -m py_compile scripts/backfill_llm_usage_cost.py
# 檔案路徑: scripts/backfill_llm_usage_cost.py
# 產生時間: 2026-08-04 +08:00
# 版本: v1.0
# 模組定位:
#   一次性維運腳本：把 llm_usage_log 裡「當初沒單價、現在查得到」的紀錄補算金額。
# 背景:
#   cost_usd 是在 record() 當下就算好寫進 DB 的。所以事後往 llm_pricing 補單價，
#   **既有紀錄不會自己重算**——文獻列表會一直顯示「≥ $X（部分模型未設定單價）」，
#   即使那個模型現在已經有價了。這支腳本補的就是這個落差。
# 主要責任:
#   1. 掃出 cost_usd IS NULL 的紀錄。
#   2. 用目前的單價表重算；查得到才寫回，查不到就維持 NULL。
# 呼叫來源:
#   維運手動執行（容器內）：python scripts/backfill_llm_usage_cost.py [--apply]
# 輸入輸出契約:
#   - 預設 dry-run，只印出會改什麼。加 --apply 才真的寫入。
#   - 只寫 cost_usd 與 price_note 兩欄，不動 token 數與時間戳。
# 安全邊界:
#   - **絕不把查不到單價的紀錄寫成 0**。查不到就跳過、維持 NULL，
#     讓 UI 繼續顯示「未設定」。寫 0 會讓使用者以為那幾次呼叫免費。
#   - 只 UPDATE 既有列，不新增不刪除。執行前請自行備份 DB。
# 維護提醒:
#   - 這是「補算歷史」，不是「重算全部」。已經有金額的紀錄一律不動——
#     那些是當時單價下的真實估算，事後用新價覆寫等於竄改歷史帳。

import argparse
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, "/app" if sys.path and __file__.startswith("/app") else ".")

from app.llm_service.llm_pricing import estimate_cost_usd  # noqa: E402
from app.llm_service.llm_usage import get_db_path  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="實際寫入（預設 dry-run）")
    args = ap.parse_args()

    path = get_db_path()
    print(f"DB = {path}")
    print(f"模式 = {'APPLY（會寫入）' if args.apply else 'DRY-RUN（只列出）'}\n")

    conn = sqlite3.connect(path, timeout=15)
    try:
        rows = conn.execute(
            "SELECT id, vendor, model_name, input_tokens, output_tokens,"
            " cached_input_tokens FROM llm_usage_log WHERE cost_usd IS NULL"
        ).fetchall()

        fixable = defaultdict(lambda: {"n": 0, "cost": 0.0})
        skipped = defaultdict(int)
        updates = []

        for rid, vendor, model, i, o, c in rows:
            cost, note = estimate_cost_usd(vendor, model, i, o, c or 0)
            key = f"{vendor}/{model}"
            if cost is None:
                # 查不到就維持 NULL。寫 0 會被當成「這次免費」。
                skipped[key] += 1
                continue
            fixable[key]["n"] += 1
            fixable[key]["cost"] += cost
            updates.append((cost, note, rid))

        print(f"cost_usd IS NULL 的紀錄共 {len(rows)} 筆\n")
        print("== 現在查得到單價，可補算 ==")
        if not fixable:
            print("  （無）")
        for k, v in sorted(fixable.items()):
            print(f"  {k:45s} {v['n']:4d} 筆  合計 US${v['cost']:.6f}")

        print("\n== 仍查不到單價，維持 NULL ==")
        if not skipped:
            print("  （無）")
        for k, n in sorted(skipped.items()):
            print(f"  {k:45s} {n:4d} 筆")

        if args.apply and updates:
            conn.executemany(
                "UPDATE llm_usage_log SET cost_usd = ?, price_note = ? WHERE id = ?",
                updates,
            )
            conn.commit()
            print(f"\n已寫入 {len(updates)} 筆。")
        elif updates:
            print(f"\n（dry-run）加 --apply 會寫入 {len(updates)} 筆。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
