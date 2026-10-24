# I10 交付：I09 證據鏈獨立核對與反證線索保留

日期：2026-10-24。起點 `900394e32279673ae1520e23e22a34fcafe27573`（`codex/sai-i09-scientific-foundation` 最新已推送提交）。
隔離分支 `codex/sai-i10-scientific-review`。最終**實作**凍結 `ce9dc8c`；之後只有文件與證據提交。

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

## 專家流程與 custody 稽核

全文閱讀 `expert_evaluation.py`、`prepare_expert_holdout.py` 與兩支既有測試（共 452 行），
每個假設都以可執行反例驗證。下列每一項都**先重現、再修補、再加對抗測試**；
標 CONFIRMED 者為實測結果，不是推理。

### 已修（8 項，`ce9dc8c`）

| # | 嚴重度 | 缺陷 | 重現結果 | 修法 |
|---|---|---|---|---|
| 1 | **高** | 角色金鑰唯一性比較 raw hex 字串，而 `bytes.fromhex` 大小寫不敏感且跳過空白 | 同一把金鑰寫成大小寫兩種 hex 即同時擔任 reviewer1／reviewer2，一路通過 `freeze`／`verify-gold`／`score` 並回報 `label_agreement 1.0`、`disputes []`；custodian 兼 reviewer2 亦通過 | 正規化後比較，並驗證公鑰編碼 |
| 2 | 中 | 簽章覆蓋 canonical 重編碼，不是檔案 bytes | 已簽檔案加入重複鍵後 `read`／`verify` 全部通過，Python 取 last-wins；檔案 bytes 與 canonical 不同 | `read()` 拒絕重複鍵；新增 `read_signed()` 要求簽署產物就是 canonical bytes |
| 3 | 中低 | `verified_gold` 用 `g!=expected`，Python 認為 `False==0`、`1.0==True` | 保管人改掉 `scientific_validation_pass`、`label_agreement` 或 gold 內科學旗標的**型別**後重簽即通過，下游 `is False` 失效 | 改比較 canonical bytes |
| 4 | 中 | 任務 payload 無輪次識別 | 同母版同計畫重跑可產生位元相同的任務，上一輪標註可直接重放 | 加入保管人產生的 `round_nonce`，並檢查任務欄位集合 |
| 5 | 中低 | 裁決欄位檢查不對稱：任務與標註都檢查 version／kind 與欄位集合，裁決獨缺 | 任意額外鍵（含 `model_prediction`）寫入凍結 gold 且 `verify-gold` 通過 | 補齊 version／kind 與兩層欄位集合檢查 |
| 6 | 中 | `score` 的 `engine_sha256` 只檢查長度 | 任選 64 字元字串即可「釘住」受測模型 | 套用既有 commitment hex 驗證 |
| 7 | 低 | `keygen` 先 `save` 後 `chmod` | 實測窗口內權限為 **0o644** 且私鑰內容已寫入 | 改用 `os.open(...,0o600)` 直接建檔 |
| 8 | 低 | `--owner-public` 非必填且無格式驗證 | 省略時死於 `TypeError: fromhex() argument must be str, not None` | 非 keygen 模式必填並驗證編碼 |

新增 `test/unit/test_expert_custody_hardening.py`（30 個對抗情境）。既有
`test_condition_disagreement` 手組裁決 payload，依新契約補上 version／kind，
其原本驗證的版本綁定行為不變。

### 稽核確認**原本就已防護**的項目

這些方向都實跑過反例，確實擋得住，不應被誤讀為尚未處理：A 案標註搬到 B 案；
換掉 reviewer 實際看到的來源正文（連用保管人金鑰重簽也擋）；跨計畫／跨 commitment 重放；
裁決指向舊版原件或換掉原件後重簽；缺欄位／缺案例／重複案例／型別錯誤被當成有效標註；
symlink（含 dangling）、TOCTOU 與路徑穿越；`score` 吃錯計畫或子集案例。
`verified_gold` 的重建鏈（Owner 簽計畫 ＋ 兩位 reviewer 各自簽提交 ＋ adjudicator 簽裁決）
是整套裡最紮實的一層，原本只被型別鬆散削弱（第 3 項已修）。
`save()` 的 `open('xb')` 比聲稱的更強，symlink 一併拒絕。

