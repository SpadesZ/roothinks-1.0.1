# I12 交付：瀏覽器範圍擴大、語意資源可行性評估、I11 記錄更正

日期：2026-10-25。起點 `16f1be36f46ad9948f829eea440ba261447047f4`（I11 最新已推送提交）。
隔離分支 `codex/sai-i12-browser-and-semantic-probe`。**I10／I11 基線與產品引擎未被動到。**

**判定：瀏覽器範圍擴大完成並通過；語意路徑有可行方案但margin比預期薄，建議不要直接整合；
確認 P0 缺陷 0 個；I11 證據有兩處錯誤已更正。**

本輪**沒有改動產品引擎**（`advisory-rules-4`，hash `4c2034bf…` 不變），因此沒有新的採用門檻需要
預先登記；I11 預先固定的門檻與凍結基線完整保留（見 [I11_PROTOCOL.md](I11_PROTOCOL.md)）。

## Owner 決策的執行結果

| 決策 | 執行 |
|---|---|
| 1. 擴大瀏覽器測試至 KaTeX、Split.js 等可觸及頁面；plotly 可用已驗證的 npm 資產，但 `cdn.plot.ly` 未授權；Google Drive OAuth 仍延後；保留 I11 通過的檢查與研究者自主 | **完成**。新增 study 頁面流程，KaTeX 與 Split.js 實測載入且被程式消費。plotly 取自 npm 並以樣板 SRI 驗真；**對 `cdn.plot.ly` 這個網域沒有發出任何連線**（導流在本機完成），且兩種設定都跑並保留結果。未執行 Drive OAuth。I11 的 12 項檢查重跑全部仍通過 |
| 2. 選項 A：調查可透過現有 PyPI／npm 取得的剖析／NLI 資源，做隔離可重現探測，涵蓋可得性、授權、相容性、資源成本與跨子句失敗 | **完成**，見下方排序評估。全部探測可重現，腳本已入庫 |
| 3. 自主調查並修復範圍內已確認的 P0；保留預先登記標準、負結果、凍結基線與可重現證據 | **確認 P0 共 0 個**，照實回報不誇大。I11 的預先登記標準、負結果與凍結基線全部保留 |
| 未經核准不得整合新語意引擎、擴大網路權限、放寬科學判定門檻 | **三項皆未發生**。產品引擎 hash 未變 |

未虛構專家、未開封 holdout、未 merge main、未部署、未改正式資料、未碰 LAVA-Cowork。

## 一、I11 證據的兩處錯誤（已更正）

I11 的導流包裝只記錄「被導流的請求」，沒有記錄未導流與失敗的請求，導致外部主機的失敗
在證據裡消失。I12 的包裝改為記錄**每一個**外部請求、每一次 `requestfailed` 與 console 錯誤。

| I11 的說法 | 實測結果 |
|---|---|
| `cdn.plot.ly` plotly「未被這兩條流程請求」 | **錯誤。** 兩條流程都請求 `https://cdn.plot.ly/plotly-2.27.0.min.js` 並失敗（`net::ERR_TUNNEL_CONNECTION_FAILED`），科學流程 5 次、自主性流程 16 次。根因：`_header.html` 無條件載入 plotly，而 `manuscript_workspace.html` 用的是 `_navbar.html`，我由此誤推 |
| 完全沒提到 `accounts.google.com` | **漏記。** `manuscript_workspace.html:640` 載入 `https://accounts.google.com/gsi/client`，該主機在本容器**可達且載入成功**（curl 200、瀏覽器無失敗）。這是第四個外部網域，I11 從未記錄 |
| KaTeX 與 Split.js「未被這兩條流程請求」 | **正確。** 兩者只在 `study.html`，I11 的流程未造訪該頁。I12 已新增流程涵蓋 |

## 二、瀏覽器證據

四次執行全部 `status: passed`、`pageerror` 為空：

| 流程 | checks | 說明 |
|---|---:|---|
| `sai_scientific_browser.cjs` | 8 | I11 既有，重跑仍全通過 |
| `sai_autonomy_browser.cjs` | 4 | I11 既有，重跑仍全通過 |
| `sai_assets_browser.cjs`（不服務 plotly） | 4 | **本輪新增** |
| `sai_assets_browser.cjs`（服務已驗真 plotly） | 4 | 外部失敗歸零 |

新增流程的四項檢查：

1. `splitjs_loaded_and_consumed_by_app` —— `study.html:437` 用 `typeof Split !== 'undefined'` 包住，
   載入失敗時面板只會**無聲地**不可拖動。本流程要求 `window.Split` 存在，**且程式真的消費它**：
   實測產生 **2 個 gutter**，`#matrix-pane` 寬 969px。
