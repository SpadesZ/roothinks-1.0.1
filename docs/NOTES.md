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

## NOTE-004：切章的持久化必須綁在唯一入口

- 決策日期：2026-08-10
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `requestSectionSwitch()`。
- 決策：記住目前章節（`rememberChatSection`）一律在 `requestSectionSwitch()` 內執行，
  呼叫端不得各自處理，也不得繞過本函式自行 emit `cmd_list_blocks`。
- 原因：章節下拉（`manuscript_wsui.js` 的 `sectionDropdownMenu`）當初直接呼叫本函式
  而未經 `switchChatSection`，於是 localStorage 永遠停在上一次 2A 同步的章節。
  實測：切到 results → 重整 → 畫面回到 abstract，使用者的所在位置遺失。
- 驗證：`test/unit/test_manuscript_section_switch.py`；瀏覽器實測「切到 discussion
  → 重整 → 仍停在 discussion V0.2」。

## NOTE-005：頁載延遲還原不得覆蓋使用者已進行的切章

- 決策日期：2026-08-10
- 適用範圍：`app/static/js/manuscript_ws.js` socket connect 後的 500ms 還原計時器。
- 決策：`_sectionReqSeq > 0`（已有人正式切過章）時直接略過還原。
- 原因：使用者在這 500ms 內自己切章甚至已開始打字時再還原，會把畫布換成快取章節的
  已存版本，未存內容無聲消失。
- 驗證：`test/unit/test_manuscript_section_switch.py` 的還原守衛測試。

## NOTE-006：字數只計算 .card-content

- 決策日期：2026-08-10
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `updateWordCount()`。
- 決策：只統計 `.card-content`，不統計整個 `editorCanvas.innerText`。
- 原因：每張 editor-card 都帶章節標籤 badge，整片統計會把 UI 文字算成內容。
  實測：切到從未存檔的 Reference 時，空白畫布顯示「1 字」，那個 1 就是標籤本身。
  存檔取的也是 `.card-content`，統計口徑必須與儲存口徑一致。
- 已知限制：以空白切分，中日韓文整段會被算成 1 字。此為既有行為，本次未改動，
  以免所有章節顯示的數字一次全變。
- 驗證：瀏覽器實測「空白章節字數為 0」。

## NOTE-007：2C 以章節為鍵原位 upsert，不得 append

- 決策日期：2026-08-10
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `cardActionPushToFusion()` 與
  `_ensureFusionBlock()` / `_findFusionBlock()` / `_clearFusionPlaceholder()`。
- 決策：2C 每一章對應唯一一個 `.fusion-block[data-section=<id>]`；推送時原位取代該區塊，
  不存在才依 `app.sections` 的宣告順序插入。沒有 `data-section` 的既有內容原地保留。
- 原因：舊版 `fusionCanvas.innerHTML += ...` 是純 append，每修一次 Introduction 再推一次
  就多一段 Introduction，且排列順序取決於推送先後而非論文章節順序。
  身分放在 `data-section` 是因為 2C 存檔存的就是 `fusionCanvas.innerHTML`、載入時原樣
  回填，屬性能隨 G.Ver 往返，重整後仍冪等。
- 不猜舊內容歸屬：實測既有存檔（PAPER1-p 的 V2）是 0 個 fusion-block 的裸段落，
  沒有任何章節身分；靠標題文字猜歸屬只會把使用者的舊稿搬錯位置。
- **2026-08-15 修正（見 NOTE-037）**：上一行對「裸段落」仍然成立，但對
  **NOTE-007 之前產生的 `.fusion-block`** 不成立 —— 那些區塊有標題卻沒有
  `data-section`，於是每推一次就在最後面多一個同名章節（擁有者在 G.Ver V12 的
  專案上實機撞到兩個 Abstract）。NOTE-037 因此加了一道**極窄的**一次性認領：
  只認領「完全沒有 `data-section`」且「標題與章節 label **全等**」的區塊，
  認領時不搬動位置、只補寫識別屬性。
- 驗證：瀏覽器實測「亂序推 introduction→method→abstract 得到 canonical 順序」、
  「改內容重推原位取代仍 3 塊」、「存檔 V3 後重整再推第三次仍不重複」。

## NOTE-008：留言必須綁定被評論的版本

- 決策日期：2026-08-10
- 適用範圍：`app/models.py` 的 `ChapterComment.scope / s_ver / g_ver`。
- 決策：`scope='section'` 綁 `s_ver`（2B 章節留言）、`scope='paper'` 綁 `g_ver`
  （2C 全文留言）。版本定位在建立當下寫死，之後不隨內容編輯改變。
- 原因：舊結構只有 `pid + section_key`，2B 存成新版後，針對舊版寫的意見會原封不動
  出現在新版旁邊，看起來像在說新版的問題；2C 換一個 G.Ver 就整批漂移。
- 驗證：`test/unit/test_comment_versioning.py`。

## NOTE-009：留言版本欄位採純增量遷移，舊留言不回填

- 決策日期：2026-08-10
- 適用範圍：`fix_db_schema.py` 的 `_upgrade_chapter_comments_table()`。
- 決策：只 `ADD COLUMN`，不重建資料表；舊留言的 `s_ver`/`g_ver` 保持 NULL。
  `scope` 給 `DEFAULT 'section'`（既有留言全來自 2B 側欄，這個預設對它們正確）。
- 原因：我們無從得知舊留言當時針對哪一版，硬把它回填成目前版本等於偽造證據，
  讓使用者以為那句意見是在說現在這一版。純增量也讓程式碼回退後舊版仍可運作。
- 驗證：migration drill 實測「列數 2→2、既有內容完好、integrity ok」。

## NOTE-010：留言依版本嚴格篩選，舊留言另立分類

- 決策日期：2026-08-10
- 適用範圍：`app/core_pro/manuscript/chapter_routes.py` 的 `list_comments()` /
  `create_comment()`，與 `app/static/js/manuscript_collab.js`。
- 決策：帶了 `s_ver` / `g_ver` 就嚴格篩選；沒有版本定位的舊留言只有在
  `include_legacy=1` 時才回傳，並以 `legacy_unversioned` 標記，前端顯示為「未標版本」。
- 原因：把來歷不明的留言預設顯示在每一版旁邊，正是「留言漂移」本身。前端顯示版本
  徽章也是必要的：同一章不同版的意見會並存，看不到版本就無從判斷這句話在講哪一版。
- 2C 全文留言的 ACL 與「能否讀整篇」同一道門檻（section-scoped 角色一律 403）：
  全文是所有章節組裝的成品，放行等於讓限定編輯繞過章節層讀取限制。
- 驗證：`test/unit/test_comment_versioning.py` 的版本隔離、scope 隔離、legacy 與
  ACL 正負向案例。

## NOTE-011：草稿復原提示不得使用原生 confirm()

- 決策日期：2026-08-10
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `_offerDraftRestore()` 與
  `_showDraftRestoreBar()`。
- 決策：改用與 `conflict-bar` / `peer-update-bar` 同一套的畫布內非阻塞提示條，
  提供「復原草稿」與「保留目前內容」兩個選項，且**不自動消失**。
  語意與原本完全相同：復原＝載入草稿；忽略＝保留目前內容，草稿在下次存檔時被覆蓋。
- 原因：
  1. 原生 `confirm()` 會凍結整個分頁 —— 對話框開著時 socket 回應全部排隊，
     使用者連捲動看一眼「目前內容是什麼」都做不到，卻要當場決定是否覆蓋它。
  2. 它是**自動彈出**的（切章時偵測到草稿就跳），不是使用者主動觸發的破壞性操作。
  3. 原生對話框不在 DOM 裡，會讓瀏覽器自動化驗證整個停擺：實測 `preview_eval`
     連 `1+1` 都逾時，且無法從頁面內關閉，只能重啟瀏覽器。
- 不在此範圍內：由使用者主動點擊觸發的破壞性確認（刪留言、取消章節指派、
  還原主論文版本、刪章節、開啟舊版前的強制備份）仍保留 `confirm()` —— 那些是
  使用者剛按下按鈕、預期被問一次的情境，且不會不請自來。
- 驗證：`test/unit/test_manuscript_section_switch.py::TestDraftRestoreIsNonBlocking`；
  瀏覽器實測「切到有草稿的章節時出現提示條、頁面仍可操作」。

## NOTE-012：COC 由伺服器依真實版本組裝，前端不得指定版本真相

- 決策日期：2026-08-10
- 適用範圍：`app/core_pro/manuscript/manuscript_routes.py` 的 `chat_message` handler、
  `app/core_pro/manuscript/manuscript_ruling.py` 的 `validate_and_prepare()`、
  以及 `app/static/js/manuscript_soed.js` 送出草稿請求的兩處。
- 問題背景：前端把 `s_ver: '0.1'` 寫死送出（`manuscript_soed.js` 兩處），後端
  `data.get('s_ver', '0.1')` 連預設值都是 0.1。`_read_local_file(pid, section, s_ver)`
  因此永遠讀 V0.1，而且只在 2B 畫布不足 50 字時才觸發。正文本身則是
  `editorCanvas.innerText.substring(0, 3000)` —— 既截斷長文，又把卡片章節 badge
  等 UI 文字一起送進 LLM（innerText 含 badge 已由字數統計的 NOTE-006 實證）。
- 決策：前端只送 `pid / section / user_msg / job 資訊`。版本、正文、其他章節與 2C
  一律由伺服器依真實 S.Ver / G.Ver 讀取乾淨來源後組裝成 COC bundle。
- 原因：版本是資料真相，不能由使用者端宣告。前端宣告版本同時造成三種錯誤：
  版本偽造（留言與 audit 綁到不存在的版本）、UI 文字污染語料、以及長文被靜默截斷。
- 否決方案：讓前端組整包 COC 送上來。否決理由是它把版本真相與跨專案取材權都交給
  瀏覽器，等於把偽造版本與跨專案資料外洩變成一個 payload 就能達成的事。
- 不變量：
  1. 進入 prompt 的正文只能來自伺服器端讀取的版本檔或清理過的 `.card-content`，
     不得包含 badge、按鈕字樣等 UI 文字。
  2. 任何寫入 audit / 留言 / snapshot 的版本號都必須來自伺服器解析，不得取自請求體。
  3. COC bundle 只含本專案資料；跨專案取材一律視為缺陷。
- 遷移/回滾：前端仍可送 `s_ver`，但伺服器忽略之（只記錄於 audit 供比對）。
  保留一輪相容期後再移除欄位，避免舊快取的前端送出即失敗。
- 驗證：`test/unit/test_coc_bundle.py`；以哨兵資料確認真正送出的 provider request
  內含真實 S.Ver 而非 0.1，且不含 badge 文字。

## NOTE-013：Manuscript evidence 只接受 included，excluded/unknown 不得靜默注入

- 決策日期：2026-08-10
- 適用範圍：`app/services/evidence_index_service.py` 的 `search_evidence()`、
  `app/core_pro/manuscript/context_inject.py` 的 `retrieve_paragraph_context()`。
- 問題背景：`retrieve_paragraph_context` 只傳 `project_id / query / top_k`，
  而 `search_evidence` 僅依 `project_id` 與 `source_types` 過濾。整條檢索路徑
  沒有任何欄位承載人類的納入／排除決策，因此作者標記為排除的論文仍可能被引用。
- 決策：文獻狀態三分並在檢索層強制：
  `included` 可作為正式 Drafter 依據；`excluded` 硬阻擋；
  `unreviewed/unknown` 不得作為正式寫作依據，但保留在 Literature discovery。
  取不到納入狀態時 fail-closed（當作 unknown 而非放行）。
- 原因：人工篩選是作者的學術判斷。若排除後仍可能被引用，那個判斷等於沒有效力，
  而且錯誤會直接出現在稿件的引用裡 —— 這是學術誠信問題，不是體驗問題。
- 否決方案：在 prompt 裡用文字提示 LLM「不要引用這些」。否決理由是那不是強制力，
  且會浪費 token；過濾必須發生在檢索層。
