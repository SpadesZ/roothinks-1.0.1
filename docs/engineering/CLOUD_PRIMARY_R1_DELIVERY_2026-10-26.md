# Cloud primary R1 交付：3.12 開發基線、deep-translator、測試隔離、科學天花板

日期：2026-10-26。角色：主要實作工程師（Owner 與 ChatGPT 為獨立稽核與決策層）。
起點：整合分支 `codex/py312-local-first-integration-20261025` 的 `29e87c68e6a08c94087483a3144d660b5043c101`（已核對）。
本輪分支：`codex/py312-cloud-primary-20261026`。只推個人倉庫。**沒有 merge main、沒有部署、沒有碰 LAVA-Cowork、科學引擎未改**（`4c2034bf…`）。

## 摘要

| Owner 任務 | 結果 |
|---|---|
| 1. 接手實作 | 3 個修正 commit ＋ 1 個預先登記 commit（`2ccb573`、`af6f5e6`、`be05fa6`、`2d2123c`），見第七節 |
| 2. 以整合的 3.12 環境為開發基線 | 在雲端主機以 `requirements-py312.lock` 重建 Python 3.12 venv，完整回歸 **1,506／50／0**，與本機 agent 的結果相同 |
| 3. 處理遷移風險，尤其 PYSEC-2022-252 | **已關閉**：查清是通報範圍造成的誤判，仍以自有小型客戶端取代並修掉兩個實際缺陷；pip-audit **1 → 0** |
| 4. 獨立驗證相容性並保留回滾 | 完整回歸、Node 12／12、真瀏覽器 4／4、Mermaid SRI 獨立核對；本輪修改都是可單獨 revert 的 commit |
| 5. 環境穩定後繼續科學可靠性 | 預先登記並執行「天花板分析」；寫好 Gate D 協議草案，等 Owner 核准模型 |
| 6. 修正已驗證的 P0／P1 | 3 個 P1 已修（第三、四節）；P0 0 個 |
| 7. 改變分類前先設計實驗 | 分類行為沒有改；天花板分析先提交協議再執行（`2d2123c`） |

## 一、3.12 開發基線

- 雲端主機 Python 3.12.3，用整合分支的鎖定檔以 `--require-hashes --no-deps --only-binary=:all:` 安裝 111 個套件。
  只少 torch／torchvision：它們來自 PyTorch CPU index，本環境代理回 403。
- 完整回歸（test/unit、tests、test/integration_e2e、test/integration_smoke）：**1,506 通過／50 略過／0 失敗／0 錯誤**。
  與本機 agent 在完整 Docker 映像中的 1,506／50／0／0 完全相同（不同機器、不同 3.12 修補版本、沒有 torch）。
- 50 個略過與本機相同類別：34 個 NLTK、13 個 WordNet、2 個 Windows 路徑別名、1 個需要正式 DB。

## 二、deep-translator PYSEC-2022-252（已關閉）

**通報本身**：PyPA 官方資料（`pypa/advisory-database` 的 `PYSEC-2022-252.yaml`）寫明受影響版本只有 `1.8.5`，
是 2022 年帳號被盜後上傳的惡意版。但它的範圍寫成「1.8.5 起，沒有修復版」，所以之後的每一版都被掃描器標記。
PyPI 的釋出紀錄中 1.8.5 已不存在（1.8.3 → 1.9.0），我們鎖定的 1.11.4 是 2023-06-28 上傳。

**我核對的事**：1.11.4 wheel 的 sha256 與鎖定檔相同；wheel 安裝不執行程式碼；程式碼中沒有 subprocess、exec、base64；
讀環境變數只發生在其他翻譯服務的 API 金鑰預設參數。所以「被駭版本」這件事對 1.11.4 不成立。

**仍然選擇移除，因為發現兩個實際缺陷**：

1. Google 請求**沒有逾時**。連線卡住時，翻譯工作會一直等。
2. 空譯文會被當成功。批次路徑把它直接寫進譯稿；標點相同時它回傳 `None`，譯稿會出現字面 `None`。
   重現結果：`['好句子。', '', 'None']`，應該是 `[未翻譯]` 標記。

**取代方式**：`app/core_pro/literature/literature_quick_google.py`（約 100 行，只用標準函式庫的 HTMLParser 與 requests）。
協定完全相同（GET `translate.google.com/m`，參數 tl、sl、q；先找 `div.t0`，再找 `div.result-container`），
每次請求逾時 (5, 30) 秒，找不到譯文或譯文是空的就報錯，讓呼叫端走既有的逐段補救與 `[未翻譯]` 標記。

**差異測試**（離線，同版本 hash 安裝的 deep-translator 1.11.4 ＋ bs4 4.15.0）：2,000 個隨機 HTML ＋ 13 個手寫案例 ＋ 7 種 HTTP 狀態 ＋
3 種長度。送出的網址與參數全部相同；**呼叫端層級的行為 0 差異**。唯一的值差異是 488 個「譯文為空」的案例，
舊版回傳 `''`、新版報錯，這正是要修的缺陷。

