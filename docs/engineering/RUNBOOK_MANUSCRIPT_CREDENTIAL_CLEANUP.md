# Runbook：清理歷史稿件檔中的 token（可逆、以版本檔為單位）

狀態：**工具與流程已備妥，只在合成資料上執行過**。正式資料只能在 Owner 授權後、維護窗口中執行。

## 為什麼要做

- 舊前端把 `?access_token=` 寫進圖片的 `<img src>`，並存進章節版本、草稿與主論文版本。
- 熱修部署後：新存檔不會再有 token，讀取時也會去除，但**舊檔本身仍含 token 原文**。
- 這些舊檔還可能出現在備份、複製出去的資料夾，或讀檔工具的輸出中。
- **清理不能取代 token 輪替。** 先輪替（見 `RUNBOOK_QUERY_TOKEN_SHUTOFF.md` §2），清理只是減少殘留。

## 工具

`scripts/security/manuscript_credential_cleanup.py`

- **範圍**：`<data>/<pid>/manuscript/` 底下所有 JSON 的字串值。
- **只改**：HTML `src`／`href` 中的 `access_token`／`token`／`api_key` 參數。正文、版本號、`_rev`、時間戳不動。
- **記錄**：每個改過的檔加 `_credential_cleanup`：時間、移除幾個參數、原檔雜湊。
- **版本感知**：
  - 每個版本檔（`V0.1.json`、`V1.json`……）各自獨立處理，不合併、不重新編號、不新增版本；
  - 主論文版本的 `sections` 對照表不變。
- **報告與 manifest 不含 token 或稿件內容**，只有相對路徑、雜湊、計數。

## 步驟

1. **演練**（可在正式資料副本上做；不寫任何檔）：
   ```sh
   python scripts/security/manuscript_credential_cleanup.py --data-root <data 副本> --out dryrun.json
   ```
   檢查 `files`、`params_removed`、`by_kind`，並與 `log_exposure_audit.py --manuscript-data` 的數字核對。
2. **維護窗口**：停止應用程式，或至少停止 Manuscript 寫入。對整個 `data/` 做一次獨立快照（第二層保護）。
3. **套用**（Owner 授權後）：
   ```sh
   python scripts/security/manuscript_credential_cleanup.py --apply --data-root <data> --backup-dir <新的空目錄> --manifest cleanup-manifest.json
   ```
   - 工具會先把要改的檔逐一備份並驗證雜湊；**全部備份成功後才開始改寫**；每個檔原子替換。
   - 備份目錄含 token 原文，權限要限制（只給維運），保留期由 Owner 決定。
4. **驗證**：
   - 再跑一次演練，`files` 應為 0（工具可重複執行）；
   - 用 `log_exposure_audit.py --manuscript-data` 確認 `token_params_found` 為 0；
   - 啟動應用程式，開幾個舊版本，確認圖片正常。
5. **還原**（需要時）：
   ```sh
   python scripts/security/manuscript_credential_cleanup.py --restore --manifest cleanup-manifest.json
   ```
   只還原「目前內容仍等於清理後雜湊」的檔。清理後又被改過的檔會列在 `refused`，不會覆蓋新內容。
6. **收尾**：Owner 決定何時安全銷毀備份目錄。銷毀前，備份與第 2 步的快照都含 token 原文。

## 已驗證（合成資料，`test_manuscript_credential_cleanup.py`）

| 項目 | 結果 |
|---|---|
| 演練 | 不寫任何檔（前後檔案雜湊相同）；報告不含隨機秘密 |
| 套用 | 只改屬性；正文裡的 `token=…` 文字保留；`ver`／`_rev`／`sections` 不變；第二次執行 0 個 |
| 還原 | 逐位元組回到原檔；清理後被改過的草稿拒絕還原 |
| 安全 | 備份目錄不是空的時候拒絕執行，而且沒有改任何檔 |