- 不變量：
  1. 正式寫作路徑（Drafter evidence）永遠不得出現 `excluded` 的來源。
  2. 狀態未知時預設不可用於寫作，不得預設放行。
  3. `unknown` 不等於刪除：Literature 探索介面仍要看得到，避免資料從系統消失。
- 遷移/回滾：既有 EvidenceSegment 沒有納入狀態欄位，遷移後一律視為 `unknown`，
  由作者逐步標記。回滾只需讓過濾層放行，不動資料。
- 驗證：`test/unit/test_evidence_inclusion.py`；哨兵論文標為 excluded 後，
  確認它不出現在真正送出的 request，且仍能在 Literature 清單查到。

## NOTE-014：stale ContextChain 對寫作路徑 fail-closed

- 決策日期：2026-08-10
- 適用範圍：`app/services/context_chain_service.py` 的查詢回傳、
  `app/core_pro/manuscript/context_inject.py` 的 context chain fallback、
  `app/core_pro/study/study_routes.py` 取用 `compact_context` 處。
- 問題背景：`update_from_task3_search()` 每次直接重新指定整個 L1/L2/L3/KG
  並寫 `stale: False`，舊內容只剩 provenance event，無法再供下游使用（覆寫非累積）。
  查詢雖回傳 `l2_stale` / `kg_stale`，卻同時照常回傳 `vector_results` 與
  `compact_context`；Study 直接取 `compact_context`，Manuscript 的
  `_collect_context_chain_candidates()` 讀 L2 時也沒有檢查 stale。
  結果是人工修正 L1 後，下游仍可能拿舊內容繼續寫作。
- 決策：標記為 stale 的層級，在重新生成前不得供「寫作路徑」使用。
  呼叫端必須顯式選擇：等待重生、或明確接受降級並記錄於 audit。
  診斷與探索介面可以顯示 stale 內容，但必須標示。
- 原因：stale 的語意就是「上游已被人類改過，這份衍生內容不再代表作者現在的判斷」。
  照常供應等於讓人類的修正無法傳下去 —— 正是 COC 要解決的問題本身。
- 否決方案：只在 UI 顯示 stale 警告而仍照常注入。否決理由是 Drafter 沒有人在看警告，
  警告對自動化路徑沒有任何約束力。
- 不變量：
  1. 寫作路徑（Drafter evidence / prompt 組裝）永不讀取 stale 層級。
  2. 降級一定要留痕：走了降級路徑就必須寫進 context_audit。
  3. stale 判斷以層級為單位，不得因為同一份 chain 有其他新鮮層級就整包放行。
- 遷移/回滾：既有 chain 的 stale 旗標語意不變，只改變消費端行為。
  回滾即恢復照常回傳，資料不需要變更。
- 驗證：`test/unit/test_context_chain_stale.py`；把 L2 標為 stale 後，
  確認哨兵字串不出現在真正送出的 request，且 audit 記錄了該次降級。

---

## NOTE-015：prompt 是一條讀取管道，必須套用與 cmd_load_block 同一套章節 ACL

- 適用範圍：`manuscript_routes.handle_chat` / `_build_coc_for_request`、
  `coc_bundle.build_coc_bundle`，以及任何未來把資料組進 LLM prompt 的路徑。
- 問題背景：COC 上線後，`chat_message` 只驗 `_ensure_socket_project_access`
  （專案成員），而 `section` 完全來自請求體；`build_coc_bundle` 又無條件載入
  目前章節正文、2A 歷史與 2C 全篇，`readable_sections` 只限制得到「其他 2B 章節」。
  限定編輯（coauthor）因此可以偽造 socket payload 取得未指派章節的生成結果，
  或即使只指定自己的章節也讀得到完整 2C。同檔的 `cmd_save_block` 早有章節層判定，
  是這條**新路徑**沒有沿用。
- 決策：
  1. 送進 LLM 的內容視同讀取。凡是經由 prompt 進入模型的正文，都必須先通過
     `can_read_section` / `_socket_can_read_paper`，判準與 socket 讀取事件一致。
  2. `chat_message` 以**寫入**權限為門檻（與 `cmd_save_block` 同線）——
     該事件會 `save_chat_history()` 到該章，產出的草稿也是要寫進該章的。
  3. 權限一律在 socket handler（仍有 request context）內算完再往下傳。
     生成跑在 `_CHAT_EXECUTOR` 的 worker thread，那裡沒有 `current_user`。
  4. `build_coc_bundle` 的 ACL 參數**不得有預設值**，必須是必填關鍵字。
- 原因：整個缺陷的形狀是「新接的呼叫端忘了帶 ACL」。沒有預設值時，忘了帶會
  當場 TypeError；有寬鬆預設時，忘了帶會靜默外洩 —— 後者沒有任何外顯症狀。
- 否決方案：
  - 由 `readable_sections` 是否包含目前章節來推導權限。否決理由：`'general'`
    這種預設值與尚未寫入 sections 資料表的新章節都不在清單裡，會把合法請求的
    正文靜默丟掉，而正文丟失同樣沒有外顯症狀。
  - 在 handler 先把 `section` 做 `_safe_component` 清洗以求一致。否決理由：
    `ManuscriptIO._block_dir` 對非 ASCII 章節另有對應邏輯，提前清洗會讓中文章節
    指向錯誤目錄。清洗留在各儲存層，授權層只管授權。
- 不變量：
  1. 2A 對話歷史與該章正文共用同一道讀取判定。讀不到正文卻讀得到對話，
     等於留下同等的外洩管道。
  2. 2C 全篇對章節限定角色一律不供應：它是所有章節組裝出來的成品，
     放行等於整篇繞過章節層限制。
  3. 被權限擋下的段落要進 `notes` → audit，作者要知道草稿少了什麼。
  4. ACL 判定本身例外時一律視為不通過。
- 遷移/回滾：純行為收緊，無資料變更。回滾即恢復無條件載入。
- 驗證：`test/unit/test_coc_acl.py`。所有「不在場」斷言都配有同資料、同路徑、
  只換角色的 owner 對照組 —— 否則 2C 從未被組進去時，「2C 不在場」恆為真。

---

## NOTE-016：schema migration 只在「目標 DB 就是本次要用的 DB」時執行

- 適用範圍：`app/__init__.py` 的 `create_app` 啟動流程、`fix_db_schema`。
- 問題背景：`fix_db_schema._resolve_db_path()` 的路徑相對該檔位置寫死為
  `repo/data/roothinks.db`，**完全不看 `SQLALCHEMY_DATABASE_URI`**；
  `create_app()` 又無條件呼叫 `_run_schema_fix()`。於是每一個把資料庫指到
  tmp 的測試，在建立 app 時仍會對**正式資料庫**跑一次 migration ——
  而 `fix_db_schema` 自己的檔頭載明「安全邊界：破壞性操作（DROP TABLE／重建表）」。
  跑一次完整測試套件即 700 餘次。
- 決策：加入 `_should_run_schema_fix(app)`，只有在 `fix_db_schema.target_db_path()`
  與 `SQLALCHEMY_DATABASE_URI` 指向同一個檔案時才執行 migration。
- 原因：migration 的作用對象必須是本次 ORM 真正要用的那一顆資料庫。
  「改 A 卻用 B」在任何情境下都不是預期行為，測試只是最容易觀察到的那一種。
- 否決方案：改用 `app.config["TESTING"]` 判斷。否決理由：TESTING 描述的是
  「這是不是測試」，而真正該問的是「目標對不對」。設了 TESTING 卻指向正式 DB
  的腳本仍應該被擋，沒設 TESTING 的正式啟動仍應該要跑。
- 不變量：
  1. 非 sqlite 設定維持原行為（未來換資料庫時不得被這道判斷擋住）。
  2. `target_db_path()` 解析失敗時維持原行為，讓既有的
     「開不起來也不要帶著壞 schema 跑」路徑接手，不得在此吞掉。
- **這條 NOTE 沒有解決的部分（不要誤讀範圍）**：`test/integration_smoke/*` 是
  `create_app({"TESTING": True})`，完全沒有覆寫 DB 設定，因此它們「設定的 DB」
  就是正式 DB，本判斷會正確放行 —— 那 26 個測試仍然連上 `data/roothinks.db`、
  `data/manu_core.db` 與 `data/sys/llm_match.db` 並跑 migration。
  本 NOTE 堵的是「已經自行隔離 DB、卻仍被 migrate」那條意外路徑。
  smoke 測試要不要改指 tmp 是另一個決策：改了之後路由可能照樣回 200，
  變成通過但覆蓋範圍縮水，屬於「綠得沒有意義」那一類，需要人類判斷。
- 遷移/回滾：無資料變更。回滾即恢復無條件執行。
- 驗證：`test/unit/test_schema_fix_scope.py`。除了判斷邏輯的 A/B，另有一個
  直接比對正式 `data/roothinks.db` mtime 的牙齒測試 —— 那才是使用者真正在意的結果。

---

## NOTE-017：token 預算的唯一結算點在 provider request 層

- 適用範圍：`Task8Drafter._generate_text_response` 組裝 `system_instruction` 之處、
  `manuscript_routes._pack_prompt_context`、`ManuscriptRuling.validate_and_prepare`、
  以及兩份 `_truncate_to_budget`。
- 問題背景：先前把預算收斂做在 handler 的 packer（只管 context）與 ruling
  （加上 upstream 與檢索）。但真正送給 provider 的是整串 system 規則、使用者指令、
  附件，**外加同一輪稍早那一次 intent-router 呼叫**——三者共用同一個 TPM 視窗。
  實測：合法的 4000 字中文指令 + 約 20K token context、**連附件都沒有**，
  router 2,054 + draft 26,744 = 28,798，超過 25,000 上限 3,798。
- 決策：
  1. 上限以 `LLM_REQUEST_MAX_TOKENS`（預設 24000）表示，並扣除
     `DRAFTER_ROUTER_RESERVE_TOKENS`（預設 2200，取自實測 2054 再留餘裕）。
  2. 在組裝成最終字串的那一刻結算：先量「context 以外的骨架」佔多少，
     剩下的才是 context 的額度，超了就把 context 再收斂一次。
  3. **截斷提示字串本身必須先占預算**：二分搜尋的判斷式要套在最終回傳字串上。
- 原因：只在中間層收斂，任何一段沒被計入就會讓總量失控，而失控的症狀是
  被 provider 節流後重試——沒有明確錯誤訊息，很難追。
- 否決方案：
  - 即時量測 router 實際用量再傳給下游。否決理由：`ai_drafter` 是模組層單例，
    被多條 worker thread 共用，把每輪量測值掛在 instance 屬性上會互相污染。
  - 先扣掉截斷提示的估算成本再做二分搜尋。否決理由：`_estimate_tokens` 以
    `int()` 取整，`int(a)+int(b)` 可能比 `int(a+b)` 小 1，仍會固定差一個 token
    （實測 budget=500 回 501）。
  - 超標時擋下請求。否決理由：使用者拿不到草稿的代價，高於被節流重試。
    因此最後那道校核只記錄不阻擋。
- 不變量：
  1. `_truncate_to_budget(text, n)` 的回傳值（含提示字串）估算不得超過 n。
     兩份實作（`source_context` 與 `Task8Drafter`）必須一致。
  2. 送出的 request 總量（router + draft）以系統自己的估算為準不得超過上限。
  3. 需要「骨架佔多少」這種量測時，prompt 模板必須是可帶入空 context 重組的形式——
     行內 f-string 做不到，所以模板抽成模組常數。
- 遷移/回滾：純行為收緊，無資料變更。把 `LLM_REQUEST_MAX_TOKENS` 設得極大即等同回滾。
- 驗證：`test/unit/test_provider_request_ceiling.py`。斷言對象是 `dispatch_task`
  實際收到的字串、兩次呼叫加總；並附「關掉收斂則同一組輸入會超過 25K」的對照組——
  否則測試資料不夠大時，「沒超標」這種斷言恆為真。

---

