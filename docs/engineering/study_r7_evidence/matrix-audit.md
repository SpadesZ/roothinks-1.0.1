# R7 多篇研究矩陣：改版前稽核（程式 d5382c5，2026-11-02）

範圍：Study「比較矩陣」從按鈕到畫面的完整資料流。數字來自兩個量測：
- 離線量測 `audit_measure.py` 的結果在 `audit-before-measure.json`。它用 HEAD 的 MatrixEngine/Comparator，加上會記錄提示詞的替身 bus。
- 真瀏覽器量測 `before/matrix-r7-before.json` 與截圖，在合成站 `STUDYMX-p` 上跑。這是一個四篇論文的合成專案，比較器是替身，不呼叫模型。

截圖（改版前）：
- `before/before-00-legacy-latest.png`：開頁時自動載入的舊記錄
- `before/before-01-matrix-4-papers.png`：勾 4 篇後產生的矩陣
- `before/before-02-matrix-4-papers-full.png`：同一畫面整頁

## 1. 資料流

```
側欄「生成比較矩陣」 → StudyCore.runTask6()                 study_core.js:538
   送 {pid, paper_ids}，paper_ids 依 DOM 勾選框順序；沒送 criteria，也沒送基準
POST /api/study/compare → run_matrix_comparison()              study_routes.py:1451
   validate_id 失敗的 id「靜默略過」（continue，不記錄）
   直接讀 05_interprets/fusion/full_text.json；不用閱讀器的 _read_fulltext_source
     → 不讀 flowb 譯文檔，也不注入分割公式 → 段落編號不保證與閱讀器 p{page}-b{n} 一致
   所有 block content 以 "\n\n" 接成一個字串；標題取 fusion.title，沒有就用 paper_id
     → metadata.title 被忽略，畫面顯示 paper_id
MatrixEngine.generate_matrix(pid, papers, criteria)            study_matrix.py:46
   papers[0] = 基準；_prep()：字串 ≤12,000 字全取，否則頭／中／尾各 3,800 字
   ThreadPool(3) 對每篇 target 呼叫一次 Comparator（N 篇 = N−1 次模型呼叫）
   as_completed → 結果依「完成順序」append
   每次呼叫都把 paper id 寫進原始碼目錄的 app/core_pro/study/matrix_debug.log（已 gitignore，但無上限）
Comparator.compare_pair(base, target, criteria)                task_6comp.py:79
   提示詞：base['text'][:2500] + target['text'][:2500]；固定 5 維；criteria 只當「焦點」字串
   _normalize_result：只保留固定 5 維（另做前 2 字的模糊比對）
     缺的維度補 'N/A'＋「資料不足，待補充比較證據。」＋significance Low
     baseline_view／target_view 截 220 字、synthesis 截 260 字，沒有任何標記
儲存（route）
   data/<pid>/study/matrix_history/mx_<time>_<rand>.json＋latest.json＋latest_matrix.json
   全部失敗 → 502，不保存（正確）；部分失敗 → 207，保存
讀取
   /matrix/history、/matrix/<id>、/matrix/latest、rename、delete
   舊 papers_comp/*.json 以 legacy::<name> 唯讀
畫面 StudyView.renderMatrix(data)                               study_view.js:1018
   只畫 comparisons[0]（基準＋1 篇＋Synthesis 三欄）
   標頭寫「N+1 Papers」，標題卻是 `已比較 ${Math.min(n+1, 2)} 篇`
   儲存格再截 260／300 字加「...」；點儲存格把 AI 文字當作 chat context
   沒有證據、沒有狀態、沒有涵蓋說明
```

## 2. 截斷與科學影響（實測數字）

