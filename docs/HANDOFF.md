# HANDOFF — roothinks 正式站（給接手的 AI 讀）

最後更新：2026-08-10　分支 `release/vm-20260806`（HEAD 193393a，本輪改動尚未 commit）

維護規範：每次實作結束更新本檔，與該次改動一起 commit。只寫已實際查證的事，
推測要標明是推測 —— 下一個 AI 會信任這裡的每一句話。

---

## 0. 動手前必知

- 三個 remote 共用血緣但**不自動同步**：`origin`=LAVA-Cowork/roothinks、
  `personal`=SpadesZ/roothinks、`r10005`=SpadesZ/roothinks-r-10005。
  要部署就**三邊都 push**，否則 VM `git fetch` 抓不到分支。
- VM 屬於 `kx4492.hddspace1@gmail.com`（不是預設帳號）：
  ```
  gcloud compute ssh roothinks --project=project-ce416b1d-0d83-41d9-a4f \
    --zone=asia-east1-c --account=kx4492.hddspace1@gmail.com --command="..."
  ```
- 原始碼是 bind-mount（`~/roothinks-app` → `/app`），改碼
  `docker compose restart roothinks-paq` 即生效。
  **改環境變數必須 `docker compose up -d`** —— `restart` 不重讀 compose。
- 容器內跑腳本要自己給 `PYTHONPATH=/app`，否則 `ModuleNotFoundError: No module named 'app'`
  （`-w /app` 沒用，`sys.path[0]` 是腳本所在目錄）。臨時腳本放
  `~/roothinks-app/tmp_ops/`（`tmp*/` 已 gitignore）。
- 測試（`--user 0:0` 不可省，否則 200+ 個假的 PermissionError）：
  ```
  docker run --rm --user 0:0 -v "<repo>:/app" -e SOCKETIO_ASYNC_MODE=threading \
    -e API_AUTH_ENABLED=0 -e FLASK_ENV=development -w /app \
    roothinks10005-roothinks-test:latest python -m pytest test/unit -q
  ```

---

## 1. 現況數字

| 項目 | 值 |
|---|---|
| 測試 | **814 passed / 0 failed**（`pytest test/unit tests`；起點 773） |
| 為什麼不是 `pytest test tests` | 那條含 integration_smoke，會連正式 DB。含 smoke 的歷史數字是 819 |
| 隔離證據 | 同輪 sqlite/makedirs spy：只碰到寫死的 `data/sys/llm_match.db`，未開啟 roothinks.db / manu_core.db，未新建目錄 |
| VM 工作目錄 | `git status --porcelain` 空 |
| 容器 | `roothinks_progress_paq_v8_10005` healthy，`0.0.0.0:80->10005` |
| 專案數 | 7；`name`/`research_title` 分岔筆數 0 |
| 手稿實際資料 | **全站只有 `DGVRYV-p/abstract/V0.1.json` 一個 block 版本，paper 版本 0 個**（2026-08-09 全量掃描） |

---

## 2. 本輪最重要的：三個「先前的診斷是錯的」

### 2.1 Drafter 無聲卡死 —— 真因是 CORS，不是 handler

舊交接文件斷定 `handle_chat` 死在 `emit('job_queued')` 之前（`save_chat_history`
的 FileLock 逾時），依據是「日誌裡 `manu_chat` 出現 0 次」。**這是錯的。**

實測日誌：
```
http://34.80.240.29 is not an accepted origin.
"GET /socket.io/?EIO=4&transport=websocket" 400   × 128 次
polling 請求 0 次
```
Socket **從來沒連上**，`chat_message` 送不到伺服器，handler 一次都沒被呼叫。
`manu_chat` 為 0 的觀察是對的，推論是錯的。

根因：`docker-compose.override.yml` 寫死 `CORS_ALLOWED_ORIGINS`，不含對外的
`34.80.240.29`。而 compose 的 `environment:` **優先序高於 `env_file:`**，
所以就算該機 `.env` 設對也會被蓋掉。已修（`923c8e6`）改為 `${CORS_ALLOWED_ORIGINS:-…}`。

可重複驗證：
```
curl -o /dev/null -w '%{http_code}\n' -H 'Origin: http://34.80.240.29' \
  'http://127.0.0.1/socket.io/?EIO=4&transport=polling'    # 期望 200
curl -o /dev/null -w '%{http_code}\n' -H 'Origin: http://evil.example.com' \
  'http://127.0.0.1/socket.io/?EIO=4&transport=polling'    # 期望 400（檢查仍有效）
```

**兩個衍生陷阱**：
- socket.io 客戶端失敗夠多次後停止重試。修好後**使用者必須重新載入頁面**，
  舊分頁不會自己重連 —— 這害我們白繞一輪。
- UI 右上角綠色「**Online**」徽章在 socket 完全沒連上時仍顯示 Online。**它在說謊**，
  別拿它當健康指標。（未修）

### 2.2 改題目沒反應 —— 資料有存，只是存到另一個欄位

`projects` 有 `name` 與 `research_title` 兩欄。編輯 modal 只寫 `name`；
dashboard 正式研究卡片與**所有 LLM task**（`task_1paqswot`／`task_2cubegen`／
`task2A_paqchat` 的提示詞、手稿初始標題、文獻 context、`workflow_status`）
讀的都是 `research_title or name`。而 `research_title` 只在轉正那一刻寫入一次，
之後沒人更新 → 轉正後題目永遠改不掉，AI 模組一直用舊題目。

擁有者決策：**專案名稱即研究題目，兩欄永遠相等**。`0530e94` 收斂三個寫入點
（`update_project` 同步、PAQ 轉正 `name=resolved_title`、直接建正式專案取 name）。

**通則：查「改了沒反應」先問畫面畫的是哪個欄位，不要只看 API 有沒有回 success。**

### 2.3 破圖「修好了」是錯的理由造成的綠燈

第一次修（`82507cc`）加 `flex-wrap` 後，我用 `bar.right - pane.right` 當溢出量得到 0，
宣告修好。但 `.matrix-toolbar` 是 block-level flex 容器，box 寬度**恆等於** pane 寬度
—— 那個差值永遠是 0。**量到的是恆真式。** 真正跑出去的是它的子元素
（`overflow: visible` 讓子元素照畫）。

正確量法是 `document.elementFromPoint` 命中測試：被裁切的內容不可命中，
而 bounding box **不反映裁切**。實測 pane=25px 於 pane 右緣外取樣 99 點：
有 `#matrix-pane { overflow:hidden }` → 命中 0；拿掉 → 命中 4。`9748361` 修正。

---

## 3. Drafter 語料接線（本輪主線）

### 3.1 原本的問題

Drafter 唯一的參考材料是瀏覽器送來的 `context` 欄位，內容是
`manuscript_soed.js` 的 `editorCanvas.innerText.substring(0,3000)` —— 2C 編輯區文字。
Abstract 這種從零開始的章節 2C 必定是空的，於是 LLM 只拿到 title 就寫。
`task_8drafter` v2.5 還把 Zero-Context Guard 註解掉了，明知沒材料仍照生。

### 3.2 現在的資料流

```
data/<pid>/study/<pid>_note.json          → notes（人類想法）
data/<pid>/literature/papers/<name>/
    05_interprets/fusion/full_text.json   → 依 reading_order 取 block，
                                             濾掉 Header 與 page noise
                 ↓
app/core_pro/manuscript/source_context.py
  build_drafter_corpus(pid, title, budget_tokens) -> dict
    { corpus, sources, skipped_papers, estimated_tokens }
                 ↓
manuscript_routes.handle_chat → task_8drafter.process_request
```

預算**依論文平均分配**，不是串接後截斷 —— 後者會讓排在後面的論文整篇靜默消失。

### 3.3 四道會把語料吃掉的限制（踩過兩次，只調一道沒有用）

| 位置 | 變數 | 現值 | 踩過的坑 |
|---|---|---|---|
| `source_context.py` | `DRAFTER_CORPUS_MAX_TOKENS` | 16000 | 原 120000，超過 TPM 節流 → 永遠生不出東西 |
| `task_8drafter.process_request` 與 `_generate_text_response` | `DRAFTER_MAX_CONTEXT_CHARS` | 400000 | 原 24000 **字元**，把 233544 字元語料砍到只剩第一篇開頭，使用者指名的 SEBASR **從未抵達 LLM** |
| `_compress_context_for_prompt` | `DRAFTER_COMPRESS_CONTEXT_MAX_TOKENS` | 110000 | 原為硬寫死 2200 tokens |
| `docker-compose.override.yml` | `GOOGLE_LLM_GLOBAL_TPM_LIMIT` | 25000 | **這才是真正的天花板**，不是 gemini-2.5-flash 的 context 視窗 |

要放大語料就必須**連同 TPM 一起提高**，只改 `DRAFTER_CORPUS_MAX_TOKENS`
會讓草稿生成整個停擺。

### 3.4 驗收證據

正式站 `DGVRYV-p` 實跑（容器內直接呼叫 `process_request`）：

- 語料 metadata：`sources` = Beyond_Monolingual、SEBASR_IJMIR；
  `skipped_papers` = ASR-Syllable、Code-Switching_Red-Teaming（缺 `full_text.json`）；
  全量 81749 tokens / 233544 chars。
- 全量送出 → `LLM_PROVIDER_ERROR: quota throttle (tpm_limit) exceeded wait budget 180s`。
- 降到 16098 tokens → 成功產出 Abstract，內含：
  > ...reducing the Mixed Error Rate (MER) ... from approximately **65% to under 13%**,
  > with the lowest recorded average error rate reaching **7.56%** on specific datasets.

  `MER`、`65%→13%`、`7.56%` 都不在標題內，只可能來自 SEBASR 全文 —— 這是語料
  真的抵達 LLM 的判準。**對照組**：字元上限還是 24000 時，同一個 prompt 產出的
  Abstract 一個具體數據都沒有。

---

## 3.5 手稿工作區三個「訊號在說謊」（2026-08-09）

擁有者回報的兩個問題查完了，根因都不是原本猜的那個。

### 3.5.1 舊版載入回報成功、畫布空白 —— execCommand 沒有插入點

`insertEditorCard()` 用 `document.execCommand('insertHTML', …)` 插卡片，而
execCommand **插在「目前的插入點」**：插入點不在 contenteditable 畫布內時，
它會安靜地什麼都不做（不丟例外、不回 false）。

同一支檔案裡三條會動的路徑自己把前提寫出來了 —— `importToEditor()`、
Word 匯入都在呼叫前 `editorCanvas.focus()`。壞掉的兩條沒有：

| 呼叫點 | 清空畫布 | focus | 結果 |
|---|---|---|---|
| `importToEditor()` | ✓ | ✓ | 正常 |
| Word 匯入（`manuscript_wsui.js`） | ✓ | ✓ | 正常 |
| `block_loaded`（開啟舊版） | ✓ | ✗ | **回報成功、畫布空白** |
| 草稿復原 `restore()` | ✓ | ✗ | **同上（擁有者尚未回報，但一樣壞）** |

另外 `queryCommandSupported('insertHTML')` 在 Chrome 恆為 true，所以那條
`innerHTML +=` 的 fallback（會動的那條）**永遠碰不到**。

已修：前置動作 `_placeCaretInEditor()` 移進 `insertEditorCard()` 本身
——前提屬於這個函式，不該要求每個呼叫端各自記得。

### 3.5.2 Title 重整回舊值 —— 兩層原因，缺一都修不好

1. **存進去就被毀掉**：`save_paper_version` / `save_block_version` 對 title 呼叫
   `_safe_component()`（`[^A-Za-z0-9._-]+` → `_`，截斷 80 字元）。那是舊格式把
   標題寫進檔名時代的遺留，新格式檔名早已是 `V2.json`（見 `list_papers` 的
   v1.0 註解）。正式站實測存著的值：

   ```
   'An_LLM-Augmented_Validation_and_Analysis_Framework_for_A_Self_Evaluated_Bilingua'
   ```

   空白全變底線，而且在 `Bilingual` 中間被切斷。
2. **沒有任何程式碼把它讀回輸入框**：頁面載入一律填
   `proj.research_title or proj.name or pid`，所以重整必然回到舊值。

擁有者決策（2026-08-09）：**手稿 Title 就是專案題目**，與先前「專案名稱即研究
題目、兩欄永遠相等」一致。已修：離開輸入框時 POST 既有的
`/api/project/update/<pid>`（`{"name": title}`，後端同步 `research_title`）。

**刻意不新增 socket 端點**：該 HTTP 端點已有 `enforce_project_ownership`
（POST 需 editor 以上），自建一條等於繞過權限檢查。`_safe_component` 保留給
路徑用（中文章節目錄靠它去撞），另拆出 `_clean_display_title` 給 payload。

### 3.5.3 Online 徽章不是「會說謊」，是根本沒接線

它是 `manuscript_workspace.html` 一段**寫死的靜態 HTML**，沒有 id、沒有任何
JS 綁定，永遠顯示綠色 Online。已改為 `id="socketStatusBadge"`，由
`connect` / `disconnect` / `connect_error` 更新（`connect_error` handler 本來
不存在 —— 連不上時畫面沒有任何跡象）。

**第一版修法是錯的，瀏覽器實測才抓到。** 只把徽章初始值設成 `Connecting…`
再靠事件更新，結果是：`socket.connected === true`、console 完全沒有
`[Socket] Connection established`、徽章卻一直停在灰色 `Connecting…`。
原因是 socket 在 `manuscript_ws.js` 就建立，**往往在 `setupSocketEvents()`
註冊 handler 之前就已連上**，那個 `connect` 事件不會補送。

一度比原本更糟：寫死綠燈至少在「已連線」這個常見情況下是對的。
已改為註冊 handler 前先讀 `socket.connected` 初始化。

**這是本輪最重要的教訓**：靜態測試（「原始碼裡有沒有呼叫 `_setConnectionBadge`」）
當時是**綠的**，缺陷照樣存在。事件式 UI 的狀態一定要同時處理「當下狀態」與
「之後的變化」，只做後者就是這個下場。

### 3.5.4 檢索失敗不再靜默

`manuscript_ruling.py` 那個 `except Exception: injected_context_items = []`
原本完全不留痕跡。先前那個 `UnboundLocalError` 正是躲在同一個區塊裡。
已加 `logger.exception`，仍維持 degrade-not-crash。

---

## 3.6 檢索三個實測缺陷與「日誌本身是壞的」（2026-08-09 下午）

### 3.6.1 先講最重要的：logger.info 從來沒有輸出過

這個 repo 從未設定 root logger，Python 預設 `WARNING`，**所有 `logger.info()`
被無聲丟棄**。前一輪診斷 drafter 卡死時，正是以「容器日誌裡 `manu_chat`
出現 0 次」推論 handler 沒被呼叫 —— 但 `[manu_chat] job_queued` 那幾行全是
`logger.info`。**觀察對、訊號壞、推論必錯。**