## NOTE-018：PAQ 只有一個 canonical reader，且 PaqSurvey 優先

- 決策日期：2026-08-11
- 適用範圍：`app/core_pro/manuscript/coc_producers.py` 的 `_load_paq_taxonomy()` /
  `_load_paq_voxels()`；`manuscript_ruling._load_upstream_context` 已移除 PAQ 讀取。
- 問題背景：writer 與 reader 讀寫的**不是同一個檔**。
  `paq_routes.save_taxonomy` 寫 `taxonomy_manual_update.json`＋PaqSurvey；
  `task_1paqswot` 寫 `taxonomy_v1.json`＋PaqSurvey；`task_2cubegen` 寫
  `cube_v1.json`＋PaqSurvey。而舊 reader **只找 `taxonomy_manual_update.json`**，
  且只取 `axis_labels`/`axis_tags` 兩個 dict。實測真實 data：
  `taxonomy_manual_update.json` **0 個**、`taxonomy_v1.json` 3 個、`cube_v1.json` 3 個
  —— 也就是整條 PAQ 對 Drafter 在架構上是斷的，cube 與 rag_context 從未進過 prompt。
- 決策：
  1. 全站只留一個 PAQ 讀取入口（`coc_producers.load_paq_context`）。
  2. 優先序 **PaqSurvey 資料表 > taxonomy_manual_update.json > taxonomy_v1.json**。
  3. cube 一律經 `PaqMatrix.transform` 取 `rag_context`，不自己拼字串。
- 原因：
  - DB 優先是因為手動與 AI **兩條 writer 路徑都會** `_update_survey_data()`，
    它一定是最新的那一份。先讀檔案的話，作者手動改完又重跑 AI 時會讀到
    已被取代的舊 taxonomy，而且沒有任何外顯症狀。
  - 重用 `PaqMatrix.transform`：那份實作早就存在且是唯一為 LLM 準備的表示法，
    但 `paq_routes` 只取 `view_data`、**把 `rag_context` 丟掉**。自己再寫一份
    會讓作者在 PAQ 畫面看到的與 LLM 讀到的各自漂移。
- 否決方案：依 mtime／`updated_at` 挑最新。否決理由：檔案 mtime 與 DB 的
  tz-aware→sqlite naive `updated_at` 不同基準，比較結果會隨部署方式改變；
  用固定且說得出理由的優先序，再把實際採用的來源記進 audit，比猜哪個新可靠。
- 不變量：
  1. PAQ 的 `source_type` 必須是 `paq_note` —— 它在 `has_grounding` 的允收清單裡。
     換名字會讓「只有 PAQ 資料」的專案突然被 grounding guard 擋下。
  2. 實際採用的來源（`paq_survey_db` / `taxonomy_manual_update` / `taxonomy_v1`）
     要寫進 item 的 `source_id`，taxonomy 與 cube 來源不一致時另記 note。
- 已知限制（不要誤讀範圍）：`PaqCore.run_paq_task` **全無呼叫端**（前端打的是
  `paq_routes.run_paq_task`），因此它裡面的 `_save_chat_record()` 與
  `IndexService.register_index()` 從未執行 —— PAQ 2A chat 目前沒有伺服器持久化。
  本 NOTE 沒有修這一條。
- 遷移/回滾：純讀取行為變更，無資料異動。回滾即恢復舊 reader。
- 驗證：`test/unit/test_coc_producers.py::TestPaqReachesProvider`（哨兵到 provider）。

---

## NOTE-019：未經 screening 的搜尋結果不得進入寫作路徑

- 決策日期：2026-08-11
- 適用範圍：`manuscript_ruling._load_upstream_context` 移除的 `[Literature Hints]` 區塊；
  `coc_producers.load_literature_context`。
- 問題背景：`_load_upstream_context` 直接讀 `search_results.json` 的
  `keywords[:8]` 與 `apa_citations[:3]` 組成 `[Literature Hints]` 送進 prompt。
  那是搜尋引擎的原始輸出，**沒有經過任何人的納入判斷** —— 等於在 NOTE-013 的
  fail-closed 旁邊開了一條旁路：作者標為 excluded 的論文擋得住檢索，卻擋不住這裡。
  teeth test 實測哨兵確實經此進入 provider request（`test_coc_producers.py:318` 紅）。
- 決策：寫作路徑的文獻**只能**來自 `LiteratureLibrary` 且 `screening_status=='included'`；
  `search_results.json` 永遠不是 COC 的輸入。採用理由（`screening_note`）與閱讀筆記
  （`reading_note`）要一起傳遞 —— 它們才說明「為什麼這篇可以拿來寫」。
- 原因：人工篩選是作者的學術判斷。留一條繞過它的路徑，等於那個判斷沒有效力，
  而錯誤會直接出現在稿件的引用裡。
- 否決方案：把 `[Literature Hints]` 保留但加上過濾。否決理由：兩個 reader 並存時
  一邊擋掉、另一邊照送，而且沒有任何外顯症狀 —— 這正是本輪要消滅的形狀。
- 不變量：
  1. `excluded` / `candidate` / `unknown` 的**標題與註記**一個字都不得出現在
     回傳值或 audit notes 裡；只有**數量**可以留痕。
  2. 被擋下的數量一定要進 notes → audit，作者要知道「還有 N 篇沒篩」。
  3. library 讀取失敗時 fail-closed（不採用任何文獻），不得退回原始搜尋結果。
- 順帶修正的路徑錯誤：舊 reader 找的是 `<base_pid>/search_results.json`，
  而真實檔案落在 `<pid>-p/search_results.json` —— 旁路目前**剛好**因路徑不符而未觸發。
  這不是防護，只是巧合；測試刻意種在 reader 讀得到的位置，否則斷言會假綠。
- 遷移/回滾：純行為收緊，無資料異動。
- 驗證：`test/unit/test_coc_producers.py::TestUnscreenedSearchResultsBypassIsSealed`
  （含「同一篇被人工 included 後就到得了 provider」的對照組）。

---

## NOTE-020：screening 決策需要角色門檻與可歸屬的 actor

- 決策日期：2026-08-11
- 適用範圍：`LiteratureLibrary.update_entry()`、
  `literature_library_routes.update_library_entry()`、
  `literature_routes.current_screening_actor()`。
- 問題背景（**2026-08-11 更正過一次，見下**）：`/api/literature/library/update`
  接受 patch 裡的 `screening_status`，而**沒有任何欄位記錄「誰、什麼時候」
  做了這個決定** —— 納入與否直接決定哪些文獻成為 Drafter 的寫作依據（NOTE-013），
  卻無法歸屬到任何人。
- **更正**：本 NOTE 第一版寫「完全沒有角色檢查，任何人都能標 included」，**那是錯的**。
  `enforce_project_ownership` 這個 before_request 守衛依 HTTP method 推 min_role，
  POST/PUT/PATCH 一律要 `editor`（`security.py` 約 L620），
  另有模組守衛擋掉 coauthor 進入 Manuscript 以外的模組。
  實測把本輪新加的 route 檢查整段拿掉，viewer 與 coauthor 仍然 403。
  **真正缺的只有「決策無法歸屬」這一項**，不是角色門檻。
- 決策：
  1. 改動 `screening_status` 需要 workspace 角色 >= `editor`
     （owner=PI、editor=Co-PI；`coauthor` 是章節限定編輯，一律不得改）。
     **這一條目前與既有守衛重複**，保留的唯一理由是那道守衛的門檻由 HTTP method
     推導：日後若有人加了 GET 帶參數的變更路徑、或改了 method 對應表，
     保護會無聲消失。screening 屬學術誠信控制，值得在做決定的那一行再確認一次。
     不得把它描述成「補上原本缺少的角色檢查」。
  2. `update_entry` 收到 `screening_status` 而沒有 `actor` 時**直接 ValueError**。
  3. 決策 provenance（`screening_decided_by/at/batch_id`）由 `update_entry`
     依 actor 自行寫入，**不接受從 patch 帶入**。
- 原因：
  - actor 的必填性一定要放在 service 層：那是唯一所有呼叫端都會經過的位置。
    只靠 route 自律的話，新接的呼叫端（批次腳本、遷移工具、CLI）忘記帶
    就會靜默寫出一筆無主決策，而那沒有任何外顯症狀。
    這是本 NOTE **唯一真正改變行為**的部分（A/B 可紅：
    `test_literature_screening_gate.py:78` DID NOT RAISE）。
  - provenance 不從 patch 取：開放的話任何人都能宣稱某個 PI 做過這個決定。
- 否決方案：另開一個 `set_screening()` 方法、`update_entry` 維持原樣。
  否決理由：舊路徑仍然允許無主體地改狀態，等於門沒關。
- 不變量：
  1. `merge_candidates` 永遠只產生 `candidate`，不得自動 `included`
     —— 批次標成 included 等於偽造「作者已審核」。
  2. 重跑遷移不得覆蓋既有的人工決策（`_merge_into_entry` 不碰 `_USER_FIELDS`）。
  3. 非 screening 的更新（metadata、reading_note）不需要 actor，不得擋過頭。
- 遷移/回滾：`screening_decided_*` 是新增欄位，既有 entry 沒有這些鍵，
  讀取端一律當空字串處理（不回填，理由同 NOTE-009：回填等於偽造證據）。
- 驗證：`test/unit/test_literature_screening_gate.py`（service 層 actor 守衛，
  6 cases，含「同一個操作帶了 actor 就成功」的對照組）；
  `test/unit/test_literature_screening_http_acl.py`（HTTP 角色矩陣：
  owner/editor 通過並記下決策者、viewer/coauthor 403 且狀態不變）。
  後者是**行為契約**，不是新程式碼有效的證明 —— 見上面的更正。

## NOTE-021：source contract gate 的 scope 必須含未追蹤檔

- 決策日期：2026-08-11
- 適用範圍：`scripts/audit_source_contract.py` 的 `candidate_files()` 與
  `HEADER_SIGNALS`。
- 問題背景：原本 scope 只有 `git ls-files`（tracked）。本輪新增的 13 個 source
  檔（`coc_producers.py`、`coc_bundle.py` 與 11 個新測試）全是 untracked，
  **驗證器從頭到尾沒讀過它們**。逐檔實跑 `missing_header_signals()` 的結果是
  13 個檔全紅（缺 position/integration/boundary），但 gate 顯示的是
  `FAIL (3 header)`，那 3 個全是既有檔。真值是 16。
  **「只紅 3 個」反映的是驗證器的視野，不是程式碼的狀態。**
  同一形狀在 NOTE-018/019 的「defined but never referenced」誤報上已經發作過
  一次：當時只修了 NOTE 那一半，沒有回頭問 header 那一半是否同病。
- 決策：
  1. worktree 模式的 scope = tracked ＋ untracked-not-ignored
     （`git ls-files --others --exclude-standard`，仍尊重 `.gitignore`）。
  2. `--cached` 模式維持只看 index。該模式問的是「即將發布的那棵樹」，
     未追蹤檔在那棵樹裡本來就不存在，回報「不在 index」是正確答案。
  3. `HEADER_SIGNALS` 補上 2026-08-10 起新檔採用的細分寫法
     （`子系統定位`／`上游呼叫者`＋`下游服務`／`明確不負責`＋`不變量`）。
- 原因：這些新寫法把單一欄位拆得更細、資訊量只增不減，卻因為字面不符而被判缺欄。
  若不補同義詞，gate 會實質**逼開發者把 header 寫得更粗**才能過關 —— 那是把
  驗收工具的詞彙表誤當成品質標準。同義詞只收「與既有詞同等具體」的說法：
  自我檢測已加反例，`說明`／`備註` 這類泛稱仍然必須紅。
- 否決方案 A：把 14 個新檔的 header 改回舊詞彙。否決理由：資訊會變少，
  且下一輪寫新檔的人會再踩一次，問題沒有修在源頭。
- 否決方案 B：只放寬同義詞、不改 scope。否決理由：那只讓「已經看得到的檔」變綠，
  真正的缺陷（看不到新檔）原封不動，下一輪新增的程式碼照樣沒被驗到。
