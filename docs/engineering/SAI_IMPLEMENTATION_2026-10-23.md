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
- [ ] A2：共用筆記及對話追加採原子版本保護；前端保留衝突與未保存文字。
- [ ] A3：比較、保存、索引及降級狀態正確；原有成果不可因失敗被覆寫。
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

## 中斷接續

先讀本檔、`git status`、本分支最近提交與 `docs/ACCEPTANCE_MATRIX.md`。
完成項目須有實際結果才能勾選；保留失敗證據，不重寫成原本已通過。