補充：其實有一個 `RotatingFileHandler` 寫到
`data/_logs/system/runtime.log`，app 日誌一直在那裡（`manu_chat` 有 31 筆），
只是沒進 stdout，`docker logs` 看不到。**查這個系統的問題要看那個檔案，
不要只看 `docker logs`。** 已在 `create_app()` 補 `_configure_logging()`
（`LOG_LEVEL` 預設 INFO，輸出到 stdout）。

### 3.6.2 Drafter 其實是通的；壞的是「結果綁在 sid 上」

擁有者回報夥伴帳號按草稿生成卡在 25%。實際查 `runtime.log`：

```
17:02:09 job_start msg_len=174  → 17:02:16 job_done 7205ms
17:03:06 job_start msg_len=1460 → 17:03:12 job_done 6304ms
17:05:38 job_start msg_len=1555 → 17:05:47 job_done 9879ms
17:16:46 job_start msg_len=64   → 17:16:56 job_done 9754ms
```

四個 job 全部成功，草稿也都在 `chat_abstract.json` 裡（15 筆）。
`l78131037` 是 user 14、DGVRYV-p 的 **owner**，不是權限問題。

真因：結果是 `socketio.emit(..., to=sid)` 送的，**綁在單一 sid**。中途斷線重連
sid 就換人，結果永遠送不到畫面；而 `save_chat_history()` 在 emit 之前就跑完，
所以伺服器端是成功的。已修：重連後重抓該章聊天紀錄（`_resyncAfterReconnect`），
並收掉綁在舊 sid 的轉圈。

**第一版修法是錯的，瀏覽器實測才發現**：用 `_hasConnectedOnce` 旗標「跳過第一次
connect」，但頁面載入時的 `connect` 事件根本沒發生（socket 早於 handler 註冊就
連上，§3.5.3 同一個坑），於是真正的重連被當成初次連線而跳過。改為以
`socket.connected` 初始化。**同一個坑在同一天踩第二次。**

### 3.6.3 檢索：NL 導向有效，但窗口把數據切掉了

可證偽的對照（正式站 DGVRYV-p）：兩個指名不同論文的查詢，回傳
**segment 重疊 0/12** —— NL 真的在導向，不是某篇剛好佔多數。

但 `7.56%` 進不到 prompt。逐層查下去：

| 可能 | 實測 |
|---|---|
| 索引沒有這個數字 | ✗ 有 2 段含 7.56 |
| 沒被檢索到 | ✗ 兩段都在 top-12 |
| **窗口切掉了** | ✓ `sec-22` 6865 字元，7.56 在第 6824 字元 |

`_snippet` 原本用 `min(每個詞第一次出現)` 當窗口左界，**永遠貼著段落開頭**；
而論文數據幾乎都在章節尾巴。sec-22 的 17 個命中有 13 個落在最後 3000 字元，
舊窗口錨在 494。已改為挑「命中最密集」的窗口。

**另一個更大的浪費**：`fsec-18` 標題是 `References`、**143,482 字元**，是全篇最長
段落，幾乎命中任何查詢詞，在指名 SEBASR 的查詢裡排到第 1 名 —— 一個 top_k
名額與 3000 字元預算全花在別人的論文標題上。已在**檢索時**排除書目／致謝／
OCR 殘渣（不動索引，引用建議仍需要書目）。

修正後（本機以正式站索引實測）：三種查詢**全部** `7.56=True`（修正前全 False），
且回傳段落不再出現 References。NL 導向仍是 0/12 重疊，沒有被改壞。

### 3.6.4 語料預算：現在只剩 13% 餘裕，補完文獻就會爆

真實 UI 路徑是**兩條疊加**：`handle_chat` 先 `build_drafter_corpus()`（整包倒），
再由 `validate_and_prepare()` 疊上檢索。

| | 估 token | 隨論文數成長？ |
|---|---|---|
| 整包倒語料 | 11,492 | **會**（上限 16,000） |
| 只走檢索 | 9,049 | **不會**（top_k×snippet 封頂） |
| 疊加（實際送出） | **21,773** | 會 |
| `GOOGLE_LLM_GLOBAL_TPM_LIMIT` | 25,000 | — |

`skipped_papers` 還有兩篇缺 `full_text.json`。**一旦補齊，整包倒會漲到 16,000，
16,000 + 9,049 = 25,049 > 25,000，草稿生成直接被節流擋死。**
檢索既然已能帶出 7.56，下一步應該是拿掉整包倒 —— 那是唯一能隨論文數擴展的路。
（本輪未動，屬於行為變更，需擁有者決定。）

**注意**：檢索「不爆」指的是注入量，不是「每篇都會被看到」。`top_k=12` 是總數，
50 篇也只取 12 段。要廣泛覆蓋時該調的是 top_k，不是回頭倒整包。

---

## 3.7 語料改為純檢索（2026-08-09 晚，擁有者拍板）

問題：「我要用 a+b+c+d 這幾篇寫某一段」會怎樣？實測答案分兩半。

**檢索不會爆，但會偏食。** 純按分數取 top_k（正式站 DGVRYV-p）：

| 查詢 | 分配 | context |
|---|---|---|
| 只指名 SEBASR | 12 : 0 | 9,219 tokens |
| 同時指名兩篇 | **10 : 2** | 9,209 tokens |
| 指名兩篇並要求「比較」 | 8 : 4 | 9,214 tokens |

注入量幾乎不隨篇數變動 —— token 不是問題。**偏食才是**：說了兩篇卻只讀了一篇，
產出看起來有引用，實際上沒有。

**整包倒會爆，而且論文越多越沒用。** `per_paper_budget = (16000 - 筆記) // 篇數`
之後**取開頭**：4 篇剩 16000 字元、16 篇剩 4000 字元。而數據在章節尾巴
（§3.6.3）。它固定吃滿 16000，加檢索 9000 = 25000，剛好撞 TPM。

### 做了三件事

1. **整包倒預設停用**（`DRAFTER_CORPUS_ENABLED`，預設 0）。研究筆記不受影響 ——
   `_load_upstream_context` 另外會讀，實測 source_manifest 仍有 study_note。
   保留開關是為了不必重新部署就能回退。
2. **檢索預算放大**：`MANUSCRIPT_RETRIEVAL_TOP_K` 12→20、
   `MANUSCRIPT_RETRIEVAL_MAX_TOKENS` 12000→18000。
3. **依「被點名的論文」分配名額**（`_apply_per_paper_quota`）。訊號用的是評分階段
   既有的 alias 比對（`named_match`），三種情況分開處理：

   | 情況 | 行為 |
   |---|---|
   | 點名 ≥2 篇 | 名額在這幾篇之間**輪流**分配 |
   | 點名 1 篇 | 不介入（純分數；被點名那篇本來就有 +1.0） |
   | 沒點名 | 給最相關的幾篇保底 3 段，避免單篇壟斷 |

   **只給地板值不夠**：top_k=20、地板 3 時剩下 17 個名額仍被最高分那篇全拿，
   實測變成 17:3，比不做還明顯。必須輪流。

### 驗證（本機掛正式站索引實測）

| 查詢 | 修正前 | 修正後 |
|---|---|---|
| 只指名 1 篇 | 12 : 0 | **12 : 0**（沒被稀釋） |
| 同時指名 2 篇 | 10 : 2 | **6 : 6** |
| 指名 2 篇並比較 | 8 : 4 | **6 : 6** |

`7.56` 三種查詢仍全部 True（配額沒把 `sec-22` 擠掉）。
注入量 15,739 / 15,901 / 16,071 —— 不隨篇數成長，離 TPM 25,000 還有餘裕。
617 passed（+5）。

**寫測試時踩到的**：`test_single_named_paper_is_not_diluted` 一開始是紅的。
第一版實作在「只點名一篇」時仍給別篇 3 段保底（9:3），而真實資料剛好是 12:0
—— 因為別篇分數 ≤0 被濾掉了。**恰好對，不是設計對。** 已把三種情況分開。

---

## 3.8 2B 切章／2C 冪等／留言版本化（2026-08-10）

本輪三件事都先取得可重現的證據才動手，且每一項都在瀏覽器實機驗證過。
**全部改動仍在工作區未 commit。**

### 先更正上一輪交接的兩處說法

1. **「preview 容器資料是隔離副本」對 10099、對 10098 錯。**
   `roothinks_preview_authnone_10098`（AUTH_MODE=none 的免登入容器）當時**沒有**
   data volume，整個 repo 以 rw 掛在 `/app`，等於直接讀寫真實 `data/`，
   而 `data/` 在 `.gitignore` 第 20 行**不受 git 保護**。
   已改為掛 scratchpad 的隔離副本（種 `roothinks.db`/`manu_core.db`/`sys/`/`PAPER1-p`
   等小專案，433K）。動任何寫入路徑前先確認掛載：
   `docker inspect <name> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'`
2. **「test/ 662 passed」在上一輪結束時已經不成立。**
   上一輪把模板的 `?v=` 從 2.2 → 2.3，但 `test/unit/test_manuscript_grounding.py`
   的第 120／146／147 行把版號寫死，所以那兩個測試在交接當下就是紅的。
   本輪已同步更新為 2.7 / 5.1。**以後改 JS 一定要一起改這三行。**

### 本輪最重要的坑：Jinja 模板快取讓 `?v=` 失效

改了 `manuscript_workspace.html` 的 `?v=`，容器內檔案是新的、mtime 也是新的，
但伺服器**仍渲染舊版號** —— 編譯後的模板留在 gunicorn worker 記憶體裡。
必須重啟行程才生效。

意義：**正式站只同步靜態檔而不重啟，使用者永遠拿到舊 JS。**
（本輪一度誤以為修好了，其實是 static 檔靠 mtime 重新驗證而僥倖生效，不是乾淨驗證。）
驗證方式：`curl -s "<url>/manuscript/?pid=X" | grep -oE "manuscript_soed\.js\?v=[0-9.]+"`

### 另外兩個會讓自動驗證誤判的陷阱

- **量到過場動畫中的數字。** `.manu-panel` 有 `transition: width 0.2s`，
  呼叫 `hidePanel()` 後立刻量寬度會拿到中間值。本輪就因此差點把「沒壞的版面」
  當成 bug 去改。量版面一律等 ≥600ms。
- **`preview_screenshot` 的畫布寬度與頁面實際寬度不一致**，會拍出根本不存在的
  大片空白。版面結論一律以 DOM 量測為準，截圖只當輔助。
- **原生 `confirm()` 會凍結整個 renderer**，`preview_eval` 直接逾時，連 `1+1` 都不回。
  **草稿復原的那一個已改成非阻塞提示條（NOTE-011），本輪結束後不會再擋自動化。**
  其餘 `confirm()` 都是使用者主動點擊破壞性操作才跳（刪留言、還原主論文版本、
  刪章節、開啟舊版前強制備份），刻意保留 —— 那些不會不請自來。
  若又遇到卡住：先清隔離副本裡的 `_draft*.json`，或頁面載入後第一時間覆寫
  `window.confirm`；已經卡住就只能重啟 preview 容器。

### 靜態字串測試的共通陷阱：比中自己寫的註解

「不可以用 X」這種禁令，往往同時寫在註解裡（`// 這裡不能用原生 confirm()`）。
直接對原始碼比對 `confirm(` 會比中說明文字，禁令測試因此永遠紅、卻完全沒檢查到
真正的程式碼。`test_manuscript_section_switch.py` 已加 `_code_only()`（剝除整行註解
與 `/* */` 區塊）供這類斷言使用。**上一輪與本輪各踩過一次。**

另外：用 PowerShell 的 `[IO.File]::ReadAllText` 改檔做牙齒測試時，
**`Set-Location` 對 .NET API 無效**（它看的是行程工作目錄），會安靜地寫不進去，
於是「注入壞碼後測試仍綠」被誤讀成測試沒有效力。一律給絕對路徑。

### P1 2B 切章：三個根因

上一輪的 token 協調器本身是對的（A→B→C 遲到回應確實被丟棄）。本輪補了三個：

- **NOTE-004** 章節下拉直接呼叫 `requestSectionSwitch()` 而繞過 `rememberChatSection`，
  localStorage 永遠停在上次 2A 同步的章節。實測：切到 results → 重整 → 回到 abstract。
  修法是把持久化收斂進「唯一入口」`requestSectionSwitch()`。
- **NOTE-005** socket 連上後 500ms 的還原計時器會覆蓋使用者已做的切章與未存輸入；
  加 `_sectionReqSeq > 0` 守衛。
- **NOTE-006** 字數統計整個畫布的 innerText，把卡片的章節 badge 也算進去，
  空白章顯示「1 字」。改為只數 `.card-content`（與存檔口徑一致）。

實測（PAPER1-p，隔離副本）：Introduction(V0.5) → Reference 得到 `<p><br></p>` +
`contenteditable=true` + 「尚無版本」；存檔只建立 `reference/V0.1.json`，
introduction 的 5 個版本檔 md5 全未變。

### P2 2C：以 `data-section` 為鍵的原位 upsert（NOTE-007）

根因是 `cardActionPushToFusion` 的 `innerHTML +=`（純 append）。
身分能存活是因為 2C 存檔存的就是 `fusionCanvas.innerHTML`、載入時原樣回填，
所以 `data-section` 會隨 G.Ver 往返。
**既有存檔沒有任何章節身分**（實測 PAPER1-p 的 V2 是 0 個 fusion-block 的裸段落），
因此舊內容一律原地保留、不猜歸屬。

實測：亂序推 introduction→method→abstract 得到 canonical 順序 abstract→introduction→method；
改內容重推原位取代仍 3 塊；存成 G.Ver V3（`from_ver=V2`，V1/V2 未被覆寫）；
重整後載回身分完整；再推第三次仍不重複；6 個舊裸段落全程未動。

### 草稿復原改為非阻塞提示條（NOTE-011，擁有者當場指示）

擁有者在驗收過程中親眼看到那個對話框反覆跳出，指示處理掉。
`_offerDraftRestore()` 不再用 `confirm()`，改走與 `conflict-bar` / `peer-update-bar`
同一套的畫布內提示條 `_showDraftRestoreBar()`：兩顆按鈕（復原草稿／保留目前內容）、
綁 `data-section`、不自動消失、文字走 `_esc()`。
語意完全不變：復原＝載入草稿；忽略＝保留目前內容，草稿在下次存檔時被覆蓋。

實測（不裝 `window.confirm` stub，讓凍結本身當反證）：切走再切回有草稿的章節，
`preview_eval` 順利回傳（同一情境先前必定 30 秒逾時），提示條出現在卡片內、
畫布顯示的是正式版 V0.5 而非草稿；按「復原草稿」畫布換成草稿內容且提示條消失；
重整後按「保留目前內容」內容維持 V0.5 不變。
守衛測試 `TestDraftRestoreIsNonBlocking` 已做牙齒測試（注入真的 `confirm()` 會紅）。

### P4 留言版本化（NOTE-008 / 009 / 010）

`ChapterComment` 原本只有 `pid + section_key`，沒有任何版本欄位 —— 留言必然漂移。
新增 `scope` / `s_ver` / `g_ver`，`scope='paper'` 的 2C 全文留言用保留鍵
`section_key='__paper__'` 填 NOT NULL 欄位，真正判別依據是 `scope`。

