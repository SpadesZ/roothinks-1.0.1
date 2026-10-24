# I10 交付：I09 證據鏈獨立核對與反證線索保留

日期：2026-10-24。起點 `900394e32279673ae1520e23e22a34fcafe27573`（`codex/sai-i09-scientific-foundation` 最新已推送提交）。
隔離分支 `codex/sai-i10-scientific-review`。**本文件持續更新中，尚未凍結。**

**目前判定：I09 的科學診斷證據經獨立重現，數字誠實且可重算；科學品質仍未通過，真專家評估仍未量測。**
本輪新增的是資訊保留與證據可重現性，不是語意能力提升。

## 執行環境落差（必讀）

I09 的接手 prompt 假設 Windows 工作站。**本輪實際執行環境不同**，影響可做與不可做的範圍：

| 項目 | I09 記載 | I10 實際 | 影響 |
|---|---|---|---|
| 平台 | Windows，`C:\Users\Franky Kuo\...` | Linux 雲端容器 `/home/user/roothinks-1.0.1` | 行尾相關的 hash 差異（見下） |
| 倉庫 | `personal` remote | 個人倉庫以 `origin` 掛載；LAVA-Cowork 在另一個目錄且未被碰 | 未 fetch／push／改動 LAVA-Cowork |
| Python | 專案 venv，3.10 | 容器 `python3` 3.13.17，無專案相依 | 需另建 venv；純 stdlib 工具可直接跑 |
| SciFact dev | Windows TEMP 既有 blind 目錄 | **不存在**，自公開 SciFact 重建並以 hash 核對 | 見「資料重建」 |
| I09 raw QA | Windows TEMP 173 份產物 | **不可取得** | 只能核對 I09 已提交的 hash，不能核對 raw 檔本身 |
| I08 真 holdout | I08 工作樹 `output/...` | **不存在** | 本輪完全未接觸（原本亦禁止） |
| Node | codex runtime | 容器 Node v22.22.0 | `test/js/*.cjs` 可實跑 |
| Chromium | Windows ms-playwright | 容器預裝 Chromium + Playwright | 瀏覽器可行性另行評估 |

## 資料重建與證據鏈核對（已完成）

自公開 SciFact（`data.tar.gz`，corpus 5183 筆、claims_dev 300 筆）以倉庫自有的
`scientific_benchmark.py prepare` 決定性重建 dev 輸入與 gold，再與 I09 記錄的 hash 逐一比對。

來源檔 SHA-256：`corpus.jsonl` = `b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62`；
`claims_dev.jsonl` = `86f0435d08fdb65d1aa41d1472684f57e6e71930626497bdf4d7a9ec1a632217`。
重建得 300 案、283 份不重複引用文獻，與 I09 交付所述一致。

| 項目 | I09 記錄 | I10 重算（LF） | 判定 |
|---|---|---|---|
| I08 凍結引擎 | `e99d9bce…0a8a` | `e99d9bce…0a8a` | **位元相符** |
| I09 凍結引擎 | `64a5b526…0ebe` | `64a5b526…0ebe` | **位元相符** |
| `inputs.jsonl` | `0fde332e…221b` | `ded7f378…1ce5` | 轉 CRLF 後相符 |
| `evaluator-only-gold.jsonl` | `d49ab22b…4b76` | `4374b195…681f` | 轉 CRLF 後相符 |
| I08-morph 實驗引擎 | `132a04b6…5cca` | `19cb8a4c…a712` | 轉 CRLF 後相符 |
| I09-morph 實驗引擎 | `6cddd23d…7a16` | `5a41133d…4697` | 轉 CRLF 後相符 |

兩個凍結引擎原本就相符，是因為 `diagnose` 對它們用 `write_bytes`；其餘四項走文字模式寫入，
在 Windows 上被套上 CRLF。**證據鏈沒有造假**，是工具的平台相依缺陷（已修，見下）。

### 逐項重現 I09 的 dev 診斷

以核對無誤的輸入與凍結 I08 基線重跑 `scripts/scientific_diagnose.py`，四組
（I08、I09、I08-morph、I09-morph）的 unknown、SUPPORT／CONTRADICT 的 TP／FP／FN／TN、
前三句與首句定位 precision／recall、失敗分解 **全部與 `SAI_I09_SCIENTIFIC_DIAGNOSTICS_2026-10-24.json` 逐項相符**。
I09 所報的「支持 TP 1→1、矛盾 TP 2→0、UNKNOWN 297→299、定位不變、詞形檢索更差」全部成立。

## 風險取捨的獨立審查結論

**I09 的 2→0 矛盾召回退步，根因不是新增保守閘門，而是 `advisory` 換了判讀路徑。**
I08 的 `advisory` 用「詞袋方向／否定差 + 相似度 ≥ 0.35」直接總結矛盾；I09 把這條路降為
`legacy` 專用，改走逐候選的有界關係比較。兩個 TP 是前者湊巧命中，不是有原則的判讀。

逐案追查（`scientific_advisory.py` 的 `_relation_frame`／`assess_relation`）：

- **案 249**：主張 `reduces whole-body energy expenditure`，來源 `Whole-body energy expenditure was
  also increased upon CDCA treatment`。真矛盾。被動句使主客體在表層顛倒，且主張用全名
  `Chenodeosycholic acid`（原文另有拼寫差異）而來源用縮寫 `CDCA`。規則棄權，理由是
  `different_arguments_or_conditions`——**這個理由本身有誤導性**，論元其實相同，只是被動化與縮寫未解析。
- **案 674**：主張 `has no involvement`，來源 `has a causal role`。真矛盾。`causal`、`involvement`
  屬名詞化／形容詞化謂詞，不在 `_RELATIONS` 詞表（詞表有 `associate` 卻沒有 `association`，
  有 `reduction` 在 `_LEMMA` 卻不在 `_RELATIONS`，覆蓋本身不一致），兩邊都解析失敗。

