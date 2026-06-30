#路徑(app/core_pro/literature/flowb_runner_cli.py) #版本 v0.1 #更版時間 20260429
#功能概要:
#1. 相容轉接：保留舊模組路徑給既有 subprocess 呼叫使用。
#2. 實際邏輯委派至 literature_flowb_runner_cli.main。
#3. 避免舊執行中程序因模組改名產生 ModuleNotFoundError。

from app.core_pro.literature.literature_flowb_runner_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
