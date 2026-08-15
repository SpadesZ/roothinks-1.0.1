# Roothinks source maintenance contract
# 檔案路徑: scripts/seed_paq_preview.py
# 模組定位:
#   PAQ 實機驗收環境的種子腳本（P0-1~P0-4）。
# 主要責任:
#   在一份**隔離的**資料目錄裡建立驗收所需的最小狀態：三個測試帳號（owner/
#   editor/viewer）、三個測試專案（正常／voxel 壞掉／尚未建立矩陣），
#   以及一條指向 stub adapter 的 LLM 連線與 task_2a_chat 綁定。
# 上游呼叫者:
#   人工。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只寫「執行時所在 checkout」的 data/（由 create_app 設定推導）。
#   PID 一律以 PAQ 開頭並以 -p 結尾，與真實專案命名空間分開；
#   帳號一律 *@preview.local。
# ACL/安全邊界:
#   - 建立可登入的帳號並綁定 stub LLM。**只能在拋棄式 worktree 上跑。**
#     偵測到目標 data/ 已有非測試專案就直接拒絕執行。
#   - 密碼是固定的測試密碼，與擁有者真實帳密無關，也不得帶到正式站。
#   - NOTE(NOTE-028)：stub 連線需要 ROOTHINKS_ALLOW_STUB_LLM=1 才真的跑得起來，
#     本腳本只負責建立綁定，不負責解鎖。
# 不變量:
#   - 可重入：重跑不會產生重複帳號／專案／綁定。
#   - 不修改任何既有的非測試資料。
# 相關 NOTE:
#   NOTE-025（含連字號 PID）、NOTE-026（voxel 錯誤分類）、
#   NOTE-027（formal 對話 ACL）、NOTE-028（stub adapter）。
# 驗證:
#   py -3.10 scripts/seed_paq_preview.py  → 印出建立的帳號與專案，結束碼 0。
# ------------------------------------------------------------------------------
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PASSWORD = "preview-only-pass"

# NOTE(NOTE-025): 三個 PID 都帶連字號，正是舊前端 regex 解析不了的形狀。
PROJECTS = [
    {
        "pid": "PAQTST-p",
        "name": "PAQ 驗收專案（正常）",
        "status": "formal",
        "cube": [
            {"x": 0, "y": 0, "z": 0, "val": 0.82, "hover": "ok-1"},
            {"x": 1, "y": 1, "z": 1, "val": 0.41, "hover": "ok-2"},
        ],
    },
    {
        # NOTE(NOTE-026): 缺 val 的 voxel → cube_renderer 的 v.val.toFixed(2) 抛
        # TypeError。舊碼會把它誤報成「Project Name Load Failed」。
        "pid": "PAQBAD-p",
        "name": "PAQ 驗收專案（voxel 壞掉）",
        "status": "formal",
        "cube": [
            {"x": 0, "y": 0, "z": 0, "val": 0.5, "hover": "good"},
            {"x": 1, "y": 1, "z": 1, "hover": "missing-val"},
        ],
    },
    {
        "pid": "PAQNUL-p",
        "name": "PAQ 驗收專案（尚未建立矩陣）",
        "status": "formal",
        "cube": [],
    },
]

USERS = [
    ("paqpi", "paqpi@preview.local", "owner"),
    ("paqcopi", "paqcopi@preview.local", "editor"),
    ("paqviewer", "paqviewer@preview.local", "viewer"),
]

TAXONOMY_LABELS = {"x": "Methodology", "y": "Domain", "z": "Evidence Level"}
TAXONOMY_TAGS = {
    "x": ["Qualitative", "Quantitative"],
    "y": ["Speech", "Clinical"],
    "z": ["Pilot", "RCT"],
}


def main():
    from app import create_app, db
    from app.models import Project, PaqSurvey, User, WorkspaceMember
    from app.llm_service.llm_model import LLMModel

    app = create_app()
    with app.app_context():
        # 自我保護：目標 data/ 若已有非測試專案，代表這不是拋棄式環境。
        foreign = (
            Project.query
            .filter(~Project.project_id.in_([p["pid"] for p in PROJECTS]))
            .filter(Project.project_id != "BOOTP1-p")
            .count()
        )
        if foreign:
            print(f"REFUSING: 這份 data/ 有 {foreign} 個非測試專案，"
                  "不是拋棄式驗收環境。")
            return 2

        users = {}
        for username, email, _role in USERS:
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(username=username, email=email)
                u.set_password(PASSWORD)
                db.session.add(u)
                db.session.flush()
            users[email] = u

        for spec in PROJECTS:
            proj = Project.query.filter_by(project_id=spec["pid"]).first()
            if not proj:
                proj = Project(
                    project_id=spec["pid"],
                    name=spec["name"],
                    research_title=spec["name"],
                    status=spec["status"],
                )
                db.session.add(proj)
                db.session.flush()
            # 每次都重設：沒有 members 時畫面會顯示「Principal Investigator Unknown」，
            # 那是種子缺漏而不是缺陷，但它會在全頁掃描時製造假警報，
            # 掩蓋掉真正該注意的 Failed／Unknown。
            proj.members = [
                {"role": "主持人 (PI)", "name": {"original": {"surname": "測", "given": "試員"}}},
                {"role": "共同主持人 (Co-PI)", "name": {"original": {"surname": "協", "given": "同者"}}},
            ]

            survey = PaqSurvey.query.filter_by(project_ref_id=proj.id).first()
            if not survey:
                survey = PaqSurvey(project_ref_id=proj.id)
                db.session.add(survey)
            survey.axis_labels = TAXONOMY_LABELS
            survey.axis_tags = TAXONOMY_TAGS
            survey.cube_data = spec["cube"]

            for _username, email, role in USERS:
                existing = WorkspaceMember.query.filter_by(
                    user_id=users[email].id, pid=spec["pid"]
                ).first()
                if not existing:
                    db.session.add(WorkspaceMember(
                        user_id=users[email].id, pid=spec["pid"], role=role
                    ))

        db.session.commit()

        # --- stub LLM 連線與 task_2a_chat 綁定（可重入） ----------------------
        LLMModel.init_db()
        row = LLMModel.execute_query(
            "SELECT id FROM llm_connections WHERE vendor = ? AND model_name = ?",
            ("stub", "stub-1"), fetch_one=True,
        )
        if row:
            conn_id = row["id"]
        else:
            # execute_query 只有 commit=True 才會 commit（並回傳 lastrowid）。
            # 少了它，INSERT 會在 conn.close() 時被丟棄，而後續 SELECT 拿到 None。
            conn_id = LLMModel.execute_query(
                "INSERT INTO llm_connections"
                " (name, vendor, model_name, api_key, is_encrypted, status)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ("PREVIEW STUB (fabricated output)", "stub", "stub-1",
                 "stub-not-a-real-key", 0, "active"),
                commit=True,
            )

        # task_bindings 的 task_id 是主鍵，UPSERT 才可重入。
        LLMModel.execute_query(
            "INSERT INTO task_bindings (task_id, connection_id) VALUES (?, ?)"
            " ON CONFLICT(task_id) DO UPDATE SET connection_id = excluded.connection_id",
            ("task_2a_chat", conn_id),
            commit=True,
        )

        print("seeded users   :", ", ".join(e for _u, e, _r in USERS))
        print("seeded password:", PASSWORD)
        print("seeded projects:", ", ".join(p["pid"] for p in PROJECTS))
        print("stub connection:", conn_id, "-> task_2a_chat")
        return 0


if __name__ == "__main__":
    sys.exit(main())