- 不變量：scope 擴大只能增加受檢檔案，不得成為新的豁免管道；
  `EXCLUDED_PATHS` 仍是唯一的豁免入口，且必須逐條寫原因。
- 驗證：`test/unit/test_source_contract_scope.py`
  （在 tmp_path 建拋棄式 git repo，證明未追蹤的壞 header 會被抓到、
  被 `.gitignore` 的檔不會、`--cached` 模式不看未追蹤檔）；
  `python scripts/audit_source_contract.py --self-test`（同義詞與泛稱反例）。

## NOTE-022：PAQ 2A 對話存進伺服器，且必須讀得回來

- 決策日期：2026-08-11
- 適用範圍：`paq_routes.run_paq_task()` 的 `task_2a_chat` 分支、
  `PaqCore._save_chat_record()` / `load_chat_records()`、
  `GET /api/paq/chat_history/<pid>`、`paq_interact.js` 的 `loadChatHistory()`。
- 問題背景：`PaqCore.run_paq_task` **全 repo 沒有任何呼叫端**（app／test／js 全掃過）。
  前端打的一直是 `paq_routes.run_paq_task`，那是另一份平行實作。因此寫在 PaqCore 裡的
  v0.5「對話存檔」與 v0.6「把最新 taxonomy/cube 餵給 chat」**一次都沒有執行過**。
  伺服器上沒有任何一筆 PAQ 對話紀錄，前端的 `chatSessionHistory` 只是瀏覽器變數，
  重新整理就消失。
- **「從未執行過」的硬證據**（不是靠推論）：PaqCore 版寫
  `success, response_text = execute_paq_chat(...)` 然後把 `response_text`
  當字串存進 record 的 `ai` 欄，但 `execute_paq_chat` 回傳的是
  `(True, {"reply": ...})` —— 是 dict。只要真的跑過一次，磁碟上就會出現
  `"ai": {"reply": "..."}` 這種紀錄。一筆都沒有。
- 決策：
  1. **修在 route，不復活死碼。** 把「帶 taxonomy/cube」「存檔」「註冊索引」
     三件事補進前端真正會打到的那個分支。
  2. 同時補讀取端（`load_chat_records()` ＋ GET 端點 ＋ 前端 init 載入）。
  3. record 增加 `actor` 欄（誰講的），與 `user` 欄（講了什麼）分開。
  4. 存檔失敗時回應帶 `persisted: false`，前端明示這一輪沒存下來。
- 原因：
  - 不復活死碼：PaqCore 版的回傳契約是 `{'response': ...}`，而前端讀的是
    `data.reply`；它還帶著上面那個 dict-當字串的 bug。改成委派過去等於同時
    改動前端契約並把一個沒跑過的實作放上線，影響面遠大於這個修復該有的大小。
  - **寫了一定要有人讀**：`IndexService` 就是現成教訓 —— 三個呼叫端一路寫入
    `metadata_index`，全 app 沒有任何讀取端，連
    `search_indices_by_module()` 必定回空清單這件事都沒人發現（見該檔 header）。
    只補存檔會製造第二個同形狀的洞：測試會綠、磁碟上有檔、使用者仍然什麼都看不到。
  - `actor`：理由同 NOTE-020，沒有主體的紀錄事後無法歸屬。
  - 存檔失敗不回滾整個回覆：LLM 已經算完了，把使用者的回覆吃掉是更大的損失；
    但也不得靜默 —— 靜默正是本專案反覆踩到的形狀。
- **明確不做：對話不進入寫作路徑。** PAQ 2A 是自由對話，沒有經過任何篩選，
  把它接進 COC／Drafter 與 NOTE-019 擋掉的「未經 screening 的搜尋結果進入寫作路徑」
  是同一個形狀。日後若要接，必須先有明確的人工確認步驟，不得因為
  「資料已經存下來了」就順手接上去。
- 否決方案：讓 route 委派給 `PaqCore.run_paq_task`，收斂成單一實作。
  否決理由見上。**死碼本輪刻意保留未刪**（那是擁有者的裁量），
  但已在該方法上加註警告 —— v0.5 與 v0.6 兩次都改到死的那一份，
  沒有警告的話第三次還會發生。
- 不變量：
  1. 存檔與讀取是一組，不得只留其中一半。
  2. 舊 record 沒有 `actor` 鍵時一律當空字串，**不回填**（理由同 NOTE-009）。
  3. 單一 session 檔損毀只能跳過該檔並留 warning，不得讓整段歷史消失，
     也不得假裝那個檔不存在。
  4. GET 走 viewer 門檻、POST 走 editor 門檻，由既有的
     `enforce_project_ownership` 依 method 推導（同 NOTE-020 的更正，
     這不是本輪新加的保護）。
- 遷移/回滾：`actor` 是新增欄位，既有 chat 檔（目前全站 0 個）不受影響。
  回滾只需移除 route 內的持久化區塊，資料格式向後相容。
- 驗證：`test/unit/test_paq_chat_persistence.py`（走真實 HTTP：落磁碟、讀得回、
  taxonomy/cube 到位、存檔失敗回 persisted=false、損毀檔不吃掉其餘歷史、
  viewer 不能發話但讀得到）。前端 `loadChatHistory()` 的 DOM 行為**未經瀏覽器實測**，
  見 docs/HANDOFF.md §3.15。

## NOTE-023：程式化渲染畫布不得觸發 autosave

- 決策日期：2026-08-11
- 適用範圍：`manuscript_soed.js` 的 `scheduleAutosave()`、
  `_renderWithoutAutosave()`、`_renderBlankSection()`、`block_loaded` 處理。
- 問題背景（**瀏覽器 runtime 堆疊實測，不是推測**）：
  只切章、一個字都沒打，磁碟上就會出現 `_draft__1.json`。呼叫鏈是
  ```
  _applySectionSwitch → _renderBlankSection → insertEditorCard
    → document.execCommand('insertHTML')
    → 派發 isTrusted 的 input 事件（瀏覽器標準行為，不是本專案的 bug）
    → manuscript_ws.js 的 editorCanvas input listener
    → scheduleAutosave() → 1.5s → _flushAutosave() → 寫檔
  ```
  使用者看得到的後果：下次進該章會被提示「本章有未存檔的自動儲存草稿，
  要復原嗎」，而那份草稿與已存版本**逐字相同**、且他從沒編輯過；
  空白章甚至會**自動復原**一份空草稿並邀請「按存檔可將它建立為正式版本」——
  照做就產生一個空的正式版本。
- 決策：新增 `_renderWithoutAutosave(render)`，在程式化渲染期間設旗標，
  `scheduleAutosave()` 見到旗標直接 return。只套用在**切章的兩條渲染路徑**：
  `_renderBlankSection()` 與 `block_loaded` 載入既有版本。
- 原因：
  - `execCommand` 的 input 事件是**同步**派發的（實機堆疊裡 `insertEditorCard`
    就在 listener 的呼叫堆疊上），所以 try/finally 這個視窗蓋得到它。
  - `finally` 是必要的，不是防禦性寫法：少了它，任一次渲染拋例外就會讓旗標
    卡在 true、autosave **從此永久靜音** —— 那比原本的 bug 嚴重得多。
- **否決方案：直接在 `insertEditorCard()` 內部封鎖。**
  否決理由：插入素材、Word 匯入、2A 複製草稿、使用者按「復原草稿」
  都走同一個函式，那些是真的使用者動作，草稿必須照存。
  包錯層會讓真正的編輯存不進去 —— 用「沒有草稿」換「沒有草稿」。
- 不變量：
  1. 旗標一定要在 `finally` 清掉。
  2. 判斷依據是「這段渲染是誰發動的」，不是「內容有沒有變」。
     用內容比對當判準的話，使用者把內容改回原狀時就無法清掉舊草稿。
  3. 使用者主動觸發的插入路徑不得靜音。
- 驗證：`test/unit/test_manuscript_section_switch.py::TestSwitchingDoesNotFabricateDrafts`
  （含「不得包在 insertEditorCard 內」與「復原草稿後仍可 autosave」兩個對照組）。
  實機 A/B（隔離實例）：切兩章不打字 → 草稿檔 **0 個**（修前為 2 個）；
  接著用 `execCommand('insertText')`（同樣是 isTrusted input）真的打字 →
  草稿**照常產生**且含打進去的字，UI 顯示「已自動儲存」。

## NOTE-024：schema migration 只升級「已初始化」的 DB，全新 DB 交給 create_all

- 決策日期：2026-08-13
- 適用範圍：`fix_db_schema.py` 的 `_resolve_db_path()` / `target_db_path()` /
  `upgrade_database()`；`app/__init__.py` 的 `_run_schema_fix()` 呼叫點。
- 問題背景（**在乾淨 detached worktree 實測復現，不是引用交接文件**）：
  全新機器 checkout 之後沒有 `data/`（整個目錄 gitignored 且零 tracked 檔），
  `create_app()` 會連續死在兩個地方：
  ```
  1) FileNotFoundError: Database file not found in: [<repo>/data/roothinks.db, ...]
     fix_db_schema.py:66  ← app/__init__.py:442 _run_schema_fix()
  2) 補上空的 DB 檔之後：
     RuntimeError: Missing 'projects' table. Please initialize database first.
     fix_db_schema.py:242
  ```
- 真因是**一個**而不是兩個：`create_app()` 在 `__init__.py:442` 呼叫的是
  **legacy 升級器**，而 `db.create_all()` 在 **:659** —— 相隔 217 行。
  升級器整支的前提是「DB 已經被初始化過」，它沒有「尚未初始化」這條分支。
  兩個 traceback 只是同一個前提在兩個不同深度爆開。
- 決策：
  1. `_resolve_db_path()` 新增 `must_exist` 參數。`target_db_path()` 用
     `must_exist=False`，回傳「**將會**使用的路徑」——即使檔案還不存在。
  2. `upgrade_database()` 在「DB 檔不存在」或「存在但沒有 `projects` 表」時
     **視為尚未初始化，記 log 後直接 return**，把建置交給 `db.create_all()`。
- 原因：
  - 「這顆 DB 還沒被初始化」不是錯誤，是全新安裝的正常狀態。
    升級器對它無事可做 —— 沒有 legacy schema 需要被升級。
  - **刻意不改 `create_app()` 的順序**。既有 DB 必須「先升級舊表結構、
    再 `create_all()` 補新表」；把 migration 移到 create_all 之後，
    legacy DB 的欄位修復就會晚於 ORM 首次使用。順序是對的，缺的是空集合分支。
  - `target_db_path()` 不再抛例外，`_should_run_schema_fix()`（NOTE-016）才能
    真的做比對。原本它一遇到 FileNotFoundError 就 `return True`，
    於是全新機器必然走進那條「開不起來」的路徑。
- **否決方案 A：在啟動腳本或測試 fixture 預先塞一顆假 DB。**
  否決理由：那是把缺陷搬到部署流程裡，新機器仍然「直接跑 create_app 開不起來」，
  而且假 DB 的 schema 版本一旦和 ORM 不同步就會產生更難查的錯誤。
- **否決方案 B：讓 migration 自己 `db.create_all()` 或建表。**
  否決理由：會出現兩套建表真相（`models.py` 與 migration），
  兩邊漂移時沒有人是對的。建表只能有一個來源。
- 不變量：
  1. **既有 DB 一律走原本的冪等 migration 路徑**，本改動不得讓任何
     既有資料庫少跑一次升級 —— 判斷依據是「有沒有 `projects` 表」，
     不是「檔案新不新」。
  2. 不重建、不覆寫、不清空任何既有 DB。新增的分支只會 `return`。
  3. `must_exist=True`（人工執行 `python fix_db_schema.py`）維持原本會抛
     FileNotFoundError 的行為 —— 那條路徑是人明確要求升級某顆 DB。
- 驗證：`test/unit/test_fresh_bootstrap.py`
  （空目錄首次啟動、第二次啟動冪等、建資料後重啟仍在、既有 DB 仍會被升級的對照組）。
  端對端：`scripts/verify_fresh_bootstrap.py` 在 detached worktree 上實跑。

