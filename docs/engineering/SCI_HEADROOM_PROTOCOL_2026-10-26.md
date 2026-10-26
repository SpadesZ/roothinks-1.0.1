# 科學判讀天花板分析：預先登記協議

日期：2026-10-26。執行前提交。**只做診斷，不改任何分類行為**；產品引擎 `advisory-rules-4`（`4c2034bf…`）凍結。

## 要回答的問題

I13 Gate A 的規則式子句 frame 只多救回 1 個判定，而且是假 frame。下一步（Gate D：句法剖析模型）值不值得做，
取決於**就算剖析完美，在不放寬任何保守閘門的前提下，最多能多救回幾個判定**。本分析量這個天花板。

## 資料與限制

- SciFact dev 300 筆（已曝光，只作開發證據）。inputs sha256 `ded7f378…`，gold sha256 `4374b195…`。
- 凍結引擎的預測：`scripts/scientific_benchmark.py predict` 的輸出（已驗證在 3.10／3.12／3.13 逐位元相同，sha256 `a7a22bcf…`）。
- 不讀 sealed holdout。gold 只用 evidence 句子位置與標籤。
- 執行前我已看過「全部 comparison」的理由分布（未依 gold 分組）：`unsupported_sentence_structure` 762、`source_hedged` 73、
  `different_arguments_or_conditions` 69、其他 8。gold 分組後的數字尚未看過。

## 分類規則（固定）

對每個 gold 為 SUPPORT 或 CONTRADICT 的主張（124＋64＝188），取引擎在該主張上的 comparison（每個主張前 3 句），
以句子區間重疊判定哪些 comparison 落在 gold 證據句上（與 `score` 的 localization 相同方法）。依序分類：

1. **H0 已正確**：引擎標籤等於 gold。
2. **H3 沒檢索到**：沒有任何 comparison 落在 gold 證據句上（前 3 句都不是證據句）。
3. **H1 結構可救**：至少一個落在 gold 證據句的 comparison，理由屬於 I13 的觸發集合
   `{unsupported_sentence_structure, different_arguments_or_conditions}`。更好的論元比對可能改變它，而不需要碰保守閘門。
4. **H2 閘門擋下**：其餘。落在證據句的 comparison 全部被保守閘門擋下（如 `source_hedged`、`different_quantities`、
   `significance_is_not_direction`、`source_does_not_establish_claim`）。改變它等於放寬閘門，**不允許**。

H1 是**樂觀上限**：主張層級還有條件／警示閘門，即使句子層級對齊，主張也可能維持 UNKNOWN。

## 決策規則（固定）

- I13 的 G6 門檻是「SUPPORT TP ＋ CONTRADICT TP ≥ 6」。若 **H1（SUPPORT＋CONTRADICT）＜ 6**，則任何剖析方法在 dev 上都不可能過 G6，
  建議放棄 Gate D 選項 1。
- 若 H1 ≥ 6，Gate D 仍值得在 Owner 核准模型後做；H1 就是它的天花板，H3 的大小決定檢索是否應優先。
- 若 H3 ＞ H1，建議下一步先做檢索（證據句排序）而不是語意。

## 輸出

`scripts/scientific_headroom.py` 產出 JSON：各類別計數（依 gold 標籤分開）、落在證據句的 comparison 理由分布、
各類別的 claim id（不含文字）。結果寫入交付證據，不入產品。