- migration 走 `fix_db_schema._upgrade_chapter_comments_table()`，**純 ADD COLUMN**。
  舊留言的 `s_ver`/`g_ver` 保持 NULL 不回填（回填等於偽造「這句話在說現在這版」），
  由 API 標成 `legacy_unversioned`，前端顯示「未標版本」。
- drill 結果：列數 2→2、既有內容完好、`integrity_check=ok`、3 個索引建立。
- 2C 全文留言的 ACL 與「能否讀整篇」同一道門檻，section-scoped 角色 403。

**注意：`fix_db_schema._resolve_db_path()` 無視 `SQLALCHEMY_DATABASE_URI` 覆寫，
永遠解析到 `<repo>/data/roothinks.db`。** 所以只要跑一次測試（會 create_app），
真實 dev DB 就會被套用 migration。本輪確認過：只加了 3 個欄位，
**所有資料表列數完全相同、零筆資料變動**，備份在
`scratchpad/realdb-backup/`。純增量也代表程式碼回退後舊版仍可運作。

---

## 3.9 COC 斷鏈全圖（2026-08-10，逐條用程式碼驗證過）

擁有者定義的 COC：PAQ、Literature、Study、Mentor/Reviewer、Manuscript 2A/2B/2C 中
**人類的思考、決策、理由與互動結果，必須逐環保存、被下一環讀取，最後真的進入
Drafter prompt**。「檔案存在但下一環沒讀」仍算斷鏈。

### 結構性根因（不是 N 個獨立的洞）

送進 Drafter job 的 payload 只有固定 9 個 key（`manuscript_routes.py` 的
`chat_message` handler，約 L1160）：
`user_msg / context_text / target_lang / attachment / import_type / pid / title / section / s_ver`。
**除了這 9 格，人類脈絡在架構上沒有通道。**
（注意：payload 缺 key 本身不足以證明「沒通道」，伺服器也可能自行載入；
擁有者已交叉搜尋 server loader，確認 Mentor/Reviewer、其他章節、2C 都沒有載入端，
結論才成立。日後推翻任何一條，要用同樣的雙向查法。）

### 十三條斷點（檔案:行為本輪實查）

| # | 斷點 | 證據 |
|---|---|---|
| 1 | 2A 歷史 write-only | `save_chat_history()` 有寫（routes 約 L1102），payload 無 history 欄位 |
| 2 | `s_ver` 寫死 0.1 | `manuscript_soed.js` 兩處送 `s_ver:'0.1'`；routes 約 L1066 預設也是 `'0.1'` |
| 3 | 章節歷史幾乎讀不到 | `manuscript_ruling.py` 約 L81 `if original_len < 50` —— 畫布有字就完全不讀 |
| 4 | 正文截斷＋UI 污染 | `editorCanvas.innerText.substring(0,3000)`；innerText 含卡片 badge（見 NOTE-006） |
| 5 | PAQ 只剩兩個 dict | `manuscript_ruling.py` 約 L260 只讀 `axis_labels`+`axis_tags` |
| 6 | Literature 只剩提示詞 | 同檔約 L274 `keywords[:8]`+`apa_citations[:3]` |
| 7 | Study 只剩 note | 同檔約 L313 `notes[:2400]`，無 matrix/版本史/對話 |
| 8 | 上游總量硬截 6000 | 同檔約 L318 `merged[:6000]` —— 所有人類脈絡共用這個天花板 |
| 9 | 排除文獻擋不住檢索 | `retrieve_paragraph_context` 只傳 project_id/query/top_k；`search_evidence`（evidence_index_service 約 L345）僅依 project_id + source_types 過濾 |
| 10 | Mentor/Reviewer 無通道 | payload 無欄位，且無 server loader |
| 11 | **ContextChain 覆寫前次 active** | `update_from_task3_search()` 約 L451 起把 L1/L2/L3 整包重新指定並寫 `stale: False`；舊內容只剩 provenance event |
| 12 | **stale 仍被下游使用** | 查詢約 L687 同時回傳 `l2_stale/kg_stale` **與** `vector_results/compact_context`；`_collect_context_chain_candidates()` 讀 L2 沒檢查 stale；Study 直接取 `compact_context` |
| 13 | **Metadata Index write-only** | `index_service.py` 只有 `register_index` 的 3 個 caller（paq_core L138/L156、project_service L268）；全 app 無 `get_latest_context` / `search_indices_by_module` 讀取端 |

### 順帶發現的錯誤維護資訊

`app/services/index_service.py` 的 header 宣稱「協調 Evidence Index 的建置、增量更新、
查詢與狀態回報」，但它 import 的是 `MetadataIndex`、維護的是 `metadata_index` 表，
與 Evidence Index 是兩回事。**header 本身就是錯的維護資訊**，重寫時要依實際
producer / consumer / 持久化位置與不變量寫，不得保留模板文字。

### 第一刀的範圍（擁有者拍板）

server-side COC assembly + 2A history 回接真正的 LLM request。
**前端只送 `pid / section / user_msg / job 資訊`**，版本真相與正文由伺服器取（NOTE-012）。
COC bundle 不是把全部紀錄硬倒進 prompt，優先序：
1. 本輪人類指令與目前章節
2. 本章歷史中的人類決策／理由
3. 其他章節與 2C 的最新有效版本
4. PAQ / Study / Reviewer 的 active 人類決策
5. 已人工納入的文獻證據

文獻三分 included / excluded / unreviewed-unknown（NOTE-013）；
stale 對寫作路徑 fail-closed（NOTE-014）。
**先不要同時重構全部 COC schema。**

第一刀只需四個強證據：
1. 前一輪 2A 人類哨兵出現在下一輪真正的 provider request
2. 真實 S.Ver 取代固定 0.1
3. excluded / unknown 哨兵不存在於正式寫作 request
4. 另一個專案與 stale ContextChain 的哨兵不存在

### 第一刀目前進度（2026-08-10 晚）

**已完成並有測試**（`test/unit/test_coc_bundle.py`，含牙齒測試）：

- 新增 `app/core_pro/manuscript/coc_bundle.py`：依優先序組裝、分層 token 預算、
  被裁掉的部分寫進 `notes` 不靜默遺失。
- `chat_message` handler 改用 `resolve_section_version(pid, section)` 取代
  `data.get('s_ver','0.1')`；前端若仍送 s_ver 只記 log 供比對，不採信。
- **2A 歷史真的被讀回來了**（`load_chat_history`），這是 write-only 缺口的修復。
  組裝時機刻意排在 `save_chat_history` **之前**，否則本輪訊息會同時出現在
  `user_msg` 與歷史裡，白白吃掉預算。
- 可讀章節在 socket handler（仍有 request context）內算好再傳進組裝層 ——
  生成跑在 `_CHAT_EXECUTOR` 的 worker thread，那裡沒有 `current_user`，
  在下游判權限等於在沒有身分的情況下決定餵哪些章節給 LLM。
- 前端兩處改送 `_draftTextForPrompt()`：只取 `.card-content`、不截斷、不宣告版本。
  舊的 `editorCanvas.innerText.substring(0,3000)` 會把章節 badge 當正文送出去。

**NOTE-013 文獻納入/排除**（`test/unit/test_evidence_inclusion.py`，5 passed）：

沿用**既有的** `LiteratureLibrary`（`screening_status` 早已定義
candidate/included/excluded），不另建納入狀態 —— 平行來源會立刻產生
「兩邊不一致時聽誰的」。`search_evidence` 新增 `inclusion_scope`：
`any`（探索，excluded 仍硬擋）與 `writing`（正式寫作，只收 included）。
`retrieve_paragraph_context` 走 writing。

**最容易寫錯的一條**：`EvidenceSegment` 同時裝 `paper_segment` 與
`study_note`/`paq_note`/`manuscript_note`。篩選只能套在 `paper_segment` —— 一起濾掉
等於把作者自己的筆記刪光，正好摧毀 COC 要保護的東西。已有測試守（拿掉
source_type 守衛會紅）。

**現況警告**：真實 data 下**一個 `library.json` 都不存在**，screening 從未被使用。
依 NOTE-013 的 fail-closed，這代表**目前所有論文都是 unknown，不會進寫作 request**。
這是擁有者在 NOTE-013 遷移段落已接受的結果（由作者逐步標記），但實務上等於
Drafter 暫時失去論文依據，上線前要先決定是否先批次標 included。

**NOTE-014 stale fail-closed**（`test/unit/test_context_chain_stale.py`，5 passed）：

`_collect_context_chain_candidates()` 改回傳 `(candidates, skipped_layers)`，
stale 的 L2/L3 不進寫作路徑並記 log。
**方向很重要：L1 刻意不受影響** —— 人工 `manual_override` 會把 L2/L3/KG 標 stale
而 L1 保持新鮮，L1 裝的正是人類剛改好的內容，擋掉它等於把作者的修正也丟掉。

**四個強證據已在真正的 provider request 上取得**
（`test/unit/test_coc_provider_request.py`，3 passed）：
攔截 `task_8drafter.dispatch_task` 拿到實際送出的 prompt 字串後斷言。

### 這一輪最重要的教訓：否定斷言差點空過

第一版 e2e 測試「excluded/unknown 不在 request 裡」是**綠的，但理由是錯的**。
A/B 反證時把 scope 從 `writing` 改回 `any`，測試竟然**還是綠**——代表檢索根本沒回
任何東西，否定斷言從頭到尾沒被檢驗過。

真因：檢索是 lexical 的，我的指令「請依前面的討論續寫 Introduction」與證據文字
「triage …」沒有共同詞元，`score <= 0` 全被丟棄，any scope 也是 0 筆。

修法有兩層，缺一不可：
1. 指令與哨兵證據共用詞元（`triage accuracy`），檢索才真的跑。
2. 新增 `TestRetrievalIsActuallyLive` 當**自我驗證護欄**：先證明同一批資料在
   `any` scope 撈得到、在 `writing` scope 撈不到，下面的否定斷言才有意義。

改完後 A/B 才真的成立：scope 改回 `any` → 驗收測試變紅；還原 → 綠。
**通則：任何「X 不在場」的斷言，都要同時有一個「X 在別的條件下會在場」的對照，
否則無法分辨「擋掉了」與「根本沒產生」。**

---

## 4. 卡在哪 / 還沒做

1. **瀏覽器實機驗證：§3.5 三項已實測通過（見 §6），其餘仍未點過。**
   仍待確認：Drafter 草稿生成走真實 socket、LAVA 綁定選單、Study 分割與破圖。
   Chrome extension 一直連不上（`list_connected_browsers` 恆為空、computer-use
   對瀏覽器只有唯讀權限），**但那不再是藉口** —— §6 的免登入預覽容器不需要它。
2. **`DGVRYV-p` 的研究筆記是空字串。** 擁有者說筆記應是主要來源，但欄位沒東西。
   目前只能用單元測試證明筆記路徑會動，無法用真實資料證明。
3. **文獻流程有失敗沒被發現**：`Code-Switching_Red-Teaming` 缺 `full_text.json`；
   `SEBASR_IJMIR` 的 `05_interprets/summary.json` 帶 `LLM_PROVIDER_ERROR` 與
   `mode: fallback_synthetic`。要不要回頭補跑待決定。
4. **手稿模組兩個既有問題已修（§3.5），但一次都沒有用瀏覽器點過。**
   全部驗證都是靜態檢查與容器內單元測試。特別注意：`insertEditorCard` 的修法
   是「補上 focus／range 前置動作」，而**沒有任何自動化測試能證明 execCommand
   真的插入了** —— 這個 repo 沒有 DOM 測試環境，靜態檢查只能證明那行程式碼還在。
   真正的判準只有一個：開啟舊版之後畫布裡看得到內容。
5. **`Project.name` 宣告 300 但線上 SQLite 表的 DDL 仍是 `VARCHAR(100)`。**
   `fix_db_schema.upgrade_database()` 只在缺數值主鍵的 legacy 情況才重建整張表。
   SQLite 不強制長度所以無影響，但**遷移到 PostgreSQL 時 schema 必須從
   `models.py` 生，不能從 sqlite dump 出 DDL**（會把舊長度帶過去然後截斷）。
6. **本機 repo 有 4 個非本輪造成的刪除未提交**：`TREE_SIMPLIFIED.txt`、
   `debugging/FLOWB_MONITORING_RECOVERY_CHECKLIST.md`、`patch_matrix.py`、
   `patch_schema.py`。開工前就是刪除狀態，刻意沒動。
   另有 5 個未追蹤的 `probe_*.py` / `tmp_ops_probe.py`，同樣不得動。

### 2026-08-10 這一輪還沒做完的（依擁有者需求編號）

7. **P3「隱藏 2A 後仍保留空白寬度」重現不出來。** 在 610px 與 1440px 兩種視窗下，
   涵蓋真實按鈕點擊、JS API、先把分隔條拖成像素寬度再隱藏、全螢幕進出後再隱藏、
   連續隱藏兩欄、以及各種恢復往返，共 9 個情境，**容器寬度與子元素寬度總和的差
   全部是 0**（隱藏 2A 時 `dragHandle2` 與 `panel2A` 都是 `display:none`、寬度 0，
   2B/2C 各拿 50%）。唯一量到空白的是「2A 全螢幕中」，但那時 2A 是 `position:fixed`
   覆蓋整個畫面，底下的空白看不到，屬預期。
   **刻意沒有改動版面程式碼** —— 對重現不出來的回報去改正常運作的 CSS/JS，風險大於收益。
   需要擁有者提供實際重現步驟（哪個瀏覽器、視窗寬度、之前做過什麼操作）。
   注意先前兩次誤判來源：過場動畫中量測、以及 `preview_screenshot` 的假空白（見 §3.8）。

   **一條值得先查的線索**（做 NOTE-011 提示條時順手量到）：2B 畫布掛著 Bootstrap
   `p-5`（左右各 48px 內距）。在 610px 視窗下 `panel2B=193px`、畫布 191px，
   扣掉 96px 內距只剩約 95px，實測 `.editor-card` 被壓到 **78px 寬、2003px 高** ——
   正文本身就被擠成一條。畫面上會看到「內容只佔中間一小條、兩側大片空白」。
   這跟「隱藏 2A 後的空白」是不同機制（欄寬分配是對的，空白來自內距），
   但**使用者看到的現象可能就是這個**。若擁有者的重現環境是窄視窗，先查 `p-5`。
8. **P5 2B 編輯器格式功能只驗證了一部分。** 上一輪加的 B/I/U、H1/H2、項目符號、
   1/2/3 層編號清單與 CSS 已在（`.manus-editor-canvas ol ol` 等三層樣式），
   本輪確認了畫布真的可編輯（`contenteditable=true`）、打字後存檔的 JSON 內容正確、
   重整後讀得回、切章不跨章覆寫。**但逐個格式按鈕的實際 DOM 產出沒有一一點過。**
9. **P4 留言版本化的 UI 只接了 2B 側欄。** 後端與 API 已支援 `scope=paper`＋`g_ver`
   （測試涵蓋），但 2C 全文留言的介面還沒做。多角色 ACL 也只有單元測試，
   **沒有用瀏覽器以 Reviewer / Mentor / PI / Co-PI 各自登入點過** ——
   那需要 AUTH_MODE=session 的 10099 容器與真實帳號。
