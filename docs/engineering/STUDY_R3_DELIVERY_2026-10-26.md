# Study R3 交付：跨模組安全、共研旅程、翻譯保護層、評估協議 v2

日期：2026-10-26。分支 `codex/study-coresearch-r3-20261026`（從 R2 `ae8faa6` 開出）。**未合併、未部署；沒有任何一項是 RELEASED。**
規格與整體狀態以 [`docs/SAI.md`](../SAI.md) 為唯一入口。

## 1. 分支與 commit

| commit | 內容 |
|---|---|
| `ff71c07` | 安全：跨模組權限矩陣、runtime log 只給系統管理員、Flow B 探針只回本專案、AI 對話納入審閱範圍、R2 缺陷修正 |
| `994ee33` | 翻譯保護層原型＋比較研究 |
| `34ab1df` | 評估協議 v2（在任何真模型呼叫之前登記） |
| `fba0c46` | 共研 UX：繁中用語、機器翻譯標示、證據筆記寫入研究筆記、不確定性、修訂前內容、矩陣重複請求 |
| `0de2f3d` | 本文件、SAI、驗收矩陣、接手文件 |
| `8808665` | 測試隔離：權限矩陣測試測完還原環境變數與延遲建立的服務單例 |

## 2. 安全與權限（R3-1）

工具 `test/integration_e2e/acl_route_matrix.py`：列舉 **90 個 GET 路由 × 6 角色**（owner、editor、教授＝審閱範圍、舊 MentorLink 導師、團隊 viewer、外人），
共 888 次請求，在私人筆記、AI 對話、矩陣、其他專案的 log 等位置埋入哨兵字串，回應中出現哨兵就記為發現。

| 發現 | 嚴重度 | 修補前 | 修補後 |
|---|---|---|---|
| `/api/literature/debug/runtime_log_tail` 沒有 pid，Literature ACL 不會執行；**任何登入者（含外人）可讀整份伺服器 log** | P1 跨專案 | 洩漏 | 只限系統管理員 |
| `flowb_probe` 用通用關鍵字過濾共用 log，成員可讀到**其他專案**的 log 行；並回傳伺服器 log 路徑 | P1 跨專案 | 洩漏（6 角色） | 只回含本專案 pid 的行（字詞邊界，`ACLP1` 不會命中 `ACLP12-p`）；不回路徑 |
| 教授（審閱範圍）可讀 Literature AI 對話 | 審閱範圍 | 洩漏 | 403 `review_scope` |
| 教授可讀 PAQ AI 對話 | 審閱範圍 | 洩漏 | 403 `review_scope` |
| 教授可讀 Literature 文獻庫的閱讀筆記 | 審閱範圍 | 洩漏 | **保留**：是否收窄是 Owner 決定（PA-01 擴充，見 SAI §8） |

證據：`study_r3_evidence/acl-matrix-before.json`（19 筆：16 跨專案、3 審閱範圍）→ `acl-matrix-after.json`（1 筆：上表最後一列）。
測試 `test/unit/test_acl_cross_module.py`（8 個；先在修補前跑出 7 個失敗再修）。它鎖定矩陣結論：唯一剩下的發現就是教授＋文獻庫閱讀筆記。

**舊 MentorLink 導師**：新設定 `REVIEW_SCOPE_LEGACY_MENTORS`。
- `keep`（預設）：維持現有存取，**不撤銷任何歷史權限**。
- `review_scope`：把「只有 MentorLink、沒有教授邀請」的 viewer 收窄成審閱範圍。
- 團隊邀請的 viewer 永遠不收窄。
- 切換前先跑唯讀報告 `python scripts/review_scope_legacy_report.py`，列出會受影響的成員。

**低風險、只記錄**：`system_profile` 與 `preload_translation_models` 只回系統資訊，不含專案資料。

## 3. R2 獨立複核（R3-2）

- 審閱副本（`review_shared`）原本帶出修訂前的全文（`previous_statements`）與未處理的成員建議 → 改成只留動作、人、時間；不帶建議。
- 依舊版本提出的建議被接受時，會默默蓋掉作者之後的改寫 → 建議記錄 `based_on_revision`。過期時回 409 `stale_suggestion`；
  前端要作者確認後才帶 `allow_stale` 重送。
- 作者改寫遇到 409 時，重新載入並保留已輸入文字。
- 引文比對、錨點、所有權其餘部分複核無誤（R2 單元測試與瀏覽器檢查原樣通過）。

## 4. 共研旅程與 UX（R3-3）

依據：R2 瀏覽器截圖與程式閱讀，**不是**美化。