### 未修：屬 Owner／協定決策，不是程式能補的（CONFIRMED）

| 項目 | 實況 | 為什麼不自行修 |
|---|---|---|
| **多份簽章擇一遞交** | 同一份任務上可產生多份有效且相異的提交，保管人決定遞交哪兩份；沒有任何東西紀錄「還存在其他已簽提交」。實測 3 份有效提交留下零分歧那一份 | 需要真正獨立的保管人，不是程式檢查；Owner 任命事項 |
| **盲化仍不完整** | 盲化任務含 `doi`、`title` 與全文 XML URL（abstract-only 任務卻遞上全文入口）；同來源的變換探針家族可互相 diff 還原，其中 `missing_citation` 與 `author_quote` 兩個探針的主張逐字相同 | 改動 reviewer 實際看到的內容＝改動 rubric 與代表性，須 Owner 核准 |
| **commitment 不是簽署物** | 預測與 gold 的 hash 是 CLI 字串；預測 payload 只綁計畫，**從不綁 gold digest**。結構上允許先讀 gold 再造預測 | 協定變更（commitment 改為保管人簽署的 envelope） |
| **完全沒有時序證據** | 凍結記錄對 `time｜date｜stamp｜nonce｜round｜seq｜epoch` 搜尋在本輪修補前為零命中；`prepare_expert_holdout` 的解封匯出從未被 `expert_evaluation` 引用，兩支腳本無連結 | 需要外部時間源（RFC3161 或透明log），不是本機程式能提供 |
| `no_predictions_seen` 零資訊 | 必須恰為 `True` 才被接受，因此永遠記不下「看過預測」，卻在凍結記錄裡看起來像具結 | 移除或允許為 false 會改動 reviewer 具結語意，須 Owner 決定 |
| 條件值與定位強度 | `condition['value'] in quote` 是純子字串比對；`evidence_sentence_locators` 的 span 不要求句界，整段 abstract 合法 | 收緊會改變 reviewer 的填寫負擔與 rubric，須 Owner 核准 |
| Unicode 與 offset 單位 | offset 是 Python code point；JS／Java 為 UTF-16、Go／Rust 為 UTF-8 byte，含非 BMP 字元時跨語言驗證器會靜默不一致。無 NFC 正規化 | 須在協定明定單位與正規化，才不會讓既有 I08 封存的 hash 失效 |
| 兩套 canonical JSON | `prepare_expert_holdout.encoded()` 缺 `separators` 與 `allow_nan=False`，與 `expert_evaluation.encoded()` 對同一份資料給出不同 hash | 統一會改變 I08 已封存的 `holdout_plaintext_sha256`，不可在未開封前變更 |
| 衍生物無簽章 | `score`、`disputes` 輸出無 envelope；`verify-gold` 的輸出是拆封後的 payload，保管人簽章被丟掉 | 協定變更 |

### 必須更正的措辭（文件層，非程式缺陷）

- `I09_EXPERT_WORKFLOW.md` 寫「必填 I08 指南的 **12** 欄」，`FIELDS` 實際為 **11** 欄。
- 「精確驗證…必要條件值」：條件值只要求出現在被引用 span 內某處，且 span 不要求是句子。
- 「兩份獨立亂序的盲化任務」：獨立性在本輪修補前未被強制，盲化至今仍是部分的。
- `SAI_I09_DELIVERY` 稱共享參照已改用簽署快照：`sign()` 已修，但 `freeze(private=None)`
  的回傳值仍別名其輸入（CLI 不可利用，屬程式庫層面）。
- `label_agreement`／`cohen_kappa`：在擇一遞交不受約束的前提下，本工具給出的 kappa
  不能當作 inter-rater reliability 證據。

