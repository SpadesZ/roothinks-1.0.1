# I13 Gate A：子句範圍關係框架的預先登記評估

日期：2026-10-25。起點 `29aefb0`（I12 最新已推送提交）。分支 `codex/sai-i13-gate-a-and-deps`。

**本契約在任何 Gate A 量測之前提交。** 方法、資料、指標與門檻都在此固定；看過結果後不得調整，
任何修改都必須另立一輪並另寫契約。

## Owner 決策（本輪據此執行）

- Gate A **核准**：預先登記、隔離的科學評估。量測決定性誤報、召回、棄權、否定／條件錯誤與
  跨子句假 frame，對照凍結基線；回報分母、退步與失敗案例。
- 已曝光的 SciFact dev 只是**開發證據**，不是獨立驗證。
- Gate B **延後**：不整合候選、不打包 PropBank I、不擴大網路權限。
- 研究者自主不可退讓；holdout 保持封存；專家驗證待定。

**因此不論結果如何，本輪都不整合任何東西。** 本評估只決定候選「是否值得帶到 Gate B 討論」。

## 凍結基線

| 項目 | 值 |
|---|---|
| 基線引擎 | `app/core_pro/manuscript/scientific_advisory.py`，`advisory-rules-4` |
| 基線 hash | `4c2034bf8fdbf2ad49276d136a0d37c6201240bd2570bffca5b1fbc0f5f7bd82`（自 I10 起未變） |
| dev 輸入（LF） | `ded7f378b97aca168037271d0725437b035c32f2ac033563d94122178ced1ce5` |
| dev gold（LF） | `4374b195bd49dd3e2202d013e593955e00e078b7bd6f564307d992d041f4681f` |
| 檢索 | BM25、前三句，**完全不變** |

## 候選（實驗專用，以附加原始碼產生，不修改產品檔案）

兩個變體，差別只在謂詞詞表，用來分離 PropBank（授權未釐清）的貢獻：

| 變體 | 謂詞詞表 |
|---|---|
| **C1** | VerbNet ∪ 基線關係族動詞（**不含 PropBank**） |
| **C2** | VerbNet ∪ PropBank ∪ 基線關係族動詞 |

### 何時啟動（保留全部既有保守閘門）

候選**只在**基線 `assess_relation` 以下列兩個理由棄權時才執行子句分析：
`unsupported_sentence_structure`、`different_arguments_or_conditions`。

基線的其他判定一律原樣保留，候選**不得覆寫**：`text_aligned`、`possible_contradiction`、
`unresolved_stance_scope`、`source_does_not_establish_claim`、`source_hedged`、
`significance_is_not_direction`、`different_quantities`、`different_relationship`、
`negated_direction_is_not_inverse`。`assess_claims` 的條件、數值、模態、外推、因果與
`usable` 需無缺漏條件的邏輯全部不變。

### 子句分析（固定，不得事後調整）

1. 前綴去除：與基線 `_relation_frame` 相同。
2. 斷詞：`re.findall(r"\w+|[,;:]", text)`；詞性：NLTK `averaged_perceptron_tagger_eng`（JSON 版，**不用 pickle 版**）。
3. 子句切點：`, ; :`，以及 `where which that while whereas although because when after before whether`；
   `and`／`or` 若其後 6 個詞內出現 `VB*` 詞性亦切。
4. 子句內謂詞：詞性為 `VB*`，且 WordNet `morphy(詞,'v')`（取不到則用原詞）在詞表內；
   **排除 `be`、`have`、`do`**（助動詞與輕動詞）。
5. **單一謂詞紀律以子句為單位**：子句內謂詞數不等於 1 就不產生 frame。
6. 論元：主詞為子句內謂詞之前的詞、受詞為之後的詞，正規化沿用基線 `_arguments`。
   被動：謂詞前（略過 `RB*`）為 `be` 的形式、謂詞為 `VBN`、且子句內謂詞之後有 `by` → 主詞取 `by` 之後、
   受詞取助動詞之前。**被動但沒有 `by` 施事者 → 不產生 frame。**
7. 否定：子句內謂詞之前出現 `not no never without neither`。
8. 關係：兩邊謂詞若都在基線關係族 → 沿用基線的族／方向邏輯（含 `negated_direction_is_not_inverse`）；
   否則**只有同一原形**才可比較（方向視為相同，否定不同即對立）；其他一律不可比。
