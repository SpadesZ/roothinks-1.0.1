# Roothinks source maintenance contract
# 檔案路徑: scripts/seed_manuscript_preview.py
# 模組定位:
#   Manuscript 2B/2C 素材與匯出鏈（NOTE-030～NOTE-034）的實機驗收種子腳本。
# 主要責任:
#   在一份**隔離的**資料目錄裡建立驗收所需的最小狀態：
#     1. 三個測試帳號（owner / editor / viewer），密碼是固定的測試密碼。
#     2. 一個 formal 測試專案 MSTEST-p（含連字號，與真實專案命名空間分開）。
#     3. 一張真的 PNG 存進圖片庫（走 ManuscriptImage.save_image_asset 本人，
#        不自己寫檔 —— 否則驗的就不是產品路徑）。
# 上游呼叫者:
#   人工。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只寫「執行時所在 checkout」的 data/（由 create_app 的設定推導）。
# ACL/安全邊界:
#   - 會建立可登入的帳號。**只能在拋棄式 worktree 上跑。**
#     偵測到目標 data/ 已有非測試專案就拒絕執行。
#   - PID 一律 MSTEST-p；帳號一律 *@preview.local。
# 不變量:
#   - 可重入：重跑不會產生重複帳號／專案／圖片。
#   - 不修改任何既有的非測試資料。
# 相關 NOTE:
#   NOTE-030（2B→2C 保真）、NOTE-031/032（DOCX 匯出）、
#   NOTE-033/034（上傳把關與編號）。
# 驗證:
#   py -3.10 scripts/seed_manuscript_preview.py  → 印出帳號與專案，結束碼 0。
# ------------------------------------------------------------------------------
import io
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PASSWORD = "preview-only-pass"
PID = "MSTEST-p"
ACCOUNTS = [
    ("msowner", "owner"),
    ("mseditor", "editor"),
    ("msviewer", "viewer"),
]


def _png(width, height, color):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def main():
    os.environ.setdefault("FLASK_ENV", "development")
    os.environ.setdefault("AUTH_MODE", "session")
    os.environ.setdefault("SECRET_KEY", "preview-only-not-a-real-secret-0000000000")

    from app import create_app, db
    from app.models import Project, User, WorkspaceMember

    app = create_app()
    with app.app_context():
        # 安全閘：目標 data/ 若已有非測試專案，代表跑錯目錄了。
        others = [p.project_id for p in Project.query.all()
                  if not p.project_id.startswith(("MSTEST", "PAQ"))]
        if others:
            print("REFUSE: 目標資料庫已有非測試專案:", others[:5])
            return 2

        if Project.query.filter_by(project_id=PID).first() is None:
            db.session.add(Project(project_id=PID, name="Manuscript 驗收專案",
                                   status="formal"))
            db.session.flush()

        for name, role in ACCOUNTS:
            email = f"{name}@preview.local"
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(username=name, email=email, system_role="user")
                user.set_password(PASSWORD)
                db.session.add(user)
                db.session.flush()
            if WorkspaceMember.query.filter_by(user_id=user.id, pid=PID).first() is None:
                db.session.add(WorkspaceMember(user_id=user.id, pid=PID, role=role))
        db.session.commit()

        # 圖片走產品路徑存入，編號由伺服器指派（NOTE-034）。
        from app.core_pro.manuscript.manuscript_image import ManuscriptImage
        import base64

        existing = ManuscriptImage.get_image_registry(PID)
        if len(existing) < 2:
            for w, h, colour in ((320, 200, (30, 90, 200)), (240, 240, (200, 80, 30))):
                raw = _png(w, h, colour)
                ManuscriptImage.save_image_asset(
                    PID,
                    "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                    f"seed_{w}x{h}.png",
                    ManuscriptImage.next_figure_label(PID),
                    f"Seeded figure {w}x{h}",
                    source="gallery_upload",
                )

        registry = ManuscriptImage.get_image_registry(PID)
        print("SEED_OK")
        print("  project:", PID)
        for name, role in ACCOUNTS:
            print(f"  account: {name}@preview.local / {PASSWORD}  role={role}")
        for entry in registry:
            print(f"  image: {entry['id']} {entry['fig_id']} {entry['filename']}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
