# Roothinks source maintenance contract
# 檔案路徑: app/project_portfolio/__init__.py
# 模組定位: 專案與成員控制層；連接 Dashboard API、Project/WorkspaceMember 與 formal 專案資料。
# 主要責任: 建立 Project Portfolio Blueprint，註冊 Dashboard 專案、成員與角色管理 API。
# 上下游: Dashboard/API -> ProjectService/WorkspaceMember -> ORM 與 formal project data；其他模組只消費授權結果。
# 維護邊界: client 傳入的 user/PID/role 不可信；每個讀寫入口都要重新驗 session、membership 與最小權限，禁止跨租戶列舉。
# 驗證: python -m pytest test/unit tests -q
# Placeholder for app/project_portfolio/__init__.py
