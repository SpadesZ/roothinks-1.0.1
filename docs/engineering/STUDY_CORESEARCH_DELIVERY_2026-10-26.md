# Study 人機共研升級：交付報告（停在 Owner Gate）

日期：2026-10-26。分支 `codex/study-coresearch-20261026`（從 `27f5862` 開出，只推個人倉庫）。**沒有 merge、沒有部署。**
設計與驗收標準：[`STUDY_CORESEARCH_DESIGN_2026-10-26.md`](STUDY_CORESEARCH_DESIGN_2026-10-26.md)，在改產品碼之前 commit（`42b4e13`）。

## 1. 做了什麼

| 優先序 | 結果 |
|---|---|
| 1 盤點 | 找出 5 個最高影響缺口：原文無錨點、AI 回答無可查引文與不確定性、筆記與證據斷開、**頁碼錯位**、Study 沒有人類回饋管道（詳見設計文件 §1） |
| 2 錨點契約 | `roothinks.study.anchor/1`：論文、來源版本（內容 SHA-256）、頁、段落、原文／譯文側、引文與位置；**由伺服器產生與驗證**，狀態 `exact／relocated／ambiguous／not_found` |
| 3 垂直切片 | 選字 →「解釋／批判」→ 結構化回答（AI 解讀、已核對原文引文可點回原文、被排除的引文、AI 自評與系統檢查分開）→ 可編輯草稿 → **明確接受**才存成證據筆記 |
| 4 人類回饋 | 證據筆記可留言、請求審閱；權限全部沿用既有 Study ACL 與教授審閱門檻 `_require_review_project`；導師工作台新增「Study 審閱請求」區塊 |
| 5 互動與清楚度 | 頁碼順序修正；選字動作列（捲動時跟隨）；證據定位會捲動、外框與引文高亮；草稿存在本機可在重新整理後復原；衝突（舊版本）時保留草稿並提示；Tutor 標題列不再擠壓 |

重用而非重建：Tutor（同一 `task_7_qachat` 綁定與對話保存）、筆記的 `_rev` CAS 與 `.history`（抽成 `mutate_versioned_json`，`check_and_save_json` 語意不變）、
全文載入（抽成 `_read_fulltext_source`，閱讀器與錨點驗證共用編號）、既有 Study 與導師權限。沒有新增資料表、沒有新相依。

## 2. 驗收結果

| 編號 | 結果 | 證據 |
|---|---|---|
| AC1 錨點屬性＋頁碼順序 | 通過：6／6 列帶屬性；每列都在自己頁碼標記之後 | 瀏覽器 `01-reader.png` |
| AC2 選字動作＋伺服器驗證 | 通過：3 個動作；選段 `exact` | `02-selection-toolbar.png` |
| AC3 假引文不成為證據 | 通過：證據 1、排除 1；引文全被排除的論點不預設進草稿，也沒存進筆記 | `03-structured-answer.png`、`05-draft.png`；單元測試 |
| AC4 點證據回原文 | 通過：同一 `data-ref`、在畫面內、引文高亮 | `04-evidence-revealed.png` |
| AC5 不確定性兩欄 | 通過 | `03-structured-answer.png` |
| AC6 接受前不存檔 | 通過：草稿編輯中與重新整理後伺服器 0 則；接受後 1 則，三類別正確，接受者有紀錄 | `05`、`06`；單元測試 |
| AC7 不可偽造原文事實、不可覆蓋 | 通過：6 種偽造情境 400；舊 `base_rev` 409 且內容不變、歷史快照存在 | 單元測試 |
| AC8 原文重跑後不假裝 | 通過：`relocated＋version_changed`；文字刪除後 `not_found` | 單元測試 |
| AC9 權限 | 通過：隊友可留言；viewer 寫入 403；外人 403；非專案成員的教授 403；教授只能在已請求審閱的筆記留言 | 單元測試＋`08`、`09` |
| AC10 舊流程不變 | 通過：不帶 `mode` 的 Tutor 回應與改版前相同；既有 Study／導師／版本存檔測試全過 | 瀏覽器＋回歸 |
| AC11 沒有教授或證據也能存與匯出 | 通過：全文讀不到時存成「無已驗證證據」；Markdown 匯出帶類別標籤 | 單元測試＋瀏覽器 |
| AC12 完整回歸 | 見 §4 | `pytest` |

## 3. 改版前後（同一合成站、同一合成論文、同一模型替身）

