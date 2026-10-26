# Roothinks 現行規格與進度入口（唯一入口）

更新日期：2026-10-26。本文件是 SAI 的**唯一維護入口**：說明產品願景、目前真實狀態、未完成事項、路線圖與發佈邊界。
不另立 SAI_v1.3、SAI_FINAL 等競爭文件。

## 0. 文件權威與版本關係（不變）

| 文件 | 角色 | 狀態 |
|---|---|---|
| [SAI v1.1.0](spec/roothinks_SAI_v1.1.0_2026-10-23.md)（[PDF](spec/roothinks_SAI_v1.1.0_2026-10-23.pdf)） | 原規格：需求 ID（UX／COC／RES／COL／SUB／REL／GOV）、狀態契約、驗收案例 T01–T15 | **凍結**，不覆寫 |
| [v1.1.1 修訂條款](spec/roothinks_SAI_v1.1.1_2026-10-24.md) | 三種研究流程、P0-AUT 身分與權限、COC-05、REL-05、SCI-BASE | 規範，已核准 |
| [v1.1.2 科學完整性建議](spec/roothinks_SAI_v1.1.2_2026-10-24.md) | SCI-ADV：科學比較只作建議，不阻擋寫作／保存／匯出；I09 評估補充 | 規範，已核准 |
| [驗收矩陣](ACCEPTANCE_MATRIX.md) | 逐需求的證據、SHA、限制 | 隨證據更新 |
| 本文件 §8「提議修訂」 | 實作中發現需要改契約之處 | **待 Owner 核准；未核准前不是規範** |

- 原始基底：`C:\Users\Franky Kuo\Desktop\ai-system\文件\roothinks_SAI_v1.0.1.pdf`（2026-07-08，17 頁，原件保留）。
- 規格程式基線：`76edddf`。`main` 目前為 `9655546`（2026-10-02），**以下所有 SAI v1.1.x 工作都不在 `main`**（2026-10-26 以 `git merge-base --is-ancestor` 逐一核對）。
- 文件版號不代表應用程式已升版、已部署、全面安全或研究品質已驗收。

## 1. 產品願景與端到端體驗

RootThinks 的目標是一個**整合的人機協作科學研究工作區**：AI 幫助研究者理解、質疑、比較與發展科學想法；**科學判斷與決定權永遠在人**。

研究者預期的一天：

1. **建立研究、收集文獻**：從問題或一份 PDF 開始。系統保留原件、記錄為什麼納入或排除，不自動替研究者決定。
2. **雙語閱讀**：PDF 被轉成保留版面、公式與圖表位置的原文／譯文對照；可依頁碼與段落導覽，譯文永遠標示為機器翻譯。
3. **以證據為本的 Study**：在原文選一句，請 AI「解釋」或「批判」。AI 的回答把三件事分開——原文中逐字找得到的引文（可點回原處）、AI 自己的解讀、不確定性；
   系統只檢查引文是否存在，**不替研究者判斷引文是否支持論點**。
4. **跨文獻比較**：研究矩陣依研究者選定的基準與維度比較多篇論文；不相容的條件明示，缺資料不排名。
5. **研究者控制的證據筆記**：AI 回答只會成為草稿；研究者改寫、標示自己的假說與「是否支持」的判斷後，**明確接受**才成為筆記。原文事實鎖定不可改寫。
6. **人類回饋**：團隊成員留言或提出可追溯的修改建議，作者決定是否接受；作者可選擇請指導教授審閱特定筆記與稿件。教授只是選用角色，不是前置條件。
7. **寫作與輸出**：寫作時帶著被確認的主張、條件與來源；科學建議（含 UNKNOWN）只提示、不阻擋；稿件有版本，可輸出 DOCX 與研究紀錄。

**不變條件**：UNKNOWN 科學建議、缺少證據、或沒有教授，**都不能阻擋**研究者獨立寫作、保存或匯出（v1.1.1 三流程、v1.1.2 SCI-ADV §1）。

## 2. 狀態標記

| 標記 | 意義 |
|---|---|
| IMPLEMENTED | 程式在指定分支存在 |
| TESTED | 有自動測試或真瀏覽器證據（指出 SHA 與結果）；不代表品質量測 |
| NOT MEASURED | 品質或效果沒有用代表性資料量測 |
| FAILED RESEARCH | 預先登記的實驗未過門檻，保留為負結果，不整合 |
| DEFERRED | 已規劃、刻意延後 |
| BLOCKED | 缺授權、憑證、網路或 Owner 決定 |
| RELEASED | 已合併進 `main` **且**部署；分支測試通過不算 |

## 3. 目前實作狀態（2026-10-26）

全部位於一條線性的隔離分支堆疊上，**沒有任何一項是 RELEASED**。

