# HANDOFF — roothinks 正式站（給接手的 AI 讀）

最後更新：2026-08-08　分支 `release/vm-20260806`　VM HEAD `76a05ac`

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
| 單元測試 | 570 passed（本輪起點 549） |
| VM 工作目錄 | `git status --porcelain` 空 |
| VM HEAD | `76a05ac`，追蹤 `origin/release/vm-20260806` |
| 容器 | `roothinks_progress_paq_v8_10005` healthy，`0.0.0.0:80->10005` |
| 專案數 | 7；`name`/`research_title` 分岔筆數 0 |

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

## 4. 卡在哪 / 還沒做

1. **瀏覽器實機點擊從未完成。** 本輪所有 UI 驗證都是伺服器端量測與靜態驗證棚，
   Chrome extension 未連線，沒有一次是真的用滑鼠點過。待確認：Drafter 草稿生成、
   LAVA 綁定選單、Study 分割與破圖、正式研究改題目。
2. **`DGVRYV-p` 的研究筆記是空字串。** 擁有者說筆記應是主要來源，但欄位沒東西。
   目前只能用單元測試證明筆記路徑會動，無法用真實資料證明。
3. **文獻流程有失敗沒被發現**：`Code-Switching_Red-Teaming` 缺 `full_text.json`；
   `SEBASR_IJMIR` 的 `05_interprets/summary.json` 帶 `LLM_PROVIDER_ERROR` 與
   `mode: fallback_synthetic`。要不要回頭補跑待決定。
4. **手稿模組兩個既有問題（擁有者回報，未處理）**：
   - 舊版載入回報成功，但編輯畫布不顯示內容（回讀顯示問題）。
   - 手稿 Title 重整後回到舊值 —— 標題沒有獨立存檔路徑，只能靠存 2C 一併寫入。
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
docker run --rm --user 0:0 ... python -m pytest test/unit -q   # 期望 570 passed
```

**判準提醒**：測試綠燈不足以證明目標達成。本輪 567 個測試全綠的同時，
`manuscript_ruling.py` 有一個 `UnboundLocalError`（import 在 `try` 內、
呼叫在 `try` 外）會讓草稿生成在真實路徑上直接崩潰（`beff6a8` 修）——
因為測試沒有覆蓋真實呼叫路徑。
**判斷 Drafter 好不好，看產出文字裡有沒有論文專屬的具體數據，不要看測試數字。**
