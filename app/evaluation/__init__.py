# Roothinks source maintenance contract
# 主要責任: 界定離線 evaluation package 並匯出 OCR/解析品質指標，不註冊線上 route。
# 上下游: main/create_app 啟動本層，再註冊 Blueprint、DB、Socket、runtime service 供 HTTP/worker 使用。
# 維護邊界: 設定與共享狀態只能在既定初始化邊界改動；錯誤不得以表面成功掩蓋資料或授權不完整。
# 驗證: python -m pytest test/unit tests -q
# 檔案路徑: app/evaluation/__init__.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   Local-first evaluation utilities。
# -----------------------------------------------------------------------------