9. 比對：主張的每個子句 frame 對來源的每個子句 frame，要求主客體**完全相同**且關係可比。
   只有對齊 → `text_aligned`；只有對立 → `possible_contradiction`；兩者都有或都沒有 → 維持基線棄權。
   新判定的 `reason` 分別記為 `clause_ordered_predicate_arguments`、`clause_opposed_predicate_or_negation`。

### 必須先寫的對抗案例

子句範圍正確、跨子句不得拼接（含仿案 837 的結構）、子句內被動、否定對立與
`negated_direction_is_not_inverse`、同原形非關係族動詞、輕動詞排除（案 674 仍不解）、
弱語氣與未建立證據等既有閘門不得被覆寫、條件不同時最終仍為 `unknown`、研究者自主不變、
NLTK 資料不可用時逐案退回基線。

## 指標（全部附分母）

- 完整混淆矩陣；SUPPORT 與 CONTRADICT 的 TP／FP／FN／TN（gold 分母：SUPPORT 124、CONTRADICT 64、NEI 112）。
- **決定性輸出**＝預測為 SUPPORT 或 CONTRADICT。**決定性誤報**＝決定性輸出與 gold 不符
  （含 gold NEI 上的任何決定性輸出）。**決定性 precision**＝正確決定性輸出／全部決定性輸出。
- 棄權：UNKNOWN 數與比例、棄權原因分布。
- 定位：前三句與首句 precision／recall，**必須與基線完全相同**。
- **否定錯誤**：候選新增的決定性輸出中，其判定依賴否定對比者，逐案對 gold 標示對錯。
- **條件錯誤**：決定性輸出所屬主張帶有 `missing_condition`、`numeric_mismatch`、`modality_mismatch`、
  `unsupported_generalization` 任一警訊者（依構造應為 0，仍須量測）。
- **跨子句假 frame**：操作者稽核（**不是獨立專家**），稽核時**不看 gold 標籤**：
  - (a) 每一個參與候選新增決定性輸出的 frame（主張與來源兩側）；
  - (b) 以固定種子 `13` 從檢索成功的首候選來源句所產生的全部子句 frame 中抽 40 個，在稽核前抽定。
  - 評分：`CORRECT`（主客體都是該謂詞在同子句內的真論元）、`CROSS_CLAUSE`（至少一個論元來自別的子句）、
    `WRONG_ROLE`（同子句但主客體顛倒或不是該謂詞的論元）、`UNSCORABLE`。
- 每一個變動的預測、每一個決定性誤報都列出主張、來源句與 frame。

## 帶到 Gate B 的門檻（在看到任何結果之前固定）

| 編號 | 門檻 | 類型 |
|---|---|---|
| G1 | 產品引擎的既有對抗控制在候選引擎下全部通過（`test_scientific_scope`、`test_scientific_advisory`、`test_scientific_leads`） | 硬性 |
| G2 | 研究者自主不變：`blocks_writing` 恆 false、`scientific_proof` 恆 false、`original_text` 逐字不變 | 硬性 |
| G3 | 定位指標與基線完全相同 | 硬性 |
| G4 | gold SUPPORT 上的決定性 CONTRADICT 為 **0**，且 gold CONTRADICT 上的決定性 SUPPORT 為 **0** | 硬性 |
| G5 | 決定性 precision **≥ 0.80**（分母含 gold NEI 上的決定性輸出） | 效果 |
| G6 | SUPPORT TP ＋ CONTRADICT TP **≥ 6**（基線為 1，即至少多 5 個） | 效果 |
| G7 | 稽核 (a) 中 `CROSS_CLAUSE`＋`WRONG_ROLE` **≤ 10%**；稽核 (b) 中 `CROSS_CLAUSE` **≤ 20%** | 效果 |
| G8 | 新寫的否定與條件對抗案例 **100%** 通過 | 硬性 |

變體各自判定。**G1–G8 全部通過才稱「值得帶到 Gate B 討論」**；即使通過，也只是在已曝光資料上、
由實作者自行稽核的開發證據，**不是科學驗收**，也**不構成整合核准**。
任何一項不通過即為負結果，完整保留。

## 不做

不整合、不改產品引擎、不打包 PropBank I、不擴大網路、不開 holdout、不虛構專家、
不為過門檻調整方法或門檻、不刪除失敗輪。
