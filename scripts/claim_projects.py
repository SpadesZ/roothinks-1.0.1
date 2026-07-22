# 檔案路徑: roothinks/scripts/claim_projects.py
# 產生時間: 2026-07-20 10:40 +08:00
# 版本: v1.0
# 模組定位:
#   一次性 CLI:把「無任何成員的既有專案」認領給指定使用者(設為 owner)。
#   單機時代的專案沒有 workspace membership,開啟 AUTH_MODE=session 後
#   所有人都看不到它們——用本工具移交給第一位帳號。
# 主要責任:
#   1. --username <帳號> [--pid P1 P2 ...]:指定專案認領;不給 --pid 則
#      認領「目前沒有任何成員」的全部專案。
#   2. 已有成員的專案一律跳過(避免奪權),除非 --force。
# 維護提醒:
#   - 在容器內執行:docker exec roothinks_progress_paq_v8_10005 \
#       python scripts/claim_projects.py --username <你的帳號>
#     或 host 端(需可用的 python 環境)在 repo 根目錄執行。
#   - 冪等:重跑不會重複建 membership。
# 驗證方式:
#   - 執行後輸出認領清單;該使用者登入後 Dashboard 應看到這些專案。
# ------------------------------------------------------------------------------
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="認領既有專案給指定使用者(owner)")
    parser.add_argument("--username", required=True, help="要成為 owner 的帳號")
    parser.add_argument("--pid", nargs="*", default=None, help="指定專案 ID;省略=全部無成員專案")
    parser.add_argument("--force", action="store_true", help="連已有成員的專案也強制加為 owner")
    args = parser.parse_args()

    from app import create_app, db
    from app.models import Project, User, WorkspaceMember, ROLE_OWNER

    app = create_app({"TESTING": False})
    with app.app_context():
        user = User.query.filter_by(username=args.username).first()
        if not user:
            print(f"[ERROR] 找不到帳號 '{args.username}',請先到 /auth/register 註冊。")
            sys.exit(1)

        if args.pid:
            projects = Project.query.filter(Project.project_id.in_(args.pid)).all()
        else:
            projects = Project.query.all()

        claimed, skipped = [], []
        for p in projects:
            pid = p.project_id
            existing = WorkspaceMember.query.filter_by(pid=pid).count()
            mine = WorkspaceMember.query.filter_by(pid=pid, user_id=user.id).first()
            if mine:
                skipped.append((pid, "已是成員"))
                continue
            if existing > 0 and not args.force:
                skipped.append((pid, f"已有 {existing} 位成員(用 --force 強制)"))
                continue
            db.session.add(WorkspaceMember(user_id=user.id, pid=pid, role=ROLE_OWNER))
            claimed.append(pid)

        db.session.commit()
        print(f"[OK] 認領 {len(claimed)} 個專案給 {args.username}(owner):")
        for pid in claimed:
            print(f"  + {pid}")
        if skipped:
            print(f"[SKIP] 略過 {len(skipped)} 個:")
            for pid, why in skipped:
                print(f"  - {pid}: {why}")


if __name__ == "__main__":
    main()