**結果**：鎖定檔 113 → 110 項（移除 deep-translator、beautifulsoup4、soupsieve，沒有其他套件依賴它們）。
pip-audit：舊鎖定檔 111 個（不含 torch）**1 筆**（PYSEC-2022-252）→ 新鎖定檔 108 個 **0 筆**。torch 2.13.0、torchvision 0.28.0 在 PyPI 也是 0 筆。

**沒驗到的**：本環境連不到 translate.google.com（代理 403），所以新舊實作都沒有做線上實測。線上 Google 改版的風險和原本相同。

## 三、測試會寫入 repo 的 `data/`（P1，已修）

正式主機上 `./data` 就是研究資料 volume。在那份 checkout 跑完整回歸，會在 `data/` 建立 **23 個項目**：

| 來源 | 寫入 |
|---|---|
| `test_routes_smoke.py`、`test_literature_flowb_refactor.py` 用 `create_app({"TESTING": True})` 沒給 DB | `data/roothinks.db`、`manu_core.db`（含 WAL）、`.roothinks-runtime/` 租約、`.schema_fix.lock` |
| 任何 `create_app()` 的預設值 | `data/sys/llm_match.db`、`data/_logs/system/runtime.log` |
| Flow B 除錯輸出（產品程式） | `data/<專案>/literature/papers/<paper>/06_translates/reflow/_debug/*.txt`（含論文內容的 LLM 原始回覆） |

用 Python audit hook 攔截每個寫入並印出呼叫堆疊，逐一確認來源。

**修正**：

- 根目錄 `conftest.py`：執行前後比對 `data/` 的檔案清單、大小、mtime，有任何新增、修改或刪除就讓整次執行失敗（只做 stat，沒有略過開關）。
  `pytest.ini` 只用來固定 rootdir，讓單跑一個檔案或子目錄時也會載入這個守門。
- 同一個 conftest 在測試期間把 `create_app()` 的預設 log、LLM DB、literature `DATA_ROOT` 導到 tmp。產品預設值不變。
- 兩個 smoke 測試改用 tmp SQLite（只改準備，斷言不變）。
- **產品缺陷**：`literature_flowb_helpers._flowb_write_debug_reply` 用寫死的 repo `data/`，Flow B 其他部分用 `literature_routes.DATA_ROOT`。
  正式環境剛好同一路徑，但只要 `DATA_ROOT` 指到別處，含論文內容的原始回覆就會外漏到 repo。先寫紅測試，再改成跟著 `DATA_ROOT` 走。
- 瀏覽器 fixture 的 log 也改到它自己的 tmp。

**紅 → 綠**：加守門後，修正前 1,524 通過但整次失敗（列出 23 項）；修正後 1,525 通過、`data/` 完全沒有被建立。
另驗證：從子目錄單跑時守門會載入；`data/` 原本有檔案時，測試只要改到它就會失敗。

## 四、整合分支的產品程式審查

逐一審查 `literature_nllb.py`（Transformers 5 原生 generate）、`literature_stacka.py`（離線不下載）、`google_driveapi.js`、
`manuscript_image.js`（Mermaid 延遲載入、錯誤改純文字）、`manuscript_wsui.js`（Drive 回應比對專案與章節）、模板（Plotly 只留 PAQ、
Google SDK 未設定不載入）、`app/__init__.py`、兩支 bake 腳本。

- **沒有發現缺陷。** Plotly 只有 `paq.html` 用到，載入順序正確；`tojson` 輸出到 JSON script 區塊是安全的。
- Mermaid 10.9.6 的 SRI 我**獨立**核對：npm tarball 的 sha512 與 registry 相同，`dist/mermaid.min.js` 的 sha384 與程式碼相同。
- Node 行為測試 12／12 通過。
- 真瀏覽器 4 次（伺服器 Python 3.12.3）全部 passed，檢查數與 I12 相同（自主性 4、科學 8、資產 4×2）。
  **不服務 plotly 時外部失敗從 I12 的 3 次變成 0 次**（Plotly 已移出寫作頁），Google SDK 未設定時也不再被請求。

## 五、科學可靠性：天花板分析（預先登記，已執行）

協議 `2d2123c` 先提交，再執行。問題：就算剖析完美、不放寬任何保守閘門，最多能多救回幾個判定？

| gold 標籤 | 已正確 | 結構可救（H1） | 被保守閘門擋下（H2） | 沒檢索到（H3） |
|---|---:|---:|---:|---:|
| SUPPORT（124） | 1 | **99** | 6 | 18 |
| CONTRADICT（64） | 0 | **50** | 4 | 10 |

- **瓶頸明確在語意／結構比對**：188 個中有 149 個的證據句已在前 3 名，只是句型比不上。檢索（28）與保守閘門（10）不是主要問題。
- 依預先固定的規則：H1 ≥ 6，所以 Gate D 值得在 Owner 核准模型後實測。I13 在這 149 個裡只多救回 1 個，還是假 frame。
- 事後描述（未預先登記）：H1 裡有 15 個主張另帶缺條件、反證線索或因果未建立等發現，嚴格天花板約 134。
- 這是已曝光 dev 上的診斷，不是科學驗收。分類行為沒有改。

