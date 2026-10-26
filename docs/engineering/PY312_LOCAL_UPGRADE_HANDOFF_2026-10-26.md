# 本機 Python 升級接手（2026-10-26）

使用者要求改由新聊天室「升級python環境」接續。這是未完成工作交接，**目前本機服務仍為 Python 3.10.20，不能宣告升級完成。**

## 授權與正確工作樹

- 最新任務：「把待處理的問題幹掉後升級到3.12」。已授權本機實際升級，不再只做候選驗證。GCP 不動。
- **所有 Git／產品實作明確使用** `C:\Users\Franky Kuo\.codex\worktrees\sai-py312-local\roothinks-1.0.1`。
- 分支 `codex/py312-local-first-integration-20261025`，產品凍結 SHA `a3c5c0ad852b2fa9bded50f01d36ec47dc9dfe80`。接手文件另有文件提交；不可把文件 SHA 說成已測映像來源。
- 專案主目錄 `C:\Users\Franky Kuo\Desktop\roothinks-1.0.1` 是 junction，實際 `D:\DockerData\HostBindings\Desktop\roothinks-1.0.1`。這是仍供舊服務掛載的 **60cf9f7** 工作目錄，**不可 reset、切分支或覆寫**。
- 沿用 AGENTS.md：繁中、CodeGraph first、Rickie privacy gate、保留 dirty／歷史、不索引或提交真資料／秘密。使用者沒有要求子代理；本輪沒有委派。
- 不 merge main、不修改 LAVA-Cowork origin、不動科學決策規則、不讀 sealed holdout、不自動啟動雲端接手。
- 完成安全／秘密掃描後，只 push personal 的上述隔離分支。尚未 push 本輪兩個工程提交；觀察到 personal 同名分支仍為 `29e87c68e6a08c94087483a3144d660b5043c101`，接手須 live 核對。

## 已提交修復

1. `b6f0b3bc9e5996767612f630210e4aedfb6481c6`：移除有 PYSEC-2022-252 通報且無修復版的 deep-translator；以既有 requests 的固定 HTTPS JSON client 保留快速翻譯。目標語言傳入單句、兩個 batch 呼叫及 realtime fallback。OCR 模板先拆 argv，再替換資料，shell=False；拒絕 shell／interpreter code flags。移除 NLLB SHA1 fallback，正式 manifest 一律 SHA256。
2. `a3c5c0ad852b2fa9bded50f01d36ec47dc9dfe80`：明示 Google HTTP 狀態；空白／None 的 batch 結果不可當成翻譯成功。

依賴由 113 降為 110，只移除 deep-translator、beautifulsoup4、soupsieve；其餘版本零改動、零新增。原 pins 保留在 `validation/python312/pre-upgrade.constraints`，正式 hash lock 已更新。

## 凍結版本真實證據

本輪 QA：`D:\Documents\RootThinksValidation\py312-upgrade-20261026`。前輪：`D:\Documents\RootThinksValidation\py312-integration-20261025`。

- 正式 Dockerfile 完整 build：Python 3.12.15、CPU Torch 2.13.0+cpu／TorchVision 0.28.0+cpu、真 NLLB／EasyOCR、離線 reload。
- Runtime image `sha256:f297185fb0bb9e4d7b746805caf8ace6a37e87d6f7498e2845860cb5449ad0da`。
- Verification image `sha256:903f7f7d5022a291fc4badaa8ac7e9fa634d8e8033f0fa8af9a2391a378aa080`。
- `frozen-v2/source-manifest.json`、兩個 build receipts；`runtime-source-receipt.json` 核對 212 個 runtime source files 精確相同。
- **完整回歸：1538 passed / 50 skipped / 0 failed / 0 errors，777.26 秒。** `regression-final/{receipt.json,stdout.txt,full.xml}`。50 skipped 與前輪相同；1290 個既有 warnings，不能隱藏。
- 缺陷 red：原 13 個針對測試失敗，包含 Linux 真 sentinel shell injection；green 與 full 都保留。原有斷言沒有削弱；fake 的函式簽名跟隨新目標語言參數。
- 11 支 Node 通過；原 autonomy／assets／scientific 三支瀏覽器通過（獨立研究、選用教授、團隊、邀請／撤權／移除後保留歷史和匯出）。
- `integrated-pdf-pdf-receipt.json`：真 browser PDF／同 ID recovery 通過。Eventlet／gthread HTTP session/CSRF／Socket polling／PDF 上傳與兩次 recovery、capabilities、真 NLLB、EasyOCR 均有 final-* 通過 receipts。
- 300 個已曝露 SciFact dev 與原 3.10 byte-identical；SHA256 `a7a22bcfa8438daec9e1871ec5b8159e38db5e57c7a42c53ec5d214fd347d09e`。不是新盲測，沒有科學品質提升證據。
- `dependency-audit-all.json`：110 項、0 vulnerabilities、0 skipped；CPU wheels 用 public version 對應 OSV，實際 CPU wheel 仍由固定 hash 驗證。
- Gitleaks 新提交 `29e87c6..a3c5c0a` exit 0。Bandit 留 9 個 high SHA1 findings（歷史 ID／目錄／鎖名／稽核 fingerprint）；trace 顯示 context chain fingerprint 寫 provenance ledger，不是密碼／授權或完整性簽章。不為 scanner 重編既有 ID。前輪既有掃描與 hash 誤報證據仍保留。

