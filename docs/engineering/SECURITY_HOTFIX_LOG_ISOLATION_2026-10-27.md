# 安全熱修候選：跨專案日誌外洩（只含安全修補）

日期：2026-10-27。分支 `codex/security-hotfix-log-isolation-20261027`，**從 `main`（`9655546`）直接開出**。
只含兩個跨專案日誌外洩修補與必要測試；**不含** R1–R4 的任何功能、審閱範圍（PA-01）或其他政策變更。**未合併、未部署。**

## 1. 問題（在 `main` 上重現）

| 編號 | 端點 | 問題 | 誰受影響 |
|---|---|---|---|
| H1 | `GET /api/literature/debug/runtime_log_tail` | 沒有 `pid` 參數，Literature 的專案 ACL（`before_request`）直接放行；回傳**全站** `runtime.log` 尾端最多 400 行，以及伺服器日誌路徑 | session 模式：**任何登入者**（含沒有任何專案的人）；Bearer 模式：任何有效 token |
| H2 | `GET /api/literature/debug/flowb_probe`、`POST /api/literature/debug/recover_flowb` | 快照用通用字（`flow b`、`run_translation` 等）過濾全站日誌，把**其他專案**的 Flow B 行給本專案成員；並回傳伺服器日誌路徑 | 任一專案的 viewer 以上成員（`recover_flowb` 需 editor） |

三個端點自 `32e1949`（2026-06-30，main 初始 commit）就存在。前端（`app/static`、`app/templates`）**沒有呼叫**這三個端點；只有手動送出的請求會觸發。

## 2. 修補

- `app/security.py`：新增 `require_system_admin()`。
  - `AUTH_MODE=session`：未登入回 401。已登入但 `system_role != admin` 回 403。
  - `API_AUTH_ENABLED`（沒有 session）：token 無效回 401。token 有效但 `TOKEN_ACL_MAP` 沒有明確寫 `system_role: admin` 回 403。專案 wildcard `*` **不算**全站權限。
  - 兩種認證都關（只有開發／測試）：放行，行為不變。
- `literature_debug_routes.py`：`runtime_log_tail` 先呼叫 `require_system_admin()`。
- `literature_bflow.py`：快照只保留**明確提到本專案 pid** 的行。
  - pid 允許 `[A-Za-z0-9_-]`，所以字界包含 `_` 與 `-`。`LOGA` 不會命中 `LOGA12-p`、`LOGA-x`、`LOGA_x`、`XLOGA-p`；唯一接受的後綴是同一專案的 `-p` 別名。
  - 不再回傳伺服器日誌路徑。
- 殘餘風險（寫明，不宣稱解決）：如果一行日誌同時提到本專案與其他專案，仍會整行回傳。R4 分支改成結構化 pid 欄位，不再靠子字串比對。

## 3. 驗證

| 項目 | 結果 |
|---|---|
| 新測試 `test_security_log_isolation.py`（13） | 修補後 13／13 通過；**還原修補後 9 個失敗**（紅測試成立） |
| 調查工具測試 `test_log_exposure_audit.py`（2） | 通過 |
| 完整回歸，Python 3.12 | 1,109 通過、1 略過、1 失敗。失敗的是 `test_roothinks_full_user_journey_e2e`，**未修補的 main 上同樣失敗**（測試替身參數數量不符，既有問題，與本修補無關） |
| 完整回歸，Python 3.10（main 正式映像版本，用 uv 取得的 CPython 3.10.20；不含 torch／easyocr） | 見 §7 |

已驗證的認證組合：
- session 模式：owner、viewer、mentor、外人都 403；未登入 401；admin 200。
- Bearer 模式：無 token／錯 token 401；專案 token、wildcard token 403；admin token 200。
- 開發模式：維持 200。
- Flow B 探針：owner／viewer／mentor 只看到本專案的行；外人 403；未登入 401；只限本專案的 token 也只看到本專案的行。
- `recover_flowb` 的快照：同樣過濾。

## 4. 部署設定與相容性（只讀檢查，未修改）

| 設定檔 | 認證模式 | 修補後誰能讀全站日誌 |
|---|---|---|
| `docker-compose.gcp.yml` | `AUTH_MODE=session`、`API_AUTH_ENABLED=0` | 只有 `system_role=admin` 的帳號 |
| `docker-compose.prod.yml`／`env.prod.template` | `FLASK_ENV=production` → 預設 `AUTH_MODE=session`；`API_AUTH_ENABLED=1` | session admin；或 `TOKEN_ACL_MAP_JSON` 中明確 `system_role: admin` 的 token |
| `docker-compose.override.yml`（開發） | 都關 | 不變 |