2. `katex_loaded_and_renders_math` —— `study_view.js:1639` 以 `window.katex.render` 渲染
   `.katex-eq[data-latex]`。實測渲染後產生 `.katex` 節點且文字含公式內容。
3. `study_page_usable_without_professor_or_evidence` —— 無教授、無證據下 study 頁面正常載入
   （標題 `ROOTHINKS-B Study Field`），無阻擋性警訊遮罩。
4. `authorized_assets_have_no_silent_failures` —— 記錄全部外部失敗，並對已授權網域要求零失敗。

### 真品證明

樣板宣告 SRI 的 6 個資產，npm bytes 重算**全部相符**（I11 的 5 個 ＋ 本輪新增的 plotly）：

| 資產 | 結果 |
|---|---|
| `bootstrap@5.3.0` js / css | 通過 |
| `bootstrap-icons@1.11.1` css | 通過 |
| `socket.io-client@4.7.2` | 通過 |
| `split.js@1.6.0` | 通過 |
| **`plotly.js-dist-min@2.27.0/plotly.min.js`** | **通過** |

plotly 的 SRI 相符代表 npm 的 `plotly.js-dist-min@2.27.0` 與 `cdn.plot.ly` 服務的
`plotly-2.27.0.min.js` **位元完全相同**。KaTeX 與 mermaid 的樣板未宣告 integrity，只能記 SHA-256。

### 誠實界線

- 資產由本機 vendor 服務，**未驗證 CDN 實際出網與 TLS 鏈**。
- Google Drive OAuth 依 Owner 決策未執行；只驗證 `gsi/client` 腳本可載入。
- headless Chromium 的版面與計時可能與真實桌面瀏覽器不同。
- **產品本身仍會無聲降級**：`typeof Split !== 'undefined'` 的保護在資產失敗時不給任何提示。
  新流程會在已授權網域失敗時斷言失敗，但這是測試層的防護，不是產品層的。未自行修改產品行為。

## 三、語意資源可行性評估（排序）

I11 的瓶頸量測：取回成功的 160 案中 `unsupported_sentence_structure` 佔 130（81.3%），
161 個首候選句有 93 句（57.8%）在 WordNet 擴充後仍零謂詞。本輪針對此做可執行探測。

### 可得性（實測，非清單推測）

| 資源 | 結果 |
|---|---|
| PyPI / npm / `raw.githubusercontent.com` | 可達 |
| **HuggingFace** | **000 不可達** → 任何 NLI 或神經剖析權重都拿不到 |
| **github.com** | **400 不可達** |
| spaCy `en_core_web_sm` | **拿不到**。`/simple/` 回 200 但是空索引（soft-404），`pip` 回 `from versions: none` |
| stanza | 套件可裝，但 `resources_1.10.0.json` 的 url 模板指向 `huggingface.co`（000），`nlp.stanford.edu` 亦 000 → **拿不到模型** |
| NLTK 語料 | **全部可取得**：wordnet、verbnet、propbank、framenet_v17、averaged_perceptron_tagger(+_eng)、punkt、maxent_ne_chunker、treebank |
| npm `wink-nlp` + `wink-eng-lite-web-model` | 可取得，MIT，自帶模型 |
| npm `compromise` | 可取得，MIT |

NLTK 的下載器會拒絕走代理（SSRF 防護）；**本輪沒有繞過該安全控制**，改用 curl 取檔後讓 NLTK 只讀本機檔案。

### 關鍵量測：謂詞覆蓋

謂詞詞表規模：現行產品 **48** → VerbNet 3,626 ＋ PropBank 3,319 = 聯集 **4,744**（約 99 倍）。

161 個檢索成功的首候選句：

| | 現行 48 字詞表 | VerbNet+PropBank |
|---|---:|---:|
| 來源句零謂詞 | 111（**68.9%**） | **2（1.2%）** |
| 主張句零謂詞 | 102（63.4%） | **13（8.1%）** |

**覆蓋問題基本可解。** 但歧義隨之爆開，而且**兩種實作差距很大**：

| 指標 | wink-nlp（JS） | NLTK perceptron（Python，產品語言） |
|---|---:|---:|
| 整句恰好一個謂詞 | 37/161（23.0%） | **11/161（6.8%）** |
| 整句 ≥2 謂詞（會被現行紀律否決） | 115/161（71.4%） | **148/161（91.9%）** |
| 子句切分後的子句數 | 509（3.16/句） | 333（2.07/句） |
| **子句恰好一個謂詞** | **245（48.1%）** | **130（39.0%）** |
| 子句仍 ≥2 謂詞 | 70（13.7%） | **148（44.4%）** |
| 吞吐 | 44,000 句/秒 | **68 句/秒**（161 句耗 2.37 秒） |