I09 已經誠實寫明、本輪稽核同意且無須更動的措辭：`I09_PROTOCOL` 的
「同一電腦管理員仍能繞過流程或讀同機密鑰。簽章只證明持鑰者簽署，不能證明學術資格、
獨立性或沒有看過答案」、`scientific_validation_pass` 恆為 false、
manifest 的 `independent_custodian_assigned=False`。

### 稽核本身的界線

本輪稽核由本代理執行，**不是第三方安全審查**，也沒有做滲透測試或密碼學形式驗證。
AGENTS.md 要求的 CodeGraph-first 與 Rickie `brain_context` 在本執行環境**沒有掛載**
（工具清單與 `ToolSearch` 皆無，`.codegraph/` 與 `codegraph` CLI 不存在），
改以 452 行全文閱讀加可執行反例替代，此規模下閱讀覆蓋率 100%，但這是環境落差，須記錄。

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
| `9bb5a95` | 交付文件：起點核對、I09 證據鏈重現、退步根因、線索量測 |
| `8d36972` | `scientific_phenomena.py`：檢索與語意分離、科學現象分項計數、手算測試 |
| `ce9dc8c` | 專家流程 8 項已確認 custody 缺陷修補 ＋ 30 個對抗測試 |
| `732832c` | custody 稽核紀錄；專家操作指南事實更正 |

## 工程驗證（最終）

最終 source 凍結於 `ce9dc8c`（之後只有文件與證據提交）。**完整回歸在該凍結之後執行**；
修補前那一輪（1,436 通過）不作最終證據。

| 項目 | 結果 |
|---|---|
| 完整回歸 `test/unit tests test/integration_e2e` | **1,466 通過／3 略過／0 失敗／0 錯誤**，1,469 項，179.77 秒 |
| 略過原因 | 2 項 Windows 路徑別名語意（本輪在 Linux 執行）、1 項本機沒有正式 `data/roothinks.db` |
| 引擎對抗控制 | 21 項（以 importlib 繞過需要 flask 的 `app` 套件），含 I09 全部 7 項 scope 控制與 3 項 polarity 控制加入線索後行為不變 |
| 專家流程測試 | 59 通過（既有 29 ＋ 新增 30 項 custody 對抗情境） |
| JS 行為檢查 | **10 支全通過**（Node v22.22.0 實跑）：9 支既有 ＋ 1 支新增 `test/js/test_scientific_lead_rendering.cjs`（4 情境） |
| 可重現性測試 | 2 項通過 |
| dev 診斷 | 以最終工具重跑；標籤混淆矩陣、前三句與首句定位 precision／recall、失敗分解與凍結 I09 **完全相同** |

I09 歷史比較：I09 在 Windows 為 1,415 通過／2 略過（1,417 項）。本輪 1,469 項 = 1,417 ＋ 本輪新增 52 項；
略過數 3 vs 2 的差異來自平台（Linux 少一個 Windows symlink 略過、多兩個 Windows 路徑別名略過與一個正式 DB 略過）。

機器證據：[SAI_I10_EVIDENCE_2026-10-24.json](SAI_I10_EVIDENCE_2026-10-24.json)、
[科學現象分項](SAI_I10_SCIENTIFIC_PHENOMENA_2026-10-24.json)。

## 檢索與語意的分項診斷（最終）

這是本輪對「是否泛化到科學」最直接的量測結果。

| 指標 | 數值 |
|---|---:|
| 有官方 rationale 的案例 | 188 / 300 |
| 檢索取回 rationale | 160（**失敗 28**，14.9%） |
| 取回後棄權 | **159** |
| 取回後判對 | 1 |
| 取回後判錯 | 0 |
| **取回成功後的語意失敗率** | **99.375%** |

取回正確句之後的棄權原因分布：

| 原因 | 次數 |
|---|---:|
| `unsupported_sentence_structure` | **130**（160 案的 81%） |
| `different_arguments_or_conditions` | 17 |
| `source_hedged` | 12 |
| `source_does_not_establish_claim` | 1 |