10. **P6 client_id/user_id Base62 架構完全沒動。** 依擁有者指示只做規格與 migration
    drill、不動舊資料，本輪沒有進行到這一步。現行 2B 正文仍在
    `<DATA_ROOT>/<formal_pid>/manuscript/block/<section>/V0.x.json`。
11. **P8 正式站回歸與部署完全沒做。** 正式站 `http://34.80.240.29` 這一輪一次都沒碰。
    Reviewer / Mentor / PI / Co-PI / 2A / 2B / 2C / PDF / URL 全部仍待回歸，
    Mentor 必須獨立測不可用 Reviewer 代替。部署前務必記得 §3.8 的模板快取問題：
    **只換檔不重啟，使用者拿到的還是舊 JS。**

---

## 5. 怎麼驗證

```bash
# VM 乾淨
git status --porcelain                                   # 期望空

# Socket 傳輸層
curl -H 'Origin: http://34.80.240.29' 'http://127.0.0.1/socket.io/?EIO=4&transport=polling'

# 語料組得出來
sudo docker exec -e PYTHONPATH=/app roothinks_progress_paq_v8_10005 python - <<'PY'
from app import create_app
from app.core_pro.manuscript import source_context as sc
with create_app().app_context():
    r = sc.build_drafter_corpus('DGVRYV-p')
    print({k: v for k, v in r.items() if k != 'corpus'})
PY

# 全測試
docker run --rm --user 0:0 ... python -m pytest test/unit -q   # 期望 592 passed
```

**瀏覽器實機**（作法見 §6）。2026-08-09 在 `PAPER1-p` 上實測結果：

| 動作 | 期望 | 實測 | 對應 |
|---|---|---|---|
| 2B 按「開啟舊版」選 V0.1 | 編輯畫布出現內容 | ✅ 845 字元／1 張卡；拿掉修正則 0／0 | §3.5.1 |
| 改 Title → blur → 重載 | 標題留著、`name`=`research_title` | ✅ 中文與括號完整保留，兩欄一致 | §3.5.2 |
| 徽章（已連線） | 綠色 Online | ✅（第一版修法在此為灰色，已再修） | §3.5.3 |
| 徽章（`socket.disconnect()`） | 紅色 Offline＋原因 | ✅ tooltip 帶 `io client disconnect` | §3.5.3 |
| 按「草稿生成」 | 內文含論文專屬數據（如 7.56%） | ❌ 尚未測（本機複本沒有那批語料） | §3 |

---

## 6. 怎麼在沒有 Chrome extension 的情況下做實機驗證

連續三輪卡在「Chrome extension 連不上」，2026-08-09 找到不需要它的路：
**另起一顆關掉認證的本機容器，用 preview 工具內建的瀏覽器驅動它。**

```powershell
# 1) 複製一份最小 data root（別讓兩個行程同時寫同一個 SQLite；
#    也保證不會動到使用者的真實資料）
#    需要的只有：roothinks.db、要測的專案目錄、sys/、_locks/、_logs/

# 2) 起容器。三個關鍵旗標，缺一不可：
docker run -d --name roothinks_preview_10099 --user 0:0 `
  -p 127.0.0.1:10099:10000 `                # gunicorn 聽 10000，不是 10005
  -v "<repo>:/app" -v "<複本>:/app/data" `
  -e AUTH_MODE=none `                       # 免登入，不必碰帳密
  -e API_AUTH_ENABLED=0 -e FLASK_ENV=development `
  -e RATELIMIT_STORAGE_URI=memory:// -e SOCKETIO_MESSAGE_QUEUE= `
  -e LOCK_ROOT=/tmp/rlocks -e GUNICORN_WORKERS=1 `
  -e "CORS_ALLOWED_ORIGINS=http://127.0.0.1:10099,http://localhost:10099" `
  roothinks10005-roothinks-paq:latest
