# Study R2 交付報告（停在 Owner Gate）

日期：2026-10-26。分支 `codex/study-coresearch-r2-20261026`，起點 Study R1 `1e06c9f`。**沒有 merge、沒有部署。**
設計與驗收標準（先於程式碼 commit `679a29b`）：[STUDY_R2_DESIGN_2026-10-26.md](STUDY_R2_DESIGN_2026-10-26.md)。
規格入口：[docs/SAI.md](../SAI.md)；PA-01／PA-02／PA-03 為**提議修訂，待 Owner 核准**。

## 1. 安全與科學完整性發現

| 等級 | 發現 | 處理 |
|---|---|---|
| **P1** | 以「教授邀請」加入的教授（規格：只授予審閱權）可讀研究者的 Study 自由筆記、AI 對話、研究矩陣、全部證據筆記與匯出；導師 API `mentees/<id>?notes=1` 回傳自由筆記與整段對話 | 修正（R2-A）；屬 PA-01 契約收窄，待核准 |
| P1 | editor 可覆寫 owner 已接受的證據筆記內容，也可替別人的筆記請求審閱 | 修正（R2-C）；PA-03 |
| P2（科學完整性） | 引文比對用 NFKC：`10²` 與 `102`、`H₂O` 與 `H2O` 判為相符；沒有字界：`prove` 在 `improvement` 內相符；同段多處相同文字默選第一處並宣稱精確位置；欄位與畫面用「verified／已驗證」描述純文字比對 | 修正（R2-B）；PA-02 |
| 確認無問題 | 撤銷後、跨專案、外人的拒絕都正確；沒有 pid 的 Study 請求不會繞過 ACL（每條資料路由都要求 pid） | — |

沒有發現 P0。

## 2. 實作變更

| 項目 | 檔案 |
|---|---|
| 審閱身分判斷 `is_review_scoped`（viewer＋已接受未撤銷的教授邀請） | `app/security.py` |
| Study 白名單：審閱身分只可用論文與證據筆記端點；清單與匯出只含已請求審閱的筆記；頁面帶 `STUDY_REVIEW_SCOPE` | `app/core_pro/study/study_routes.py`、`app/templates/study.html` |
| 導師 API 對審閱身分專案不回傳自由筆記與對話 | `app/mentor/routes.py` |
| 錨點：`TEXT_FOUND`／`LOCATED`、嚴格正規化（不用 NFKC）、字界、太短引文、`ambiguous_in_block`、`occurrence`＋總數一致才採用、譯文推定標記 | `app/core_pro/study/study_anchors.py` |
| 筆記：`quote_match`、`claim_support`（研究者設定、記錄設定者、需有證據）、作者限定、建議／接受／不採用、修訂紀錄、R1 舊檔讀取轉換 | `app/core_pro/study/study_evidence_notes.py` |
| API：`/api/study/evidence_notes/suggest`、`/suggestion`；`update` 與 `review` 限作者 | `study_routes.py` |
| 前端：審閱模式；回答卡與筆記不再說「已驗證」；支持判斷選單；作者「改寫」／非作者「提出修改建議」；作者處理建議；協作紀錄；同段多處只標段落；送出 `occurrence` | `study_view.js`、`study_chat.js`、`study_core.js`、`study_evidence.js`、`mentor.js` |
| 真模型評估：協議、合成資料集與產生器、工具（乾跑／模擬／真模型）、盲化人類評分表 | `docs/engineering/STUDY_R2_EVAL_PROTOCOL_2026-10-26.md`、`evaluation/study_r2/`、`scripts/study_r2_eval.py` |

沒有新資料表、沒有新相依、沒有新模型。科學引擎 `advisory-rules-4` 未改。

## 3. 改版前後（探針 `test/integration_e2e/study_r2_probe.py`，真邀請流程）

| 路徑 | 教授 改版前 → 改版後 | 團隊 viewer（相容） |
|---|---|---|
| 自由筆記 | 200 讀到 → **403** | 200 → 200 |
| 對話清單／單一對話 | 200／200 讀到 → **403／403** | 200 → 200 |
| 矩陣 latest／history | 200 讀到 → **403／403** | 200 → 200 |
| 證據筆記清單／匯出 | 200 讀到未分享筆記 → **200，只含已請求審閱** | 200 全部 → 200 全部 |
| 導師 API `notes=1` | 自由筆記＋整段對話 → **不回傳（withheld）** | 403 → 403 |
| 全文、文獻清單、導師審閱 API | 200 → 200 | 200 → 200 |
| 撤銷後／跨專案／外人 | 403 → 403 | — |