| 里程碑 | 狀態 | 證據與 commit |
|---|---|---|
| Batch A／B、段落主張→證據→版本師生審閱、研究者自主三流程（I01–I06） | IMPLEMENTED、TESTED（指定切片） | [驗收矩陣](ACCEPTANCE_MATRIX.md) I05／I06 段、[Batch A/B 交付](engineering/SAI_DELIVERY_2026-10-23.md) |
| 科學完整性建議（SCI-ADV，I07–I08） | IMPLEMENTED、TESTED；科學品質 **未通過** | [I07 交付](engineering/SAI_SCIENTIFIC_ADVISORY_DELIVERY_2026-10-24.md)、[I08 交付](engineering/SAI_I08_DELIVERY_2026-10-24.md) |
| I09 專家完整性與範圍棄權 | TESTED（`2937722`，1,415／2／0）；SciFact 矛盾 TP 2→0、UNKNOWN 99.67%：**科學未通過** | [I09 交付](engineering/SAI_I09_DELIVERY_2026-10-24.md) |
| I10 證據鏈獨立重現、custody 修補 | TESTED（`ce9dc8c`，1,466／3／0） | [I10 交付](engineering/SAI_I10_DELIVERY_2026-10-24.md) |
| I11 真瀏覽器驗證；WordNet 語意實驗 | 瀏覽器 TESTED（`780e1e1`）；語意實驗 **FAILED RESEARCH**（不採用） | [I11 交付](engineering/SAI_I11_DELIVERY_2026-10-24.md)、[負結果](engineering/SAI_I11_SEMANTIC_NEGATIVE_RESULT_2026-10-24.json) |
| I12 瀏覽器範圍擴大、語意資源可行性 | TESTED（`567e630`，1,470／16／0）；語意資源 **未整合** | [I12 交付](engineering/SAI_I12_DELIVERY_2026-10-25.md) |
| I13 Gate A（擴充謂詞規則） | **FAILED RESEARCH**：決定性 2／300，未過 G6／G7，不整合 | [I13 交付](engineering/SAI_I13_DELIVERY_2026-10-25.md)、[Gate A 結果](engineering/SAI_I13_GATE_A_RESULT_2026-10-25.json) |
| I13 Python 3.10 上傳缺陷（每次上傳 503） | IMPLEMENTED、TESTED（`5b02f3c`） | 同上 |
| Python 3.12 開發環境 | TESTED：整合分支 `29e87c6` 1,506／50／0；本機正式映像建置 `84b0ea9` | [本機交付](engineering/PY312_LOCAL_FIRST_DELIVERY_2026-10-26.md) |
| Cloud R1：移除 deep-translator、Google 翻譯客戶端、測試資料隔離、科學天花板分析 | TESTED（`cfd488c`，1,525／50／0）；線上 Google **BLOCKED**（雲端 403） | [R1 交付](engineering/CLOUD_PRIMARY_R1_DELIVERY_2026-10-26.md) |
| R2-1／R2-2 資料防護：寫入 repo `data/` 前阻擋、執行前拒跑、Flow B debug fail closed | TESTED（`aa8ff3e`，1,553／50／0） | [接手文件](engineering/CLOUD_PRIMARY_HANDOFF.md)、`test/unit/test_data_safety_guard.py` |
| Study 共研 R1：伺服器錨點、解釋／批判、拒絕假引文、研究者接受的證據筆記、留言與審閱請求、頁碼順序修正 | TESTED（`1e06c9f`，1,588／50／0；瀏覽器 29／29） | [Study R1 交付](engineering/STUDY_CORESEARCH_DELIVERY_2026-10-26.md) |
| Study R2：教授審閱範圍、字面相符與主張支持分開、同段重複引文、筆記所有權與建議、真模型評估預先登記 | IMPLEMENTED、TESTED（程式 `4fb65e9`，1,620／50／0；瀏覽器 40／40 於 `c100ac3`）；PA-01／02／03 **待核准** | [Study R2 交付](engineering/STUDY_R2_DELIVERY_2026-10-26.md)、[設計](engineering/STUDY_R2_DESIGN_2026-10-26.md)、[證據](engineering/study_r2_evidence/) |
| Study 真模型回答品質 | **NOT MEASURED／BLOCKED**：協議、資料集（`e4ef1759…`）、工具與成本估計已登記；環境沒有產品模型憑證 | [評估協議](engineering/STUDY_R2_EVAL_PROTOCOL_2026-10-26.md) |

科學引擎 `advisory-rules-4`（`4c2034bf…`）自 I09 起未改；產品 SUPPORT TP 1、CONTRADICT TP 0、UNKNOWN 299／300（I13 記錄）。

