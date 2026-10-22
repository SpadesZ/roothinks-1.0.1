# SAI v1.1.0 延伸優化執行紀錄

規格：`60cf9f73d15cf0e43927cfe572bf217e4547d943`。隔離分支：`codex/sai-v1.1.0-batch-a-b`。
本紀錄區分已實作、已測試及未驗證；不把測試數量當成研究品質通過。

## 範圍與保護

- Phase A：GOV-01／COL-01、REL-01／03／04，及過期 E2E 替身。
- Phase A 驗證通過後，Phase B：文獻單一身分、理由編輯、確認者與決策下游的垂直流程。COC-02 全模組覆蓋需另列尚未完成項目。
- 不合併 main、不部署、不改正式資料、不讀取真實研究秘密、不推送 LAVA-Cowork。
- 測試只用隔離工作樹及合成資料；原 PDF、既有 ID、歷史及未授權章節不能被改寫或洩漏。

## 分支整合

`personal/main` 相對共同祖先只有 README 與說明圖片的三筆文件提交。
合併到實作分支，保留新版說明、SAI 入口及既有容器埠修正；main 本身不變。

## 可驗證工作清單

- [x] 核對規格 SHA、工作樹、個人 remote 與分歧；建立隔離工作樹。
- [x] 建立本 chat 2026-10-23 06:10 一次性接續任務。
- [ ] A0：以現行測試及新增負向案例重現漏洞／失敗，保存未修前結果。
- [x] A1：全站 LLM 設定頁及管理 API 使用一致系統角色；保留合法模型任務。
- [x] A2：共用筆記及對話追加採原子版本保護；前端保留衝突與未保存文字。
- [x] A2b：同一人的兩個手稿草稿分頁也要防衝突，並接通 Socket／離頁保存。
- [x] A2c：來源決策沿用鎖內更新，新增修訂版比對與失敗 UI 還原。
- [x] A3b：原檔 overwrite／併發分配與登記失敗先重現，再修復。
- [x] A3：比較、保存、索引及降級狀態正確；原有成果不可因失敗被覆寫。
- [ ] A4：修正 E2E 替身的介面，不改弱 assertion；針對性與完整回歸後進入 B。
- [ ] B1：PDF 與搜尋項目連結；候選、原件、別名與來源歷史保留。
- [ ] B2：一般介面編輯理由與筆記；伺服器記錄確認者，防止偽造。
- [ ] B3：確認文獻決策進入實際寫作請求；排除項目與章節 ACL 不繞過。
- [ ] C：完整回歸、負向／對抗檢查、差異自我稽核、驗收矩陣及剩餘風險。

## 驗證紀錄

### A1：全站管理權限

- 未修前：完整 session HTTP 下，專案 owner 讀取 `/api/llm/connection/list` 回 200，預期 403；新增測試確實失敗。
- 修復：管理 blueprint 與設定頁共用系統角色守門。專案 owner／editor／viewer／coauthor／mentor 不推導成全站 admin。Bearer 另需明確 `TOKEN_ACL_MAP` 的 `system_role=admin`；project wildcard 不授權。
- 管理寫入只記錄伺服器確認的使用者 ID／token 指紋、端點與狀態，不記錄金鑰或請求內容。
- `pytest test/unit/test_llm_admin_acl.py test/unit/test_llm_binding_guard.py test/unit/test_module_access_scope.py -q -p no:cacheprovider`：**132 通過**。
- 覆蓋每條管理 API、五種非管理角色、偽造 body、匿名、合法管理者、設定頁、Bearer 及原有專案閱讀。未宣稱所有跨模組 ACL 已完成。

### A0：其他基線重現

- 原 E2E：1 失敗；PAQ 替身 3 參數與現行 5 參數不符。測試 assertion 尚未改動。
- 新增 Study 保存案例：10 失敗，重現舊筆記覆蓋、無／錯誤版本仍寫入、破損 JSON 被覆蓋、同時保存兩次均成功、對話磁碟錯誤仍 `ok=true`。
- 本輪測試使用既有隔離 Python 依賴；OCR／翻譯與真實模型品質仍未驗證。