```

踩過的三個坑：
- **少了 `--user 0:0`** → `PermissionError: '/app/app/llm_service/.../task_6_debug.log'`，
  worker failed to boot。與跑測試時同一個原因。
- **gunicorn 實際聽 10000**，不是 compose 對外寫的 10005。映射錯的話容器 healthy
  但 curl 回 `http=000`（curl exit 52，empty reply）。
- **preview 工具不接管已佔用的 port**：要先 `docker stop`，再讓
  `.claude/launch.json` 的設定用 `docker start -a <name>` 把它起起來。

驗證時 `preview_eval` 直接讀 DOM 最有效（`preview_snapshot` 對這個畫面太雜）：

```js
// 舊版載入：有修正 → editorLen 845 / cardCount 1；拿掉修正 → 0 / 0
document.getElementById('editorCanvas').querySelectorAll('.editor-card').length
// 徽章：務必同時檢查 socket.connected 與徽章文字，兩者不一致就是有 bug
({c: wsApp.socket.connected, t: document.getElementById('socketStatusBadge').textContent})
```

**A/B 反證是這輪唯一真正有說服力的證據**：把修正拿掉、重載、重跑同一組點擊，
確認畫布是空的，再還原。靜態測試綠燈證明不了 execCommand 真的插入了。

---

**判準提醒**：測試綠燈不足以證明目標達成。本輪 567 個測試全綠的同時，
`manuscript_ruling.py` 有一個 `UnboundLocalError`（import 在 `try` 內、
呼叫在 `try` 外）會讓草稿生成在真實路徑上直接崩潰（`beff6a8` 修）——
因為測試沒有覆蓋真實呼叫路徑。
**判斷 Drafter 好不好，看產出文字裡有沒有論文專屬的具體數據，不要看測試數字。**

---

## 3.10 COC 第一刀的 code review 修復（2026-08-11）

上一輪宣告「第一刀完成」被 code review 判 **Needs work**。本節記錄實際修了什麼、
以及複驗過程中挖出來的東西。**先看更正，再看修復。**

### 先更正四處先前交接的說法（含本輪自己犯的一處）

1. **「791 passed」的指令不是本 repo 各檔頭寫的 `pytest test/unit tests`**。
   那條指令實際是 773。791 = `pytest test tests`（`test/unit` 699 + `integration_smoke` 26
   + `integration_e2e` 1 + `tests` 65）。**各檔頭的「驗證」欄位寫的指令跑不出交接數字**，
   對帳前先確認你跑的是哪一條。
2. **「stale 已證明不到 provider request」是假的**。舊測試只檢查
   `_collect_context_chain_candidates()` 的回傳值，從未呼叫 `dispatch_task`。已重寫成
   在攔截到的 provider request 字串上斷言，並補了「fresh 層在場」的對照組。
3. **「真實 data 完全沒變」不成立**，但原因不是測試隔離做壞 —— 見下面的 fix_db_schema。
4. **本輪自己犯的**：我一度依 `probe_nl.py` 的 PID 常數推論
   `data/manuscript_context_audit/` 裡有真實專案 `DGVRYV-p` 的 audit 檔。實查後
   目錄裡**只有測試 pid**（COCACL-p、COCE2E-p），推論是錯的。已清掉、無真實資料受影響。

### P0：prompt 是一條讀取管道，而它沒有經過 ACL

`handle_chat` 只驗 `_ensure_socket_project_access`（專案成員），而 `section`
完全來自請求體。同檔的 `cmd_save_block` 早就有章節層判定，這條新路徑沒有。
後果：限定編輯（coauthor）偽造 socket payload 指定未指派章節，就能拿到該章的
生成結果，並把聊天紀錄寫進他無權編輯的章節。

`build_coc_bundle` 這邊更廣：目前章節正文、2A 歷史、2C 全篇都是**無條件載入**，
`readable_sections` 只限制得到「其他 2B 章節」那一段迴圈。也就是說即使只指定
自己的章節，仍能經由 prompt 讀完整篇 2C。

修法與**為什麼這樣修**：

- handler 用**寫入**而非讀取當門檻，與 `cmd_save_block` 同一條線 —— 這個事件會
  `save_chat_history()` 到該章、產出的草稿也是要寫進該章的。ACL 判定本身例外時
  一律拒絕（fail-open 的代價是把別人的章節送進 LLM）。
- **刻意不在 handler 對 `section` 做 `_safe_component` 清洗**：`ManuscriptIO._block_dir`
  對非 ASCII 章節另有對應邏輯，在這層先清洗會讓中文章節指到錯的目錄。清洗留在
  各儲存層，這裡只負責授權。
- `build_coc_bundle` 的三個 ACL 參數（`readable_sections` / `can_read_current_section`
  / `include_paper`）改成**必填關鍵字、沒有預設值**。整個缺陷形狀就是「新接的呼叫端
  忘了帶 ACL」，沒有預設值 = 忘了帶當場 TypeError，而不是靜默外洩。
- **2A 歷史與正文共用同一道判定**：讀不到本章的人不該讀到本章對話，那是同等的管道。
- `can_read_current_section` **不從 `readable_sections` 推導**：`'general'` 這種預設值、
  以及還沒進 sections 資料表的新章節都不在清單裡，用「不在清單=不可讀」會把合法
  請求的正文靜默丟掉，而正文丟失沒有任何外顯症狀。

### P1：四項（全域預算／audit provenance／留言版本／stale 證據）

- **全域 packer**（`_pack_prompt_context`）：`_PROMPT_MAX_TOKENS=20000` 由
  未存草稿 → COC → retrieval 共用，retrieval 不再吃寫死的 18000。
  舊狀態 COC 12K + retrieval 18K + 前端 6K + upstream 1.5K ≈ 37.5K，必撞 25K TPM。
- **去重**：前端未存草稿優先於伺服器已存版本，只留一份。同時送兩版會讓 LLM
  看到衝突正文而無從判斷優先序。
- **留言版本**：`s_ver=NULL` 的留言原本會穿過 `if s_ver and row.s_ver and ...`。
  改為對齊 NOTE-010 的嚴格語意；被排除的**數量**進 notes → audit，不靜默。
  `s_ver` 為 None（章節從未存檔）時接受全部 —— 沒有版本可比對，NOTE-010 的
  嚴格規則本來就只在「有給版本」時適用。
- **coc_items 不再是 dead payload**：payload → `_process_chat_job` →
  `process_request` → `validate_and_prepare` → `write_context_audit(coc_sources=...)`。

### 複驗代工成果時抓到的六個問題（**這一段最值得先讀**）

代工回報「全綠、任務完成」，但：

1. **audit 的觸發條件是 `if injected_block:`** —— 而全站沒有任何 `library.json`，
   依 NOTE-013 fail-closed，寫作路徑的檢索結果**恆為空**。也就是這條 audit
   一次都不會被寫，整個 provenance 修復在正式站是空轉的。
   **這是「程式碼存在但條件永不成立」，與原本的「檔案存在但下一環沒讀」同一類。**
2. COC 沒併進 `source_manifest`，回傳的 `context_sources` 看起來像「這份草稿沒用到
   作者任何脈絡」。（併入時確認過**不會**放寬 `has_grounding`：它的允收清單裡
   沒有任何 COC 的 source_type。要不要把作者已存正文算成 grounding 是產品決策。）
3. **packer 用正規表示式比對顯示字串 `[目前章節 … · S.Ver …]` 來定位去重目標**。
   改個標籤文字 → 去重靜默失效、章節送兩份；而測試是手工組同樣格式的字串，
   永遠是綠的。已改成 `build_coc_bundle` 回傳結構化 `blocks`，packer 依 `tier` 判斷。
4. 去重後 `items` 沒同步移除 → audit 仍宣稱伺服器版本是來源。**provenance 說謊
   比沒有 provenance 更糟**，因此 `_strip_coc_section` 必須同時砍 blocks 與 items。
5. `write_context_audit` 沒收到 `data_root`，用相對路徑 `Path("data")` ——
   違反 `manuscript_ruling.py` 自己檔頭第 15 行宣告的不變量。
6. `context_audit.py` 有整段重複貼上的註解。

### 最大的坑：跑測試會對正式資料庫跑破壞性 migration

`fix_db_schema._resolve_db_path()` 的路徑是**相對該檔位置寫死**的
`repo/data/roothinks.db`，**完全不看 `SQLALCHEMY_DATABASE_URI`**；而
`app/__init__.py` 在 `create_app()` 裡無條件呼叫它。於是**每一個把 DB 指到 tmp 的
測試，在建立 app 時仍然對正式資料庫跑一次 migration** —— 而該檔自己的檔頭寫著
「安全邊界：破壞性操作（DROP TABLE／重建表）」。跑一次完整套件就是 700 多次。

這才是「真實 data 未變動」在這個 repo 結構下不可能成立的原因，跟 audit sidecar
或 probe 腳本都無關。已加 `_should_run_schema_fix()`：只有在「migration 的目標 DB」
就是「這個 app 要用的 DB」時才跑；非 sqlite 或解析不到目標時維持原行為
（那條「開不起來也不要帶著壞 schema 跑」的路徑刻意保持會爆）。

**未修**：`probe_*.py` / `tmp_ops_probe.py` 這五個未追蹤的一次性診斷腳本都是
`create_app()` 不帶測試設定，直接對正式設定跑。其中 `probe_nl.py` 的 docstring
寫「只讀不寫」，但它走的 `ManuscriptRuling.validate_and_prepare` 內部會寫 audit
sidecar。**要再跑這類腳本前先確認它碰得到什麼。**

### 怎麼驗證

```
cd <repo>
py -3.10 -m pytest test tests -q -p no:cacheprovider      # 對得上交接數字的那一條
py -3.10 -m pytest test/unit/test_coc_acl.py -q           # ACL（含 provider request 級證據）
py -3.10 -m pytest test/unit/test_prompt_budget.py -q     # packer / audit provenance
py -3.10 -m pytest test/unit/test_schema_fix_scope.py -q  # 跑測試不得碰正式 DB
```

**A/B 反證的規則（本輪唯一真正有說服力的證據形式）**：把修復關掉 → 對應測試必須
變紅，而且要確認**是預期的那一條斷言**紅的（`--tb=line` 看行號）；還原 → 綠。
反證完必須確認沒有殘留標記 —— **但不要用 `git grep`**：新增的檔案在 `git add` 之前
是未追蹤的，`git grep` 結構上看不到它們。本輪就因此回報過一次不精確的「零殘留」。
用 `rg ABTEST` 或編輯器的全域搜尋。

**「X 不在場」的斷言，一定要配一個同資料、同路徑、只改一個變數的「X 在場」對照組。**
X 根本沒被產生時，「X 不在場」恆為真，連 A/B 都抓不到。本輪最有說服力的一次反證：
把 `coc_bundle` 的 tier 名稱改掉（模擬顯示層改動）→ **11 passed, 1 failed** ——
手工組字串的 11 個測試全綠，只有接真實 `build_coc_bundle` 的那個護欄紅了。

### 還卡在哪（**上線前必須先解決**）

1. **screening 遷移未做**。全站 0 個 `library.json` → 所有論文 unknown →
   strict writing scope 之下 Drafter 沒有論文依據。review 的裁示是：**不要**批次
   標成 included（那等於偽造「作者已審核」），要先建成 candidate + 一次性
   PI/Co-PI 確認頁（記 actor／時間／批次決策 ID）。**完成前不要部署 strict scope。**
2. COC 的其他 producer（PAQ cube／Study matrix／Literature 多輪搜尋累積／
   Mentor-Reviewer 進 prompt）都還沒接。目前只打通「Manuscript 讀取 → provider request」。
3. `ContextChain` 的 `update_from_task3_search()` 仍整包覆寫 L1/L2/L3，缺 append/supersede。
4. stale 的重算生命週期（誰觸發、成功後如何解除、失敗如何通知）未定。
5. `coc_sources` 目前沒有 fingerprint（`None`）。要做「S.Ver 反查來源」還需要補。
6. 正式站完全沒碰：P8 部署、逐角色瀏覽器 ACL 走查、2C 全文留言 UI 都還在。

### 補充：這個 repo 有五套資料位置解析，其中三套不看設定

追「跑測試為什麼會在真實 `data/` 長出東西」時挖出來的。並存的有：

| 來源 | 規則 | 看 `SQLALCHEMY_DATABASE_URI` 嗎 |
|---|---|---|
| `manuscript_io._get_data_root()` | URI 所在目錄，無 context 時 fallback `cwd/data` | 是 |
| `evidence_index_service._resolve_data_root()` | 同上（刻意重做一份，避免層次倒置） | 是 |
| `ContextChainService.__init__` 預設 | **原始碼所在位置 /data**，寫死 | **否** |
| `fix_db_schema._resolve_db_path()` | **原始碼所在位置 /data/roothinks.db**，寫死 | **否** |
| `LLMModel.get_db_path()` | **原始碼所在位置 /data/sys/llm_match.db**，寫死，且讀取時就 makedirs | **否** |

正式站四者剛好相同（容器工作目錄就是 repo 根），所以一直沒有症狀。
但只要工作目錄或部署方式一變，ContextChain 與 schema migration 就會悄悄指到別的地方。

**本輪修的**（都是「同一個函式裡的不變量要一致」，正式站行為不變）：

- `manuscript_ruling` 呼叫 `write_context_audit` 與 `retrieve_paragraph_context` 時
  都補上 `data_root=_get_data_root()` —— 該檔第 15 行本來就宣告了這條不變量。
- `LiteratureLibrary._library_path()` 原本**無條件 makedirs**，於是
  NOTE-013 的 `_screening_sets()` 每次檢索（包含沒有文獻庫的專案）都會建目錄。
  改成唯讀路徑不建目錄、檔案不存在直接回空（連 FileLock 都不取，取鎖也會建檔）。
  **讀取不該有副作用**，這才是源頭；逐個改測試 fixture 是打地鼠。

**未修（要決定）**：`ContextChainService` 的預設 root 仍是原始碼相對路徑。
改成設定推導的風險是：若既有 chain 落在舊位置而新規則指到別處，資料會「消失」。
要動之前先確認 VM 上 `~/roothinks-app` 的實際工作目錄與 `data/` 位置是否一致。

**驗證方法（比看 mtime 強）**：把目標目錄**刪掉**再跑，看它會不會被建回來。
`makedirs(exist_ok=True)` 不會更新既有目錄的 mtime，所以「mtime 沒動」證明不了
「沒有寫入」—— 本輪就先用 mtime 得到過一次錯誤結論。
腳本形式：跑前後對 `data/` 做遞迴快照（排除 `_logs` / `_locks`）比對新增/消失/被改。

### 補充二：smoke 測試仍然跑在正式資料庫上（未修，要裁示）

**更正（本節前一版寫得過度概括）**：不是「26 個測試都連三顆 DB」。
可由原始碼直接確認的是 **2 個 fixture、共 3 個測試**用了預設的 main/manuscript DB
（`create_app({"TESTING": True})`，完全沒有覆寫 `SQLALCHEMY_DATABASE_URI` 與
`SQLALCHEMY_BINDS`）；而 `create_app()` 這條路徑本身還會碰到寫死的
`data/sys/llm_match.db`。`sqlite3.connect` spy 證明的是「**這一輪跑下來**連上了
`data/roothinks.db`、`data/manu_core.db`、`data/sys/llm_match.db`」，
不足以推論每個測試都如此 —— 隔離仍要補，但要按實際呼叫逐條處理。
並且會跑 schema migration（NOTE-016 的判斷會正確地放行 —— 它們「設定的」
確實就是正式 DB）。

**沒有直接改的理由**：把它們指向空的 tmp DB 之後，路由 smoke 很可能照樣回 200，
於是測試通過但覆蓋範圍縮水 —— 屬於「綠得沒有意義」那一類，換誰通過都看不出差別。
要改就要同時決定它們到底該驗什麼（有資料的真實情境？還是純路由可達性？）。

**在那之前的作業規則**：跑 `pytest test tests` 之前先確認
`data/roothinks.db` 有備份，或只跑 `test/unit tests`（773，不含 smoke）。


## 3.11 全域預算重做（2026-08-11 第二輪 review 後）

第二輪 review 判定：P0 可保留，**P1 的「全域 20K」實測反證失敗**，需局部重做。
三個缺陷都復現了，修法與理由：

### 1. `0` 被當成「沒給」（三層都踩同一個陷阱）

`manuscript_ruling` 用 `retrieval_budget > 0` 判斷是否採用呼叫端的預算，
於是**預算剛好用完（0）時反而退回 18000** —— reviewer 的 probe：
`REQUESTED_RETRIEVAL_BUDGET=0 / ACTUAL_MAX_TOKENS=18000`。正是這機制要防的事。

同樣的形狀在下游還有第二層：`context_inject._apply_limits` 的
`max_tokens or 1200`，明確傳入的 0 會被放大成 1200。

修法：`>= 0` 才是有效值判斷、`None` 才代表沒給；`_apply_limits` 改成
`1200 if max_tokens is None else max(0, ...)`。**而且預算為 0 時上游直接不呼叫檢索**
—— 依賴下游每一層都正確處理 0 太脆弱，這是三層都證實過的。

### 2. upstream context 沒被計入

`_load_upstream_context()`（研究筆記／PAQ／背景）是在 `validate_and_prepare` 內部
才併進 `final_context` 的，packer 算得再準也管不到它 —— 所以舊版根本不能叫「全域預算」。

修法：packer 回傳的改名為 `remaining_budget`（剩餘全域額度，不是「檢索的預算」），
由 `validate_and_prepare` 做最後收斂：upstream 先扣（它是作者自己的筆記，
優先序高於檢索到的論文段落），不夠就截斷並記錄，剩下的才是檢索額度。
**截斷必須排在 source_manifest 的 marker 掃描之前**，否則 manifest 會宣稱
一份已經被裁掉、沒有進 prompt 的來源。

### 3. 24,000 字元截斷是隱形的

`handle_chat` 先把 2B 內容截到 `MANUSCRIPT_CHAT_MAX_CONTEXT_CHARS`，packer 收到的
已經是截斷後的字串，無從得知。**而該處註解宣稱「packer 會補進 packer_notes」——
那是錯的**，實測 `PACKER_NOTES=[]`，作者尾端剛寫的內容靜默消失。
修法：截斷當下就記 note，併進 `coc_notes` 一路進 audit。

### 這一輪的 A/B（四條，全部落在預期斷言）

| 關掉的修復 | 紅在哪 |
|---|---|
| `>= 0` 改回 `> 0` | `test_prompt_budget.py:416` 預算為 0 卻仍檢索了（**max_tokens=18000**，完全重現 reviewer 的 probe） |
| upstream 不扣預算 | `:470` upstream 已吃光預算，檢索卻還是跑了（max_tokens=500） |
| `_apply_limits` 改回 `or 1200` | `:444` 明確的 0 沒有被遵守 |
| 字元截斷不進 notes | `:356` notes 只剩 packer 自己那筆 `24000→13372`，字元層那次不見了 |

### 契約補正（review 指出）

- `_pack_prompt_context` 的**型別註記**仍寫三項（docstring 已改四項，annotation 沒改）。已修。
- NOTE-015／016 已在相依程式旁補上 `NOTE(NOTE-NNN)` 反向連結。


### 本輪最終數字與隔離證據（2026-08-11）

```
py -3.10 -m pytest test/unit tests -q      →  798 passed / 0 failed
```

刻意**不跑** `test/integration_smoke`（它會連正式 DB，見上）。同一輪掛了
`sqlite3.connect` 與 `os.makedirs` 的 spy 實測：

```
連上的正式 DB : ['data/sys/llm_match.db']
新建的目錄    : 無
```

**收尾驗證（最強的那一種）**：先把 A/B 過程留下的殘留全部刪掉，再跑一次
`pytest test/unit tests`（798 passed / 0 failed），前後對 `data/` 做遞迴快照：

```
新增: 無      消失: 無      被改: 無
```

也就是這一輪跑完，真實 `data/` 的每一個檔案與目錄（排除 `_logs`／`_locks`）
連 mtime 都沒有變。

也就是 `data/roothinks.db` 與 `data/manu_core.db` 在這一輪**完全沒有被開啟**，
真實 `data/` 沒有長出任何新目錄。唯一還碰得到的是
`LLMModel.get_db_path()` 那顆寫死的 `llm_match.db`（`test/integration_e2e`
的 fixture 已經知道要 monkeypatch 它，其他地方還沒）。

**跑測試的建議指令**：`pytest test/unit tests`（798）。
要跑 `pytest test tests`（含 smoke）之前先確認 `data/roothinks.db` 有備份。


### 追殘留時學到的：spy 的過濾條件會讓它漏掉東西

用 `os.makedirs` spy 抓「誰在真實 `data/` 建目錄」時，我加了
`if not os.path.exists(p)` 想避免噪音 —— 結果**目錄已經存在時就完全不回報**，
於是 spy 說「新建的目錄：無」，但目錄其實正在被寫。

**兩種方法的適用範圍**（都踩過才知道）：

| 方法 | 抓得到 | 抓不到 |
|---|---|---|
| 看 mtime | 檔案內容變更 | `makedirs(exist_ok=True)` 對既有目錄（mtime 不變） |
| spy + `not exists` 過濾 | 第一次建立 | 對既有目錄的重複寫入 |
| **刪掉再跑，比對前後快照** | 兩者都抓得到 | — |

結論：**驗「有沒有寫到不該寫的地方」，一律用「刪掉→跑→比對快照」**。
A/B 過程中程式處於「已關掉修復」的狀態時也會產生殘留，收工前要再清一次。


## 3.12 provider request 層的總量結算（2026-08-11 第三輪 review 後）

第三輪 review：三個 P1 修復成立，但「全域 25K 已收斂」仍不成立。兩個新缺陷：

### 1. 20K 只管 context，沒涵蓋整個 request

攔截真正送往 dispatcher 的字串（reviewer 的 probe，本輪已復現）：

```
修復前：router 2,054 + draft 26,744 = 28,798   → 超過 25K 上限 3,798
修復後：router 2,054 + draft 21,800 = 23,854   → OK
```

測試案例只是**合法**的 4,000 字中文指令 + 約 20K token 的 context，連附件都沒有。
也就是原本要防的 TPM 爆量仍可重現。

原因：handler 的 packer 只結算 context 那一段，但送出去的是整串 system 規則、
使用者指令、附件，**外加本輪稍早那一次 intent-router 呼叫**（同一個 TPM 視窗）。

修法（NOTE-017）：把 prompt 模板抽成模組常數 `_INSTRUCTION_TEMPLATE`。
抽出來的理由不是整潔，是**可量測** —— 要算「除了 context 以外的骨架佔多少 token」，
就必須能把 context 換成空字串再組一次，行內 f-string 做不到。
接著在組裝的那一刻結算：`ctx_budget = ceiling - router_reserve - overhead`，
超了就把 context（優先序最低的那一段）再收斂一次。

`router_reserve` 用 env 常數而非即時量測，是為了執行緒安全：`ai_drafter` 是模組層
單例，被 4 條 worker thread 共用，**不能把每輪的量測值掛在 instance 屬性上**。

### 2. `_truncate_to_budget()` 的回傳值必定超過傳入預算

提示字串是二分搜尋**之後**才附加的。實測（兩份實作都一樣）：

| 傳入預算 | 中文回傳 | 英文回傳 |
|---|---|---|
| 500 | 513 | 514 |
| 40 | 54 | 54 |
| 8 | 21 | 22 |

預算越小，超標比例越誇張。上游拿它當邊界用，每一段都固定溢出。

**修法有一個不直覺的地方**：第一版我改成「先扣掉提示字串的估算成本再搜尋」，
結果還是差 1（500 → 501）。因為 `_estimate_tokens` 用 `int()` 取整，
`int(a) + int(b)` 可能比 `int(a+b)` 小 1。正確做法是**把二分搜尋的判斷式直接套在
最終回傳字串上**（`cand = src[:mid] + marker`），這樣才恆真。

### A/B（三條，全部復現 reviewer 的數字）

| 關掉 | 結果 |
|---|---|
| provider request 層收斂 | `:79` 實際送出總量 **28798** 超過上限；帶附件 **30057** |
| `source_context` 截斷改回事後附加 | `:144` **513 / 514**（budget 500），小預算 54/40、21/8 |
| `task_8drafter` 截斷改回事後附加 | `:155` 同上數字 |

**A/B 的變異本身也會寫錯**：我第一次只把搜尋條件改回 `src[:mid]` 卻沒改 return，
做出來的是「不加提示」而不是原本的「事後附加」，於是紅的是「提示不見了」而不是
「超標」—— 看起來有反證，其實驗的是另一件事。變異必須忠實還原**原始實作的形狀**。


### 修好一個 bug 之後，一個「一直是綠的」測試才紅

修好 `_truncate_to_budget` 的超標問題後，`test_upstream_context_consumes_the_same_budget`
變紅：`max_tokens=1`。

原因是那個測試**一直靠著這個 bug 才綠**：截斷從前會超標，剛好把剩餘額度壓成 0，
於是「額度用盡就跳過檢索」看起來一直成立。截斷改成精確之後還剩 1 token，
檢索就被呼叫了。

**這不是測試壞了，是它終於開始測真的東西。** 真正缺的是一道檢索下限：
幾十個 token 的「證據」是一段被切斷的殘片，卻仍會在 audit 裡登記成一筆來源 ——
provenance 宣稱有依據、內容卻不成句，比沒有證據更糟。
已加 `MANUSCRIPT_RETRIEVAL_MIN_TOKENS`（預設 200），低於下限就整段跳過並記錄。

**通則**：修掉一個會「剛好抵銷」的 bug 時，要預期有測試會轉紅；
轉紅的那些要逐個判斷是「測試寫錯」還是「原本就沒測到」，不要直接改斷言讓它變綠。

### 又一次真實 data/ 污染：沒有 app context 的測試

新增的 `test_provider_request_ceiling.py` 直接呼叫 `Task8Drafter`（沒有 Flask app
context），而 `ManuscriptRuling` 會往下走到 ContextChain 的 fallback，
`_get_data_root()` 在沒有 context 時退回 `os.getcwd()/data`——真實資料目錄。
快照抓到 `data/CEIL-p` 被寫入。已在該檔加 autouse fixture 隔離。

**這是本輪第三次踩同一個形狀**（前兩次是 audit sidecar、ContextChain fallback）。
規則：**任何直接呼叫 app 層物件、又沒有 app context 的測試，都必須自己釘住 data root**，
而且要逐一 patch 每個 `from ... import _get_data_root` 的模組（各自持有獨立綁定）。


## 3.13 COC 第二刀：P0 Literature 與 P0 PAQ（2026-08-11 第四輪）

**先看範圍：這一輪只做完兩個 P0，P1 一條都沒動。COC 尚未完成。**

### 環境先講：兩個 MCP 這一輪不可用

`codegraph_*` 與 Rickie `brain_context` 在本次 session **沒有掛上**
（ToolSearch 查無此工具；`.codegraph/codegraph.db` 存在但沒有 CLI，PATH 也沒有）。
AGENTS.md 要求的「CodeGraph 優先」這一輪做不到，改用 `rg` + 直接讀檔。
**這是能力缺口不是選擇** —— 下一輪若 MCP 可用，值得用 `codegraph_impact`
複查本輪改動的 `update_entry` 簽章有沒有漏掉呼叫端（我是用 `rg` 全域列舉的）。

### 接手時先驗過的三件事（都復現）

| 項目 | 交接聲稱 | 本輪實測 |
|---|---|---|
| `pytest test/unit tests` | 814 passed | **814 passed** ✅ |
| 真實 `data/` 不變 | 不變 | **2473 檔、aggregate 完全相同** ✅ |
| repo/branch/HEAD | — | `release/vm-20260806` @ `193393a`，40 個 dirty 項目（他人變更，未動） |

### P0 PAQ：writer 與 reader 讀寫的不是同一個檔（NOTE-018）

實測真實 data：`taxonomy_manual_update.json` **0 個**、`taxonomy_v1.json` **3 個**、
`cube_v1.json` **3 個**、`library.json` **0 個**、`search_results.json` **2 個**。

舊 reader（`_load_upstream_context`）只找 `taxonomy_manual_update.json`，
所以**整條 PAQ 對 Drafter 是斷的**，cube 與 rag_context 從未進過 prompt。

新增 `app/core_pro/manuscript/coc_producers.py` 當唯一入口，
優先序 PaqSurvey DB > manual_update > taxonomy_v1（理由見 NOTE-018）。

**順手挖到但沒修的兩條**（下一輪要處理）：
1. **`PaqCore.run_paq_task` 完全沒有呼叫端。** 前端打的是
   `paq_routes.run_paq_task`（另一份實作）。因此 `PaqCore` 裡的
   `_save_chat_record()` 與 `IndexService.register_index()` **從未執行過** ——
   PAQ 2A chat 沒有伺服器持久化，這是擁有者要求的 P0 後半段，本輪未做。
2. **`PaqMatrix.transform` 算出來的 `rag_context` 被 `paq_routes` 丟掉**
   （只取 `view_data`）。本輪由 coc_producers 重新算一次來用，
   但 producer 端仍然沒有存下來。

### P0 Literature：旁路是真的，而且 teeth test 抓到了（NOTE-019）

`_load_upstream_context` 直接把 `search_results.json` 的 `keywords`／`apa_citations`
組成 `[Literature Hints]` 送進 prompt，**完全不經過 screening**。

**這條旁路目前在正式站剛好沒觸發，但不是因為有防護**：舊 reader 找
`<base_pid>/search_results.json`，而真實檔案在 `<pid>-p/search_results.json`。
純粹是路徑不符的巧合。測試刻意把哨兵種在 reader 讀得到的位置，
否則「旁路已封死」的斷言會因為路徑不符而假綠 —— 這正是 §3.9 空過的同一形狀。

改成只讀 `LiteratureLibrary` 的 `included`，並把**採用理由**（`screening_note`）
與閱讀筆記一起傳遞。

### P0 Literature 的第二個洞：任何人都能標 included（NOTE-020）

`/api/literature/library/update` 對 `screening_status` **沒有任何角色檢查**。
而 included 直接決定 Drafter 的寫作依據。已補：
- 角色門檻 >= `editor`（owner=PI、editor=Co-PI；coauthor 一律不得改）。
- `update_entry` 收到 `screening_status` 卻沒有 `actor` → **直接 ValueError**。
- 決策 provenance 三欄由 service 自行寫入，不接受從 patch 帶入（否則可偽造決策者）。

**這個改動讓 12 個既有測試轉紅**，全部是 fixture 用 `update_entry` 種 screening
狀態卻沒帶 actor。逐條判斷後認定是「fixture 要符合更嚴的契約」而非「測試寫錯」——
它們本來就在模擬「某個 PI 做了決定」，補上 actor 之後反而更忠實。
**生產端只有兩個呼叫端**（`literature_batch_routes` 只設 paper_id、不受影響；
`literature_library_routes` 已改），是用 `rg` 全域列舉確認的。

### audit：fingerprint 原本被寫死成 None

`manuscript_ruling` 組 `coc_source_items` 時 `"fingerprint": None` 是寫死的，
於是 audit 看起來有 provenance、實際上什麼都查不回來。已改成原樣帶過來
（`segment_id` 同樣）。coc_producers 對 PAQ 與每一篇 included 文獻都算了指紋。

### A/B 反證（四條，全部落在預期斷言）

| 關掉的修復 | 紅在哪 |
|---|---|
| literature 過濾改為接受 excluded | `test_coc_producers.py:301` 被排除的文獻標題出現在寫作 request |
| 還原 `[Literature Hints]` 旁路 | `:318` 未經 screening 的關鍵字進入 request；`:339` 對照組同時紅 |
| PAQ reader 還原成只讀 manual_update＋不讀 DB | `:223` axis_labels 不在場；`:267` PaqSurvey 不在場 |
| `fingerprint` 改回寫死 None | `:362` paq_note 的 fingerprint 是空的 |
| `update_entry` 移除 actor 守衛 | `test_literature_screening_gate.py:78` DID NOT RAISE |

**A/B 的變異本身又寫錯了一次**（§3.12 警告過的形狀）：第一次用 bash heredoc
重新插入 `[Literature Hints]`，f-string 裡的 `\n` 被吃掉，變成
`SyntaxError: unterminated string literal`，7 個測試全 ERROR。
那不是反證，那是把檔案弄壞。**改用 Write 工具寫一支 apply/restore 腳本**
（`scratchpad/ab_bypass.py`）才做出忠實的變異。
教訓：A/B 變異碼不要走 shell 轉義，寫成檔案再執行。

### 本輪數字與隔離證據

```
py -3.10 -m pytest test/unit tests -q   →  828 passed / 0 failed   （814 → +14 新測試）
真實 data/ 遞迴快照（排除 _logs/_locks）：2473 檔
  跑測試前 aggregate = 1793824f5404...e428b
  跑測試後 aggregate = 1793824f5404...e428b   （完全相同，連 mtime 都沒動）
