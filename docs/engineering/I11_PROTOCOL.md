# I11 執行與評估契約

日期：2026-10-24。起點 `9deaa948b6a923c7a59f38df4ced756026b7a27d`（I10 最新已推送提交）。
隔離分支 `codex/sai-i11-semantic-and-browser`。I10 基線保持不動。

**本契約在任何量測之前固定並提交。** 之後的實作與量測都以此為準；要改門檻必須另立一輪，不得在看過結果後調整。

## Owner 核准的邊界（本輪據此執行）

1. 真瀏覽器驗證**已核准**，但只允許所需的三個 CDN 網域，**不得開放無限制網路**。保留真實結果與失敗，**不得用 mock 冒充通過**。
2. 專家任命與正式 holdout 評估**延後**。不得虛構 reviewer、不得開封 holdout、不得自訂科學接受門檻。
3. 持續實質工程工作，優先處理已量測的語意瓶頸，須有凍結基線、預先固定的評估協議、對抗案例與誠實的退步回報。
4. 研究者自主不可退讓：科學警訊**永遠不得**阻擋寫作、儲存、修訂或匯出。

## 問題定義（來自 I10 的量測，不是猜測）

I10 已量測：188 案有官方 rationale，檢索失敗 28，取回的 160 案中 **159 棄權**，
取回後語意失敗率 **99.375%**。取回成功後的棄權原因分布：

| 原因 | 次數 | 佔 160 案 |
|---|---:|---:|
| `unsupported_sentence_structure` | **130** | **81.3%** |
| `different_arguments_or_conditions` | 17 | 10.6% |
| `source_hedged` | 12 | 7.5% |
| `source_does_not_establish_claim` | 1 | 0.6% |

`unsupported_sentence_structure` 來自 `_relation_frame`：它要求句中**恰好有一個**出自約 40 字小型詞表的謂詞。
真實科學句常見的名詞化（`association`、`reduction`、`involvement`）、被動語態與多謂詞都會直接落空。
**這是本輪要攻擊的目標。**

## 凍結基線

| 項目 | 值 |
|---|---|
| 基線提交 | `9deaa948b6a923c7a59f38df4ced756026b7a27d` |
| 基線引擎 `app/core_pro/manuscript/scientific_advisory.py` | `4c2034bf8fdbf2ad49276d136a0d37c6201240bd2570bffca5b1fbc0f5f7bd82` |
| 基線引擎版本字串 | `advisory-rules-4` |
| 對照用 I08 引擎 | `e99d9bceb7c0ee69ab2d588181ec1148ef27d38206e09328a8318d4f35dc0a8a`（git `c33f258`） |

比較時以位元複製的凍結檔執行，不以「當前工作樹」當基線。

## 資料角色與時間

- SciFact dev 300 claims **早已曝光**，本輪所有使用皆為**開發診斷**，不得稱盲測。
  輸入與 gold 自公開 SciFact 決定性重建（I10 已核對與 I09 記錄一致）。
- 先產生全部預測，再讀 gold。封存輸入、引擎、輸出與工具 hash。
- UNKNOWN、ERROR 與難例全部留在分母。不得排除失敗案例。
- 真 holdout **不開封**。真人專家 gold 不存在，相關指標一律 NOT MEASURED。

## 方法（預先固定）

### 採用的語意資源

WordNet 3.0，經 NLTK 介面讀取本機資料（`wordnet.zip` SHA-256
`cbda5ea6eef7f36a97a43d4a75f85e07fccbb4f23657d27b4ccbc93e2646ab59`）。只用兩項能力：

1. **`morphy` 詞形還原**：取代現有的土法去尾（`w[:-1] if len(w)>5 and endswith('s')`）。
2. **派生關係還原名詞化謂詞**：`derivationally_related_forms()` 取回 POS 為動詞的形式。

### 刻意不採用的能力（已先量測，結果為負）

**WordNet 反義關係不用。** 實測結果：

| 詞 | WordNet 反義 | 本專案需要的對立 | 判定 |
|---|---|---|---|
| `increase` | `decrease` | `decrease` | 正確 |
| `activate` | `inactivate` | `inhibit` | **錯** |
| `promote` | `demote` | `suppress` | **錯** |
| `inhibit` | （無） | `activate` | **缺** |
| `suppress` | （無） | `promote` | **缺** |
| `improve` | （無） | `worsen` | **缺** |