| 位置 | 截斷 | 實測 |
|---|---|---|
| task_6comp 提示詞 | 每篇 `text[:2500]` | 合成論文 `mx_b_annealing` 全文 3,187 字，送給模型 2,500 字（78%）。其中 Methods 252 字、Results 264 字、Limitations 135 字全部 **0 字**送達。提示詞紀錄也確認：提示詞裡沒有 "Samples were annealed for 20, 40, and 60 minutes"，也沒有 "18 percent"。模型卻仍被要求填「方法」「結果」「限制」三維 |
| MatrixEngine._prep | 字串 >12,000 字取頭／中／尾 3×3,800 | 之後又被 `[:2500]` 截掉，中段與尾段是**死碼**。4 萬字的論文只有前 2,500 字（6.25%）送達，而且永遠是標題、摘要、緒論 |
| _prep 的 list 分支 | 前 30 個 block | 路由永遠給字串，這個分支實際不會走到 |
| _normalize_result | 220／260 字 | 模型給 400 字 → 存 220 字；給 450 字 → 存 260 字。`flag_present: false`：完整文字不保存，也沒有截斷旗標 |
| renderMatrix | 260／300 字加「...」 | 只在畫面上截，沒有「展開」 |
| 基準全文 | 每對都重送一次 | 4 篇共 3 次呼叫，提示詞長度 4,756／2,632／2,610 字 |

科學影響：
- 方法、結果、限制寫在 2,500 字之後的論文，模型「看不到」卻要填這三維。輸出只能是猜測，或「N/A」。
- 「N/A」在畫面上與「論文沒報告」無法區分。
- 研究者看不到 AI 讀了哪些段落，因此無法判斷「沒看到」是不是「論文沒寫」。
- 220／260 字截斷會切掉數字、條件或否定句（例如 "...not tested at other temperatures"），而且不留痕跡。

## 3. 論文順序

- 前端送出的是 DOM（資料夾字母）順序，不是研究者勾選的順序；也沒有「基準」這個欄位。
- 實測：研究者依序勾 c→b→a→d（預期基準是 calibration），送出 `["mx_a_lowtemp","mx_b_annealing","mx_c_calibration","mx_d_position"]`。基準變成 77 K 的低溫論文，研究者不知道。
- comparisons 依完成順序排列。8 次重跑出現 **5 種不同順序**（`audit-before-measure.json` 的 `order_runs`）。
- 記錄只存 paper_ids，沒有欄位 id 與基準旗標。

## 4. 比較維度（criteria）

- 前端從不送 criteria，所以每筆記錄都是 `General`。
- API 接受任意字串，但提示詞永遠是固定 5 維；criteria 只出現在標題和「焦點」一句話。研究者不能自選或自訂維度。
- 模糊比對 `dim[:2] in k or k[:2] in dim` 可能把模型的「資料不足說明」之類維度誤對到「資料與實驗設定」。

## 5. 證據與原文

- 提示詞沒有要求引文，輸出沒有引文欄位，也沒有頁碼、段落。任何一格都無法回到原文。
- 模型宣稱的內容沒有任何伺服器核對。例如替身說「作者承認本文沒有做任何實驗」，這句被原樣保存。
- 點儲存格會把 AI 文字當成 chat 的 content，後續 Tutor 會把 AI 摘要當作「內容」。

## 6. 錯誤、部分失敗、缺資料

- 模型失敗時，該對是 `status=failed`，帶系統訊息；全部失敗 → 502，不保存（正確）。
- 部分失敗時畫面只畫 comparisons[0]：第一筆失敗會蓋住成功的其他筆，其他筆失敗則完全看不到。
- **缺資料被填補也被排名**：模型漏回的維度補成 `N/A` 與「資料不足，待補充比較證據。」（significance Low）。對照論文是 N/A 時，synthesis 與 significance 照樣保存。
  - 實測記錄 `before/mx_1793599192_a5d6f41e.json` 中，`mx_d_position`「結果與證據強度」的 target_view 是 `N/A`，synthesis 卻是「基準論文的結果明顯優於對照論文。」，significance 是 `High`。
