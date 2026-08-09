# Roothinks source maintenance contract
# 主要責任: 界定無業務狀態的共用工具 package，避免 route 間複製安全轉換。
# 上下游: main/create_app 啟動本層，再註冊 Blueprint、DB、Socket、runtime service 供 HTTP/worker 使用。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/utils/__init__.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   輕量共用工具 package。
# 維護提醒:
#   - 只放無外部重依賴、可被測試直接匯入的 utility。
# -----------------------------------------------------------------------------

