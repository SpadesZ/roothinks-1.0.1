# Roothinks 決策紀錄

本檔保存無法只從語法還原、且後續維護不得任意改寫的產品／安全決策。
程式中的 `NOTE(NOTE-NNN):` 必須能在此找到同號條目；若行為改變，需同步更新
決策、測試與引用處，禁止留下失效 reference。

## NOTE-001：MentorLink 不授予 2C 專案內容權限

- 決策日期：2026-08-10
- 適用範圍：`app/mentor/routes.py` 的 reviewer 2C API。
- 決策：`MentorLink` 只表示 mentor／mentee 關係。一般 mentor 必須與 mentee
  同時具有目標 workspace 的非 section-scoped membership，才能讀寫該專案的
  reviewer 意見／資源；admin 可明確跨專案，但 mentee 本人仍須能讀完整 2C。
- 原因：若關係資料同時充當內容授權，mentor 只要輸入任意已註冊 Email 就能自行
  建立讀取權，繞過 project owner。
- 驗證：`test/unit/test_mentor_scope.py` 的 shared-project、link-only、section-scoped
  與 admin cases。

## NOTE-002：聊天取消必須終止實際 provider 工作

- 決策日期：2026-08-10
- 適用範圍：Manuscript 2A chat job、LLM dispatcher／bus 與三個 provider adapter。
- 決策：取消訊號使用同一個 `threading.Event` 由 Socket job state 一路傳到 provider；
  OpenRouter、OpenAI、Google 的 cancellable transport 必須關閉進行中的 HTTP coroutine，
  quota wait 與 retry backoff 也必須可中斷。取消後不得啟動下一個 LLM stage、寫入 AI
  對話或送出成功事件。
- 原因：只在 provider 回傳後丟棄結果，仍會消耗 token 並佔住最多四個 chat worker。
- 維護邊界：新增 adapter 時必須接受可選 `cancel_event`；沒有 cancellation 的普通呼叫
  仍沿用既有同步路徑以控制變更面。
- 驗證：`test/unit/test_llm_cancellation.py`、
  `test/unit/test_manuscript_chat_ack.py::test_worker_passes_cancel_event_into_drafter`。

## NOTE-003：Reviewer 畫面只接受目前選擇的非同步回應

- 決策日期：2026-08-10
- 適用範圍：`app/static/js/mentor.js` 的 mentee 與 2C project 載入。
- 決策：回應套用前必須同時確認 request sequence、mentee id 與 project id；舊 request
  的成功或錯誤都不得覆蓋目前畫面或 `currentReview`。
- 原因：A 專案晚於 B 回來時，畫面若顯示 A、寫入 URL 卻仍指向 B，reviewer 會把意見
  寫到錯誤論文。
- 驗證：`test/js/test_mentor_review_race.cjs` 以 B 先回、A 後回的可執行情境驗收。
