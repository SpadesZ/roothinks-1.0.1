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
- [x] A2d：自我稽核新增失敗：A 分頁 ack 會清掉 B 分頁的本機副本；研究筆記及手稿需隔離每份復原紀錄。
- [x] A3b：原檔 overwrite／併發分配與登記失敗先重現，再修復。
- [x] A3：比較、保存、索引及降級狀態正確；原有成果不可因失敗被覆寫。
- [x] A4：修正 E2E 替身的介面，不改弱 assertion；針對性與完整回歸後進入 B。
- [x] B1：PDF 與搜尋項目連結；候選、原件、別名與來源歷史保留。
- [x] B2：一般介面編輯理由與筆記；伺服器記錄確認者，防止偽造。
- [x] B3：確認文獻決策進入實際寫作請求；排除項目與章節 ACL 不繞過。
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

### Phase A 完整回歸第一輪

- 固定 SHA `023fb207cc8d5e7166084ba7915b64048c99cb7a`，命令 `python -m pytest test tests -q -p no:cacheprovider --tb=short --show-capture=no`。
- 結果：**1,203 通過、2 失敗、1 略過**，1206 案例，463.93 秒。沒有把這輪標成通過。
- 兩項是本輪引入的回歸：未授權章節 autosave 多回一個 ack；`manuscript_ws.js` 版本號改成非數字格式，既有 cache-buster 下限斷言拒絕。
- 保留原有測試斷言，恢復未授權 autosave 原行為，資產版號改為數字 5.4。
- 章節權限及 grounding 組 **63 通過**。完整回歸第二輪待執行；Phase B 保持未開始。

### Phase A 完整回歸第二輪及瀏覽器

- 固定 SHA `6987dae4661a2401f8914b4e8f311c32c593df7a`：**1,206 通過，0 失敗、0 略過**，426.75 秒。
- 第一輪略過的 `test_real_db_is_untouched_by_create_app` 在第二輪改為執行，是因為隔離工作樹已由 smoke 測試產生合成 DB；未讀取或測量正式 DB。
- Playwright 以全新容器瀏覽器＋隔離 Flask＋合成帳號實測：A 分頁保存，B 舊分頁得到衝突，A 文字與伺服器 rev=2 保留；B 文字仍在畫面，刷新可復原，下載內容與 B 相同。證據在 ignored `output/playwright/study-note-conflict.png`、`study-note-recovered.png`、`research-notes.txt`。
- 瀏覽器只連測試站，無真實論文與模型綁定。Socket 測試站首次 CORS 不含 Docker origin，修正測試站設定後連線成功，沒有改產品 CORS。
- 自我稽核加入共享 localStorage 的雙分頁測試，確實失敗：A ack 清除共用 key，使 B 的副本消失。A2d 尚未修好，Phase B 尚未開始。

## 中斷接續

先讀本檔、`git status`、本分支最近提交與 `docs/ACCEPTANCE_MATRIX.md`。
完成項目須有實際結果才能勾選；保留失敗證據，不重寫成原本已通過。

### A2d 追加修復

- 手稿共享 Map 回歸先失敗；兩種編輯器改用每份本機復原紀錄獨立 key，成功 ack 只清自己的副本。舊 key 仍可明確選擇復原，不自動取另一分頁文字。
- 筆記重新核對只顯示伺服器文字，必須由使用者按明確按鈕採用目前文字另存；CAS 仍在伺服器鎖內。
- 筆記舊版先重現無法找回，再新增鎖內原子歷史快照及 `?rev=N` 唯讀讀取；快照失敗不更動 canonical。並未遷移正式資料。
- Node 各 5 個情境通過；筆記／草稿 Python 組 18 通過。後續最終完整回歸將涵蓋此變更。

### Phase B 文獻垂直流程（待最終完整回歸）

- PDF 起步先重現沒有文獻項目，修為獨立 candidate；不依檔名自動合併。搜尋先有條目時可在兩個既有上傳入口選目標；PDF 先有時可在理由介面明確連結。
- 原 upload entry 保留為來源別名，原 paper_id、版本清單、上傳者、檔名及 PDF SHA256 保留。變更 active PDF 需重新確認；不能同一請求換既有 PDF 並宣稱 included，也不能把同一 PDF 偷連兩個主條目。
- 一般介面可填採用／排除理由及閱讀限制，以明確儲存動作確認。伺服器取真實 actor，鎖內保存修訂歷史。未存輸入以使用者＋專案＋entry 隔離於 sessionStorage，衝突時需檢視伺服器版本並明確另存。
- 新增筆記／理由修改而沒有明確重確認會回 candidate，避免把新內容冒充舊確認。文獻指紋加入內容、revision、確認人與 PDF，修正同秒修訂指紋不變。
- 引用建議先重現 candidate 可進正式引用，再改 writing scope；positive fixture 先明確人工納入，保留原 assertion。相同文字跨篇 upsert 改掉來源身分亦先重現，現在去重限定同來源類別及同篇文獻。
- 真實 Task8 provider 邊界測試已驗：未確認 PDF 不在場；確認理由、閱讀限制、PDF 證據在場；excluded、candidate、其他專案不在場；確認者及 revision 在 bundle 逐筆可查。不是成稿作者性或 AI 率的量測。
- 已跑組別：文獻／CoC 34 通過；provider／citation／index 13 通過；HTTP 人工決策及跨角色 4 通過；Node 文獻決策 2 情境（送出後新文字先失敗再修）。
- 隔離瀏覽器已上傳合成 PDF、匯入同篇搜尋書目、明確連結、填理由與限制、確認 included，來源別名與修訂可見。證據 `output/playwright/literature-human-decision.png`。瀏覽器發現內嵌表格過短會藏操作，已改為頁面自然捲動，待新版畫面複查。
- 這是 Literature → Manuscript 的垂直實作；COC-02 全模組確認、作者性、中文輸入法及教授審閱完整閉環仍未驗收。
- 06:10 接續提醒已觸發並移除，避免重複啟動。本 chat 保持同一隔離工作樹與分支繼續。

### Phase A/B 完整回歸第一輪及瀏覽器追加發現

- SHA `f0877399cad836e23554f5e9c4c409e3b326463e`：1,216 通過、3 失敗，413.55 秒。證據 `phase-ab-full-1.xml/.txt/-receipt.json`，未標成通過。
- 一項舊 smoke 要求 JS 完全不可讀 provenance；SAI UX-03 現要求一般介面能查，改驗主表仍四欄、詳情可查來源、仍無列上傳入口。引用 smoke positive fixture 先明確納入，原引用／決策 assertion 保留。草稿復原的舊靜態呼叫點維持 `draft.content`，不刪其 assertion。
- 真瀏覽器：A 文獻舊分頁送出遇 409；刷新復原 A 文字、顯示 B 新理由與修訂 3，只有按明確另存才成功。證據 `literature-conflict-recovery.png`。
- 真手稿瀏覽器發現新增復原選單的 input 事件會排程空白 autosave；已用真實 init 接線 Node 案例重現，監聽器排除表單控制，仍保留正文／程式插入既有路徑。
- 進一步負向測試重現：復原 stale 副本若借用最新 rev，離頁 beacon 可繞過尚未解決的衝突。手稿與筆記復原現在沿用副本原始 base_rev；未解手稿衝突只留本機副本，不送 beacon，並保留離頁提示。
- Node：手稿輸入邊界 3、手稿草稿 6、筆記 6、文獻決策 2 情境通過；編輯訊號／library smoke 21 通過。最終完整回歸及修後瀏覽器待完成。