**這個差距本身就是結論**：同一個想法的兩種實作，可用子句比例差 9 個百分點、殘餘歧義差 30 個百分點。
**子句切分的效果對詞性標註器與切分規則高度敏感，不能假設，必須先量測。**
而產品是 Python，所以要看的是較差的那一欄。

### 三個失敗案例的實測

| 案例 | 需要的能力 | wink | compromise | 結論 |
|---|---|---|---|---|
| 249 來源 `energy expenditure was also increased upon CDCA treatment` | 被動＋非 by 施事者＋縮寫對照 | 詞性正確（`increased`→`increase`），但無依存關係 | 動詞切成 `was also` | **未解**：沒有候選提供縮寫對照 |
| 674 `has no involvement` vs `has a causal role` | 名詞化／形容詞化謂詞 | **零動詞**（`has` 標為 AUX） | `has` / `has no` | **未解**：詞性閘門使名詞化不觸發，需另結合 I11 已有的 WordNet 派生關係 |
| 837 跨子句假 frame | 子句邊界 | 找到 3 個動詞與 **6 個子句切點** | 2 個動詞片語 | **可解**：子句切分正是 I11 指出所缺的能力 |

### 授權（必須法務審查）

| 資源 | 授權狀態 |
|---|---|
| WordNet 3.0 | Princeton 授權，允許商用（需署名）。NLTK 包內有 LICENSE |
| wink-nlp / wink-eng-lite-web-model / compromise | MIT |
| **VerbNet 2.1** | NLTK 包內**沒有 LICENSE 檔**，只有 README → **需確認** |
| **PropBank I** | NLTK 包內**沒有 LICENSE 檔**；`prop.txt` 明確是 **WSJ（Penn Treebank，LDC 付費授權）**上的標註 → **產品使用前必須法務審查** |

本輪只用到 PropBank 的**動詞 roleset 名稱**（不含 WSJ 正文），但這不代表授權沒問題。

### 資源成本

最小必要資料集（解壓後）：verbnet 2.9 MB ＋ propbank 27 MB ＋ wordnet 35 MB ＋ taggers 16 MB
≈ **81 MB**。FrameNet 1.7 為 **847 MB**，排除。這會是產品的**新資料相依**（不只是程式相依），
需納入部署考量。

### 排序結論

| 排名 | 方案 | 可行性 | 主要障礙 |
|---|---|---|---|
| **1** | VerbNet/PropBank 詞表 ＋ NLTK perceptron 詞性 ＋ 子句切分 ＋ 子句內規則論元連結 | **可行，但工作量大且 margin 薄** | 子句恰好一謂詞僅 **39.0%**、殘餘歧義 44.4%；無依存剖析，論元連結仍是規則；PropBank 授權待審；新增 81 MB 資料相依；68 句/秒的延遲 |
| 2 | wink-nlp 作為 Node sidecar | 技術可行（數字較好、極快、MIT） | **架構代價高**：引擎是 Python，要多一個 Node 程序與部署面；且仍無依存關係 |
| 3 | compromise | 不建議 | 比 wink 慢 57 倍；動詞片語含助動詞；被動匹配在三個案例上全空 |
| 4 | spaCy ＋ 模型 | **不可得** | 模型不在 PyPI（soft-404），github.com 被擋 |
| 5 | stanza | **不可得** | 模型只在 HuggingFace（不可達） |
| 6 | 任何 NLI 模型 | **不可得** | HuggingFace 不可達；無替代來源 |

**我的建議：不要在下一輪直接整合排名 1。** 理由是量測出來的，不是保守：
覆蓋雖然從 68.9% 誤失降到 1.2%，但 Python 路徑只有 39.0% 的子句是單一謂詞，
殘餘 44.4% 的子句仍有多謂詞；而 I11 已證實**鬆綁單一謂詞紀律會讓跨子句假 frame 生效**
（案 837 的 0/1 精確度）。在沒有依存剖析的情況下，把論元連結限制在子句內只是**縮小**風險，不是消除。

## 四、P0 缺陷調查

**確認 P0：0 個。**

方法：掃描 `app/` 全部模組層 `import` 並比對 requirements 宣告；查 `openai` 釘住範圍的實際相依；
審視三條瀏覽器流程的全部 console 錯誤與請求失敗。

