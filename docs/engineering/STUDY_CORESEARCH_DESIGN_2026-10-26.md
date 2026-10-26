# Study 人機共研升級：盤點、錨點契約與驗收標準

日期：2026-10-26。分支 `codex/study-coresearch-20261026`（從 `27f5862` 開出）。
本文件在**動手改產品碼之前**寫定；驗收標準之後不為了讓結果好看而修改。

## 1. 研究者旅程盤點（改版前，`27f5862`）

證據：`output/playwright/study-before-*`（合成站 `test/integration_e2e/study_coresearch_fixture.py`，瀏覽器腳本 `STUDY_PHASE=before`）。

| 步驟 | 現況 | 缺口 | 影響 |
|---|---|---|---|
| 讀全文 | 雙語對照（原文／譯文同列）；Block 與 Reflow 兩種版面 | 列上沒有任何來源識別（頁、`seq_id`、版本）：量測 `rows_with_anchor_attrs = 0` | 無法從任何回答或筆記點回原文 |
| 讀全文 | 頁碼標記 | **頁碼錯位**：段落卡片延後輸出，`Page 2` 標記出現在第 1 頁內容之前（截圖 `01-reader.png`） | 研究者會把引文記錯頁 |
| 選字 | 選取後只把前 200 字設成 Tutor context | 沒有可見的動作（量測 `selection_actions_visible = 0`）；沒有頁、段落、版本；長選取被靜默截斷 | 使用者不知道選字有作用；AI 收到的是無出處片段 |
| 問 Tutor | 純文字回答 | 回答不分「原文事實／AI 解讀」；沒有引文、沒有不確定性（量測 `answer_evidence_links = 0`、`answer_uncertainty_shown = 0`） | 無法檢查 AI 是否捏造；無法判斷可信度 |
| 問 Tutor | Grounding 只讀全文前 8 頁 × 10 塊、3,200 字 | 選取的段落若在後面，AI 根本看不到它的上下文 | 回答可能與選段無關卻看似有根據 |
| 記筆記 | 單一自由文字框（CAS＋歷史，已穩固） | 筆記與原文、與 AI 回答沒有連結；AI 回答只能手動複製 | 研究結論失去出處 |
| 標記 | `toggleHighlight` 存在瀏覽器 `localStorage` 全域鍵 | 不在伺服器、不分專案、換裝置就消失 | 不可作為研究紀錄（本輪不改，列為下一輪） |
| 協作 | 教授（viewer＋MentorLink）在導師工作台審 2C 稿件；團隊 editor 可進 Study | Study 沒有任何留言或審閱請求；導師工作台以 `notes=0` 讀學生資料，看不到 Study | 研究過程中的疑問無法請人看 |

**最高影響的五個缺口**（依序）：(1) 原文沒有可驗證的錨點；(2) AI 回答沒有可檢查的引文與不確定性；(3) 筆記與證據斷開；(4) 頁碼錯位；(5) 沒有對研究筆記的人類回饋管道。

## 2. 來源錨點契約 `roothinks.study.anchor/1`

設計參考 W3C Web Annotation 的「引文選擇器＋位置選擇器」：**以引文為準，位置只是提示**。錨點一律由伺服器產生與驗證；前端只送「選了哪一段的哪幾個字」，不能自己宣告位置或驗證狀態。

| 欄位 | 意義 |
|---|---|
| `schema` | 固定 `roothinks.study.anchor/1` |
| `pid`、`paper_id` | 專案與論文 |
| `source` | `flowa_fusion` 或 `flowb_trans`（與 `get_fulltext` 的 `fulltext_source` 同一套） |
| `source_sha256` | 該來源檔的 SHA-256；任何重跑解析都會改變它 = 論文版本 |
| `page`、`block_index`、`seq_id`、`ref` | 段落位置；`ref = p{page}-b{block_index}` 與閱讀器 `blockRefMap` 相同；`seq_id` 可能缺 |
| `side` | `original`（原文）或 `translation`（機器譯文） |
| `quote`、`quote_sha256` | 選取文字（原樣）與其雜湊 |
| `start`、`end` | 在該段落該側文字中的字元位置（伺服器算） |
| `prefix`、`suffix` | 前後各 32 字，用於消歧 |
| `status` | 伺服器驗證結果：`exact`／`relocated`／`ambiguous`／`not_found` |

驗證規則（`app/core_pro/study/study_anchors.py`）：
1. 引文必須在**指定段落**找到（空白正規化後比對）→ `exact`。
2. 找不到時在全文搜尋；唯一一處 → `relocated`（回報新位置）；多處 → `ambiguous`；沒有 → `not_found`。
3. `source_sha256` 與目前檔案不同時，狀態最多是 `relocated`，並附 `version_changed: true`。
4. 只有 `exact` 與 `relocated` 能成為可點擊的證據。選在譯文側時，**來源事實改用同段原文**，譯文只作輔助並標「機器譯文」。

## 3. Tutor 結構化回答契約 `roothinks.study.tutor/1`

`/api/study/chat` 多一個可選模式：`mode ∈ {explain, critique}` 且帶 `selection`。不帶時行為與改版前完全相同。