**量測後的結論：這 2 案無法用任何有原則的字面規則修補救回。** 案 249 需要縮寫／別名對照，
案 674 需要名詞化謂詞語意；單獨加被動句處理救不回案 249（縮寫仍不對應），
為了 dev 分數硬加只會擴大規則表面並製造新誤判。**因此刻意不實作**。

### 實際採用的修補：保留反證線索，不給判定

真正的缺陷不是標籤，而是**資訊遺失**：I08 在案 249 顯示
`missing_condition` + `possible_contradiction`，I09 只剩 `support_not_established`——
作者完全看不到「你寫 reduces、來源寫 increased」。保守化把問題藏起來了。

新增 `counter_evidence_lead`（`advisory-rules-4`）：**不改 `relation`、不改 `blocks_writing`、
不宣稱證明**，只列出要開哪一句與為什麼。三條訊號在量測前先固定，否定差沿用引擎既有的
0.35 相似度門檻，未新增可調參數：

1. `direction_differs_arguments_unresolved`：同一關係族但方向相反，而主客體未能對應。
2. `negation_scope_differs`：首候選的否定存在性不同且相似度達 0.35。
3. 範圍訊號轉記：`unresolved_stance_scope`、`source_does_not_establish_claim`、`significance_is_not_direction`。

已曝光 dev 300 案的量測（與預先登記的規則一致，未事後調整）：

| 指標 | 數值 |
|---|---:|
| 帶線索案例 | 10 / 300（3.3%） |
| 其中 gold CONTRADICT | 4（64 案的 **6.25%**） |
| 其中 gold NEI | 4 |
| 其中 gold SUPPORT | 2 |
| 命中中屬 gold CONTRADICT 比例 | 40.0% |
| 標籤混淆矩陣／定位 precision 與 recall／失敗分解 | **與凍結 I09 完全相同** |

訊號分布：`direction_differs_arguments_unresolved` 6、`source_does_not_establish_claim` 2、
`significance_is_not_direction` 2、`negation_scope_differs` 1。

逐案人工判讀（操作者自行檢視，非第三方審查）：4 個 CONTRADICT 命中有 3 個指向正確句子，
案 1140 屬湊巧命中（首候選只是 methods 標頭）。4 個 NEI 命中有 2 個是不同受測對象的雜訊
（案 36、1014），2 個合理（案 269、517）。2 個 SUPPORT 命中裡案 1290 合理（來源句確實含
「no evidence」子句），**案 1298 是真誤報**（主張與來源同為否定語氣，線索誤示衝突）。

**代價與界線**：換來 4/64 的反證可見度（I09 為 0，I08 為 2 個誤導性的決定性判定），
代價是 6 個非矛盾案例被標成待核對、其中 1 個明確誤導。因為標籤完全不變，
**不可能產生新的假矛盾判定，也不阻擋任何寫作動作**。絕對召回 6.25% 仍極低；
這是資訊保留，**不是語意能力提升**。

## 已修的可重現性缺陷

| 位置 | 缺陷 | 修法 |
|---|---|---|
| `scripts/scientific_benchmark.py` `write()` | `Path.open('x')` 無 `newline`，所有證據列（inputs／gold／predictions／metrics／protocol）套作業系統行尾 | 固定 `newline='\n'` |
| `scripts/scientific_diagnose.py` | 實驗引擎用 `write_text`，hash 平台相依；凍結副本用 `write_bytes` 不一致 | 改為位元串接，與凍結副本一致 |

新增 `test/unit/test_evidence_reproducibility.py` 釘住兩個不變量。
**注意**：此修補之後產生的 hash 與 I09 記錄的 CRLF 值不同；兩者的對應關係見上表，
I09 的既有證據不受影響也未被改寫。

## 本輪提交

| 提交 | 內容 |
|---|---|
| `a048898` | `counter_evidence_lead` 非決定性線索、前端顯示要開哪一句與原因、合成對抗測試 |
| `89fa94b` | 證據 hash 跨平台可重現（LF 固定、實驗引擎位元串接）、可重現性測試 |

## 已執行的驗證

- **引擎對抗控制 21 項全通過**（以 importlib 繞過需要 flask 的 `app` 套件直接執行）：
  含 I09 全部 7 項 scope 控制與 3 項 polarity 控制在加入線索後仍保持原行為。
- **JS 行為檢查 10 支全通過**（Node v22.22.0 實跑）：9 支既有 + 1 支新增
  `test/js/test_scientific_lead_rendering.cjs`（4 情境）。
- **可重現性測試 2 項通過**（獨立執行）。
- **dev 診斷**：加入線索後重跑，標籤／定位／失敗分解與凍結 I09 位元級相同。

## 尚未完成（更新中）

- 完整 pytest 回歸：容器系統 python 無 pytest 與專案相依，環境建置與可跑範圍評估進行中。
- 真瀏覽器操作：可行性評估進行中。
- 專家流程與 custody 稽核：進行中。
- 最終 source 凍結後的完整回歸與逐項驗收矩陣更新。

## 明確未測量（NOT MEASURED）

跨領域專家 gold、條件／模態／因果品質、機率校準、真正 holdout 成績、真人標註一致率與 kappa。
真專家仍為 **0 人**；本輪未開封真 holdout、未聯絡任何人、未替 Owner 設定接受門檻。

## 待 Owner 決策（與 I09 相同，未被本輪取代）

任命獨立保管人、兩位領域 reviewer 與獨立裁決者；核對身分／專長／衝突與金鑰；
建立實作者不可讀取的 custody 環境；核准來源代表性、rubric 與可接受的漏報／誤報／條件門檻。
