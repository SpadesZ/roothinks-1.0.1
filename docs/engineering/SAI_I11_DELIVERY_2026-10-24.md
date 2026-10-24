# I11 交付：真瀏覽器驗證完成，語意實驗量測後不採用

日期：2026-10-24。起點 `9deaa948b6a923c7a59f38df4ced756026b7a27d`（I10 最新已推送提交）。
隔離分支 `codex/sai-i11-semantic-and-browser`。**I10 基線未被動到。**

**判定：真瀏覽器驗證通過（兩條流程、12 項 check、零錯誤）；語意實驗依預先固定的門檻不採用；
科學分類能力與 I10 完全相同，沒有提升；真人專家評估依 Owner 決策延後。**

預先固定的執行與評估契約見 [I11_PROTOCOL.md](I11_PROTOCOL.md)，該契約在任何量測之前已提交（`652eb48`）。

## Owner 決策的執行結果

| Owner 決策 | 本輪實際做法 |
|---|---|
| 1. 真瀏覽器驗證，只允許三個必要 CDN 網域，不得開放無限制網路，保留真實結果與失敗，不得 mock | **已完成**。只攔 `cdn.jsdelivr.net`、`cdnjs.cloudflare.com`、`apis.google.com`；網路政策未改、`HTTPS_PROXY` 未動；資產為 npm 發佈真品並以樣板宣告的 SRI 離線驗真；I10 的失敗產物與本輪產物全部保留 |
| 2. 專家任命與正式 holdout 評估延後；不得虛構 reviewer、不得開封 holdout、不得自訂接受門檻 | **已遵守**。未聯絡任何人、未虛構身分、未讀 key、未解密 holdout、未設科學接受門檻 |
| 3. 持續實質工程；優先處理已量測的語意瓶頸，須有凍結基線、預先固定協議、對抗案例與誠實退步回報 | **已完成**。協議先提交再量測；對抗案例先寫再實作；結果為負，照實回報並保留 |
| 4. 研究者自主不可退讓：警訊不得阻擋寫作、儲存、修訂或匯出 | **已在真瀏覽器驗證**：`unknown_advisory_does_not_block_save_export`（含實際 DOCX 下載）、`independent_no_professor`、`team_scoped_save_export_denial_exit_history` 全部通過 |

未 merge main、未部署、未讀或改 production data、未碰 LAVA-Cowork。

## 一、真瀏覽器驗證（通過）

### 上一輪為什麼卡住

I10 記錄：樣板把 Bootstrap 5.3.0、Socket.IO 4.7.2、Split.js、KaTeX、mermaid 全部指向 CDN，
倉庫沒有任何本機 vendor 副本，而容器出網政策拒絕這些主機，UI 完全無法初始化。

### 本輪方法

`registry.npmjs.org` 可達（curl 200），而 jsdelivr 的 `/npm/<pkg>@<ver>/<path>`
就是直接服務 npm 套件裡的同一個檔案。因此：

1. 從 npm 取得**完全相同版本**的套件（`mermaid@10` 解析為 `10.9.8`）。
2. 用 Playwright request routing 把**只有那三個網域**的請求導到本機 vendor 檔案。
3. **產品碼與 `.cjs` 腳本的既有 assertion、timeout 與可見性檢查一字未改**
   （新增的線索斷言為純新增 21 行，刪除行數 0）。導流在測試層以
   `node --require vendor-route.cjs` 包裝 `chromium.launch` 完成。

### 真品證明：SRI 離線驗真

樣板對 5 個資產宣告了 `integrity="sha384-..."`。npm bytes 重算結果**全部相符**：

| 資產 | 結果 |
|---|---|
| `bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js` | 通過 |
| `bootstrap@5.3.0/dist/css/bootstrap.min.css` | 通過 |
| `bootstrap-icons@1.11.1/font/bootstrap-icons.css` | 通過 |
| `socket.io-client@4.7.2/dist/socket.io.min.js` | 通過 |
| `split.js@1.6.0/dist/split.min.js` | 通過 |

這不只是離線比對：**瀏覽器自己也會驗 SRI**，任何位元差異都會讓資產被拒絕執行。
頁面正常運作，等於瀏覽器以密碼學方式確認了這些資產是真品。
KaTeX 與 mermaid 的樣板沒有宣告 integrity，因此無法以 SRI 證明，只記錄 SHA-256。

### 結果

| 流程 | 狀態 | checks |
|---|---|---|
| `sai_scientific_browser.cjs` | **passed**，errors `[]` | 8 項 |
| `sai_autonomy_browser.cjs` | **passed**，errors `[]` | 4 項 |