- 伺服器先驗證選取錨點；`not_found` 直接拒絕（409），不呼叫模型。
- 提供給模型的上下文＝選取段落＋同頁前後各 2 個正文段落，標為 `E1…En`（取代前 3,200 字的盲讀）。
- 模型要回 JSON：`interpretation`、`points[{text, evidence[{ref, quote}]}]`、`uncertainty{level, reasons}`、`open_questions`。
- **每個引文由伺服器逐字核對**：在所指段落原文中找得到才成為證據錨點；找不到的列入 `rejected_quotes`，畫面標「模型提供但原文找不到，已排除」。沒有任何已驗證引文的論點標「AI 推論（無原文支持）」。
- 模型沒回合法 JSON：整段當 AI 解讀，`uncertainty.level = unknown`，不產生任何證據。
- 不確定性分兩欄：**AI 自評**（模型說的）與**系統檢查**（已驗證引文數／被排除引文數、錨點狀態、是否來自譯文）。系統不替模型的自評背書。

## 4. 證據筆記契約 `roothinks.study.evidence_notes/1`

存在 `data/<pid>/study/<pid>_evidence_notes.json`，沿用 Study 筆記的 `_rev` CAS 與 `.history` 快照（`check_and_save_json` 同一套語意）。原本的自由筆記不動。

- 每則筆記的陳述分三類：`source_fact`（只能是伺服器驗證過的原文引文，不可編輯）、`ai_interpretation`、`human_hypothesis`。
- AI 草稿**永不自動存檔**。只有使用者按「接受並存成證據筆記」（請求帶 `accepted: true`）才寫入；寫入時伺服器重新驗證每個錨點並記錄當下狀態與 `accepted_by`／`accepted_at`。
- 使用者可把 AI 解讀改寫，改寫後要自己選類別；伺服器拒絕把非原文文字標成 `source_fact`。
- 沒有已驗證證據也能存（標「無已驗證證據」）；匯出 Markdown 不需要教授或任何科學驗證。
- 之後的修改需 `base_rev`；舊分頁送出會得到 409 與目前版本，不覆蓋。

## 5. 人類回饋（重用既有權限，不另建協作系統）

| 動作 | 誰可以 | 依據的既有權限 |
|---|---|---|
| 看、匯出證據筆記 | 專案 viewer 以上 | Study `before_request` → `enforce_project_ownership`（GET＝viewer） |
| 留言、請求審閱、撤回請求 | 專案 editor 以上（研究者、團隊） | 同上（POST＝editor）；限定編輯本來就進不了 Study 模組 |
| 教授看「已請求審閱」的筆記並留言 | 有 MentorLink 且雙方都是該專案非限定成員的教授 | 導師 API 既有的 `_require_review_project`（與教授提交 2C 意見同一道門） |

- 導師工作台與導師 API 只列出、只允許留言在研究者**明確請求審閱**的筆記上。
- **限制（如實說明）**：教授在專案裡是 viewer，而既有 Study ACL 讓 viewer 用 GET 讀整個 Study（自由筆記、對話、證據筆記）。
  本輪不改這條既有權限；「只看已請求審閱的筆記」是導師介面的範圍，不是存取控制。要真正隱藏，需要 Owner 決定改 Study 的 viewer 讀取權。
- 留言只能追加，不能改或刪別人的留言；教授留言標 `author_role: mentor`。
- 留言與審閱請求都不改變筆記的陳述與類別：人類回饋不會被系統升格成結論。

## 6. 驗收標準（量測方式寫在括號內）

| 編號 | 標準 | 方法 |
|---|---|---|
| AC1 | 閱讀器每個正文列都帶 `data-ref`／`data-page`／`data-side`；頁碼標記順序與資料一致 | 瀏覽器：列數＝帶屬性列數；頁碼序列遞增且每頁內容在其標記之後 |
| AC2 | 在原文選字後出現「解釋／批判」動作；送出後選取錨點經伺服器驗證為 `exact` | 瀏覽器＋單元測試 |
| AC3 | 結構化回答中，模型給的假引文 **0** 個被顯示為證據，且畫面明示被排除 | 合成模型替身固定給一真一假；瀏覽器數 `[data-evidence-anchor]`＝1、`[data-rejected-quote]`＝1 |
| AC4 | 點擊證據會捲動並標示原文同一列（`data-ref` 相同） | 瀏覽器 |
| AC5 | 不確定性同時顯示 AI 自評與系統檢查 | 瀏覽器 `[data-uncertainty]`、`[data-system-check]` |
| AC6 | 接受前伺服器上沒有證據筆記；按接受後才有，且三種類別標示正確 | 瀏覽器＋API |
| AC7 | 把 AI 文字標成 `source_fact` 被拒（400）；舊 `base_rev` 被拒（409）且不覆蓋 | 單元測試 |
| AC8 | 原文被重跑後，舊錨點顯示「版本已變更／找不到」，不會假裝仍是原文 | 單元測試 |
| AC9 | 隊友（editor）可留言；導師 API 只列出並只允許留言在已請求審閱的筆記；外人與非專案成員的教授 403；viewer 不能用 Study API 寫 | 單元測試＋瀏覽器 |
| AC10 | 不帶 `mode` 的舊 Tutor 請求、自由筆記、對話保存行為不變 | 既有測試全數通過＋新回歸測試 |
| AC11 | 沒有教授、沒有已驗證證據時，筆記仍可存與匯出 | 單元測試 |
| AC12 | 完整回歸 0 失敗；執行後 repo 沒有 `data/` | `pytest` 完整回歸 |

## 7. 本輪不做

- `localStorage` 標記搬到伺服器（列為下一輪；會與證據筆記合併成同一錨點模型）。
- Reflow 版面的錨點屬性（本輪錨點以 Block 對照版為主；Reflow 選字仍走伺服器全文搜尋，可得 `relocated`）。
- 圖表與公式的錨點（契約已保留 `ref`，本輪只驗證文字段落）。
- 導師工作台的大改版：只加一個「Study 審閱請求」區塊。