- **部署前要確認**：至少一個維運帳號的 `system_role` 是 `admin`，否則沒有人能用這個除錯端點。這不影響產品功能，因為前端沒有呼叫它。
- **回滾**：沒有資料庫遷移、沒有新設定項、沒有新相依。回滾就是 revert 這個 commit。
- **與隔離開發堆疊的關係**：R3／R4 分支已經有同名的 `require_system_admin`（在 `security.py` 另一個位置）。日後合併時保留一份即可，行為相同。

## 5. 歷史暴露調查（非破壞性；**不能宣稱沒有發生過**）

工程端**沒有、也不應取得**正式日誌。以下是根據原始碼與可取得證據的結論與限制：

1. **暴露期間**：從 main 初始 commit（2026-06-30）到修補部署為止，所有部署版本都有 H1、H2。
2. **能不能知道有沒有人呼叫過**：可以部分知道。
   - 正式環境用 gunicorn，`--access-logfile -` 把每個請求（含路徑與狀態碼）寫到容器 stdout。GCP 部署在 Cloud Logging。
   - 存取日誌**沒有使用者身分**，只有 IP 與時間。保留期以外的資料無法判斷。
   - gunicorn 的 access logger `propagate=False`（已讀 gunicorn 原始碼確認），所以這些行**不會**進 `runtime.log`。
3. **被讀到的會是什麼**：
   - `runtime.log` 由 root logger 以 INFO 寫入，涵蓋所有專案（`app/system_runtime.py`）。
   - 每次呼叫最多拿到當時尾端 400 行。
   - 用合成資料的瀏覽器旅程日誌示範（`security_hotfix_evidence/synthetic-runtime-log-categories.json`）：主要內容是伺服器路徑、主機資訊、pid 與請求路徑。
   - 正式日誌的實際內容（論文標題、錯誤堆疊、提示詞片段）**未知**。程式碼中有提示詞前 15 字（`task_8drafter`）與 API key 解密失敗訊息（不含金鑰本身）的記錄點。
4. **給 Owner／維運的步驟**（只讀）：
   ```sh
   # 1) 匯出部署期間的 gunicorn 存取日誌（GCP: Cloud Logging 依容器篩選）與 runtime.log* 副本
   python scripts/security/log_exposure_audit.py --access access-*.log --runtime runtime.log* --out exposure-report.json
   ```
   報告列出受影響端點的每次 200 回應（時間、IP），以及 runtime 日誌的類別計數與**遮罩後**樣本。
   **有 200 的呼叫**：對照登入紀錄找出帳號，再依內容類別判斷是否需要通知。
   **沒有找到**：只代表「在涵蓋的時間範圍內沒有紀錄」，要連同涵蓋範圍一起報告。

## 6. 相關但不在本熱修範圍的發現

- `get_request_token()` 接受網址參數 `access_token`／`token`。Manuscript 前端（`manuscript_image.js`、`manuscript_wsui.js`）會把 token 放進網址，所以 gunicorn 存取日誌會記到 token 原文。建議另開修補：改用標頭；或在存取日誌格式中去掉查詢字串。

## 7. Python 3.10 回歸（main 正式映像的 Python 版本）

環境：CPython 3.10.20（uv），`requirements.txt`＋`requirements-dev.txt`，去掉 easyocr（需要 torch，PyTorch 套件來源在雲端 403）。

| 樹 | 結果 | 失敗清單 |
|---|---|---|
| 未修補的 `main`（`9655546`） | 1,096 通過、1 略過、1 失敗 | `test_roothinks_full_user_journey_e2e`（測試替身 lambda 參數數量不符） |
| 熱修分支 | 1,110 通過、1 失敗（新增 13 個測試；調查工具的 2 個測試另外在 3.10 跑過，通過） | **同一個**既有失敗，沒有新增失敗 |

修補前後的失敗清單逐行比對完全相同。略過數 1 → 0，是因為測試在兩個工作樹寫入 `data/` 的先後不同（`main` 沒有 R2-1 的資料防護，這是已知差異），不是本修補造成。
