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