A/B 殘留：rg 掃 app/ test/ tests/ → 無（不用 git grep，新檔未追蹤時它看不到）
```

### 還沒做（**COC 尚未完成，不得宣告完成**）

擁有者的驗收標準是「每一個 producer 都要到得了 provider」。目前到得了的只有：
Manuscript 正文／2A／2C／留言（第一刀）＋ PAQ ＋ included 文獻（本輪）。

**P0 剩餘**：
1. **Literature 遷移未做。** 全站仍 0 個 `library.json`，所以實務上
   **仍然沒有任何 included 文獻** —— Drafter 依舊沒有論文依據。
   要寫一支遷移把 `search_results.json` 的 `papers[]` 與 Paper 表併成
   **candidate**（`merge_candidates` 已保證只產生 candidate），
   再由 PI/Co-PI 逐筆確認。**遷移前不要部署**，理由同 §3.10。
   註：`search_results.json` 的 `papers[]` 欄位（title/doi/url/year/confidence/
   is_verified/source/reason）與 `normalize_entry_metadata` 幾乎一對一，遷移不難。
2. **PAQ 2A chat 伺服器持久化未做**（見上面 `PaqCore.run_paq_task` 無呼叫端）。

**P1 全部未動**：Study（latest matrix 1／history 2／conversation 4）、
Mentor/Reviewer（MentorReviewItem 的 comment/suggestion/PDF/URL、
ChapterComment scope=paper+g_ver）、Manuscript 決策鏈（其他章 2A、附件持久 reference、
resolved 意見形成的決策）、統一 packer 接管 background 1200／Study 2400／upstream 6000
三處硬截、upstream/ContextChain fallback 接 requester tenant/role。

**部署未做，且刻意不做**：本機全綠、digest 不變、無殘留都成立，
但 §3.10 的裁示是「screening 遷移完成前不要部署 strict scope」，
而遷移正是上面沒做完的第 1 項。VM（34.80.240.29）這一輪一次都沒碰。
`ContextChainService` 的 data-root 相容遷移也還沒查 VM 上的舊 `context_chain` 路徑。

### 3.13.1 Review 回應與**兩處自我更正**（2026-08-11，同輪稍後）

Review 判 Needs work，兩個驗收洞都復現了，另外我自己又抓到第三個。

**洞 1：多篇 included 文獻會多出一筆沒有 fingerprint 的假來源。**
`coc_bundle` 原本一個 block 只放得下一筆 `item`，多來源時就合成
`literature:Nitems` 彙總 —— 那筆對應不到任何真實文獻。實測 3 篇 → 4 筆來源。
而測試把 `coc_sources` 收斂成 `{source_type: source}` 的 dict，
**後面的逐篇來源覆蓋掉前面的彙總**，缺陷就被藏住了。

**同一個缺陷還有第二面（review 沒提到，我查 `_strip_coc_section` 時發現）**：
它只認 `b["item"]` 單數，所以**去重一觸發，逐篇 provenance 整批消失**，
audit 只剩那筆合成的假來源 —— 而去重是正式 handler 路徑的常態。

修法：`add()` 改收 `items` 清單，blocks 是唯一來源真相，
`_strip_coc_section` 從 blocks 攤平。不再有側channel、不再有合成彙總。
A/B：還原舊形狀 → `test_coc_producers.py:420`「應有 3 筆，實際 1」。

**洞 2：source contract audit 只讀 git tracked files。**
`NOTE-018`/`NOTE-019` 被判「defined but never referenced」，因為引用都寫在
還沒 `git add` 的新檔裡；而且我在 `manuscript_ruling` 用的是
「（NOTE-018 / NOTE-019）」而不是驗證器認得的 `NOTE(NOTE-NNN):` 形式。
補上正確形式後 **NOTE failures 2 → 0**。
（同一個形狀在 §3.10 已經記過一次：`git grep` 看不到未追蹤檔。驗證器也一樣。）

**剩下 3 個 header failure 全部不是本輪造成的**，且刻意不碰：
- `app/services/index_service.py` —— §3.9 早就記載「header 本身就是錯的維護資訊」，
  它在本 session 開始前就是 `M` 狀態（別人的 in-flight 變更）。
- `patch_matrix.py` / `patch_schema.py` —— §4 第 6 項記載的既有未提交刪除。
  修它們＝動別人的 dirty 變更，違反開工前的指示。
**所以 source contract 仍是 FAIL(3 header, 0 NOTE)，不能宣告全綠。**

### **更正：上一節說「screening 完全沒有角色檢查」是錯的**

這是本輪我自己講錯、後來用 A/B 抓到的一條，比上面兩個洞更值得記。

我在 3.13 寫「`/api/literature/library/update` 對 `screening_status` 零角色檢查，
任何人都能標 included」。寫 HTTP 角色矩陣測試時做 A/B——**把新加的
`require_workspace_role(pid, ROLE_EDITOR)` 整段拿掉，viewer 與 coauthor 仍然 403**。

真相是既有守衛早就擋住了，兩道：
1. `enforce_project_ownership`（before_request）依 HTTP method 推 min_role，
   **POST/PUT/PATCH 一律要 `editor`**（`security.py` 約 L620）。
2. 模組守衛（`app/__init__.py` 約 L515）擋掉 coauthor 進入 Manuscript 以外的模組
   —— 所以 coauthor 連 `reading_note` 都改不了，它**根本走不到** screening 那一行。

也就是說：
- **真正缺的只有「決策無法歸屬」**（沒有 actor/時間/批次），不是角色門檻。
  那一項是真的修好了，A/B 可紅（`test_literature_screening_gate.py:78` DID NOT RAISE）。
- 我加的 route 檢查**目前是重複的**。保留，但已在程式碼與 NOTE-020 註明它是重複的、
  以及保留的唯一理由（既有守衛的門檻由 method 推導，日後改 method 對應表
  或加 GET 變更路徑時保護會無聲消失）。**不得再描述成「補上缺少的角色檢查」。**

**方法論教訓（第三次踩同一形狀）**：`test_literature_screening_http_acl.py` 的
viewer/coauthor 403 斷言，在拿掉我的程式碼之後**照樣全綠** —— 它們驗的是
行為契約，不是「新程式碼有效」。
**「加了 X 之後 Y 被擋住」不等於「X 擋住了 Y」；一定要拿掉 X 再跑一次。**
NOTE-020 與該測試檔頭都已寫明這個檔證明不了什麼。

### 本輪最終數字

```
py -3.10 -m pytest test/unit tests -q   →  833 passed / 0 failed   （814 → +19）
source contract audit                   →  FAIL (3 header, 0 NOTE)  ← 3 個 header 皆為既有
真實 data/ 遞迴快照                      →  2473 檔，aggregate 與開工前完全相同
A/B 殘留（rg，非 git grep）              →  無
```

**部署狀態不變：仍然不能部署。** library.json 仍是 0 個，candidate migration 未做，
P1 全部未動，VM 一次都沒碰。

## 3.14 source contract gate 本身是瞎的（2026-08-11，接續輪）

### 本輪轉折：上一節那句「3 個 header failure 全是既有的」是**假數字**

上一輪的結論是「FAIL(3 header)，3 個都不是我造成的，所以不碰」。那個推論
建立在一個沒被檢查的前提上：**驗證器有看到本輪新增的檔**。它沒有。

`audit_source_contract.py` 的 scope 是 `git ls-files`，只列 tracked。
本輪新增的 13 個 source 檔（`coc_producers.py`、`coc_bundle.py` 與 11 個新測試）
全部 untracked，**從頭到尾沒有被讀過一次**。直接對那 13 個檔跑
`missing_header_signals()`：

```
coc_bundle.py / coc_producers.py / 全部 11 個新測試
  → missing = ['position', 'integration', 'boundary']    13/13 全紅