## 尚未關閉的額外瀏覽器失敗

`integrated-diagram-control-pdf-receipt.json` exit 1：Mermaid 元件載入逾時，`圖表元件載入逾時；原圖碼仍保留。` 原三支瀏覽器全過，但這支額外控制未過，不能省略。

這支由 `browser_runs.py -pdf extras` 執行；使用本輪 `diagram_control.cjs`、既有離線 asset hook／manifest。需分辨 CDN／離線 fixture 與產品缺陷，不能只放寬 timeout、刪除斷言或把它算成通過。若只修 QA 編碼／fixture，現有凍結產品證據可重用；若改產品 source，重新凍結並跑必要完整回歸。

## 真舊資料遷移與已成功回退

- 原服務／Compose：app `roothinks_progress_paq_v8_10005`，project `roothinks10005`，service `roothinks-paq`；Redis `roothinks_progress_redis_10005` 全程未重啟。
- 原 image `sha256:47d74fc67670303dfa0c3916ed00bbfe935c20890eddf3a0446679b253f4baf9`，Python 3.10.20，`127.0.0.1:10005`。
- 保護備份：`D:\RootThinksProtectedBackups\py312-20261026-local`，ACL 限使用者／SYSTEM／Administrators。**這裡含 .env／完整 inspect／真研究資料，不進 Git、不輸出內容、不放公開證據包。**
- online-data 含 SQLite backup API 的三 DB；offline-data 是停 app、WAL checkpoint 後的一致快照。三顆 DB `roothinks.db`、`manu_core.db`、`sys/llm_match.db` 都 quick_check=ok。原件、JSON 決策／版本／review 全含。
- Windows NTFS 舊檔權限使 appuser 無法寫 runtime.log，重現 PermissionError。舊 override 因此長期 user 0；本輪選擇 Linux native named volume，保留原宿主目錄作回退。
- `roothinks-py312-staging-20261026` 已以真舊資料副本、合成 secrets、network=none 測過：25 個舊 tables／272 rows 原欄位值完全相同、三 DB 完整、重跑 migration 為 no-op、登入頁 200／匿名 auth API 401。**沒有用真研究內容呼叫 provider。**
- 本輪實際切換：先完整離線備份，還原到新卷 `roothinks10005_py312_data_20261026`，**2478 個檔／618342535 bytes 全部逐檔 SHA256 相同**。程式改用 image-packaged source，只掛 `/app/data`，user 1000、debug off；SECRET_KEY／FERNET_KEY 保留，.env bytes 不改。
- 還原工具 `archive_selfcheck.py` 真測：正常可還原、損毀 hash／traversal 均拒絕；失敗卷保留。
- **切換驗收腳本失敗並自動回退**：`cutover_local.py` line 165 使用 `(QA/'verify_live_data.py').read_text()`，Windows CP950 解碼含繁中文字的 UTF8 docstring，拋 UnicodeDecodeError。這是 ops 腳本缺陷，尚未修；不是已證實的產品／資料缺陷。
- 錯誤發生在 Python 3.12 已健康、但原始檔／資料列的最後 live 核對還沒真正執行時，故不得把這輪算升級成功。
- 自動 rollback 已真的執行：原 image／舊 bind 啟動，running healthy，Python 3.10.20。`local-rollback-receipt.json`、`cutover-journal.jsonl` 保留。新卷與舊／新所有備份未刪除。
- 最後檢查沒有仍執行的本輪 QA Python 進程，原服務健康，Git source 乾淨。