## 4. 尚未完成與限制（必須保留）

- **科學推論品質未通過**必要門檻；真人專家 0 人、sealed holdout 未開封。單元測試數量或低誤報不得當作科學通過。
- **Study 真模型回答品質未量測**；R1／R2 只證明功能正確與假引文會被擋（用模型替身）。
- **Gate D（spaCy／依存剖析）未授權、未執行**；只有協議草案。不得下載新模型或整合剖析器。
- **REA／BabelDOC／Immersive Translate 比較研究未完成**（被 Owner 中斷）；BabelDOC 為 AGPL，整合前須授權審查。
- **線上 Google 翻譯與 Google OAuth 未驗證**（雲端請求 403）。
- **Python 3.12 正式環境遷移 NO-GO**：需要可重現的正式映像建置、真 Google OAuth、獨立稽核、相依檢查與回滾驗證。開發可用 ≠ 正式可遷移。
- **隔離分支不等於已合併或已部署**；本文件第 3 節沒有任何 RELEASED。
- 其他：Study 的 `localStorage` 標記尚未遷移到伺服器錨點；Reflow 與圖／表／公式錨點不完整；證據筆記尚未接到手稿出處；沒有矩陣時 `/matrix/latest` 重複請求。

## 5. 路線圖與發佈邊界（依相依順序）

| 階段 | 內容 | 前置條件 | 現況 |
|---|---|---|---|
| 0 獨立審查 | 對隔離堆疊（I09 → Study R2）做獨立稽核；Owner 核准 §8 提議修訂 | — | 等待 Owner／稽核 |
| 1 合併候選 | 選定要進 `main` 的範圍（例如資料防護、3.12 開發環境、Study R1＋R2），在乾淨環境重跑完整回歸與瀏覽器 | 階段 0 | 未開始；**不得自行 merge** |
| 2 品質量測 | Study 真模型評估（協議已登記）＋人類評分；翻譯比較研究 | 產品憑證與預算；評分者 | BLOCKED（憑證） |
| 3 正式遷移 | 3.12 正式映像、真 OAuth、回滾演練、相依稽核 | 階段 1；可連網環境 | NO-GO |
| 4 科學能力 | Gate D 或其他方法，先預先登記；真專家 gold 與 holdout | Owner 授權新模型／相依；專家 | DEFERRED／BLOCKED |
| 5 RELEASED | 合併＋部署＋部署後檢查 | 階段 1–3 | 無 |

每一階段只依其證據宣告完成；科學能力（階段 4）不阻擋前面的工程與寫作功能，但在通過前不得對外宣稱科學驗證。

## 6. 優化與交付規則（沿用）

實作前先讀需求 ID、現況證據與驗收案例；重用既有能力；不得以刪除原文、弱化納入規則、放寬權限或移除測試來達成新規格。
模型、網路、新相依與付費行為依 Owner 授權；測試只用合成或獲授權樣本與隔離資料。

## 7. 接手

最新工程狀態、環境與指令見 [接手文件](engineering/CLOUD_PRIMARY_HANDOFF.md)。

## 8. 提議修訂（待 Owner 核准；未核准前不是規範）

Study R2 實作時發現下列契約需要明確化或修改。程式已在隔離分支實作與測試，**只有 Owner 核准後才成為規範並可合併**。

| 編號 | 影響條款 | 提議內容 | 理由 |
|---|---|---|---|
| PA-01 | v1.1.1 P0-AUT「教授邀請只給全專案 viewer／審閱權」 | 教授邀請的審閱權包含：稿件審閱（不變）、論文本身、研究者**明確請求審閱**的 Study 證據筆記。**不包含**研究者的 Study 自由筆記、AI 對話、研究矩陣與未分享的證據筆記。一般團隊 viewer、舊 MentorLink、被 owner 改成 editor 的教授不受影響 | 改版前探針：教授可讀私人筆記、對話與所有筆記；導師 API 也回傳筆記與整段對話（P1） |
| PA-02 | v1.1.2 SCI-ADV §2（文字對齊不是科學證明） | 把「引文在原文中找到」（`quote_match`／`text_match`）與「引文支持陳述」（`claim_support`）定為兩個欄位；後者只能由研究者設定並記錄設定者；禁止用「verified／已驗證」描述引文比對；比對不得改變科學記號（不用 NFKC）；同段多處無法唯一定位時不得給精確位置 | 落實既有原則的資料契約 |
| PA-03 | v1.1.0 COL-03（可分辨誰的意見、誰可確認） | Study 證據筆記只有作者能直接改寫與請求審閱；其他成員提出只追加的建議，由作者接受或不採用；被取代的內容保留在修訂紀錄 | 改版前 editor 可覆寫 owner 已接受的解讀 |
