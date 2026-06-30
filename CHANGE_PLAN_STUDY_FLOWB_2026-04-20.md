# Roothinks-R Study/FlowB 變更可行性計畫（分析版）

- Date: 2026-04-20
- Scope: `roothinks-R/roothinks`
- Status: Analysis only（本文件僅規劃，不含程式實作）

## 1. 需求背景與目標

依據討論，需評估以下變更之可行性：

1. Study 模組的矩陣比較「最近記錄」需支援改名與刪除。
2. Study 模組看單篇時，全文能否在矩陣比較區可視化，讓使用者可邊看文邊詢問右側 AI 家教。
3. 修正單篇提問時 AI 家教回覆「沒有看到這篇」的問題。
4. Flow B（雙語對照/摘要）由「區塊直翻」提升為「語意重組」產物（abstract/introduction...）。
5. 釐清是否需要新增 LLM API key 與 LAVA task 綁定策略。

## 2. 系統現況（掃描結論）

### 2.1 Study 矩陣歷史

- 目前有讀取 API，無改名/刪除 API。
- 核心路由：
  - `app/core_pro/study/study_routes.py`
    - `GET /api/study/<pid>/matrix/history`
    - `GET /api/study/<pid>/matrix/<matrix_id>`
    - `GET /api/study/<pid>/matrix/latest`
- 前端歷史列表：
  - `app/static/js/study_core.js` (`loadMatrixHistory`, `loadMatrixRecord`)
  - `app/templates/study.html` (`#historyListContainer`)

### 2.2 單篇全文顯示與右側 AI 家教互動

- 目前全文是 modal 呈現，不是矩陣區內常駐區塊。
  - `app/static/js/study_view.js` (`loadFulltext`)
  - `app/templates/study.html` (`#fulltext-modal`)
- Task7 context 主要依賴 `focus_pids`。
  - `app/static/js/study_chat.js` (`setContext`, `_buildContextPayload`)
  - `app/core_pro/study/study_routes.py` (`/api/study/chat`)
  - `app/llm_service/matching_tasks/task_7qachat.py`
- 已發現選字監聽 selector 與渲染 DOM 不一致：
  - 監聽：`#fulltext-content .selectable-text`
  - 現行渲染未穩定提供 `.selectable-text` 節點
  - 造成單篇全文情境下 context 觸發不穩定。

### 2.3 Flow B（翻譯/雙語）現況

- Flow B 路由：`POST /api/literature/run_translation`
  - `app/core_pro/literature/literature_routes.py`
- 核心翻譯流程：
  - `app/core_pro/literature/literature_translator.py`
  - 目前主要是 block/page 層級翻譯，產出 `content_zh`，不做章節語意重組。
- 產物路徑：
  - 輸入：`05_interprets/fusion/full_text.json`
  - 輸出：`06_translates/fusion/full_text_trans.json`

### 2.4 LAVA Setup / task bindings

- 綁定表為 `task_bindings`，LAVA 前端可動態顯示 task list。
  - `app/static/js/lava_setup.js`
  - `app/llm_service/llm_routes.py`
- 重要風險點：
  - `app/llm_service/llm_model.py` 初始化會刪除非預設 task_id 的綁定列。
  - 若新增 task（例如 `task_5b_reflow`），必須同步納入 default task 清單/遷移策略。

## 3. 變更方案與可行性評估

## 3.1 Study 矩陣歷史：改名 + 刪除

- 可行性：高
- 建議策略：
  1. 保持 `matrix_id` 不可變（作為檔名與主識別）。
  2. 新增可編輯欄位 `display_name`（存入 record json）。
  3. 新增 API：
     - `PATCH /api/study/<pid>/matrix/<matrix_id>/rename`
       - payload: `{ "display_name": "..." }`
     - `DELETE /api/study/<pid>/matrix/<matrix_id>`
  4. 刪除後同步維護：
     - `study/matrix_history/latest.json`
     - `study/latest_matrix.json`
     - 若刪除的是 latest，需回填下一筆最新記錄；若無記錄則移除 latest 指標。
  5. legacy 記錄（`legacy::...`）可採「只允許刪除，不允許改名」策略，降低風險。

