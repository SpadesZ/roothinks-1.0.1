# 本機 Python 3.12 升級交付

**本機 GO，已完成 Python 3.12.15 升級；GCP NO-GO，未操作。** Owner 已授權本機切換，attempt-02 於 2026-10-26 15:08:48（臺灣時間）完成，後續獨立核對通過。網址 `http://127.0.0.1:10005`。

分支 `codex/py312-local-first-integration-20261025`。已測產品與實際映像來源 **`a3c5c0ad852b2fa9bded50f01d36ec47dc9dfe80`**；後續文件 SHA 與產品來源分開列示。未改 main、LAVA-Cowork origin、科學判斷規則或 sealed holdout，未啟動雲端接手。

## 實際切換驗收

| 項目 | 已核對結果 |
|---|---|
| Runtime image | `sha256:f297185fb0bb9e4d7b746805caf8ace6a37e87d6f7498e2845860cb5449ad0da` |
| Verification image | `sha256:903f7f7d5022a291fc4badaa8ac7e9fa634d8e8033f0fa8af9a2391a378aa080` |
| App | `roothinks_progress_paq_v8_10005`；healthy、3.12.15、UID 1000、debug off；映像內程式，只有 `/app/data` 掛載，212 個 live source hashes 相符 |
| 新資料卷 | `roothinks10005_py312_data_20261026_attempt02` |
| 新離線備份／還原 | **2,478 檔、618,373,630 bytes**，全部逐檔 SHA-256 相同；三個 SQLite quick_check=ok |
| 啟動後完整性 | **25 張舊表、272 筆舊資料、2,469 個原檔**相同；DB 以舊欄位與逐列 digest 核對，原檔用 SHA-256 |
| 認證／CSRF／Socket | 首頁 302、登入頁 200、匿名 API 401；缺 CSRF 的 HTML 303 回登入頁、API 403；合法 origin polling 200、外站 400、匿名 Manuscript namespace 拒絕 |
| 保留項目 | SECRET_KEY／FERNET_KEY 與 `.env` bytes 不變；原 60cf9f7 checkout／dirty state 不變；Redis ID／StartedAt 不變，未重啟 |

啟動後的原檔核對不含 DB、runtime logs 與 schema lock；完整還原核對涵蓋離線快照全部檔案。沒有以真研究內容呼叫 provider。

## 修復與證據

- `b6f0b3b`／`a3c5c0a` 移除 deep-translator 及兩個孤立相依，固定套件 **113 → 110**，其餘版本不改。快速翻譯使用既有 requests 固定 HTTPS client，目標語言、HTTP 錯誤及空 batch 結果有明確處理。OCR 先拆 argv 再替換輸入，shell=False；模型來源只接受 SHA-256。
- 前輪 CP950 讀 UTF-8 中文 helper 失敗，已真實自動回退到 3.10.20。舊鎖、卷、備份與失敗 receipts 保留。新 attempt 的 helper 所有文字讀取指定 UTF-8，CP950 紅測試與 UTF-8 綠測試通過。
- 切換前先唯讀核對保留候選卷。SQLite WAL 模式在唯讀卷缺 sidecars 時會開檔失敗；只對已停止且 WAL 不存在或為空的靜態卷使用 immutable，明確拒絕非空 WAL。正式 live 核對仍使用 mode=ro。
- Mermaid 額外控制以相同映像、腳本、斷言與 15 秒限制通過：npm 原 bytes、真 SVG、錯誤 HTML 不執行、原稿不變、未配置 Google 不阻塞。trace 載入 **550 ms**，畫面已檢視。前輪逾時未再現，原因未確定，保留為間歇載入風險；未改產品或放寬斷言。
- 追加 live CSRF 檢查最初把 HTML 拒絕錯預期為 400；依 `_handle_csrf_error` 既有契約改驗 303＋登入頁 Location，另要求 API 403＋CSRF 訊息。產品未改。

凍結版完整回歸 **1,538 passed／50 skipped／0 failed／0 errors**，777.26 秒、1,290 warnings。本次重新解析 JUnit、核對 487 個凍結檔案及映像 receipts，未重跑仍有效的完整回歸。11 支 Node、原三支 browser、三研究流程／教授撤權歷史／真 DOCX、PDF 原件與兩次恢復、Eventlet／gthread、真 NLLB／EasyOCR 沿用同版通過證據。Mermaid、實際切換、資料完整性與 live 認證邊界於本次執行。

300 個已曝露 SciFact dev 與原 3.10 預測逐 byte 相同，SHA-256 `a7a22bcfa8438daec9e1871ec5b8159e38db5e57c7a42c53ec5d214fd347d09e`。不是新盲測。50 個略過與既有品質界線保留。

## 備份、回退與重現

機器證據：`D:\Documents\RootThinksValidation\py312-upgrade-20261026\attempt-02`，原完整回歸在父目錄 `regression-final`。[機器索引](PY312_LOCAL_UPGRADE_EVIDENCE_2026-10-26.json)記錄 receipts 與 SHA-256。

保護備份：`D:\RootThinksProtectedBackups\py312-20261026-local-attempt02`，ACL 限目前使用者、SYSTEM、Administrators。真資料與設定只供必要備份／還原，不進 Git 或公開證據包。前輪 `py312-20261026-local` 仍保留。

日常可用 `docker restart roothinks_progress_paq_v8_10005`。重建 container 必須明確帶本輪保護備份內 `compose.py312.yml`，基底參數見 `cutover_local.py` 的 `BASE`；不能直接套原 checkout 的舊 compose。**一次性 cutover helper 不可重跑或刪 lock。**

可重複執行的檢查，每次須換新 receipt 檔名：

```powershell
& 'C:\Users\Franky Kuo\AppData\Local\Programs\Python\Python312\python.exe' 'D:\Documents\RootThinksValidation\py312-upgrade-20261026\attempt-02\post_upgrade_verify.py' 'post-upgrade-recheck-NEW.json'
```

回退 image tag：`roothinks-rollback:pre312-20261026-local-attempt02`；設定：保護備份內 `compose.rollback.yml`。切換失敗自動回退路徑保留，前輪已真實執行；還原工具正常 tar／損毀 hash／traversal 自測證據重用，邏輯未改。

**成功切換後若有新編輯，不可直接回舊 bind，因為它不含新編輯。** 應先停 app、對當前新卷建立新保護快照，再以新 attempt 驗證 3.10 相容副本，所有原卷保留。這與切換驗收期間的即時回退是不同時點。

## 剩餘風險與 Owner Gate

110 項 Python 依賴 audit 為 **0 vulnerabilities／0 skipped**，不等於全面無漏洞。Bandit 既有 **9 個 HIGH B324** 是歷史 SHA1 識別／路徑／鎖／稽核 fingerprint，另有 LOW／MEDIUM findings，未為 scanner 重編歷史身分。最新秘密掃描見機器索引。Scanner 與自我核對不等於外部獨立稽核。

Google 免費翻譯仍 429，真成功未驗；真 OAuth／Drive／Picker／撤權沒有配置帳號。NLLB 有語義錯譯、EasyOCR 有錯讀。真人專家、fresh blind holdout、科學品質、外部稽核、OS CVE 全面掃描、容量、多主機／網路磁碟與斷電恢復未完成；科學品質 **NOT MEASURED**。

**本機 GO，已升級；GCP／main merge／對外發布 NO-GO，停 Owner Gate。** 只 push personal 隔離分支，後續外部審查與雲端啟動由 Owner 決定。