## NOTE-025：PID 的真相來自伺服器注入，前端不得自行用 regex 猜

- 決策日期：2026-08-13
- 適用範圍：`app/templates/paq.html` 的 `#paq-bootstrap`、
  `app/static/js/paq_initial.js` 的 `resolveCurrentPid()`。
- 問題背景（**瀏覽器實測，非推論**）：`paq_initial.js` 舊碼用
  `path.match(/\/([A-Za-z0-9]{6,20})\/?$/)` 從網址尾端撈 PID，字元集**不含
  連字號**；而本系統的正式 PID 一律長成 `ULQ8F6-p`、`DGVRYV-p`。
  於是 `/paq/ULQ8F6-p` 比對失敗 → `currentPid` 為 null → alert 之後
  `location.href='/'`，使用者被**靜默踢回 Dashboard**。
  伺服器端從來沒有這個問題：`validate_id` 的 `project_id` pattern 是
  `^[a-zA-Z0-9_-]{1,20}$`，**本來就接受連字號**，而且 `routes.py` 的
  `paq_workspace()` 早就把驗證過的 `pid` 傳進 `render_template` —— 只是模板
  從來沒有用過它。
- 決策：模板輸出 `<div id="paq-bootstrap" data-pid="{{ pid }}">`，前端優先讀它；
  其次讀 `URLSearchParams`；最後才用 `URL` API 取 path 片段。
  三條來源都拿不到時**原地顯示錯誤，不得導頁**。
- 原因：
  - PID 的**驗證規則只能有一份**，而那一份在伺服器（`ID_PATTERNS`）。
    前端再寫一次 regex 就是第二份真相，兩邊漂移時前端這份必然是錯的
    —— 這次就是漂移了整整一個連字號。
  - `/paq/<pid>` 與 `/paq?pid=<pid>` 走的是**不同的 Flask view**，但兩者都
    `render_template('paq.html', pid=pid)`。讀注入值可以讓兩條路徑天然一致，
    不必在前端維護兩套解析。
  - 最後那條 path fallback 用 `new URL().pathname.split('/')` 而非 regex：
    切片不需要宣告合法字元集，也就不可能再漏掉某個字元。
- **否決方案：把前端 regex 的字元集補上 `-`。**
  否決理由：那只修好今天這一個字元。`ID_PATTERNS` 還允許底線，未來若放寬
  規則，同一個缺陷會以完全相同的形狀再發作一次，而且症狀（被踢回首頁）
  完全不指向 PID 解析。要修的是「有第二份真相」，不是「第二份真相寫錯了」。
- 不變量：
  1. 前端**永遠不判定 PID 合法性**，只負責取得。合法性由伺服器的
     `validate_id` 決定，無效 PID 由 API 回 4xx，前端呈現該錯誤。
  2. PID 解析失敗**不得導頁**（見 NOTE-026 的同一原則：導頁會丟掉未存內容，
     而且把「我沒讀到 PID」偽裝成「你該回首頁了」）。
- 驗證：`test/unit/test_paq_pid_resolution.py`、`test/js/test_paq_pid_resolve.cjs`；
  瀏覽器實機走 `/paq/<含連字號 PID>`、`/paq?pid=<同一 PID>` 與無效 PID 三條。

## NOTE-026：子資源載入失敗不得改寫其他責任的錯誤歸因

- 決策日期：2026-08-13
- 適用範圍：`app/static/js/paq_project.js` 的 `loadPaqStatus()` 與其拆出的
  `renderProjectIdentity()` / `renderTaxonomy()` / `renderVoxels()`。
- 問題背景：舊碼把整個載入流程（專案名稱、PI、taxonomy、3D voxel）包在
  **同一個 try** 裡，catch 一律把 `#header-pname` 寫成
  「⚠ Load Failed」並 alert。而 `cube_renderer.js` 對缺 `val` 的 voxel 會
  `v.val.toFixed(2)` 抛例外。結果是：
  **3D 渲染器壞掉 → 畫面說「專案名稱載入失敗」**，而 `/api/paq/status` 實測
  回 200 且 name 正確、名稱其實早在 catch 之前就已經正確寫進畫面了 ——
  catch 把一個**已經成功**的結果覆蓋成失敗。
- 決策：依「責任」拆成互不影響的區段，每段自己的 try/catch 只寫自己的區域：
  | 失敗的東西 | 顯示位置 | 訊息 |
  |---|---|---|
  | 專案身分（API 本身 4xx/5xx） | `#header-pname` | 專案載入失敗（帶狀態碼） |
  | voxel 不存在／空陣列 | cube 容器 | 尚未建立分類矩陣 |
  | voxel 結構壞掉／渲染抛例外 | cube 容器 | Voxel 資料格式錯誤 |
  | taxonomy 渲染失敗 | taxonomy 面板 | 分類清單顯示失敗 |
- 原因：
  - 錯誤訊息是**診斷的起點**。指向錯的元件會讓下一個人往完全錯的方向查
    —— 這一條在 HANDOFF §3.16 已經真實發生過一次。
  - 「一個子資源失敗就抹掉全部」讓使用者失去所有還能用的東西。
    專案名稱、聊天、taxonomy 與 3D 圖是四件事，其中三件不依賴 voxel。
- 不變量：
  1. 任何一段的 catch **只能寫入自己負責的 DOM 區域**，不得碰別段已寫好的值。
  2. **不得用裸 `except`／`catch` 吞掉來源**：每個分支都要能分辨
     「沒有資料」與「資料壞掉」，那是兩種不同的使用者行動
     （去建矩陣 vs 回報壞資料）。
  3. 子資源失敗**不導頁、不 alert**。
- 驗證：`test/js/test_paq_error_isolation.cjs` 四個分支各一個測試 ＋
  「voxel 壞掉時專案名稱仍在」的對照組。

## NOTE-027：formal 唯讀鎖定只鎖編輯面，不鎖對話；授權仍在後端

- 決策日期：2026-08-13
- 適用範圍：`app/static/js/paq_project.js` 的 `lockInterfaceForFormal()`；
  `app/core_pro/paq/paq_routes.py` 的 `/status/<pid>` 回傳的 `access`。
- 問題背景：`lockInterfaceForFormal()` 做的是
  `document.querySelectorAll('input').forEach(el => el.disabled = true)`
  —— 一個**全頁面**選擇器。它的意圖是鎖住 taxonomy 編輯，但 `#chat-input`
  也是 `<input>`，於是**專案一轉正，PAQ Co-Pilot 對話就完全不能用**，
  連 PI 自己都不行。轉正是專案的正常生命週期，不是降級。
- 決策：
  1. 鎖定範圍改為 taxonomy 面板內（`#panel-tax` 底下的輸入元素），
     `#chat-input` 與送出鈕明確排除。
  2. 是否唯讀改由**伺服器**決定：`/api/paq/status/<pid>` 新增
     `access: {role, can_edit}`，`can_edit` 直接問
     `require_workspace_role(pid, ROLE_EDITOR) is None` —— 也就是**問同一支
     enforcement 函式**，而不是在前端重寫一次角色比大小。
  3. viewer 的 `#chat-input` 維持 disabled，但那是**呈現**；
     真正的門檻是 `enforce_project_ownership` 對 POST 要求 editor。
- 原因：
  - `can_edit` 若在前端自行由 role 字串推導，就會出現第二份角色表
    （NOTE-025 同一個病）。直接呼叫 enforcement 函式的話，
    未來改 method→role 對應表時徽章不可能與實際授權不一致。
  - **解除 disabled 不等於取得授權**：把 `#chat-input` 放行之後，viewer 手動
    在 console 移除 disabled 仍然只會拿到 403。前端這一層是體驗，不是防線。
- 不變量：
  1. 前端**不得**成為授權來源；移除任何 disabled 都不得讓 API 放行。
  2. formal 專案對 editor 以上必須可輸入、可延續對話；readonly 專案
     （轉正後的舊 provisional 快照）維持整體唯讀 —— 那是後端既有的
     `project.status == 'readonly'` 判斷，不在本 NOTE 的變更範圍。
- 驗證：`test/unit/test_paq_formal_chat_acl.py`（formal + editor 可發話、
  formal + viewer 403、readonly 仍全面擋下）；
  瀏覽器實機：formal 專案以 PI／Co-PI 各送出一輪並讀回，viewer 為唯讀。

## NOTE-028：stub LLM adapter 進入 repo，但必須雙重上鎖

- 決策日期：2026-08-13
- 適用範圍：`app/llm_service/adapter/llm_stub.py`。
- 背景：PAQ 2A 的「寫入」半段一直無法在瀏覽器走完 —— 隔離驗證環境沒有綁模型，
  每次都停在 `LLM_NOT_BOUND`（HANDOFF §3.16）。於是「送出 → 存檔 → 重整 → 讀回
  → 下一輪帶上前文」這條鏈，從來只有契約層測試，沒有實機證據。
  綁真實 provider 不可行：要金鑰、要花錢、回覆不決定性，而且**無法證明
  第三輪的 prompt 真的含有前兩輪** —— 那需要看見送進 provider 的字串。
- 決策：把 stub adapter 放進 repo（讓驗證可重現），但加兩道鎖：
  1. `__init__` 在 `ROOTHINKS_ALLOW_STUB_LLM` 不為 `1` 時**直接 raise**。
  2. 每一則回覆都硬性帶上 `[STUB]` 前綴，且不可由呼叫端關閉。
  另外把收到的完整 prompt 寫進 `<data_root>/_stub_llm/`，
  那正是「第三輪含前兩輪」唯一的直接證據。
- 原因：
  - **不放進 repo 的代價**：驗證腳本每次都要自己生一份 adapter，
    下一個人重跑不了，等於沒有可重現的驗收 —— 這正是本專案反覆出問題的地方。
  - **放進 repo 的風險**是有人在正式站建一條 `vendor='stub'` 的連線，
    於是研究內容被假文字污染且看不出來。兩道鎖各擋一半：
    env 擋「跑得起來」，`[STUB]` 前綴擋「看起來像真的」。
    前綴刻意不做成可設定 —— 可關掉的標記等於沒有標記。
- **否決方案：用 monkeypatch 在測試裡替換 dispatcher。**
  否決理由：那只在 pytest 行程內有效，瀏覽器打的是真的 HTTP 到真的 Flask
  行程，monkeypatch 完全不在那條路徑上。要驗的正是那條路徑。
- 不變量：
  1. 沒有 `ROOTHINKS_ALLOW_STUB_LLM=1` 就必須無法建立實例。
  2. prompt 落檔只在 stub 啟用時發生，且寫在 data root 之下（不得寫死路徑）。
  3. 這個 adapter 永遠不得被當成「離線模式」或「降級 provider」使用。
- 驗證：`test/unit/test_stub_llm_gate.py`（未設 env 必須 raise、設了才可用、
  回覆一定帶 `[STUB]`）。

## NOTE-029：導頁不得靜默吃掉未存內容；草稿以 sendBeacon 保底

- 決策日期：2026-08-13
- 適用範圍：`app/static/js/manuscript_ws.js` 的 `beforeunload` 守衛、
  `app/core_pro/manuscript/manuscript_routes.py` 的
  `POST /manuscript/api/draft/flush`。
- 問題背景（**瀏覽器 navigation 證據，不是推論**）：
  「手稿工作檯偶發被帶回 Dashboard」查了三輪都沒有結果，因為前幾輪都在找錯誤
  —— 而這件事**沒有錯誤可找**。實際攔到的事件序列是：
  ```
  page-load     navType=navigate           /manuscript/?pid=PAQTST-p
  anchor-click  href="/"  text="roothinks v1.0.1"
  beforeunload
  → 現在位置 /
  console-error: 無      失敗請求: 無
  ```
  也就是 `_navbar.html` 的品牌連結（`<a href="/">`，實測位於 (12,8) 175×40，
  永遠浮在編輯區正上方）與 Dashboard 連結，本來就是**通往首頁的合法連結**。
  「偶發」的真相是**誤點**，而它之所以查不到，正是因為那是一次完全正常的導頁：
  沒有 console error、沒有失敗請求 —— 與 §3.18 記錄的觀察逐字相符。
