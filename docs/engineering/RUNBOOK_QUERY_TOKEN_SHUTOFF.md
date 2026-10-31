# Runbook：關閉網址 token（`API_QUERY_TOKEN_ALLOWED=0`）與 token 輪替

狀態：**準備好，未執行**。每一步都需要 Owner 授權。工程端不輪替、不部署、不讀正式日誌。

## 0. 前提

- 安全熱修（含 H7）已部署：
  - 前端不再產生網址 token；
  - 圖片改用票證 cookie；
  - gunicorn 存取日誌會遮罩 token；
  - 舊用戶端用網址 token 時，會記一行**不含 token** 的警告：`[auth] token sent in URL query for <path>`。
- 正式環境走 HTTPS。`deploy_preflight.py` 必須通過 `SESSION_COOKIE_SECURE`。
  - 票證 cookie 的 `Secure` 跟隨這個設定。原因：TLS 在反向代理終止，而本專案沒有 ProxyFix，所以不能依賴 `request.is_secure`。

## 1. 盤點仍在用網址 token 的用戶端（只讀；建議觀察 7 天）

```sh
python scripts/security/log_exposure_audit.py --runtime runtime.log* --access access-*.log --out inventory.json
```

- `runtime.category_lines.query_token_warning`：熱修部署後，仍用網址 token 的請求數。
- `access.lines_with_token_in_url` 與 `token_in_url_by_endpoint`：存取日誌中網址帶 token 的行數，以及在哪些端點。
- 數字 > 0：找出是哪個工具或書籤，改成用 `Authorization: Bearer …` 標頭。
  - 瀏覽器端的 Manuscript 不需要改，熱修已處理。
- `coverage` 段寫明涵蓋的時間範圍。範圍外的時間只能說「無法判斷」。

## 2. 輪替 token（重疊期，零停機）

`API_BEARER_TOKENS` 本來就接受多支 token，所以新舊 token 可以同時有效。

1. Owner 產生新 token。不要寫進 repo、聊天紀錄或工單：
   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. 部署設定加入新 token：`API_BEARER_TOKENS=<舊>,<新>`。`TOKEN_ACL_MAP_JSON` 要給新 token 與舊 token **相同**的 `allowed_pids`／`system_role`。
3. 經安全管道把新 token 交給每個用戶端，用戶端改用標頭。
4. 確認舊 token 不再被使用：
   - 第 1 步的工具顯示 `query_token_warning` 為 0；
   - 應用程式層的使用紀錄也看不到舊 token。
5. 移除舊 token。**舊 token 簽出的圖片票證會立即失效**，因為票證驗證時會比對目前設定的 token 指紋。

**應視為已暴露、需要換新的 token**：
- 曾出現在存取日誌網址裡的 token；
- 曾出現在稿件 HTML 裡的 token。
- 範圍可用 `--access` 與 `--manuscript-data` 統計。

## 3. 切換前驗證（`API_QUERY_TOKEN_ALLOWED` 仍為 1）

1. 建立一支**臨時驗證 token**（只允許一個測試專案），加入設定。
2. 執行：
   ```sh
   RT_CHECK_TOKEN=<臨時 token> python scripts/security/query_token_shutoff_check.py --base-url https://<host> --pid <測試專案> --expect-query 1 --out pre.json
   ```
3. 預期 `ok: true`：
   - 標頭可用；
   - 票證 cookie 有 HttpOnly、Secure、SameSite=Strict；
   - 網址 token 仍被接受。

## 4. 切換

- 設定 `API_QUERY_TOKEN_ALLOWED=0`，重新啟動（只改環境變數，不改程式）。
- 影響範圍：
  - HTTP 請求的 `?access_token=`／`?token=` 會被拒絕；
  - Socket 連線網址上的 token 也會被拒絕；
  - Socket 的 auth 封包與 `Authorization`／`X-API-Token` 標頭**不受影響**。

## 5. 切換後驗證與上游日誌

1. 執行：
   ```sh
   RT_CHECK_TOKEN=<臨時 token> python scripts/security/query_token_shutoff_check.py --base-url https://<host> --pid <測試專案> --expect-query 0 --out post.json
   ```
   預期 `ok: true`，網址 token 回 401／403。
2. 用報告中的 `canary`（`RTCANARY-…`，假的、不是憑證）搜尋日誌：
   - **gunicorn 存取日誌**：應該只看到 `access_token=[REDACTED]`，找不到 canary 原文。
   - **上游代理／負載平衡器／CDN 日誌**（例如 GCP Cloud Logging 的 LB 日誌）：
     - 找到 canary 原文，代表上游會記錄查詢字串。Owner 要另外決定：在上游設定遮罩，或縮短保留期並限制讀取權。
     - 這一層不在本專案程式控制範圍內。
3. **立即從設定刪除臨時驗證 token**。它在第 3、5 步出現在網址裡，所以只能用一次。

## 6. 回滾

| 情況 | 動作 | 影響 |
|---|---|---|
| 有沒盤點到的用戶端壞掉 | `API_QUERY_TOKEN_ALLOWED=1` 並重新啟動 | 立即恢復；不用改程式或資料 |
| 新 token 有問題 | 在重疊期內，舊 token 仍然有效 | 不要在確認新 token 可用前刪掉舊 token |
| 熱修本身有問題 | revert 熱修 commit | 會恢復網址 token 行為，**不建議**；先用上面兩種方式 |

## 7. 完成條件

- `pre.json`、`post.json` 都是 `ok: true`。
- gunicorn 存取日誌找不到 canary 原文。
- 已記錄上游日誌的 canary 結果，以及 Owner 的處理決定。
- 舊 token 與臨時驗證 token 都已刪除。
- `deploy_preflight.py` 的 `API_QUERY_TOKEN_ALLOWED` 為 PASS。