| 語意／所有權 | 改版前 | 改版後 |
|---|---|---|
| `10²` 與 `102` 相符 | 是 | **否** |
| `prove` 在 `improvement` 內相符 | 是 | **否** |
| 同段兩處相同文字 | `exact`＋宣稱位置 | **`ambiguous_in_block`，不給位置** |
| `verified` 用詞 | 有 | **無** |
| editor 覆寫 owner 筆記 | 200，內容被取代 | **403**，改走建議 |
| editor 替 owner 請求審閱 | 200 | **403** |

原始輸出：`study_r2_evidence/probe-before.json`、`probe-after.json`。

## 4. 測試

- 新：`test/unit/test_study_r2.py` 26 項（B1–B12）；`test/unit/test_study_r2_eval.py` 6 項。
  紅色檢查：拿掉審閱範圍、導師 API 隱藏、同段不默選、嚴格正規化、作者限定五條保護 → 8 項失敗；還原後全過。
- R1 測試只更新用詞（`VERIFIED`→`TEXT_FOUND`、`evidence_status`→`quote_match`、`support`→`quote_match`、標籤文字），斷言沒有放寬，另加「輸出不得出現『驗證』」。
- 完整回歸與真瀏覽器：見 §6。

## 5. 真模型評估

- **未執行真模型**：環境沒有 RootThinks 的模型憑證或 `task_7_qachat` 綁定；代理程式自己的雲端憑證不是產品授權，沒有使用。
- 已完成：預先登記協議、30 段合成資料（SHA-256 `e4ef1759…`）、6 個機器門檻、人類評分規則、成本估計（輸入約 12,775 tokens、輸出上限 24,000）。
- 模擬（不是模型量測）：忠實替身 6／6 門檻通過；作假替身 0／6 通過（捏造引文 50%、亂加數字 100%），證明工具量得到要量的東西。

## 6. 驗證結果

| 驗證 | 結果 | 證據 |
|---|---|---|
| 完整回歸（程式碼 `4fb65e9`，Python 3.12.3） | **1,620 通過／50 略過／0 失敗**（R1 1,588＋本輪 32）；略過項與基線相同；執行後 repo 沒有 `data/` | `study_r2_evidence/full-regression.json` |
| 真瀏覽器（乾淨工作樹 `c100ac3`，Chromium 1243） | **40／40**：R1 的 29 項（用詞已更新）＋R2 11 項（審閱模式、只看分享筆記、不呼叫私人 API、非作者只能建議、作者接受、支持判斷署名、回答卡不用「驗證」） | `study_r2_evidence/browser/` |
| 主控台 | 只有預期的 403（viewer 寫入與外人讀取的負向測試）與既有的 `/matrix/latest` 404 | `browser/asset-route-log.json` |
| 驗收標準 B1–B14 | 全部通過；B13 只到「登記＋模擬」，真模型未執行 | 本報告 §3–§5 |

## 7. 未解風險與環境阻擋

1. PA-01／02／03 未經 Owner 核准；核准前這些行為只存在於隔離分支。
2. 教授在 Literature、PAQ 模組的讀取範圍沒有審查（本輪只處理 Study 與導師 API）。
3. 舊的 MentorLink 教授（沒有教授邀請紀錄、直接被加成 viewer）維持舊的完整讀取；是否遷移需 Owner 決定。
4. 專案共用的自由筆記仍可由任何 editor 改寫（既有功能，未變）。
5. `claim_support` 只記錄研究者判斷；系統沒有語意判斷能力，也不聲稱有。
6. 同段多處的 `occurrence` 依賴前端與伺服器看到的處數一致；不一致時退回只標段落（安全方向）。
7. 真模型品質、翻譯比較研究、Gate D、Google 線上服務與 3.12 正式遷移仍未完成（見 SAI 入口 §4）。

## 8. Owner 需要決定

1. 是否核准 PA-01（教授審閱範圍）、PA-02（證據語意）、PA-03（筆記所有權）。
2. 是否提供產品模型憑證與預算，執行已登記的真模型評估（30 次呼叫）。
3. 舊 MentorLink 教授是否也套用審閱範圍。
4. 是否把 Literature／PAQ 的教授讀取範圍列入下一輪。