- **真正的缺陷不是那個連結，是全 app 沒有任何 `beforeunload` 守衛**，
  而 autosave 是 **1500ms debounce**。實測：在編輯區打入哨兵後 **6ms** 內點下
  品牌連結，`grep -rl` 掃過整個 data/ —— 該哨兵**不存在於磁碟任何位置**，
  永久遺失且全程沒有任何提示。
- 決策：
  1. `beforeunload` 時若有未落地的編輯，改用
     **`navigator.sendBeacon()` 打 HTTP 端點**把草稿保住。
  2. beacon 送不出去時（沒有 pid、payload 過大、瀏覽器不支援）才設
     `returnValue` 讓瀏覽器跳原生確認框。
- 原因：
  - **不能用 socket 補送**：`_flushAutosave()` 走 `socket.emit('cmd_autosave_block')`，
    而 unload 期間連線正在拆除，emit 不保證送達。`sendBeacon` 就是為這個時機
    設計的（瀏覽器接手送出，不受頁面銷毀影響）。因此必須有一條 HTTP 路徑，
    這不是重複實作，是同一個儲存動作的第二種傳輸。
  - **優先「保住」而不是「攔住」**：擋下導頁要跳原生確認框，那東西會凍住
    整個 renderer（§3.8 已記載 alert/confirm 的災情），而且使用者真的想離開時
    只是多一次點擊。內容不掉才是目的，攔截只是手段。
  - 端點刻意放在 `/manuscript/api/` 之下：`is_api_request_path()` 認得它，
    於是 CSRF 守衛（`__init__.py` 約 L487）會跳過 —— 這是必要的，
    **`sendBeacon` 無法設定自訂標頭**，帶不了 CSRF token。
    授權因此必須在端點內自己做，不能靠 method 推導。
- **否決方案 A：把 `_navbar.html` 的 `href="/"` 拿掉或改成 JS 攔截。**
  否決理由：那是使用者要用的正常導覽，拿掉會讓人離不開工作檯；
  而且只擋住這兩個連結，重整、關分頁、上一頁、外部連結全都還是會掉內容。
  要修的是「沒有守衛」，不是「有連結」。
- **否決方案 B：把 autosave debounce 調到 0 或很短。**
  否決理由：每次按鍵都寫檔會把磁碟與 FileLock 打爆，
  而且無論多短都仍有視窗 —— 這是把機率調小，不是把缺陷修掉。
- 不變量：
  1. beacon 端點的 ACL 必須與 `cmd_autosave_block` **完全一致**
     （專案成員 + 章節可寫），不得因為「只是草稿」就放寬。
  2. 只寫 `_draft` 檔，**不得產生版本、不得寫 RevisionLog** —— 與 socket 路徑同語意。
  3. 守衛不得阻止使用者離開（只在 beacon 失敗時才提示）。
  4. 記錄導頁證據時**不得寫入稿件內容、token 或個資**，只留元素識別與長度。
- 驗證：`test/unit/test_navigation_guard.py`；
  瀏覽器實機 A/B（打字 → 6ms 內點品牌連結 → 草稿仍在磁碟上）。

## NOTE-030：2B 推進 2C 搬運的是 HTML，不得再套用純文字換行轉換

- 決策日期：2026-08-13
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `cardActionPushToFusion()`。
- 決策：把 2B 卡片內容寫進 2C 區塊時，**原樣搬運 `innerHTML`**，
  不得再做 `\n` → `<br>` 之類的純文字換行補償。
- 問題背景：這一行是 v0.5 留下的殘骸。當時搬的是 `innerText`（純文字，
  換行是語意的，必須補 `<br>` 才看得到分行）；v0.6 為了讓圖片不消失把來源
  改成 `innerHTML`，**但沒有把配套的換行轉換一起拿掉**。
  於是每一個 HTML 原始碼裡的排版換行都被當成使用者的分行，變成一個真的 `<br>`。
- 為什麼這件事「看起來只是偶爾怪怪的」：純文字段落的 innerHTML 通常沒有換行，
  所以多數情況看不出來。但**素材插入用的樣板都是多行字串**
  （`manuscript_image.js` 的 `imgTag`、`manuscript_wsui.js` 的 `insertHtml`
  都是跨 5 行的 template literal），所以**只要那一章有圖，推進 2C 就會多出
  4～5 個空行**，圖片被擠開、caption 與圖分家。章節有沒有圖，決定了症狀出不出現
  ——這就是它被描述成「好像怪怪的」而不是「壞了」的原因。
- 不變量：
  1. 2B 與 2C 對同一段內容的**渲染結果必須一致**。2C 是 2B 的組裝，不是再排版。
  2. 任何未來要在這條路徑上做的字串轉換，都必須先問「來源是 HTML 還是純文字」。
- 相關：NOTE-007（2C 以章節為鍵原位 upsert）。同一個函式，不同的不變量。
- 驗證：`test/unit/test_fusion_push_fidelity.py`。

## NOTE-031：匯出成品由結構化轉換產生，畫布 chrome 不得進入文件

- 決策日期：2026-08-13
- 適用範圍：`app/core_pro/manuscript/manuscript_docx.py`、
  `POST /manuscript/api/export/docx/<pid>`、
  `app/static/js/manuscript_wsui.js` 的 `exportFusionToWord()`。
- 決策：2C 匯出必須產生**真正的 OOXML `.docx`**（`zipfile` 手寫），
  由伺服器把 2C 的 HTML 轉成 `w:p` / `w:tbl` / `w:drawing` 結構；
  **廢除**把 `fusionCanvas.innerHTML` 包一層 `<html>` 再標成
  `application/msword` 的假匯出。
- 問題背景（擁有者回報「2C 的匯出排版完全亂掉」的真因）：
  舊作法送出的是一份**沒有任何樣式表的 HTML**，副檔名 `.doc`。
  Word 會讀，但它讀到的是 Bootstrap 的 class 名稱而不是樣式：
  ```
  <div class="fusion-block mb-4 border-start border-4 border-success ps-3">
    <h5 class="text-success fw-bold"><i class="bi bi-check2-circle me-1"></i>Introduction
      <span class="badge ... fusion-src-ver">S.Ver 0.3</span></h5>
  ```
  於是 (a) 所有間距／縮排／對齊**全部消失**（class 沒有對應的 CSS）；
  (b) Bootstrap icon 的 `<i>` 變成空字元或亂碼方塊；
  (c) 編輯器才需要看的 `S.Ver 0.3` 徽章**被當成標題的一部分印進論文**；
  (d) 圖片 `src` 是伺服器相對路徑（還帶 `?access_token=`），
      離線或換一台電腦開就是一排破圖。
  這四件事加起來就是「排版完全亂掉」，而它不是樣式沒調好，
  是**根本沒有在產生文件**。
- 決策細節：
  1. **轉換在伺服器端做**，輸入是 2C 的 HTML 字串。
     瀏覽器端拿不到圖片的二進位、也不該拿到別人專案的檔案。
  2. **畫布 chrome 一律剔除**：`.fusion-src-ver` 徽章、`<i class="bi-*">` 圖示、
     `button`／`script`／`style`／`svg`。判準是「這個節點是給編輯者看的，
     還是論文的一部分」，不是「它長得像不像內容」。
  3. 章節標題（`.fusion-block` 的直屬 `<h5>`）映成 `Heading1`，
     不照 HTML 的標籤層級硬換 —— 那個 `h5` 是版面選擇，不是文件層級。
- 不變量：
  1. 產出必須是可被 `zipfile` 開啟、且含
     `[Content_Types].xml`、`_rels/.rels`、`word/document.xml`、
     `word/_rels/document.xml.rels` 的合法 OOXML 套件。
  2. MIME 必須是
     `application/vnd.openxmlformats-officedocument.wordprocessingml.document`，
     副檔名 `.docx`。
  3. `document.xml` 內**不得**出現 `fusion-src-ver`、`S.Ver`、`bi-` 等 chrome 痕跡。
- 驗證：`test/unit/test_manuscript_docx_export.py`（含解壓後的 part 清單與
  document.xml 內容斷言）。

## NOTE-032：DOCX 內嵌圖片只從本專案 image registry 取，伺服器不得抓任意 URL

- 決策日期：2026-08-13
- 適用範圍：`app/core_pro/manuscript/manuscript_docx.py` 的 `RegistryImageResolver`。
- 決策：匯出時遇到 `<img src=...>`，**唯一**的解析來源是該 pid 的
  `image_registry.json`。流程是「URL path → registry 比對 → `safe_join_under`
  讀磁碟」，全程沒有任何 HTTP client。
- 原因：
  - **SSRF**：匯出的 HTML 由使用者控制。若伺服器照著 `src` 去抓，
    `http://169.254.169.254/…`（雲端 metadata）、`http://redis:6379/…`
    這種內網位址就會由**伺服器**代為請求，而且結果會被打包進使用者拿得到的檔案裡。
    不是「驗證 URL 是否安全」——是**根本不要有抓取這個動作**。
  - **跨專案越權**：registry 比對同時是 ACL。別的 pid 的
    `/manuscript/image/OTHER-p/x.png` 在本次匯出的 registry 裡查不到，直接失敗。
- 決策：`data:` URI **也拒絕**。它沒有 SSRF 風險，但它代表一張
  **沒有登記在素材庫的圖**，與 NOTE-033 的資產身分規則衝突；
  而且它讓匯出的大小不再有上界。拒絕時回可行動的訊息，不是靜默略過。
- 不變量：
  1. 這個模組不得 import 任何 HTTP client（`requests`／`urllib.request`／`httpx`）。
  2. 圖片查不到、越權或格式不支援 → **明確失敗**，
     不得產生「少一張圖但看起來成功」的 docx。
- 驗證：`test/unit/test_manuscript_docx_export.py` 的
  `test_external_url_image_is_rejected`、`test_cross_pid_image_is_rejected`、
  `test_missing_image_fails_loudly`、`test_module_imports_no_http_client`。

## NOTE-033：影像資產以 magic bytes 決定型別，副檔名與 MIME 都不可信

- 決策日期：2026-08-13
- 適用範圍：`app/core_pro/manuscript/manuscript_image.py` 的 `save_image_asset`、
  `manuscript_routes.py` 的 `cmd_save_image`。
- 決策：寫檔前先**解碼後嗅探實際位元組**判斷影像型別，並以嗅到的型別決定
  落地副檔名。前端送來的 `filename` 與 `data:` header 的 MIME **只當提示**，
  不作為判斷依據。只接受 PNG / JPEG / GIF / WebP。
- 原因：
  - 舊實作把 base64 解出來就寫，檔名只過 `_sanitize_filename`（換掉非
    `A-Za-z0-9._-` 的字元）。也就是**任何位元組**都可以用 `x.png` 這個名字
    落到 `data/<PID>-p/manuscript/image/` 底下，再由 `/manuscript/image/<pid>/<fname>`
    服務出去。
  - **SVG 是最直接的問題**：它是 XML，可以夾帶 `<script>`。若以
    `image/svg+xml` 被服務出去且瀏覽器直接開啟，那是同源的 stored XSS。
    因此 SVG 一律拒絕 —— 不是「消毒後接受」，消毒 SVG 是一場打不完的仗。
  - 副檔名偽裝（`payload.php.png`、`x.png` 內容其實是 HTML）也由同一道擋掉。
- 決策：**寫入權限門檻拉到章節可寫**。`cmd_save_image` 原本只有
  `_ensure_socket_project_access`（專案成員即可），也就是 **viewer 可以上傳圖片**
  並佔用專案容量。改用與 `cmd_save_block` 同一套 `_socket_can_write_section`。