- 前端調整：
  - `study_core.js` 的歷史列表每項新增更多操作（rename/delete）。
  - 選取中項目被刪除時，UI 需回退到下一筆可用記錄或清空狀態。

- 風險：
  - 歷史檔案直接檔案系統操作，需加強 lock 與錯誤回滾。

## 3.2 單篇全文顯示在矩陣區（方便右側提問）

- 可行性：中高
- 建議策略（推薦）：
  1. 將全文閱讀由 modal 轉為 matrix-pane 內嵌 reader mode（或新增可切換面板）。
  2. 保留右側 AI 家教 pane 常駐，形成「左側閱讀 + 右側問答」固定工作流。
  3. `study.html` 調整為 matrix 區可切換：
     - Matrix View
     - Fulltext View（單篇）
  4. `study_view.js` 調整 `loadFulltext()` 目標容器，不再依賴 modal lifecycle。

- 預估影響：
  - 主要在前端模板與 layout，不需新增後端 API。
  - `get_fulltext` API 已可提供 `blocks + translation_ready + summary`，足夠支撐。

### 3.2.1 單篇 vs 多篇比較矩陣的判定規則（新增）

- 可行性：高（建議列為 UI 行為規格）
- 核心原則：不要用「匯入篇數」自動猜目前模式，改用「使用者動作」驅動狀態機。

- 建議狀態機：
  1. `idle`：已匯入文獻，但尚未進入閱讀或比較。
  2. `single`：使用者點擊「閱讀全文」後進入單篇閱讀。
  3. `matrix`：使用者勾選至少 2 篇並點擊「生成比較矩陣」後進入比較模式。

- 觸發規則：
  1. 匯入多篇完成後預設停在 `idle`，不自動切到矩陣。
  2. 點「閱讀全文」=> `single`。
  3. 點「生成比較矩陣」且選取數 >= 2 => `matrix`。
  4. 在 `matrix` 下再點任一篇「閱讀全文」=> 切回 `single`（保留最近矩陣快取供返回）。

- Task7 context 綁定規則：
  1. `single`：`focus_pids = [active_paper_id]`
  2. `matrix`：`focus_pids` 來自點擊的矩陣 cell（A/B/Synthesis）
  3. `idle`：若尚未選定上下文，顯示提示引導使用者先點文獻或矩陣格

- 驗收重點：
  1. 匯入 3+ 篇時，系統不會自動誤判進入矩陣模式。
  2. 使用者每次切換模式時，右側 AI context badge 與 `focus_pids` 行為一致。
  3. 單篇與矩陣來回切換不遺失最近一次有效上下文。

## 3.3 單篇提問 AI 說「沒看到這篇」

- 可行性：高（可先做 P0 修正）
- 根因摘要：
  1. Task7 若 `focus_pids` 最終為空，會回退到「No specific paper selected.」。
  2. 全文選字 context 綁定 selector 不穩，導致 `setContext` 沒被觸發。

- 建議修正：
  1. 單篇全文載入成功後，立即執行一次：
     - `studyChat.setContext({ pid: paper_id, focus_pids: [paper_id], source: 'fulltext', dimension: 'Fulltext' })`
  2. 修正選字監聽：
     - 統一渲染容器 class（補 `.selectable-text`）
     - 或監聽 `#fulltext-content` 的 selection 事件。
  3. 維持後端現有 `pid_hint -> focus_pids` fallback，不移除。

## 3.4 Flow B 語意重組（非純區塊直翻）

- 可行性：中（屬流程增量，不建議直接覆蓋既有翻譯檔）
- 建議策略：
  1. 在 Flow B 翻譯完成後新增「Reflow 子步驟」。
  2. 讀取來源：
     - `05_interprets/fusion/full_text.json`（原文結構）
     - `06_translates/fusion/full_text_trans.json`（翻譯）
     - `05_interprets/summary.json`（摘要提示）
  3. 產出新檔（避免破壞既有相容）：
     - `06_translates/reflow/semantic_sections.json`
  4. 建議 schema：
     - `meta`: pid/paper_id/model/version/created_at
     - `sections`: `[{ section_label, confidence, source_block_refs, content_en, content_zh }]`