**結論：主要瓶頸不是保守化，也不只是檢索，而是 `_relation_frame` 要求句中恰好有一個
出自小型詞表的謂詞，導致 81% 的真實科學句子根本無法被解析。** 這比 I09 的四個 bucket
更精確地指出下一輪該往哪裡投資：真正的語意模型，而不是更多字面規則。

科學現象分項計數（引擎自己的警訊代碼，非專家標註）：

| 現象 | 命中案例 | 說明 |
|---|---:|---|
| 條件（`missing_condition`） | 18 | gold 分布 SUPPORT 5／CONTRADICT 5／NEI 8 |
| 反證線索（`counter_evidence_lead`） | 10 | 其中 gold CONTRADICT 4 |
| 因果（`causality_not_established`） | 3 | |
| 數值（`numeric_mismatch`） | 1 | |
| 模擬／實測（`modality_mismatch`） | **0** | 合成對抗控制涵蓋此路徑，真實 dev 從未觸發 |
| 外推（`unsupported_generalization`） | **0** | 同上 |
| 缺引用（`no_evidence`） | 0 | dev 每案都有被引用來源，恆為 0 |
| 決定性矛盾（`possible_contradiction`） | **0** | |

**這揭露一個容易誤讀的事實**：模態與外推這兩類對抗控制在真實 SciFact dev 上**一次都沒有觸發**。
合成控制證明了程式路徑可用，但在這份語料上的真實覆蓋率為零；不能用「對抗控制通過」
推論這些能力在真實科學寫作中有效。

## 縱向流程（優先工作 4）

作者端的「可執行下一步」由本輪的線索顯示補齊（原主張、來源、適用條件、不確定原因、
要開哪一句與為什麼），`original_text` 不變、`blocks_writing` 恆為 false。
教授加入／撤權／移除不丟歷史、獨立研究、選用教授與團隊協作由既有測試涵蓋並在本輪完整回歸中通過：
`test_invitation_token_identity_single_use_and_revocation_preserve_history`、
`test_team_invitation_only_grants_selected_chapter_and_can_be_removed`、
`test_revocation_cannot_remove_last_owner_after_member_role_changes`、
`test_expired_and_revoked_invitation_never_grant_access`、
`test_revoked_chapter_comment_author_cannot_mutate`、
`test_author_response_is_append_only_not_scientific_proof_and_stale_view_is_explicit`、
`test_advisory_failure_preserves_original_generated_draft`。
本輪未重複造等效測試，也未在此宣稱真人可用性。

## 真瀏覽器：本輪被環境阻擋（未取得證據）

**兩條真瀏覽器流程都實際嘗試過，都失敗，根因是本容器的出網政策，不是程式缺陷。**

容器端條件全部具備：Node v22.22.0、Playwright 1.63.0、Chromium `chromium-1243`（版本相符）、
port 8893 空閒、fixture import 全通、無 Windows 依賴。fixture 啟動成功，
**純 HTTP 直接存取伺服器完全正常**（`/auth/login` 與 `/` 都是 HTTP 200、約 12 KB 完整頁面）。

失敗發生在瀏覽器端取外部資源：

| 流程 | 首個失敗點 | 頁面錯誤 |
|---|---|---|
| `sai_scientific_browser.cjs` | 等 `window.wsApp?.socket?.connected` 超時 —— socket.io client 未載入 | `mermaid is not defined` |
| `sai_autonomy_browser.cjs` | `:51` workbench waitForFunction 超時 —— bootstrap 未載入 | `bootstrap is not defined`、`mermaid is not defined` |

`app/templates/` 把 Bootstrap 5.3.0、Socket.IO 4.7.2、Split.js 1.6.0、KaTeX 0.16.9、
mermaid@10 與 bootstrap-icons 全部指向 `cdn.jsdelivr.net` 與 `cdnjs.cloudflare.com`，
**倉庫沒有任何本機 vendor 副本**。本容器的網路政策拒絕這三個主機
（`cdn.jsdelivr.net`、`cdnjs.cloudflare.com`、`apis.google.com`），因此整個 UI 無法初始化。