- 條件不相容（77 K 對 300 K、mW/K 對 %）沒有任何欄位能表達，只能混在 synthesis 文字裡。
- `_parse_json` 的錯誤：回應是不帶 `json` 標記的 ```` ``` ```` 圍欄時，`text.split("```")[0]` 會變成空字串，有效回答因此被當成解析失敗。

## 7. 舊記錄格式（必須繼續可讀）

1. **matrix_history 記錄（R6 以前）**
   - 結構：`{matrix_id, pid, criteria, paper_ids, paper_titles, created_at, elapsed, display_name, is_legacy:false, data}`
   - `data = {pid, criteria, baseline{id,title}, comparisons[...], status, success_count, failure_count}`
   - comparisons 的每一項：`{target_paper_id, target_paper_title, comparison_points[5]{dimension, baseline_view, target_view, synthesis, significance}, overall_summary, analysis, status, quality_mode}`
   - 失敗項：`{error, target, analysis}` 或 `{error, status:"failed", target_paper_id, ...}`
2. **papers_comp v1.6**：`{criteria, baseline, comparisons[{target_paper_id|target_title|target, content|analysis}]}`，經 `_build_legacy_matrix_record` 包成 `legacy::<name>`。
3. **latest_matrix.json** 與 **matrix_history/latest.json**：都是記錄的完整複本。
4. **瀏覽器 localStorage** `study_matrix_cache_<pid>`：`{matrixId, updatedAt, data}`。
5. history 清單在 normal 分支用 `t.get('target_title') or t.get('target')`，漏了 `target_paper_title`，所以舊記錄沒有 paper_titles 時第二篇會顯示「Paper B」。

## 8. 改版方向（本輪實作）

- 研究者的勾選順序與明示的基準都存進記錄；欄位依記錄的 `papers[]` 排列，id 固定。
- 研究者可勾選預設維度或自填維度，維度隨記錄保存。
- 每格的引文由伺服器用 `study_anchors.resolve` 逐字核對（不做 NFKC）。
  - 找到的引文保存 page 與 ref，點了開閱讀器；找不到、太短或多處無法歸屬的引文標為「已排除」，不當作證據。
  - 「原文中找到」不等於「支持這個描述」。
- 每格有明確狀態：reported、not_reported、uncertain、not_comparable（附理由）、failed。
  - 缺資料不排名、不補值：伺服器丟掉 significance，缺資料或不可比較時隱藏「差異描述」。
  - 文字全部保存；只有超過安全上限才截，而且一定標記。
- 輸入改為依章節挑選：每篇在預算內優先取與所選維度相關的方法、結果、限制段落。
  - 記錄每篇實際送出的段落與頁碼，畫面標示「比較依據：p.x–p.y 共 N 段；未讀到的部分不代表論文沒有」。
  - 模型呼叫次數不變，仍是每篇對照論文一次。

## 9. 改版後（同一合成站、同一組替身）

真瀏覽器檢查 `after/matrix-r7-after.json`：**33／33 通過，0 個頁面錯誤**。

| 項目 | 改版前 | 改版後 |
|---|---|---|
| 勾 4 篇後畫面上的論文欄 | 2（基準＋comparisons[0]） | 4，依勾選順序 c→b→a→d；重新整理後順序與維度不變 |
| 基準 | DOM 第一篇（低溫論文） | 研究者勾的第一篇，或側欄「設為基準」；記錄存 `baseline_id` |
| `mx_b_annealing` 的 Methods／Results／Limitations 是否送給比較器 | 否（0 字） | 是（三段都在提示詞裡） |
| 模型呼叫 | 3 次 | 3 次（每篇對照論文一次，沒有增加） |
| 捏造的引文 | 沒有引文機制，捏造內容原樣保存 | 2 段捏造引文都被排除，不是證據；只有捏造引文的「有報告」降為「不確定」 |
| 缺資料 | `N/A`，評析照樣保存，significance=High | 「未報告」；不顯示差異描述，不排名 |
| 條件不同（77 K 對 300 K） | 無法表達 | 「不可比較」並附理由；不比較大小 |
| 截斷 | 220／260 字，靜默 | 全文保存；超過 4,000 字才截，一定標記；畫面折疊，可「展開」 |
| 比較依據 | 無 | 每篇列出頁碼範圍、段數、未提供的章節，並註明「未讀到的部分不代表論文沒有」 |
| 舊記錄 | 只畫第一個比較 | R6 記錄與 papers_comp v1.6 都能畫，並標「舊版・未核對」；N/A 顯示為未報告，當時的評析不顯示 |

改版後截圖（都在 `after/`）：
- `after-00-legacy-record.png`：舊版記錄
- `after-01-sidebar-setup.png`：側欄設定
- `after-02-matrix-4-papers.png` 與 `after-02b-matrix-4-papers-full.png`：4 篇矩陣
- `after-03-evidence-in-reader.png`：點證據後在閱讀器定位
- `after-04-reloaded.png`：重新整理後
- `after-05-phone.png`：手機寬度