科學流程 8 項：來源比較且原稿不變、來源變更 409 保留理由並追加回應、
無證據與不同條件有說明且不改寫、**未解析反證線索可見且不成為判定（本輪新增）**、
未知建議不阻擋保存與匯出、手機寬度面板不溢出、生成報告不跨章誤標、
XHR CSRF 上傳保留原檔且不宣稱已解析。

自主性流程 4 項：獨立研究不需教授、影響重核保留作者原文與原審查者再確認、
選用教授的邀請／回應／移除且保留歷史、限定團隊的保存／匯出拒絕與離場歷史。

導流紀錄：科學流程 20 次請求、自主性流程 94 次，共 6 個不同檔案；
`apis.google.com/js/api.js` **未 vendor，照原樣中止，不造假內容**，兩條流程仍通過，
故非必要資產。KaTeX、Split.js 與 `cdn.plot.ly` 的 plotly **這兩條流程從未請求**，
所以「只允許三個網域」的邊界精確守住。

### 關掉 I10 的缺口

I10 明記「前端變更僅有合成 DOM 證據，不得記為瀏覽器驗收」。本輪新增真瀏覽器斷言：
API 的 `relation` 仍為 `unknown`、`scientific_proof` 與 `blocks_writing` 仍為 `false`、
`findings` 含 `counter_evidence_lead` 且 signals 含 `direction_differs_arguments_unresolved`、
`support_not_established` 沒有被取代、畫面實際顯示「請核對：來源 … 第 1 句」
與「方向相反，但主客體未能對應」、作者原文逐字不變。**缺口已關閉。**

### 誠實界線

- 資產由本機 vendor 服務，**不是真的從 CDN 出網**。真品性由 SRI 保證，
  但這**沒有**驗證 CDN 的可用性、TLS 鏈或實際網路路徑。
- `apis.google.com/js/api.js`（Google Drive 轉接）未驗證。
- KaTeX、Split.js、plotly 未被這兩條流程請求，未驗證。
- headless Chromium 的版面與計時可能與真實桌面瀏覽器不同。

## 二、語意瓶頸實驗（量測後不採用）

### 問題（來自 I10 的實測，不是猜測）

取回成功的 160 案中 `unsupported_sentence_structure` 佔 **130（81.3%）**。
根因是 `_relation_frame` 要求句中恰好有一個出自約 40 字小型詞表的謂詞。

### 可用資源的實際限制

| 資源 | 可達性 | 結果 |
|---|---|---|
| HuggingFace（NLI 神經權重） | **被擋**（curl 000） | 無法取得預訓練模型 |
| spaCy 依存句法模型 | `en-core-web-sm` 不在 PyPI、github.com 被擋 | 無法取得剖析模型 |
| NLTK WordNet 3.0 | 可達（以 curl 走正常代理取檔） | **採用**，SHA-256 `cbda5ea6…` |

NLTK 自身的下載器拒絕走代理（SSRF 防護），**本輪沒有繞過該安全控制**，改用 curl 取檔後讓 NLTK 只讀本機檔案。

### 方法與刻意不用的部分

只用 WordNet 的兩項能力：`morphy` 詞形還原、派生關係還原名詞化謂詞
（`involvement→involve`、`association→associate`、`reduction→reduce` 等 8/10 成功）。
另加只認 `by` 施事者的被動句論元還原。

**WordNet 反義關係刻意不用**，因為先量測過結果為負：8 個關鍵謂詞只有 2 個可用
（`activate→inactivate`、`promote→demote` 都不是本專案需要的對立；
`inhibit`／`suppress`／`improve` 完全沒有反義）。方向判定維持人工關係族表，
**不聲稱方向對立已由詞彙資源支撐**。`causal` 與 `role` 在 WordNet 沒有到 `cause`
的派生連結，I10 的案 674 預期仍無法解析，本輪不為單一案例硬加詞條。

### 合成對抗案例：全部如預期

17 項控制在有 WordNet 時全跑通過：名詞化與被動改述能對齊、論元順序顛倒不對齊、
真方向衝突仍判矛盾、無施事者的被動棄權、不跨關係族、
WordNet 不可用時逐案退回基線、實驗引擎必須由凍結引擎位元串接而成。

### 真實語料結果：零變化