```

真值是 16 個 header failure，不是 3。gate 顯示的是**驗證器的視野，不是程式碼的狀態**。

**最該記的是這個形狀已經發作過一次而沒被推廣**：上一輪自己抓到
「NOTE-018/019 被誤判 never referenced，因為引用寫在未追蹤檔裡」，
修了 NOTE 那一半（把引用補進 tracked 的 `manuscript_ruling.py`），
**卻沒有回頭問 header 那一半是不是同一個病**。同一支驗證器、同一個 scope、
同一個 `git ls-files`。教訓：抓到「驗證器看不到 X」時，要問的不是
「怎麼讓這一條看得到」，是「這支驗證器還有哪幾條也看不到」。

### 13 個檔為什麼會紅：是詞彙表，不是缺資訊

紅的原因不是 header 寫得差 —— 恰恰相反，新檔的 header 比契約要求的更細：
把單一「上下游」拆成 `上游呼叫者`＋`下游服務`，把「維護邊界」拆成
`明確不負責`＋`不變量`，「模組定位」寫成 `子系統定位`。
`HEADER_SIGNALS` 只認舊字面，於是資訊更多的 header 被判缺欄。

**不修同義詞的話，這個 gate 會實質逼人把 header 寫得更粗才能過關。**
所以兩件事一起做，缺一不可（見 NOTE-021 的否決方案 A / B）：
- scope 改成 tracked ＋ untracked-not-ignored（`--cached` 維持只看 index，
  因為那個模式問的是「即將發布的那棵樹」，未追蹤檔在那棵樹裡本來就不存在）。
- `HEADER_SIGNALS` 補同義詞，但只收「與既有詞同等具體」的說法；
  `--self-test` 已加反例：`說明`／`備註` 這類泛稱仍然必須紅。

`app/services/index_service.py` 那一條就是純詞彙不符 —— 它的 v0.2 header
（2026-08-10 寫的）內容完整到記載了「本檔目前 write-only、
`search_indices_by_module()` 必回空清單」。補同義詞後自動轉綠，**沒有動它一個字**。

### patch_matrix.py / patch_schema.py：裁定為「刪除成立」，已 staged

上一輪判定「動它們＝動別人的 dirty 變更」而不碰。本輪擁有者明確授權處理，
先實查兩支一次性 patch 的產物是否已經落地，確認後才記錄刪除：

| 檔案 | 它要 patch 的東西 | 實查結果 |
|---|---|---|
| `patch_matrix.py` | 給 study_matrix 的 executor 補 try/finally + 非阻塞 shutdown | `study_matrix.py:101` `executor = ThreadPoolExecutor(...)`、`:167` `executor.shutdown(wait=False, cancel_futures=True)` 都在 |
| `patch_schema.py` | 給 fix_db_schema 注入 papers composite-key migration | `fix_db_schema.py:344` `Migrating papers table to composite primary key`、`:347-371` 建表／搬資料／rename 都在 |

兩支都是「以字串比對就地覆寫原始碼」的一次性工具，重跑只會失敗或改壞；
產物既已落地，留著它們沒有價值而有誤觸風險。除了 HANDOFF 自己的敘述之外
沒有任何引用（`rg` 全域）。`git add -A` 記錄刪除。

### 踩到的坑：新測試自己製造了一筆假 NOTE 引用

scope 修好之後 gate 立刻回報 `NOTE-042: referenced but not defined`。
`NOTE_REF_RE` 是純文字比對、會掃到測試檔自己，而我在 fixture 裡直接寫出了
完整的引用字面（`NOTE` 加括號加號碼）。**這不是誤報，是 scope 修好之後才看得見的真問題**
（原本測試檔是 untracked，驗證器根本掃不到）。
改成把號碼拼接產生（`"NOTE" + "(" + FIXTURE_NOTE + ")"`），完整字面不出現在原始碼裡。
寫任何「談論 NOTE 機制」的檔案時都要注意這一點。

### A/B 反證

用 Edit 工具改 `candidate_files()` 的 `if not cached:` 為 `if False and not cached:`
（**不走 shell heredoc**，理由見 §3.13 那次把檔案弄壞的紀錄），跑新測試：

```
FAILED test_untracked_source_is_in_scope           :105  'untracked_bad.py' not in [...]
FAILED test_untracked_bad_header_is_reported       :112  'untracked_bad.py' not in ['tracked_good.py']
FAILED test_note_reference_in_untracked_file_counts:155  NOTE-042 仍被判 never referenced
3 failed, 3 passed
```

**三個對照組照樣綠**（.gitignore 排除、`--cached` 不看未追蹤、合格 header 不被報）
—— 這才證明紅的是 scope 那一刀，不是測試整支壞掉。變異已還原，`rg` 掃無殘留。

### 本輪數字

```
py -3.10 -m pytest test/unit tests -q   →  839 passed / 0 failed   （833 → +6）
source contract audit（worktree）        →  ok，scope 260
   ├ 修之前：scope 246，FAIL(3 header)   ← 對 14 個新檔失明
   └ 修之後：scope 260，0 header 0 NOTE  ← 真實覆蓋 +14
真實 data/ 遞迴快照（排除 _logs/_locks）  →  2473 檔
   跑測試前 aggregate = 915ED1AD577B...C58B6
   跑測試後 aggregate = 915ED1AD577B...C58B6   （完全相同）
```

### 順手查到、會影響下一輪的事實

**上一節寫的「`search_results.json` 的 `papers[]`」路徑不精確**：欄位清單
（title/doi/url/year/confidence/is_verified/source/reason）**完全正確**，
但實際位置是 `data["results"]["papers"]`，不是頂層。寫遷移前先看這一行，
不要照字面去讀 `data["papers"]`（會拿到 None 而且不會噴錯）。

**本機語料規模（read-only sqlite `mode=ro` 實查，沒有經過 create_app）**：

```
library.json                 0 個（全站）
search_results.json          2 個：data/DSPWVD-p、data/ULQ8F6-p
  results.papers             4 篇 / 5 篇
  results.meta.filtered      4   / 51      ← 檔案只留 top-N，51 篩選結果沒有全存
