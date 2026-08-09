# HANDOFF — roothinks 正式站（給接手的 AI 讀）

最後更新：2026-08-09　分支 `release/vm-20260806`

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
| 單元測試 | 593 passed（本輪起點 577，新增 16） |
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