**環境變化**：spaCy 模型的 GitHub release 連結現在回 302（可取得）。I12 記錄的是不可達。
所以 Gate D 選項 1 **已不需要擴大網路**，但依規定，新模型與新相依仍要 Owner 核准。協議草案：
[GATE_D_PROTOCOL_DRAFT_2026-10-26.md](GATE_D_PROTOCOL_DRAFT_2026-10-26.md)（沿用 I13 閘門，G5 加嚴為分母 ≥ 10，新增只報告的 G9 成本）。

## 六、回歸結果

| 時點 | 環境 | 結果 |
|---|---|---|
| 起點 `29e87c6` | 雲端主機 Python 3.12.3、整合鎖定檔 | 1,506／50／0／0 |
| deep-translator 取代後 | 全新 venv、新鎖定檔 | 1,525／49／0／0（多 18 個新測試；1 個需要正式 DB 的測試因前一輪外漏的 `data/roothinks.db` 而執行） |
| 加守門、修正前 | 同上 | 1,524 通過，但**整次失敗**（23 項寫入 `data/`） |
| 修正後 | 同上 | 1,525／50／0／0，`data/` 未建立 |
| 最終 | 見機器證據 | FINAL_RESULT |

## 七、P0／P1 發現

| 級別 | 問題 | 狀態 |
|---|---|---|
| P1 | 批次翻譯把空譯文（甚至 `None`）寫進譯稿，沒有 `[未翻譯]` 標記 | 已修 `2ccb573` |
| P1 | Google 翻譯請求沒有逾時，工作可能無限等待 | 已修 `2ccb573` |
| P1 | 完整回歸會在 repo `data/` 建立正式 DB 與 23 個項目（正式主機上就是研究資料） | 已修 `af6f5e6`，並加守門 |
| P2 | Flow B 除錯輸出不跟 `DATA_ROOT` 設定走 | 已修 `af6f5e6` |
| P2 | 瀏覽器 fixture 的 log 寫到 checkout | 已修 `be05fa6` |
| P0 | 無 | — |

## 八、剩餘風險

1. **線上 Google 翻譯沒有實測**（本環境 403）。協定與舊版相同，但 Google 若改頁面，兩者一樣會壞。
2. **`google.generativeai` 官方已停止支援**（測試中出現 FutureWarning）。要換成 `google-genai` 是新相依，需另案。
3. `datetime.utcnow()` 在 3.12 已棄用（約 1,290 個警告），尚未移除，暫不影響執行。
4. 本機 agent 列的未驗事項仍然未驗：Google 真帳號 OAuth／Picker、真論文品質、正式容量、正式資料還原、獨立人類稽核。
5. torch／torchvision 在雲端沒有安裝；模型相關驗證以本機 agent 的完整映像證據為準，我沒有重做。
6. `validation/python312/requirements.*` 是本機 agent 的舊候選，仍列 deep-translator；正式建置不使用它，保留作歷史證據。

## 九、自我稽核

1. **我沒有線上驗證新的 Google 客戶端。** 差異測試只證明「同樣的 HTML 得到同樣的結果」，不證明 Google 現在的頁面長這樣。
2. **守門會讓「在有資料的 checkout 跑測試」直接失敗。** 這是刻意的；如果開發者同時在同一份 checkout 跑服務，也會被擋。
3. **conftest 的導向會隱藏「新測試忘了設 tmp」的寫法**。但守門仍會抓到任何真的寫進 `data/` 的動作，所以不會變成無聲外漏。
4. **天花板分析的 H1 是樂觀上限**，而且 gold 是已曝光 dev。它只回答「值不值得做」，不回答「做了會多好」。
5. 本輪沒有重建 Docker 映像（PyTorch index、Debian snapshot、HuggingFace 在本環境不可達）。變更都不影響 Dockerfile，
   但鎖定檔少了 3 個套件，**正式映像需要在可連網環境重建一次**確認。

## 十、需要 Owner 決定的事

1. **Gate D：是否核准 spaCy 3.8.16 ＋ `en_core_web_sm` 3.8.0 用於隔離實驗？**（我建議核准。）
   理由：瓶頸已量化在語意比對（149／188），規則式兩次失敗；模型現在不需擴大網路即可取得。代價：新相依與模型授權要核實。
2. **正式 3.12 遷移的下一步**：deep-translator 這個 NO-GO 理由已關閉。剩下的是獨立稽核、Google 真帳號 OAuth、
   可連網環境重建正式映像。我建議請獨立稽核審本分支，同時安排一次可連網的 staging 重建。

接手：[CLOUD_PRIMARY_HANDOFF.md](CLOUD_PRIMARY_HANDOFF.md)。