### 3.4.1 Reflow 的章節標示與使用者顯示規格（新增）

- 可行性：高（屬輸出 schema 與前端渲染規格）
- 規格要求：
  1. 每個 reflow 段落必須含 `section_label`，例如：`Abstract` / `Introduction` / `Methods` / `Results` / `Discussion` / `Conclusion`。
  2. 每段必須含 `source_block_refs`（page/block 對應），確保可追溯到原始內容。
  3. 前端顯示時每段都要顯示章節標籤，不可只顯示段落文字。
  4. 若章節為模型推定，需顯示輔助標記（如「AI 推定章節」），避免誤認為原文既有標題。
  5. 若無法判定章節，標示為 `未分類段落`，不可留空。

- UI 呈現建議：
  1. 段落 header：`[章節標籤] + 信心值(可選)`。
  2. 段落 footer：`來源：pX-bY...`（可展開查看更多 refs）。
  3. 可加入「僅看某章節」篩選，提升閱讀效率。

- LLM 策略：
  - 先可重用既有 task 綁定（較快落地）。
  - 若要成本與風險隔離，再新增專用 task（見 3.5）。

- 風險：
  - token 成本與執行時間上升。
  - OCR 髒資料會影響段落判斷品質，需 fallback heuristic。

## 3.5 是否需要新 LLM 與新 API key？

- 結論：不一定。

### 方案 A：沿用既有 task/connection（先上線）

- 優點：最快、改動面最小。
- 缺點：與既有任務共用配額，成本不可細分。

### 方案 B：新增專用 task（如 `task_5b_reflow`）+ 獨立 key（可選）

- 優點：可獨立控成本、模型、速率與 SLA。
- 缺點：需更新：
  - `llm_model.py` default task 白名單
  - LAVA setup 對應綁定流程
  - 測試與遷移腳本

- 建議決策：
  - 先 A 後 B（先驗證功能價值，再決定是否隔離成本與線路）。

## 4. 分階段實施路線（建議）

1. P0（0.5-1 天）：修正單篇 context 穩定性
2. P1（1-1.5 天）：矩陣歷史 rename/delete
3. P2（1.5-3 天）：全文改為矩陣區內嵌閱讀模式
4. P3（3-5 天）：Flow B reflow 語意重組（先共用現有綁定）
5. P4（0.5-1 天，可選）：若需要再拆出專用 task + 獨立 key

## 5. 驗收標準（DoD）

1. 歷史記錄可改名、可刪除，且 latest 指標與 UI 一致。
2. 單篇閱讀時可在同頁面邊讀邊問，AI 不再出現「沒看到這篇」。
3. Flow B 可產出語意重組檔，內容有章節化語意而非僅 block 直譯，且前端可見章節標示與來源對應。
4. LAVA 綁定策略明確，可在不新增 key 的情況下運作；若新增 task 也可正常保存綁定。
5. 既有 E2E（Study/LAVA 路由）不回歸。

## 6. 測試建議

- 擴充 `test/integration_e2e/test_user_journey_roothinks.py`：
  1. matrix history rename/delete API 驗證
  2. 單篇 context payload（`focus_pids`）驗證
  3. Flow B reflow 產物存在與 schema 驗證（含 `section_label`/`source_block_refs`）
  4. 若新增 task_id，驗證 binding list/update/lock/unlock 正常

## 7. 主要風險與緩解

1. `task_bindings` 非白名單刪除風險  
   - 緩解：新增 task 時同步更新 default_tasks 與 migration。
2. Reflow 增加 token/cost  
   - 緩解：限制輸入長度、分段批處理、允許降級模式。
3. OCR 噪音造成誤分段  
   - 緩解：建立 heading 優先 + heuristic fallback。
4. 前端 layout 複雜化  
   - 緩解：先做可切換模式，再進一步優化 UI 細節。

## 8. 本文件邊界

- 本文件只定義可行性與規劃，不包含任何實作程式碼變更。
- 後續若啟動實作，建議先從 P0 開始，以最快修正單篇 AI context 問題。