8 個關鍵謂詞中只有 2 個可用。**因此方向／sign 判定維持現有人工整理的關係族表，
不得聲稱方向對立已由詞彙資源支撐。** 這是負結果，保留在本契約內。

另：`causal` 與 `role` 在 WordNet 中**沒有**到 `cause` 的派生連結，
所以 I10 的案 674（`has no involvement` vs `has a causal role`）**預期仍無法解析**。
本輪不為該單一案例硬加詞條。

### 不變項（硬性）

- 檢索完全不動：候選集、BM25 參數、前三句預算（limit=3）全部不變。
  **定位 precision／recall 必須與凍結基線完全相同**，否則視為實作錯誤並回滾。
- `required_conditions`、數值、模態、外推、因果等既有警訊邏輯不動。
- WordNet 為**選用相依**。資料不可用時，引擎必須退回與凍結基線**逐案完全相同**的行為。
  產品 `requirements.txt` 本輪**不新增相依**；是否納入正式相依是 Owner 決策。

## 採用門檻（在看到任何 dev 結果之前固定）

依序檢查，任一項不滿足即**不採用**，但實作與量測結果保留為負結果：

1. **硬性閘門**：既有全部對抗控制通過，無例外。含 I09 的 7 項 scope 控制與 3 項 polarity 控制、
   I10 的 18 項線索控制、30 項 custody 控制，以及完整回歸 0 失敗。
2. **硬性閘門**：研究者自主不變。`blocks_writing` 恆為 `false`、`scientific_proof` 恆為 `false`、
   `original_text` 逐字不變。
3. **硬性閘門**：定位（檢索）指標與凍結基線**完全相同**。
4. **硬性閘門**：gold SUPPORT 上的**決定性**矛盾誤報必須維持 **0**。
   gold NEI 上的決定性判定（SUPPORT 或 CONTRADICT）不得增加。
5. **效果門檻**：`unsupported_sentence_structure` 在取回成功的 160 案中，
   必須從 **130** 降到 **115 或更低**（相對下降 ≥ 11.5%）。
   低於此幅度視為效果不足，不採用。
6. **退步門檻**：SUPPORT 與 CONTRADICT 的 one-vs-rest **FP 合計不得增加**。
   任一類 TP 下降即為退步，必須與 recall 並列回報，不得以單一平均分掩蓋。
7. 若第 5 項達標但第 6 項退步，**不採用**，保留為實驗能力，產品維持凍結基線行為。

**不得為了過門檻而硬寫個案答案、調整門檻、刪除失敗輪或改用別的資料。**
決定性準確度沒有提升時，不得宣稱科學能力改善。

## 對抗案例（實作前先寫）

新增控制必須涵蓋，且全部在實作之前寫好：

- 名詞化還原正確：`X reduces Y` vs `reduction of Y by X` 能對上，而 `X reduces Y` vs `reduction of X by Y` 不能。
- 名詞化不得越過關係族：`causes` vs `association` 仍須維持 `unknown` 並保留 `causality_not_established`。
- 被動語態：`X increased Y` vs `Y was increased by X` 能對上；`Y was increased` 無施事者時棄權。
- 被動不得吃掉真矛盾：`X increases Y` vs `Y was decreased by X` 仍須為 `possible_contradiction`。
- WordNet 不可用時逐案退回基線行為。
- 詞形還原不得把不同詞併成同一個（例如 `caused` 與 `causal` 不得被當成同一謂詞，除非 WordNet 真有連結）。
- 既有 I09／I10 控制全部重跑，不得放寬。

## 量測與回報

- 四組對照：凍結 I10、I11 候選，各自搭配不變的檢索。全部案例、全部錯誤、全部棄權保留。
- 分別回報：完整混淆矩陣、SUPPORT／CONTRADICT 的 TP／FP／FN／TN、UNKNOWN、
  棄權原因分布、取回後語意失敗率、定位 precision／recall、科學現象分項計數。
- 失敗輪與未採用的實驗引擎全部保留，輸出目錄 exclusive create，不覆寫。
- 條件／模態／因果品質、機率校準、跨領域專家 gold、真 holdout 成績一律 **NOT MEASURED**。

## 本輪不做

不聯絡任何人、不虛構 reviewer、不開封 holdout、不設科學接受門檻、
不 merge main、不部署、不讀或改 production data、不碰 LAVA-Cowork、
不開放無限制網路、不為通過而放寬任何既有 assertion。