| 項目 | 結論 |
|---|---|
| `httpx`（`llm_google.py:26`、`llm_openrouter.py:14` 模組層直接 import，requirements 未宣告） | **今天不會壞**：由 `openai` 1.x 傳遞取得。**不構成 P0**。但有具體前向風險：`openai` 最新版 **3.37.0 已改依賴 `httpx2`（非 `httpx`）**；requirements 釘 `openai>=1.0.0,<2.0` 所以目前安全，**一旦放寬該 pin，模組層的 `import httpx` 會直接失敗** |
| `sqlalchemy`、`werkzeug`、`socketio` 模組層直接 import 但未宣告 | 分別由 flask-sqlalchemy、flask、flask-socketio 傳遞保證。相依宣告衛生問題（PEP 508），非 P0 |
| 瀏覽器 console 的 403 / 409 | 皆為測試刻意觸發（團隊限定拒絕、來源變更衝突），非缺陷 |
| 前端資產無聲降級 | 真實但非 P0；修它會改動產品行為，未自行變更 |

依 Owner 的「fix confirmed P0 defects」，沒有符合條件的項目，**因此本輪沒有修改任何產品程式**。
以上四項全部照實回報，未誇大成 P0。

## 五、回歸結果

最終 source 凍結 `567e630`（之後只有文件與證據提交）。完整回歸在該凍結之後執行。

| 項目 | 結果 |
|---|---|
| 完整回歸 `test/unit tests test/integration_e2e`（產品環境） | **1,470 通過／16 略過／0 失敗／0 錯誤**，1,486 項，187.50 秒 |
| 與 I11 比較 | **完全相同**。本輪沒有改動產品程式，也沒有新增 pytest 案例（新增的是 Node 瀏覽器流程），所以數字不變是預期結果 |
| 16 項略過 | 3 項與 I10／I11 相同（2 項 Windows 路徑別名、1 項本機無正式 DB），13 項為 WordNet 專屬的 I11 語意案例（產品環境無 nltk 資料） |
| Node 瀏覽器流程 | 4 次執行全部 passed（科學 8、自主性 4、資產 4×2 設定） |
| `test/js/*.cjs` | 10 支不受本輪影響 |

機器證據：[SAI_I12_EVIDENCE_2026-10-25.json](SAI_I12_EVIDENCE_2026-10-25.json)。

## 六、剩餘風險

1. **語意能力完全沒有提升。** 自 I08 起 SUPPORT TP 1、CONTRADICT TP 0、UNKNOWN 299，三輪皆同。
   I12 沒有整合任何東西，所以這個數字不變。
2. **排名 1 方案的 margin 薄且對實作敏感**（48.1% vs 39.0%）。若整合而不先量測誤報，
   很可能重演 I11 的跨子句假 frame，而且這次會有 4,744 個謂詞在推動它。
3. **PropBank 授權未釐清**（WSJ/LDC 來源），產品使用前必須法務審查。
4. **新增 81 MB 資料相依**的部署影響未評估。
5. **`openai` pin 放寬會打斷 `import httpx`**（已有 3.37.0 改用 `httpx2` 的證據）。
6. **產品前端資產無聲降級**：使用者看不到 Split.js／KaTeX 失效的任何提示。
7. **CDN 實際出網、TLS 鏈、Google Drive OAuth 仍未驗證。**
8. 真人專家、真 gold、真 holdout 全部仍未進行（依 Owner 決策延後）。

## 七、建議的下一個決策閘門

**不是「要不要整合」，而是「先花一輪做可量測的誤報預算」。** 具體建議：

**閘門 A（建議先做，不需 Owner 新權限）**：在**不改產品**的前提下，用凍結 I11 基線做一輪
純離線實驗：VerbNet/PropBank ＋ NLTK 詞性 ＋ 子句切分 ＋ 子句內論元連結，
**預先登記**採用門檻（含 gold SUPPORT/NEI 上的決定性誤報上限、跨子句假 frame 的人工抽查比例），
再在已曝光 dev 上量測。這直接回答「排名 1 到底值不值得」，而且失敗也只是又一個保留的負結果。

**閘門 B（需要 Owner 決定）**：
- PropBank／VerbNet 授權是否可用於產品？（需法務）
- 是否接受 81 MB 新資料相依與 68 句/秒的延遲？
- 若閘門 A 的誤報預算不可接受，是否考慮開放 HuggingFace 以取得真正的 NLI 模型？
  （這會擴大網路權限，依 Owner 決策須先核准。）

**閘門 C（小且獨立）**：是否要我把 `httpx`／`sqlalchemy`／`werkzeug`／`socketio` 補進
`requirements.txt`？這是一行級的衛生修補，但會動到 Docker build，我沒有自行做。

下一輪接手文件：[I13 接手 prompt](I13_HANDOFF_PROMPT.md)。