- 不變量：
  1. 落地檔名的副檔名必須來自嗅探結果，不得來自使用者輸入。
  2. 嗅不出支援的型別 → 拒絕，**且不得留下任何檔案**（先驗後寫）。
  3. 單檔上限與專案容量上限都在解碼後、寫入前檢查。
- 驗證：`test/unit/test_manuscript_image_upload.py`。

## NOTE-034：素材身分由伺服器 asset id 決定，Figure／Table 編號在插入時推導

- 決策日期：2026-08-13
- 適用範圍：`app/static/js/manuscript_image.js`、`manuscript_wsui.js` 的素材插入、
  `manuscript_image.py` 的 registry 欄位。
- 決策：每一筆素材的身分是伺服器產生的 `id`（`img_001` / `tbl_001`）。
  畫面上的 `Figure N` / `Table N` 是**顯示用的推導值**，由素材在 registry 中的
  順序決定，不是身分。
- 問題背景：`manuscript_image.js:377` 原本寫
  `fig_id: \`Figure ${Math.floor(Math.random()*100)}\`` —— **編號是亂數**。
  後果是：同一篇論文可能出現兩張 `Figure 42`、也可能從 `Figure 7` 跳到
  `Figure 91`，而且**重跑一次就換一組號碼**，等於圖號完全不可引用。
  這不是排版問題，是資料問題：正文裡寫「如 Figure 42 所示」在下一次存檔後
  就指向別張圖。
- 決策：`Math.random()` 一律移除。AI 產圖走與圖庫上傳相同的
  `next_figure_label()`，由伺服器依 registry 現況給下一個號。
- 不變量：
  1. 前端**不得**自行產生素材編號。
  2. 編號連續且穩定；刪除素材造成的空號由使用者自行處理，
     系統不得為了補號而改動既有素材的號碼（那會讓正文引用失效）。
- 驗證：`test/unit/test_manuscript_image_upload.py::test_figure_label_is_deterministic`、
  `test/unit/test_manuscript_section_switch.py` 的素材插入斷言。

## NOTE-035：錯誤回報不得使用阻塞式原生對話框

- 決策日期：2026-08-13
- 適用範圍：`app/static/js/manuscript_wsui.js` 的 `showNotice()` 與其所有呼叫端。
- 決策：`alert()` / `confirm()` / `prompt()` 不得用於錯誤回報或流程確認，
  一律改用非阻塞提示條。
- 原因（**實測，不是偏好**）：原生對話框會凍住整個 renderer 行程。
  本專案已經因此吃過兩次虧：§3.8 記載過一次；做 PID 解析的 A/B 時
  `alert()` 讓自動化的 `preview_eval` 直接逾時、只能重啟容器。
  最嚴重的是 `saveSectionConfig()` 的 catch —— **一次存檔失敗會讓整個工作檯
  停止回應**，使用者連「重試」都按不到，而它本來只是要說一句「存檔失敗」。
- 決策細節：成功／資訊類 6 秒後自動消失；警告／錯誤類留著等使用者關閉，
  因為那通常要據此採取動作。訊息一律用 `textContent` 寫入
  （內容含伺服器回來的檔名、標題與錯誤字串）。
- 本輪範圍：只收斂 `manuscript_wsui.js`（7 處）。
  `dashboard.js`／`lava_setup.js`／`google_driveapi.js`／`paq_interact.js`
  仍有數十處，屬於獨立一輪 —— 那幾支需要各自的提示條宿主，
  硬套 Manuscript 的 host 會產生跨頁面的相依。
- 驗證：`test/unit/test_manuscript_docx_export.py` 之外，
  以真瀏覽器確認匯出失敗時提示條出現且頁面仍可操作。

## NOTE-036：章節完成度與章節設定分開存；整體比例保留欄位但現階段不計算

- 決策日期：2026-08-14
- 適用範圍：`app/core_pro/manuscript/model_section.py` 的 `ManuSectionProgress`、
  `manuscript_routes.py` 的 `/manuscript/api/progress/*`、
  `app/static/js/manuscript_progress.js`、`app/static/js/dashboard.js`。
- 決策一：完成度**另立一張表**（`manu_section_progress`，鍵為 pid + section_key），
  **不加在 `ManuSectionConfig` 上**。
- 原因（這是本題唯一真正的陷阱）：`POST /manuscript/api/sections/<pid>` 的實作是
  ```python
  ManuSectionConfig.query.filter_by(pid=pid).delete()   # 全量覆蓋，避免排序殘留
  ```
  也就是**每一次改章節名稱或調整順序，整張設定表會被刪掉重建**。
  完成度若是那張表上的一個欄位，使用者只要在「章節管理」按一次儲存，
  **全部章節的進度就會靜默歸零**，而且畫面上不會有任何錯誤。
  分表之後，「設定重寫不得清空進度」這件事是**結構上成立**的，
  不是一條要靠人記得的紀律。
- 決策二：**整體比例現階段不計算**。API 一律回 `overall: null`，
  前端顯示「尚未計算」。
- 原因：整體比例不是各章平均 —— 章節的份量差很多（Abstract 與 Results 不等重），
  而且有些章節在特定研究裡根本不會寫。在權重規則定案之前，
  **給一個看起來合理但其實錯的數字，比明白說「還沒算」更糟** ——
  使用者會拿它去回報進度。欄位先留著，等規則定了只改一個函式。
- 決策三：寫入門檻用 `_socket_can_write_section`，不是專案層的 editor 門檻。
  - viewer 一律不得寫入（前端唯讀 + **後端也擋**）。
  - owner／editor 可寫所有章節。
  - coauthor（限定編輯）**可以寫自己被指派的章節** —— 被指派的人才知道那一章寫到
    哪裡；要他回報給 owner 再由 owner 代填，等於讓最不知情的人填最需要準確的欄位。
    這與 NOTE-029 的草稿保底採同一個判定函式。
- 不變量：
  1. `progress_percent` 一律是 **0～100 的整數**；伺服器負責 clamp 與型別檢查，
     前端的 `min/max` 只是體驗優化。
  2. 讀取範圍與 `_filter_visible_sections` 一致：coauthor 看不到未被指派的章節，
     **進度 API 也不得洩漏那些章節的存在**。
  3. 沒有紀錄的章節回 `null`（「還沒填」），**不是 0**（「填了 0%」）。
     兩者在進度回報上意義完全不同。
- 驗證：`test/unit/test_section_progress.py`。

## NOTE-037：2C 的舊區塊以章節標題認領一次，認領後補寫 data-section

- 決策日期：2026-08-15
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `_findFusionBlock()` /
  `_adoptLegacyFusionBlock()`。
- 問題（擁有者實機回報，附圖）：在 2B 改完 Abstract 後按「push fuse to 2C」，
  2C **最後面**多出一個新的 Abstract，而**原本那個 Abstract 還在**
  —— 同一份稿件出現兩個 Abstract。
- 真因（查 git 歷史確認，不是推論）：NOTE-007 之前的實作（commit `32e1949`）是
  ```js
  const fusionHtml = `
      <div class="fusion-block mb-4 border-start border-4 border-success ps-3 animate__animated animate__fadeInLeft">
          <h5 class="text-success fw-bold"><i class="bi bi-check2-circle me-1"></i>${sectionLabel}</h5>
          <div class="fusion-body">${content.replace(/\n/g, '<br>')}</div>
      </div>`;
  this.app.fusionCanvas.innerHTML += fusionHtml;      // append，且**沒有 data-section**
  ```
  那些區塊**沒有章節身分**，只有一行標題文字。NOTE-007 之後 `_findFusionBlock()`
  是用 `.fusion-block[data-section]` 查的，查不到舊區塊 → 走 append 分支 → 產生第二個。
  擁有者的專案已經到 G.Ver V12，2C 裡面存的正是這種舊區塊
  （截圖佐證：舊的那個標題旁**沒有 `S.Ver` 徽章**，新的那個有 `S.Ver 1.5`）。
- 決策：`_findFusionBlock()` 找不到 `data-section` 相符的區塊時，**認領一次**
  符合條件的舊區塊，並當場補寫 `data-section`（一次性遷移，之後就是正常 upsert）。
- 認領條件（**四道全部成立才認領**，這裡是全題最危險的地方）：
  1. 該區塊**完全沒有** `data-section` —— 絕不從別的章節手上搶。
  2. 標題文字（剝掉 icon 與 `S.Ver` 徽章、正規化空白、忽略大小寫）
     **完全等於**該章節的 label —— 不是 `includes`。
     用 `includes` 會讓 `Abstract` 認領 `Abstract and Keywords`。
  3. 該區塊在 `fusionCanvas` 之內。
  4. 只認領**第一個**相符者。
- **為什麼這不違反 NOTE-007 的「不得靠標題文字猜歸屬」**：NOTE-007 擔心的是
  「把使用者的舊稿搬錯位置」。這裡不搬動任何東西 —— 區塊留在原位，只補一個
  識別屬性；而且比對是**全等**、只針對**完全沒有身分**的區塊。
  真正會搬錯位置的是相反的作法（append 一個新的到最後面），而那正是現在的症狀。
- 同名多個舊區塊：認領第一個，**其餘保留不動**，並以非阻塞提示條告知使用者
  「2C 有多個『X』區塊，已更新第一個，請確認是否移除其餘」。
  **刻意不自動刪除** —— 那是使用者的稿件，靜默刪除比留下重複更糟。
- 認領時補上 `<span class="fusion-src-ver">`（舊樣板沒有這個元素），
  否則 S.Ver 標記寫不進去，「這段來自 2B 第幾版」就永遠是空的。
- 不變量：
  1. 認領**不改變區塊在畫布上的位置**（使用者的排版是他自己排的）。
  2. 認領只發生在「該章節還沒有帶 `data-section` 的區塊」時。
  3. 認領後該章節必為**唯一**帶該 `data-section` 的區塊；同章連推 N 次仍只有一個。
- 驗證：`test/unit/test_fusion_legacy_adoption.py`；瀏覽器實機（舊 HTML 載入 2C →
  推同章 → 仍只有一個該章區塊，且內容被更新、位置不變）。

## NOTE-038：Word 貼上殘留的硬換行只由使用者明確清理，系統不自動改稿

- 決策日期：2026-08-16
- 適用範圍：`app/static/js/manuscript_soed.js` 的 `cleanupHardWraps()` /
  `_isSoftWrapBreak()`，2C 工具列的「清理硬換行」按鈕。
- 問題（在**正式站的真實稿件**上量到的，不是推論）：
  `DGVRYV-p` 的 `paper/V12.json`（13,611 字元）裡有 **146 個 `<br>`**，
  而且幾乎全部落在句子中間：
  ```
  code-switching<br>requires        comprising<br>8,420
  which<br>primarily                integrating<br>Transformer-based
  ```
  來源是 Word 貼上（內容帶 `class="MsoNormal"`、`<span lang="EN-US">`），
  原文是**每 ~70 字元硬斷行**的純文字；NOTE-030 修掉的那個
  `content.replace(/\n/g,'<br>')` 把每一個來源斷行都變成真的 `<br>`。
  於是段落被永久切成 70 字一行 —— 這正是擁有者說的「2C 的匯出排版完全亂掉」
  的另一半（第一半是假 .doc 匯出，見 NOTE-031）。
- **NOTE-030 只能阻止新的，不會回頭修好已經存進去的內容。**
  已存檔的 V12 仍帶著那 146 個 `<br>`。
- 決策：提供一顆**明確的**「清理硬換行」按鈕，**不自動執行、不在載入或存檔時偷偷做**。
- 原因：
  - 這是**改使用者的稿件**。哪些換行是作者故意的，只有作者知道；
    系統在載入時自動改寫，等於在他沒看到的時候動他的論文。
  - 清理後**不自動存檔**：畫面上先呈現結果，使用者確認後自己按 Save 才落地。
    不滿意就重新整理，什麼都沒發生 —— 這讓整個動作可逆。
