# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/literature/flowb_runner_cli.py
# 模組定位: Literature 核心層；位於上傳/解析 API、Flow A/B 處理與 evidence index 之間。
# 主要責任: 保留舊 Flow B CLI 相容入口並轉交 canonical runner，避免維護第二套 pipeline。
# 上下游: Literature routes/runner 呼叫本層，讀寫 data/<pid>/literature、EvidenceSegment 與 LLM task，結果回到 Literature UI。
# 維護邊界: 維持 PID/paper_id 隔離、來源 lineage、segment identity 與可重跑性；fallback 不得冒充高品質完成。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/core_pro/literature/flowb_runner_cli.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. 相容轉接：保留舊模組路徑給既有 subprocess 呼叫使用。
#2. 實際邏輯委派至 literature_flowb_runner_cli.main。
#3. 避免舊執行中程序因模組改名產生 ModuleNotFoundError。

from app.core_pro.literature.literature_flowb_runner_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
