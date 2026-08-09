# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/__init__.py
# 模組定位: Manuscript 核心層；位於 HTTP/Socket 工作台、章節協作與持久化之間。
# 主要責任: 建立 Manuscript Blueprint/package 邊界，集中章節協作與持久化模組接線。
# 上下游: manuscript_routes 與前端 workspace 呼叫本層，經 ManuscriptIO/DB 寫入 data/<pid>/manuscript 並回送 HTTP/Socket 事件。
# 維護邊界: 所有讀寫保留 PID、user 與 section scope；草稿/版本/快取不得跨使用者、跨章或以舊非同步回應覆蓋新狀態。
# 驗證: python -m pytest test/unit tests -q