## 接手下一步（不要直接盲跑原腳本）

1. Live 核對本文件的 SHA、容器健康／Python、進程、分支與 receipts。當前工作樹與 saved project 根目錄不同，**每個命令明示隔離 workdir**。
2. 修 QA 讀檔編碼，明確 `encoding='utf-8'`；檢查同類 read_text。先在保留的候選資料卷上唯讀跑 `verify_live_data.py`，與 protected offline invariants／file manifest 比較。不要藉重複切換才發現下一個錯。
3. 關閉 Mermaid 額外瀏覽器失敗，保留失敗原件與原 assertions。
4. 新切換輪使用**新的 attempt／備份目錄／資料卷／receipts 名稱**，保留本輪所有 bytes。現在原服務可能已有新編輯，必須取得新的 online／offline 備份，不能直接用前輪離線快照覆蓋。
5. 原 `cutover_local.py` 有 exclusive attempt lock、既有卷拒絕與檔案 x mode，這些是保護，**不可刪 lock 或覆寫舊證據硬重跑**。沿用最小必要 ops helper，以新 attempt 路徑接續即可；不需要重新下載模型或無理由重跑未改產品的完整套件。
6. 具體工具在本輪 QA：`protect_local.py`、`cutover_local.py`、`seed_archive.py`、`verify_live_data.py`。切換前 `--prepare-only`、確保自動 rollback；只停 app、不停 Redis；新卷出問題保留供資料救援，舊目錄不動。
7. 最終必須核對：actual image／source、Python 3.12.15、UID 1000、health、auth／CSRF／Socket origin 與匿名拒絕、三 DB rows／原件 hash、SECRET／FERNET 不變、Redis ID／start time 不變、三研究流程／PDF／export 的有效證據與剩餘真帳號邊界。
8. 更新 acceptance matrix、STATUS、機器證據／報告（目前仍主要是 29e87c6 的舊 NO-GO 報告，不能混稱本輪完成）。安全／秘密掃描後只 fast-forward push personal 隔離分支。交付可重現接手指令、SHA、失敗與本機／GCP 分開的 GO/NO-GO，停 Owner Gate，不主動傳訊雲端 agent。

## 保留的能力邊界

- Google 免費翻譯兩固定端點被 429 限流，原 deep-translator 也 TooManyRequests。新版明示錯誤、不偷換 explicit engine；真 provider 成功未驗收。不可繞限流，NLLB 可獨立使用。
- Google Drive client／API key 未配置；真 OAuth／Picker／撤權需授權帳號，未測。不能編造設定或帳號。
- NLLB 控制句對 measured 有語義錯譯，數字保留不等於翻譯正確；原／新版條件與 OCR 控制比較僅診斷。不能宣稱科學品質改善、真盲測／專家驗證或外部獨立稽核已完成。
- 既有 SHA1 ID／稽核 fingerprint 的 scanner warnings、Eventlet／utcnow deprecations、OS CVE／容量與斷電驗證未完成。沒有為降警告破壞原歷史。

## 執行工具

- Python `C:\Users\Franky Kuo\AppData\Local\Programs\Python\Python312\python.exe`（Windows 全域原為 3.12.10；不要誤當正在跑的容器版本）。
- Node `C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`。
- Chromium `C:\Users\Franky Kuo\AppData\Local\ms-playwright\chromium_headless_shell-1228\chrome-headless-shell-win64\chrome-headless-shell.exe`。
- audit Python／Gitleaks 在前輪 QA 的 `audit-tools\Scripts\python.exe`、`gitleaks\gitleaks.exe`。
- exec 長操作保留 session_id 並等待，不丟掉 ID 造成 stage/build race。shell log 用全新檔或 append；dcg 曾拒絕可能 truncate 的動態 redirect，改用安全 append，不繞過保護。