| 指標 | 凍結 I10 | I11 候選 |
|---|---:|---:|
| UNKNOWN | 299 | 299 |
| SUPPORT TP／FP／FN | 1／0／123 | 1／0／123 |
| CONTRADICT TP／FP／FN | 0／0／64 | 0／0／64 |
| 前三句定位 precision／recall | 0.3664／0.5656 | 0.3664／0.5656 |
| 取回後 `unsupported_sentence_structure` | **130** | **130** |
| 標籤或定位不同的案例 | — | **0** |
| 完整報告不同的案例 | — | **0** |

協議的效果門檻要求 130 降到 **≤115**，實測 **130 → 130**，未達標。
四項硬性閘門全部通過（既有對抗控制 134 項全過、研究者自主不變、
定位完全相同、gold SUPPORT 決定性誤報維持 0），但效果門檻不過，
**依預先固定的協議不採用**。產品引擎已回退至凍結基線並核對 hash
（`4c2034bf…`，`advisory-rules-4`），實驗保留為 `scripts/semantic_frame_experiment.py`。

### 為什麼沒用（本輪對下一輪最有價值的產出）

161 個首候選句的量測：

| 情況 | 數量 | 佔比 |
|---|---:|---:|
| **擴充詞表後仍然零謂詞** | **93** | **57.8%** |
| 主動句，走基線路徑 | 39 | 24.2% |
| **謂詞數 ≥2，被單一謂詞紀律否決** | **20** | 12.4% |
| 有被動助動詞但沒有 `by` 施事者 | 6 | 3.7% |
| 名詞化有 `of` 但沒有 `by` 施事者 | 2 | 1.2% |
| **實際還原成功** | **1** | 0.6% |

主張側同樣有 **94／161（58.4%）** 零謂詞。WordNet 確實有效
（字面詞表 109 句零謂詞 → 擴充後 93 句），但**擴充同時把多謂詞句從 17 推到 20**，
被單一謂詞紀律否決得更多，兩者互相抵銷。

**而那唯一一次「還原成功」其實是錯的**：`of…by` 模式跨子句匹配，從一句講表現量的
來源句（案 837）拼出橫跨兩個子句的假 frame
`subject=('rna','interference','abrogated','decidualization')`／
`object=('nr5a2','transcript','abundance')`。它只因主張側無法解析而碰巧無害 ——
**那是運氣，不是防護**。真實語料上的還原精確度 **0／1**。

**結論：更大的謂詞詞表無法修好這個架構。** 覆蓋與歧義紀律互相抵銷，
而鬆綁歧義紀律會讓跨子句假 frame 直接生效。下一輪需要真正的句法剖析
（處理多謂詞與論元結構）與語意關係模型，不是更長的字串清單。

## 三、工程驗證

最終 source 凍結 `780e1e1`。完整回歸在該凍結之後執行。

| 環境 | 結果 |
|---|---|
| 產品環境（無 WordNet，憑證變數清空） | **1,470 通過／16 略過／0 失敗**，1,486 項 |
| 有 WordNet（語意案例全跑） | **1,483 通過／3 略過／0 失敗**，1,486 項 |

產品環境多出的 13 項略過，正是 WordNet 專屬的語意實驗案例；
3 項共同略過與 I10 相同（2 項 Windows 路徑別名語意、1 項本機沒有正式 `data/roothinks.db`）。
`test/js/*.cjs` 的 10 支 Node 行為檢查不受本輪影響。

產品 `requirements.txt` **未新增相依**；WordNet 為實驗專用的選用資源，
資料不可用時實驗引擎逐案退回凍結基線（有專門測試）。

機器證據：[SAI_I11_EVIDENCE_2026-10-24.json](SAI_I11_EVIDENCE_2026-10-24.json)、
[語意負結果](SAI_I11_SEMANTIC_NEGATIVE_RESULT_2026-10-24.json)。

## 四、明確未測量（NOT MEASURED）

跨領域專家 gold、條件／模態／因果品質、機率校準、真 holdout 成績、
真人標註一致率與 kappa、CDN 實際出網與 TLS 鏈、`apis.google.com` 的 Drive 轉接路徑。
真專家仍為 **0 人**。

## 五、待 Owner 決策

專家任命與正式 holdout 評估依本輪 Owner 決策**延後**，工具與空白材料已備妥。
I10 稽核列出的 custody 協定界線（保管人擇一遞交、盲化仍含 DOI／標題／全文 URL、
commitment 非簽署物、全流程無時序證據、條件值子字串比對、Unicode 與 offset 單位未固定、
兩套 canonical JSON、衍生物無簽章）仍待 Owner 或協定決策，本輪未自行變更。

下一輪建議見 [I12 接手 prompt](I12_HANDOFF_PROMPT.md)。