### A2／A3：保存與真實失敗

- 新增共用 JSON 的鎖內讀／修改／原子保存，成功前 fsync；失敗清理暫存檔，保留舊檔。共用筆記必填基礎版號，409 保留新檔與本機輸入，缺版號 428；破損檔案禁止覆寫。讀取舊版筆記不遷移／刪除原件。
- Study 前端在收到成功 ack 後才顯示已儲存；本機副本以使用者＋專案隔離。讀取遲到、衝突、磁碟與 HTTP 錯誤均保留文字並可下載。
- 對話追加及改名使用同一鎖；保存失敗回傳 503 與可保留的回答。Tutor provider 失敗不再作為正常答案。
- Task6 provider／解析失敗保留明確失敗身分；規則診斷不經科學五維正規化。比較分 completed／partial／failed，全部失敗 502、保存失敗 503、部分成功 207；結果與保存狀態分開。
- 移除引擎中重複且會覆蓋舊檔的 deterministic 比較保存，沿用 route 的唯一歷史保存入口，既有結果不刪。
- 新發現跨角色繞過：query 的授權 PID 與 body 的資料 PID 不同仍可取資料；兩種繞過已先重現 200，現在拒絕不一致 PID，且禁止只有巢狀 context PID 的無專案查詢。
- 引用索引故障原回空清單；移除吞錯，讓既有 API 回報失敗。真正無結果仍為空清單。
- 針對性執行：110 通過；加入索引故障案例後第一次 112 通過／1 失敗，修復後索引與比較組 8 通過。完整回歸尚待 A2b 與 E2E 修復完成。
- `node test/js/test_study_notes_integrity.cjs`：4 個行為情境通過；這是合成 DOM，尚非真實瀏覽器驗收。

### A2b／A2c／A3b：草稿、來源決策及原件

- 草稿沿用每使用者隔離及 `_rev`；HTTP/session 與 Socket/session 必須帶基礎版號。清除以保留內容的標記遞增版號，避免 ABA 舊分頁在清除後重寫。既有內部 none 模式無版號 caller 保留相容；不宣稱這些 trusted caller 能防舊版本寫入。
- Socket ack 配對 request ID；衝突不提升基礎版號、不清本機文字。同一使用者＋專案＋章節的本機副本可復原；明確另存才建立新版本。離頁 beacon 帶相同版號契約。
- 文獻決策 `_rev` 比對在既有 FileLock 內；過期更新 409，session 缺版號 428。現有 positive HTTP fixture 增加版號，權限 assertion 保留。
- 原件先重現：overwrite 1 回 200 並毀掉原 PDF；四個同名併發都拿到 `paper`；DB commit 故障仍回成功。修復後每專案鎖涵蓋 ID 分配到保存，原件採 exclusive create，禁止覆蓋。圖片保留上傳原件並另產 PDF。DB／文獻連結失敗回 503 與保留原件 ID。
- 草稿、Socket 版本、導頁、編輯訊號與切章組：95 通過。新增導頁 revision 負向案例後，E2E／導頁／草稿／比較組：24 通過；原有 E2E assertion 保留，PAQ 替身只對齊兩個選用參數，筆記先讀版號。
- 上傳保全與原有 library smoke：8 通過。來源決策與既有 screening／library：19 通過。
- `node test/js/test_manuscript_draft_integrity.cjs`：4 個行為情境通過。
- 完整回歸尚待執行；Phase B 尚未開始實作。

## 中斷接續

先讀本檔、`git status`、本分支最近提交與 `docs/ACCEPTANCE_MATRIX.md`。
完成項目須有實際結果才能勾選；保留失敗證據，不重寫成原本已通過。