**我沒有用 mock、本機替身或改動樣板去讓它通過。** 首輪失敗的 receipt、evidence.json、
截圖與 YAML 全部保留（12 份產物的 bytes 與 SHA-256 記在機器證據的 `real_browser.failed_artifacts`）。
自主性流程在失敗前已產生 `independent-dashboard.png`，可見登入與 dashboard 有部分渲染。

**因此本輪前端變更（線索顯示）的最高層級證據只有合成 DOM 檢查**
（`test/js/test_scientific_lead_rendering.cjs`，4 情境，Node 實跑通過），
**不是真瀏覽器驗收**。要取得真瀏覽器證據，需在允許上述 CDN 主機的環境執行
（例如 I09 的 Windows 工作站，或把這三個主機加進本環境的網路允許清單）。
在那之前，UI 變更不應被記為已通過瀏覽器驗收。

## 本輪發現但未修（不在本輪範圍）

| 項目 | 實況 | 為什麼不修 |
|---|---|---|
| `httpx` 未列入相依 | `app/llm_service/adapter/llm_google.py:26` 與 `llm_openrouter.py:14` 在模組層直接 `import httpx`，`requirements.txt` 未列，只靠 `openai` 間接帶入 | 屬 LLM adapter 與 Docker build 範圍；本環境無法驗證建置結果，改動相依會影響正式映像 |

## 環境限制（非倉庫缺陷）

- eventlet 0.36.1 在 Python 3.13 下 `monkey_patch()` 失敗，而 `app/__init__.py` 預設 async mode 為 eventlet。倉庫目標是 3.10，測試完全未用到此路徑。
- 容器沒有 tesseract 二進位檔，也未安裝 torch／easyocr／layoutparser／transformers。OCR／CV／NLLB 翻譯與 gunicorn+eventlet 正式啟動路徑本輪**完全未涵蓋**。
- 出網政策拒絕 CDN 主機（見上）。

## 明確未測量（NOT MEASURED）

跨領域專家 gold、條件／模態／因果品質、機率校準、真正 holdout 成績、真人標註一致率與 kappa。
真專家仍為 **0 人**；本輪未開封真 holdout、未聯絡任何人、未替 Owner 設定接受門檻。

## 判定

**指定工程切片 CONDITIONAL PASS；科學品質未通過；真人專家評估仍未量測；前端變更未取得瀏覽器驗收。**

通過的部分：I09 證據鏈 15 項獨立核對全部成立；完整回歸 1,466 通過／0 失敗；
8 項確認的 custody 缺陷已修並有 30 項對抗測試；證據 hash 自本輪起跨平台可重現。

未通過或未取得的部分：科學分類能力沒有提升（標籤指標與 I09 完全相同，這是刻意的）；
取回後語意失敗率 99.375%；真瀏覽器被環境阻擋，UI 變更只有合成 DOM 證據；
真專家 0 人，真 gold／裁決不存在，真 holdout 未開封。

## 下一輪建議（詳見 [I11 接手 prompt](I11_HANDOFF_PROMPT.md)）

1. **正面處理語意瓶頸**：81% 的真實科學句子無法被 `_relation_frame` 解析。評估真正的語意方案，
   先固定預算／資料角色／方法／採用門檻再跑，與凍結 I10 對照。不要再加字面規則。
2. **補上真瀏覽器證據**：需允許 CDN 主機的環境，或評估加入本機 vendor 副本。
3. **處理刻意未修的 custody 界線**：多數需 Owner 決策或協定變更。

## 待 Owner 決策（與 I09 相同，未被本輪取代）

任命獨立保管人、兩位領域 reviewer 與獨立裁決者；核對身分／專長／衝突與金鑰；
建立實作者不可讀取的 custody 環境；核准來源代表性、rubric 與可接受的漏報／誤報／條件門檻。