papers 表                    3 筆（ULQ8F6-p 2、DSPWVD-p 1）
projects 表                  9 筆
```

也就是說 **candidate migration 在本機最多只產得出約 9-12 筆 candidate**，
真實語料在 VM（34.80.240.29），本輪仍然一次都沒碰 VM。
而且依 NOTE-020 不變量 1，`merge_candidates` 只會產 `candidate`：
**遷移做完也不解除部署封鎖**，它只是讓「人逐筆篩選」變得可能。
下一個人不要把「遷移完成」誤讀成「Drafter 有論文依據了」。

另註：`data/DSPWVD-p/search_results.json` 的 `results.keywords` 是
cp950 被當 latin-1 解讀的亂碼。遷移只吃 `results.papers`，不受影響，
但如果有人想順手救 keywords，先處理編碼。

### 本輪沒做

擁有者裁示下一步做 **PAQ 2A chat 伺服器持久化**（純程式、零資料相依），
candidate migration 押後。P1 全部未動，VM 未碰，未 push。

## 3.15 PAQ 2A chat 持久化：改的是死碼旁邊那一份（2026-08-11，同輪稍後）

### 真因：兩份平行實作，v0.5 與 v0.6 兩次都改到死的那一份

`PaqCore.run_paq_task` **全 repo 沒有任何呼叫端**（app／test／tests／js 全掃）。
前端 `paq_interact.js:398` 打的是 `/api/paq/run_task` → `paq_routes.run_paq_task`，
那是另一份平行實作。所以寫在 PaqCore 裡的：

- v0.5「對話存檔」（`_save_chat_record`）
- v0.6「把最新 taxonomy/cube 餵給 chat」

**兩件都從未執行過一次**。

**別靠推論，這裡有硬證據**：PaqCore 版寫
`success, response_text = execute_paq_chat(...)`，再把 `response_text` 當字串
存進 record 的 `ai` 欄。但 `execute_paq_chat` 實際回傳 `(True, {"reply": ...})`
—— 是 dict。只要它真的跑過一次，磁碟上就會出現 `"ai": {"reply": "..."}`
這種紀錄。全站一筆都沒有（連 chat 目錄都不存在）。
下次要判斷「這段程式到底有沒有跑過」，找這種**只要跑過就會留下痕跡的型別錯誤**
比讀呼叫圖快也可靠。

### 這題有兩半，只補存檔會造出第二個 index_service

前端的 `chatSessionHistory` 是 `paq_initial.js:52` 的瀏覽器變數，
**沒有任何 init 載入**：重新整理就沒了，伺服器上也一筆都沒有。

如果只把 `_save_chat_record` 接上去，測試會綠、磁碟上會有檔，
**使用者仍然什麼都看不到** —— 那就是 `IndexService` 的翻版
（三個呼叫端一路寫入 `metadata_index`，全 app 沒有讀取端，
連 `search_indices_by_module()` 必回空清單都沒人發現，見該檔 header）。
所以本輪同時補了讀取端：`PaqCore.load_chat_records()`、
`GET /api/paq/chat_history/<pid>`、`paq_interact.js` 的 `loadChatHistory()`
並掛進 `paq_initial.js` 的 init 序列。

決策與否決方案見 NOTE-022，含**為什麼不把對話接進 Drafter**
（未經篩選的自由對話進寫作路徑＝NOTE-019 擋掉的同一個形狀）。

死碼**刻意保留未刪**（刪不刪是擁有者裁量），但已在該方法 docstring 加上
「這是死碼、要改請改 paq_routes」的警告 —— 沒有警告的話第三次還會改錯地方。

### A/B 反證：第一次做的不是反證，是把檔案弄壞

第一次變異用 `if False:` 把持久化區塊關掉，結果 `except` 懸空 → SyntaxError，
6 個測試全 **ERROR**。**ERROR 不是 FAILED，那不是反證。**
§3.13 記過同一個坑（heredoc 吃掉 `\n`），這次換成縮排結構被破壞 ——
教訓要推廣成：**變異之後先看紅的是 FAILED 還是 ERROR**，
ERROR 幾乎一定是變異本身壞了。

改成把 route 分支整段還原成修改前的 4 行，6 個測試紅在預期位置：

```
:140 KeyError        body["data"]["persisted"] 不存在
:165 AssertionError  讀回來是空的（根本沒存）
:189 TypeError       fake_chat["taxonomy_data"] 是 None（v0.6 沒接上）
:211 KeyError        persisted 不存在
:223                 損毀檔測試的前置 chat 檔從未產生
:249 AssertionError  viewer 讀不到擁有者留下的話
6 failed（無 ERROR）
```

### source contract gate 當場咬到我兩次

scope 修好之後（§3.14），gate 立刻抓到兩件我自己寫的東西：

1. 新測試 fixture 裡有完整字面的 NOTE 引用 → 被當成指向不存在條目的引用。
2. **我在 §3.14 的 HANDOFF 內文裡也寫了一次同樣的字面** —— 記下這個坑的那一段
   自己踩了進去。`_note_integrity()` 掃的是**全部候選檔**（含 `.md`），
   不是只掃 source scope。

兩處都改成拼接產生／用文字描述。這代表 §3.14 那一刀立刻付了利息：
在 scope 修好之前，這兩個問題都不會有人發現。

### 隔離：發現真實 data/ 多了兩個檔，追到底了

跑完 PAQ 測試後遞迴快照從 **2473 → 2475**，多出：

```
data/manu_core.db-shm   32768 bytes   2026-08-11 22:07:17
data/manu_core.db-wal       0 bytes   2026-08-11 22:07:17
```

判定（**不是「大概沒事」，是量出來的**）：

- `manu_core.db` 本身 mtime 仍是 2026-07-28 16:13:46，大小 45056，**沒被改**。
- `-wal` 是 0 bytes → 沒有任何待寫入的 frame。
- **排除這兩個 sidecar 重算 aggregate = `915ED1AD577B...C58B6`，
  與開工前基準逐位元相同**（2473 檔）。所以除了多出兩個空 sidecar 之外，
  真實資料一個 byte 都沒動。

**但沒能重現**：事後單獨重跑 `test_paq_chat_persistence.py` 與
既有的 `test_literature_screening_http_acl.py`，兩者都沒有更新那兩個檔的 mtime。
也就是說「哪一次執行開了真實 `manu_core.db`」目前未定案。
`fix_db_schema.target_db_path()` 只指向 `data/roothinks.db`，不碰 manu_core，
所以**不是** NOTE-016 那條路徑。

下一個人要注意兩件事：
1. `app/__init__.py:351` 的 `manuscript_db_uri` 是從真實路徑算出來的預設值，
   靠 `test_config` 覆寫 `SQLALCHEMY_BINDS` 才會指到 tmp。任何在覆寫生效前就
   建立 engine 的路徑都會碰到正式檔。這是最可疑的方向。
2. `test/integration_smoke/*` 是 `create_app({"TESTING": True})`、完全沒有覆寫
   URI 與 BINDS（見 `test_schema_fix_scope.py` 檔頭），它們**設計上**就會連上
   真實 `manu_core.db`。本輪的全套指令是 `test/unit tests`，不含 integration_smoke。
3. **快照腳本刻意不排除 sidecar** —— 就是它把這件事叫出來的。
   要排除只能在分析時排除，不要改基準。

### 本輪數字

```
py -3.10 -m pytest test/unit tests -q   →  845 passed / 0 failed   （839 → +6）
source contract audit                   →  ok，scope 261（含新測試）
source contract --self-test             →  ok
真實 data/ 遞迴快照                      →  2475 檔（含上述 2 個 sidecar）
   全套跑前 aggregate = 12D422ECF69F...A87C
   全套跑後 aggregate = 12D422ECF69F...A87C   （相同）
   排除 sidecar 後     = 915ED1AD577B...C58B6 = 開工前基準
A/B 殘留（rg）                           →  無
```

### 沒做／未驗證（不得當成完成）

- **`loadChatHistory()` 沒有瀏覽器實測。** 契約層有測試（HTTP 進出、
  ACL、損毀檔），但「重新整理後對話真的出現在畫面上」這件事**只有靠看才算數**
  —— 文案脫節、CSS 讓氣泡疊在一起、非同步順序造成閃爍，單元測試全部測不出來。
  沒做的原因：roothinks 沒有 `.claude/launch.json`，起本機 Flask 會連上
  真實 `data/roothinks.db` 與 `manu_core.db`，而本輪的隔離證明正是靠那兩個檔
  逐位元不變；要做必須先準備獨立的 data root。

  **不靠瀏覽器能查的四項已經查了，第四項是真的踩到：**
  1. `#chat-history` 容器存在（`paq.html:158`）。
  2. script 順序：`paq_initial.js`(204) 早於 `paq_interact.js`(205)，
     但 init 是註冊在 `DOMContentLoaded`，該事件在所有同步 script 執行完才觸發，
     所以呼叫時 `loadChatHistory` 一定已定義。
  3. `currentPid` 在同一個 handler 的開頭就設好，早於我插入的呼叫。
  4. **cache-busting 版號沒跟著改** —— 兩支 JS 都改了，`paq.html` 卻還是
     `paq_initial.js?v=0.2` / `paq_interact.js?v=0.1`，瀏覽器會拿舊檔，
     實測會看到「程式改了但行為完全沒變」。已改成 `v=0.3` / `v=0.2`。
     **以後改 `app/static/js/*.js` 一律回頭檢查引用它的 template 版號。**
- 對話**沒有**接進 COC／Drafter，這是刻意的（NOTE-022）。
- candidate migration 仍未做，`library.json` 仍是 0 個，**部署仍封鎖**。
- P1 全部未動；VM（34.80.240.29）本輪一次都沒碰；未 push。

## 3.16 乾淨 worktree 驗證與瀏覽器實測（2026-08-11，同輪最後）

### 我上一個 commit 是回歸，已重做

`2c93808` 在**乾淨 worktree** 驗證 `FAIL (0 header, 18 NOTE)`，而它的 parent
`193393a` 是 `ok` —— **是我造成的**。真因：我把 ledger（`docs/NOTES.md`）commit 在
它所描述的程式碼之前，於是 NOTE-004~020 全部「defined but never referenced」；
再加上我自己在 HANDOFF 內文寫了一次完整的 NOTE 引用字面，多一條
`NOTE-042: referenced but not defined`。

**這裡要記的是流程，不是 bug**：我 commit 前只跑了 worktree 模式的 audit（會看到
未追蹤檔，所以是綠的），**沒有跑 `--cached`**，而 `--cached` 正是為了問
「即將發布的那棵樹」而存在的、我自己在同一輪寫的東西。
**擁有 gate 不等於用了 gate。commit 前跑 `--cached`，commit 後在
detached worktree 實際 checkout 再跑一次。**

處置：`git reset --soft 193393a` 後重做成單一自洽 commit `328105f`（未 push）。
乾淨 worktree checkout 實測：`source contract: ok, 261 files`、`--self-test ok`。

### 乾淨 worktree **跑不了測試**（既有缺陷，非本輪）

```
cd <detached worktree at 328105f> && pytest test/unit tests -q
  → 6 failed, 532 passed, 306 errors
  → FileNotFoundError: Database file not found in: [<wt>/data/roothinks.db, ...]
     fix_db_schema.py:66
```

`data/` 是 gitignored 且零 tracked 檔，所以新 checkout 沒有它；而
`create_app()` 一開機就呼叫 `fix_db_schema`，後者在 DB 檔不存在時直接 raise。
**在 193393a 上實測同樣失敗**，所以是既有缺陷。

第二段：把空 DB 檔生出來之後，`fix_schema()` 又在 `projects` 表不存在時
`RuntimeError("Missing 'projects' table")`。合起來就是
**全新資料庫既跑不了測試、也開不了機**，必須先用裸 Flask app 跑一次
`db.create_all()` 才能 bootstrap（本輪的 seed 腳本就是這樣繞的）。
**這對「部署到新機器」是直接風險**，VM 目前能跑只是因為它的 `data/` 早就存在。

### 瀏覽器驗收的隔離作法（可重複）

不要用正式 repo 起 server：`fix_db_schema` 的路徑相對它自己的檔案解析，
會動到正式 `data/roothinks.db`。作法是 **`git worktree` 出一份 detached checkout，
讓它有自己的 `data/`**，`app.root_path` 與 migration 路徑就都落在那份裡面。
帳號是腳本自建的測試帳號（`*@preview.local`），與擁有者真實帳密無關；
`AUTH_MODE=session` 是必要的，否則角色守衛直接放行、ACL 驗收會假綠。

### PAQ 2A 實機結果

`loadChatHistory()` **確實有效**，瀏覽器 A/B：

| | `#chat-history` 氣泡 | `chatSessionHistory` |
|---|---|---|
| 有 `loadChatHistory()` | 5（1 靜態 + 4 還原） | 4 |
| 拿掉該行 | 1（只剩靜態 System） | 0 |

UTF-8 正確、`<script>alert(1)</script>` 被轉義成 `&lt;script&gt;` 未執行、
瀏覽器實際載入的是 `paq_initial.js?v=0.3` 與 `paq_interact.js?v=0.2`。
使用者實打實送出一句時：訊息進 user 氣泡、伺服器回 `LLM_NOT_BOUND`、
UI 顯示錯誤而非假成功或卡住，失敗那輪也沒被塞進 `chatSessionHistory`。

**寫入→重整→讀回的「寫入」半段在瀏覽器沒有走完**：這個隔離實例沒有綁模型。
契約層有 `test_chat_turn_is_written_to_disk`（走真實 HTTP、斷言檔案落地與 actor），
但那不等於瀏覽器實測。要補完必須綁一個 stub provider。

### **實機才抓得到的三個既有缺陷**（都不是本輪造成）

1. **`/paq/<pid>` 對含連字號的 pid 全部失效。**
   `paq_initial.js` 的 `path.match(/\/([A-Za-z0-9]{6,20})\/?$/)` 不含 `-`，
   而真實 pid 就長成 `ULQ8F6-p`、`DSPWVD-p`。比對結果三個全 false，
   `currentPid` 為 null ⇒ alert 後 `location.href='/'`，**被靜默踢回 Dashboard**。
   目前是潛在缺陷：UI 一律用 `/paq?pid=...`（`dashboard.js:282-283`、
   `_navbar.html:37`、`_header.html:125`），但書籤或分享 RESTful 網址就會踩到。

2. **一顆格式不對的 voxel 會讓整個頁面顯示「Project Name: Load Failed」。**
   `cube_renderer.js:220` 是 `v.val.toFixed(2)`；voxel 缺 `val` 就拋例外，
   而 `renderVoxels` 是在 `loadPaqStatus()`（`paq_project.js:237`）裡呼叫的，
   例外一路冒到該函式的 catch，於是 `header-pname` 被寫成 Load Failed。
   **壞的是 3D renderer，畫面卻說專案名稱載入失敗** —— API 實測回 200 且有 name。
   這是錯誤歸因，會讓下一個人往完全錯的方向查。

3. **formal 專案的 PAQ 2A 對話在 UI 上完全不能用。**
   `lockInterfaceForFormal()`（`paq_project.js:276`）做
   `querySelectorAll('input').forEach(el => el.disabled = true)`，
   **連 `#chat-input` 一起停用**。該函式的意圖是鎖 taxonomy 編輯。
   是否要放行 chat 是產品決策，本輪不擅自改。
   （驗收因此改在 provisional 專案做。）

### 方法論：我第一次看漏了，是擁有者指出來的

我第一輪只查了 `document.getElementById('chat-history')` 就宣告 PAQ 驗收通過，
**畫面上同時寫著「Project Name ⚠ Load Failed」與「Principal Investigator Unknown」，
我完全沒看到**，是擁有者截圖指出來的。

教訓：**用 DOM 查詢做「實機驗收」，等於把視野縮成自己預設要看的那一個元素，
和只看 gate 綠燈是同一種錯誤。** 至少要做一次整頁掃描
（`document.body.innerText`、`preview_snapshot`、或截圖），
而且要主動找「有沒有哪裡寫著失敗／未知／空白」。
`#chat-input` 當時回報 `disabled: true`，我查到了卻沒解讀 —— 拿到反常數值就要當場追。

## 3.17 Reference 切章實機驗收（2026-08-11，同輪最後）

### 環境設定的坑：少一個 env，整條切章路徑根本沒送出去

第一次開手稿工作檯，連線徽章停在 **Offline**、S.Ver 卡在「載入中…」。
console 只有 `[Socket] connect_error: xhr post error`，network 是
`POST /socket.io/... → 400`，**兩者都看不出原因**。真因在伺服器 log：

```
ERROR engineio.server: http://127.0.0.1:5601 is not an accepted origin.
```

`CORS_ALLOWED_ORIGINS` 沒設。這是驗收環境設定，不是產品缺陷，但要記：
**socket 類問題不要只看瀏覽器端**，`connect_error` 與 400 都不帶原因，
真因只在 server log。launcher 已補該 env。

（另一個小坑：`preview_click` 對下拉項目失敗那一次，頁面被帶回 Dashboard。
改用元素自身的 `.click()` 就穩定 —— 仍走選單的真實 handler，不是直接呼叫函式。）

### 驗收結果：要求的六項一致性 ＋ 五個邊界，全部通過

種的形狀：`introduction` 兩版、`results` 一版、`reference` **零版**。
用 `ManuscriptIO.save_block_version()`（app 自己的 writer）種，不手工組檔案。

| 情境 | 2B 標題 | 2A target | data-section | S.Ver | 畫布 |
|---|---|---|---|---|---|
| 切到 Introduction（有版本） | Introduction | introduction | introduction | `V0.2 ← 0.1 · pi@…`／`V0.1 · pi@…` **最新在最前、帶血緣** | `INTRO-V2`（不是 V1） |
| 切到 Reference（**空白**） | Reference | reference | reference | 尚無版本 | 空的**可編輯編輯卡**，非 spinner、非 placeholder |
| 切章瞬間 | 目標章 | 目標章 | 目標章 | 載入中… | 「正在載入 X …」且 **非 contenteditable** |
| **快速切章**（intro→ref 連續） | Reference | reference | reference | 尚無版本 | 無 `INTRO-V2` 殘留 ⇒ **stale 回應被丟棄** |
| **重整** | Reference | reference | reference | 尚無版本 | Reference ⇒ **位置持久化成功** |

`collabRoleBadge` 全程「擁有者」；每一次切換後掃全頁文字，
`Failed／Error／Unknown／失敗／undefined` **零命中**。

「不得改動其他章」以磁碟逐檔 SHA-256 驗證：切了五、六次之後
`introduction` 仍是 `V0.1/V0.2`、`results` 仍是 `V0.1`、`reference` 仍然沒有任何版本檔，
**沒有任何版本檔被新增或修改**。

### 但抓到一個使用者看得到的既有缺陷：**光是切過去看，就會產生草稿**

磁碟比對時發現多出 `_draft__1.json`。隔離驗證（決定性）：
切到**從未造訪過**的 `method`、**一個字都沒打**，
`data/<pid>/manuscript/block/method/_draft__1.json` 立刻出現。

實際內容：

```
abstract/_draft__1.json      content = "<p><br></p>"                 ← 從沒打過字
method/_draft__1.json        content = "<p><br></p>"                 ← 從沒打過字
reference/_draft__1.json     content = "<p><br></p>"                 ← 從沒打過字
introduction/_draft__1.json  content = V0.2 的正文，**逐字相同**      ← 從沒編輯過
```

使用者看得到的後果（實機截到的文字）：

```
「Introduction」有未存檔的自動儲存草稿（2026/8/11 下午11:39:33）。[復原草稿] [保留目前內容]
已復原「reference」的自動儲存草稿。按存檔可將它建立為正式版本。
```

也就是：
1. 使用者被要求對**自己從未寫過**、且與已存版本逐字相同的「草稿」做決定。
2. 空白章節會被**自動復原**一份空草稿，並被邀請「建立為正式版本」——
   照做就會產生一個空的正式版本。

**尚未定案的是觸發點。** autosave 的唯一綁定是
`manuscript_ws.js:174` 的 `editorCanvas.addEventListener('input', ...)`，
而程式化寫 `innerHTML` 不會觸發 `input`；伺服器端
`ManuscriptIO.load_draft()` 也確認是只讀。所以還有第三個地方在觸發，
下一輪要從「切章渲染完成後有誰動了畫布」往下找
（`_flushAutosave` 加一行 `console.trace()` 最快）。

**這不是本輪造成的**：`git diff 193393a 328105f -- app/static/js/manuscript_ws.js`
沒有動到 autosave 的綁定或呼叫點。

### 沒做

- PAQ 2A 的「寫入」半段仍未在瀏覽器走完（隔離實例沒綁模型，回 `LLM_NOT_BOUND`）。
- 上述草稿缺陷**只診斷未修**，觸發點未定案。
- 部署（第 5 步）未做：VM 未碰、雜湊未比對、Gunicorn 未重啟。

## 3.18 假草稿根因定位與修復（2026-08-11，同輪最後）

### 定位方法：runtime wrap，不改原始碼

`console.trace()` 要改檔重啟；直接在瀏覽器把方法包起來更快也更誠實：

```js
const orig = wsApp.soed.scheduleAutosave.bind(wsApp.soed);
wsApp.soed.scheduleAutosave = function (...a) {
    window.__traces.push({stack: new Error('t').stack}); return orig(...a);
};
// 另外攔 editorCanvas 的 input，記下 e.isTrusted
```

切到**從未造訪過**的 `discussion`、一個字都沒打，抓到三筆，答案就在第一筆：

```
INPUT_EVENT  isTrusted: true
  at ManuSoed.insertEditorCard      (manuscript_soed.js:267)
  at ManuSoed._renderBlankSection   (manuscript_soed.js:853)
  at ManuSoed._applySectionSwitch   (manuscript_soed.js:887)
scheduleAutosave  section: discussion
  at HTMLDivElement.<anonymous>     (manuscript_ws.js:178)
_flushAutosave    section: discussion
```

**根因**：`insertEditorCard()` 用 `document.execCommand('insertHTML')`，
而 execCommand 在 contenteditable 上會派發 **`isTrusted: true` 的 input 事件**
（瀏覽器標準行為，不是本專案的 bug）。autosave 綁在 `editorCanvas` 的 input 上，
於是每一次切章渲染都被當成使用者編輯。

`isTrusted` 這個欄位是關鍵：沒有它我會往「哪段程式手動 dispatch 了 input」找，
而那個方向是空的。**攔事件時順手記 `isTrusted`，能直接分辨「瀏覽器產生」
與「程式碼偽造」。**

### 修法與被否決的修法

`_renderWithoutAutosave(render)`：渲染期間設旗標，`scheduleAutosave()` 見到就 return。
只套在切章的兩條渲染路徑（`_renderBlankSection`、`block_loaded` 載入既有版本）。

**否決「直接在 `insertEditorCard()` 裡封鎖」**：插入素材、Word 匯入、
2A 複製草稿、使用者按「復原草稿」都走同一個函式，那些是真的使用者動作。
包錯層等於用「沒有假草稿」換「真的編輯存不進去」。決策見 NOTE-023。

`finally` 不是防禦性寫法：少了它，任一次渲染拋例外就會讓旗標卡在 true、
**autosave 從此永久靜音**，比原本的 bug 嚴重得多。已有測試守這一條。

### 實機 A/B（隔離實例，含對照組）

| 操作 | 修前 | 修後 |
|---|---|---|
| 切兩章（一空白一有版本），完全不打字 | 假草稿 2 個 | **0 個** |
| 接著用 `execCommand('insertText')` 真的打字 | 存草稿 | **照常存**，內容含打進去的字 |

第二列是**對照組，比第一列更重要**：只驗「草稿不見了」的話，
把 autosave 整個關掉也會全綠。實機另外看到 `autosaveStatus`
從「編輯中…」變成「已自動儲存 上午12:38:43」，版本檔全程沒有被新增或修改。

瀏覽器實際載入的是 `manuscript_soed.js?v=3.0`（版號已同步 bump，
既有的 `TestCacheBusting` 下限也一起提到 3.0）。

### 觀察到但未定案

手稿工作檯**偶發被帶回 Dashboard**（`/manuscript/?pid=...` → `/`），
本輪發生兩次，兩次都沒有 console error、沒有失敗請求。
不影響本輪結論（重新導向後重新進入即可復現全部驗收），但下一輪值得追：
使用者中途被踢回首頁會直接損失未存內容。
