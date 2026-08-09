# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/study/__init__.py
# 模組定位: Study 核心層；協調論文閱讀、筆記/矩陣與 AI tutor 的專案內狀態。
# 主要責任: 建立 Study Blueprint/package 邊界，集中閱讀、矩陣與 tutor 流程接線。
# 上下游: Study routes/static JS 呼叫本層，讀取 Literature 素材並把筆記、對話或矩陣保存到 data/<pid>/study。
# 維護邊界: 所有讀寫保留 PID、user 與 section scope；草稿/版本/快取不得跨使用者、跨章或以舊非同步回應覆蓋新狀態。
# 驗證: python -m pytest test/unit tests -q