| 量測 | 改版前 `27f5862` | 改版後 |
|---|---|---|
| 帶錨點屬性的正文列 | 0／6 | 6／6 |
| 頁碼標記與內容順序 | 錯位（`Page 2` 在第 1 頁內容之前） | 一致 |
| 選字後可見的動作 | 0 | 3（解釋、批判、自己提問） |
| 選取送給 Tutor 的上限 | 200 字、無位置 | 1,400 字＋伺服器驗證的錨點 |
| 回答中可點回原文的引文 | 0 | 1（另 1 個假引文被排除並標示） |
| 不確定性 | 無 | AI 自評＋系統檢查 |
| AI 內容變成筆記的方式 | 手動複製到自由文字框 | 草稿→研究者編輯→明確接受；原文事實鎖定 |
| 人類回饋 | 無 | 成員留言、請求審閱、教授留言（只追加） |

截圖：`study_coresearch_evidence/before/`、`study_coresearch_evidence/after/`。

## 4. 測試

- 新單元測試：`test/unit/test_study_anchors.py`（20）、`test/unit/test_study_evidence_notes.py`（15）。
  紅色檢查：故意拿掉「必須明確接受」、「引文逐字核對」、「原文事實不可改寫」三條規則 → 6 個測試失敗；還原後全過。
- 既有 Study、導師、版本存檔相關測試：405 通過、4 略過。
- 完整回歸：（待填）
- 真瀏覽器：`STUDY_PHASE=after` 29 項檢查全過、0 頁面錯誤（Chromium 1243，本機 vendor 資產，外部請求 0 個被放行）。

重現：
```sh
python test/integration_e2e/study_coresearch_fixture.py &          # port 8894，資料在新 tmp
STUDY_PHASE=after NODE_PATH=/opt/node-tools/node_modules I12_VENDOR_DIR=<npm 發佈資產目錄> \
  node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/study_coresearch_browser.cjs
```

## 5. 限制（如實說明）

1. **教授的讀取範圍**：教授在專案中是 viewer，而既有 Study ACL 讓 viewer 以 GET 讀整個 Study。
   「只看已請求審閱的筆記」是導師介面與導師 API 的範圍，不是存取控制。要真正隱藏需要 Owner 決定修改 viewer 權限。
2. **教授不能在 Study 頁面內留言**：Study 的 POST 需要 editor；教授只能從導師工作台留言。這是刻意不放寬權限。
3. **模型替身**：瀏覽器與單元測試都用合成模型替身（固定一真一假引文）。**沒有用真實 LLM 量測**引文正確率或解釋品質；
   本輪只證明「系統會擋下對不上原文的引文」，不證明真模型的品質。
4. **錨點只在 Block 對照版有段落位置**：Reflow 版面選字沒有段落提示，靠伺服器全文搜尋（`relocated`，重複文字會 `ambiguous`）。圖、表、公式沒有逐字錨點。
5. **跨段落選取**只以第一段為依據（畫面會提示）。譯文選段的來源事實是整段原文，不是逐句對應。
6. **舊的 `localStorage` 標記**（`toggleHighlight`）沒有搬到伺服器，與證據筆記仍是兩套。
7. 「多個相同引文時用前後文挑選」只在同一段落內有效；DOM 的段落切分會丟掉分隔字元，前後文比對可能退回第一處。
8. 同專案的 editor 可以改寫彼此證據筆記的陳述（與既有自由筆記同為專案共用）；每次改寫都記錄 `edited_by` 並留歷史快照，舊分頁會 409。
9. 既有問題（不屬本輪）：沒有矩陣時頁面對 `/matrix/latest` 連續請求 4 次 404。
10. 本雲端環境沒有 CodeGraph 與 Second Brain 工具（`AGENTS.md` 要求的查詢無法執行），改以原始碼閱讀與測試驗證。

## 6. 邊界

沒有 merge main、沒有部署、沒有正式資料或 sealed holdout、沒有 LAVA-Cowork 變更、沒有新相依或新模型、沒有放寬任何既有權限、沒有弱化任何測試。
完整回歸結束後 repo 沒有 `data/`。

## 7. 建議的下一輪（需 Owner 決定）

1. **用真模型量測**：預先登記一份小協議（例如 30 個合成或公開論文選段），量測引文核對通過率、被排除比例與研究者評分；不達標就不宣稱品質。
2. **Viewer 權限決策**：是否讓 Study 的 viewer 只能讀「已請求審閱」的證據筆記（會改既有 ACL，需要 Owner 核准）。
3. **把 `localStorage` 標記遷移成伺服器錨點**，與證據筆記用同一契約；Reflow 版面補段落屬性。
4. **證據筆記進寫作流程**：在 Manuscript 引用證據筆記時沿用同一錨點，並在原文版本變更時提醒。
5. 修正 `/matrix/latest` 重複請求。

## 8. 暫停中的工作（Owner 中斷，未完成）

R2-3 Gate D spaCy 隔離評估、R2-4 Google 失敗行為與證據矩陣、翻譯逆向研究（Immersive Translate／BabelDOC／REA）都**沒有做**。
沒有下載 spaCy 或模型、沒有安裝 REA、沒有改 agent 設定。R2 的資料防護（R2-1、R2-2）已完成並推送（`aa8ff3e`）。