- 判定規則（**以 DOM 節點判斷，不對 HTML 跑 regex**；HTML 用 regex 改寫是已知的坑）：
  某個 `<br>` 被視為「硬換行殘骸」需同時成立：
  1. 前一段可見文字的結尾是文字或**非句末**標點（`, ; : ) ] -` 或字母數字）；
  2. 後一段可見文字（略過空白）的開頭是字母／數字／中文／左括號。
  也就是「這個換行出現在一個句子的中間」。
  另外兩種明確的 Word 垃圾一併處理：
  3. 連續 **3 個以上**的 `<br>`（Word 用來充當垂直間距）整組移除；
  4. 直接位於 `<ol>`／`<ul>` 底下、夾在 `</li>` 與 `<li>` 之間的 `<br>`。
- **刻意不移除**句末（`. ! ? 。 ！ ？`）之後的 `<br>` —— 那有可能是作者真的想分行。
- 不變量：
  1. 只動 `<br>` 節點，不得刪除或改寫任何文字節點。
  2. 不得自動存檔；清理結果必須由使用者按 Save 才進版本。
  3. 必須回報移除數量，讓使用者知道剛剛發生了什麼。
- 驗證：`test/unit/test_hard_wrap_cleanup.py`（以真實 V12 的形狀為樣本）；
  瀏覽器實機（清理前後段落數不變、文字內容逐字不變、只有 `<br>` 減少）。

## NOTE-039：對話式找文獻的幻覺防線是回查真實來源，不是 LLM 互檢

- 決策日期：2026-08-17
- 適用範圍：`app/llm_service/matching_tasks/task_3a_litchat.py`、`task_3bc_scout.py`、
  `task_3search.py` 的 `resolve_reference()`、`app/llm_service/adapter/llm_google.py`
  的 `send_text_with_search_grounding()`、`app/core_pro/literature/literature_chat_routes.py`。
- 背景：擁有者用 multi-LLMs debate + model cascades + cross-prompting 手動找到三篇
  腦神經科學關鍵論文，建立 LAVASR 對應大腦聽覺流程的理論可靠度。這個流程原本只存在
  於他的操作習慣裡，每次都要重來一遍；本輪把它做進 Literature 頁。

### 1. 為何機械驗證是主防線、互檢只是輔助

- **兩個 LLM 可以一起編出同一篇不存在的論文**，互檢會一致通過。
  但編出來的 DOI 在 Crossref 查不到、標題在 OpenAlex/PubMed/arXiv 也查不到。
- 因此 B↔C 互檢負責抓「真實但不相干」，`resolve_reference()` 負責抓「根本不存在」，
  兩者不可互相取代。任何「因為模型很有把握所以保留」的例外都不准加。
- `resolve_reference()` 查無資料時**必須回空**。放寬比對門檻換取「有東西可回」，
  等於用另一篇論文冒充使用者要的那篇——比空手而回更糟。
- 門檻：標題相似度 0.82（DOI 對得上時放寬到 0.55），年份差 > 1 年直接否決
  （會議版 vs 期刊版差 1 年是合法的，差兩年以上多半是對到同名的另一篇）。

### 1.5 要求 JSON 會讓 Gemini 完全跳過搜尋（實機量到的，不是推論）

- 實測日期：2026-08-17，`gemini-flash-latest` 與 `gemini-3.1-pro-preview`，
  同一支 `send_text_with_search_grounding()`、同一個主題，只差在 prompt：

  | prompt | `groundingMetadata` | 實際行為 |
  |---|---|---|
  | 開放式「Use Google Search… write a short survey」 | 12–18 chunks、5–9 條 `webSearchQueries` | 真的搜尋 |
  | 「Reply with JSON only: {…}」 | **整個欄位缺席** | 憑記憶編出 title/DOI |

- **失敗模式是靜默的**：HTTP 200、`finishReason: STOP`、有格式正確的 JSON、
  沒有任何錯誤訊息。唯一看得出來的差別就是 `groundingMetadata` 不見了。
  第一版的 scout prompt 正是要求 JSON-only，所以照那版上線，B/C 根本不會上網。
- 因此 `run_scout()` 是**兩段式且不得合併**：
  第一段 grounded 開放式提問負責「真的上網」，
  第二段不帶 tools 負責「轉成 JSON」（沒有 tools 就可以安心要求 JSON）。
  `_build_scout_prompt()` 裡出現 JSON 指示即為契約破損，有測試盯著。
- `chunk_count == 0` 一律**拋例外**進 `stage_errors`，不得當成「這次剛好沒找到」。
  groundingChunks 是唯一能證明「這批文獻來自搜尋而非記憶」的硬證據；
  放行等於讓整個功能靜靜地退化成兩個模型互相幻想。
- `google_search_retrieval` 已被 API 拒絕（HTTP 400：
  `google_search_retrieval is not supported. Please use google_search tool instead.`），
  所以 tools 寫法的退回順序必須是 `google_search` 優先。

### 1.6 時間預算：grounded 檢索與純文字階段不能共用同一個數字

- 實測：`gemini-flash-latest` 跑完 9 條搜尋查詢約 90 秒；
  `gemini-3.1-pro-preview` 在 75 秒的舊時限內**跑不完直接被砍**
  （`stage_errors: {'scout_C': 'timeout after 75s'}` —— 變成單邊搜尋，互檢失去意義）。
- 因此拆成三個旋鈕：`LIT_CHAT_SEARCH_TIMEOUT_SEC`（150，只給 grounded 檢索）、
  `LIT_CHAT_STAGE_TIMEOUT_SEC`（60，互檢與回查，兩者都不開 grounding）、
  以及 `LIT_CHAT_TOTAL_BUDGET_SEC`（210）當總閘門 ——
  各階段時限相加會衝破 gunicorn 的 `--timeout`（300s），
  所以前面跑久了、後面就只能拿剩下的時間，並保留約 90 秒給 A 組稿。
- 兩段都到位後的實測：B/C 各自 18/14 chunks，11 候選 → 10 篇通過回查，
  `stage_errors` 為空，全程 119 秒。

### 2. 為何 B/C 都必須綁 Gemini

- Google Search grounding 是 **Gemini 限定**：Gemma 走 Google AI API 完全不支援 tools。
  所以「BC 限定使用 gemini/gemma」在實作上只能是兩隻 Gemini（型號不同以保留互檢差異）。
- `LlmBus.send_message(grounding=True)` 在 provider 不支援時**回明確錯誤**，
  不得默默改走一般生成路徑。沒有這條，B 綁到 OpenAI 時使用者會拿到憑記憶編的清單，
  卻以為它經過搜尋驗證——這是最危險的失敗模式，因為它看起來完全正常。
- 這個錯誤在 dispatcher 被列為 fatal：綁錯 provider 不是暫時性故障，重試只是撞三次。

### 3. 為何「只到 Scholar/PubMed/arXiv」要靠後驗而不是參數

- Gemini grounding **沒有限制網域的 API 參數**，回傳的還是
  `vertexaisearch.cloud.google.com/grounding-api-redirect/...` 這種 redirect URI。
- 所以 `site:` 只能寫進查詢字串引導（提高命中率），真正的把關在回查：
  只有 Crossref/OpenAlex/PubMed/arXiv 認得的東西才進得了結果。
- 對外顯示連結只允許 `doi.org` / PubMed / arXiv / Google Scholar 四種。
  OpenAlex 給的出版社 landing page（例如 sciencedirect.com）**一律退回 Scholar 查詢連結**
  ——貼一個沒被驗證過的出版社網址等於默默違背「連結只到這三個來源」的承諾。

### 4. 為何 grounded 請求不進 response cache

- grounded 請求的價值就是「現在上網查到的」。命中舊 cache 等於拿一份沒搜尋過的
  舊答案冒充搜尋結果，而且從回應上看不出來。`dispatcher.execute(grounding=True)`
  一律略過 `llm_response_cache`。
- 另一層 cache 在 `LiteratureChatStore`（依 query hash、TTL 預設 6 小時），
  那是省 grounding 額度用的，且**stage_errors 非空的結果不入 cache**：
  半套的搜尋被快取起來，接下來 6 小時都會拿到同一份殘缺清單且看不出原因。

### 5. 為何不自動搜尋、為何匯入只到 candidate

- 每一輪搜尋是 4 次 LLM 呼叫（B/C 搜尋 + B/C 互檢）加上數十次外部 API 回查。
  意圖閘門由 A 做**語意**判斷，關鍵字快篩只是餵給 A 的提示：
  「不用再找了」含「找」但不該觸發搜尋。
- A 判不出來時退回關鍵字，但必須在 `meta.intent_source=keyword` 標明是退化路徑。
- 對話裡的論文匯入文獻庫時**永遠只產生 candidate**（沿用 NOTE-013 / NOTE-020）：
  納入與否是作者的學術判斷，而 included 直接決定 Drafter 的寫作依據。

### 6. 不變量

1. 回查不到的論文不得出現在結果，且必須出現在 `dropped` 裡並附原因。
2. 任一階段失敗都要進 `meta.stage_errors` 並在前端顯示——
   跑一半的搜尋不能長得像完整結果。
3. A 的回覆只能引用傳入的已驗證清單，不得補充清單外的論文。
4. 對話存檔必須連 `papers` 一起存，否則重新整理後 hyperlink 消失，
   使用者得再付一次驗證成本。
- 驗證：`test/unit/test_literature_chat_pipeline.py`、
  `test/unit/test_literature_chat_persistence.py`、
  `test/unit/test_llm_grounding_contract.py`；
  瀏覽器實機（純聊天不觸發搜尋、找文獻連結點得開、同句再問命中 cache）。

## NOTE-040：Google adapter 不得使用 raise_for_status()，API key 在 query param 裡

- 決策日期：2026-08-18
- 適用範圍：`app/llm_service/adapter/llm_google.py` 的所有 HTTP 錯誤處理。
- 問題（實測，不是推論）：Google Generative Language API 的金鑰是以
  `params={"key": ...}` 送出，也就是**在 request URL 裡**。而 httpx 的
  `raise_for_status()` 會把**完整 request URL** 寫進 `HTTPStatusError` 的訊息：

  ```
  Client error '400 Bad Request' for url
  'https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=AIzaSy...'
  ```

  實測對照（同一支腳本）：`ConnectError` 不帶 key；`raise_for_status()` **帶**；
  手動比 `status_code` 再自組訊息不帶。
- 為什麼這是真的洩漏而不只是理論：
  - 這條錯誤字串會回到 dispatcher，再進到各 task 的錯誤路徑，最後顯示在 UI ——
    Literature 找文獻的 `meta.stage_errors` 是直接渲染在對話視窗裡的。
  - roothinks 是**多人 workspace**（owner／editor／coauthor／viewer），
    LLM connection 是共用的。key 過期或配額用盡時，任何 editor 觸發一次
    Manuscript 草稿生成，就會在畫面上看到 owner 的 API key。
  - 出事的那行在 `_generate_with_timeout` 的 cancellable 分支，
    而 Manuscript 2A 走的正是那條（NOTE-002）。
  - 檔案標頭寫的是「API key ... 不寫 log/cache」，這條與該承諾直接衝突。
- 決策：這個 adapter **一律不使用 `raise_for_status()`**。自己比 `status_code`，
  錯誤訊息只帶「狀態碼 + response body」（body 是 Google 的 JSON 錯誤，不含 key）。
- 護欄形式是**原始碼層級的守衛**，不是行為測試。理由：出事那行要走到它得先有
  真的 genai model 與 cancel_event；實測把修復還原後，整組行為測試仍然全綠
  （toothless）。原始碼守衛則在還原漏洞時立刻變紅，已 mutation 驗證。
  守衛必須排除註解行 —— 這條 NOTE 的說明文字裡就有這個字串。
- 不變量：
  1. `llm_google.py` 內不得出現非註解的 `raise_for_status(` 呼叫。
  2. 任何從本 adapter 回出去的錯誤字串都不得包含 `self.api_key`。
- 驗證：`test/unit/test_llm_grounding_contract.py` 的
  `test_google_adapter_never_calls_raise_for_status`、
  `test_http_error_never_carries_the_api_key`、
  `test_grounded_failure_message_never_carries_the_api_key`。