| 問題（證據） | 改動 |
|---|---|
| 繁中介面殘留英文狀態字：Idle、Context: None、Original Text、Single／Matrix View、Fulltext (Block)、Block 對照版 | 全部改成研究用語（尚未開啟、尚未選取內容、原文、比較矩陣、全文（段落對照）等） |
| 譯文欄只寫「中文翻譯」，沒說是機器翻譯 | 「中文翻譯（機器翻譯）」；提示「引用請以原文為準」 |
| 沒有矩陣的專案每次開頁打 4 次 `/matrix/latest`（404 被當暫時錯誤重試） | 只有 5xx 才重試；瀏覽器量到 1 次 |
| 證據筆記接受後無法帶進寫作 | 「插入研究筆記」：附加含出處、頁碼、比對狀態、研究者判斷、AI 不確定性，以及「引文找到不代表支持」的文字；走既有有版本的存檔路徑 |
| AI 不確定性只在草稿看得到，存成筆記後消失 | 筆記卡顯示「AI 自評不確定性」 |
| 修訂紀錄只寫「誰改了」，作者看不到改之前寫什麼 | 作者與成員可展開每次修改前的內容；教授的審閱副本沒有（瀏覽器驗證） |

**不變條件**：全文不可讀、沒有教授、引文找不到時，筆記仍可存與匯出（`test_notes_save_and_export_without_professor_or_verified_evidence`）。

## 5. 翻譯（R3-5）

詳見 [比較研究](TRANSLATION_RESEARCH_2026-10-26.md)。
- **只用公開來源**。沒有安裝 REA、沒有逆向 Immersive Translate、沒有引入 AGPL 程式碼、沒有新增外部傳輸。
- 原型 `translation_guard.py`（獨立撰寫）：
  - 基本遮罩與舊作法逐字相同；
  - 佔位符被改空白或大小寫仍能還原；
  - 真的遺失時補回並標示；
  - 回報數字變動；
  - 擴充保護預設關閉。
- 模擬量測：空白型損壞下，舊作法保留 0／24 片段、20 句無聲遺失；新作法保留 24／24、0 句無聲遺失。數字變動偵測 32／32。
  **這是模擬，不是真引擎品質。**

## 6. 評估（R3-4）

[協議 v2](STUDY_EVAL_PROTOCOL_V2_2026-10-26.md) 在任何真模型呼叫前登記。v2 做了這些事：
- 修正 Q4 誤算 E1／E2 標籤與列表編號；
- 新增中文過度宣稱 Q7（處理否定詞）；
- 6 個單一錯誤負向控制，6／6 只打破預期門檻；
- 溯源資料（程式 SHA、資料集與評分程式雜湊、每段提示詞與模型原文雜湊）；
- 離線重評，可偵測竄改；
- Cohen's κ。

**真模型未執行**（沒有產品憑證，也不得自行花費）。Tutor 評估與科學引擎分開；Gate D 未啟動。

## 7. 驗證

- 完整回歸：**1,683 通過／50 略過／0 失敗**（`8808665`，3.12 venv，`APP_ENV=test`，資料防護開啟）。見 §9。
- 瀏覽器旅程（after，真 Chromium，npm 發佈資產）：**48／48**，包含 8 項 R3 新檢查。證據在 `study_r3_evidence/browser/`。
- 負向控制：權限矩陣修補前紅、修補後綠；評估 6／6；翻譯保護層對忠實引擎 0 誤報。

## 8. 風險與 Owner 決定

1. **PA-01 擴充**：審閱範圍是否也擋 Literature 文獻庫閱讀筆記（目前教授可讀）。Literature 與 PAQ 的 AI 對話已在分支上擋下，這一點也要 PA-01 一起核准。
2. 舊 MentorLink：維持 `keep`，或在跑完報告後改成 `review_scope`。
3. **NLLB 權重授權是 CC-BY-NC-4.0**（非商業）。若有商業用途，需要法務確認。
4. 任何 BabelDOC／pdf2zh_next（AGPL）整合，或任何把文件送到外部服務的決定。
5. 真模型評估所需的憑證與預算；人類評分者。
6. PA-01／02／03 仍是**提議**。

## 9. 回歸結果

- 第一次（`0de2f3d`）：1 失敗／1,656 通過。原因：權限矩陣走過每條 GET 路由時，建立了綁在矩陣 tmp 目錄的
  `literature_routes.context_chain_service` 單例；之後的 user journey 測試把 `context_chain.json` 寫到錯的根目錄。
  這是**測試之間的狀態污染**，不是產品缺陷（單獨跑兩者都通過）。
- 修正 `8808665`：矩陣測試先登記，測完再還原環境變數，以及 literature／study／paq 模組的 `DATA_ROOT` 和 `*_service`。
- 第二次（`8808665`）：**1,683 通過／50 略過／0 失敗**。略過原因與先前相同（NLTK、WordNet、Windows、正式 DB）。
- 瀏覽器旅程（`fba0c46` 的前端；之後沒有再改前端）：48／48。
