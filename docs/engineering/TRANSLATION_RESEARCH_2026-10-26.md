# 翻譯工具比較研究與保護層原型（REA、BabelDOC、Immersive Translate）

日期：2026-10-26。分支 `codex/study-coresearch-r3-20261026`。**只用公開來源；沒有安裝、執行或逆向任何工具；沒有修改代理設定。**

## 0. 研究條件與可信度

- 研究由背景子代理以公開來源完成，本文件由主工程師整理並逐項標示來源性質。
- 網路：`raw.githubusercontent.com`、`pypi.org` 可達；`github.com` 網頁、`api.github.com`、`huggingface.co`、`gnu.org`、`arxiv.org`、
  `aclanthology.org`、`immersivetranslate.com` 全部被代理拒絕。網頁擷取工具在本環境全部 DNS 失敗；搜尋工具只回傳摘要。
- 標記：**〔檔案〕** 已讀到原始檔案；**〔摘要〕** 只有搜尋引擎摘要，未讀到原頁；**〔推論〕** 本專案的判斷，不是來源事實。

## 1. BabelDOC（funstory-ai/BabelDOC）

- **授權 AGPL-3.0**〔檔案：LICENSE、pyproject.toml、PyPI JSON；最新 0.6.4〕。README 明言所有 API 視為內部 API、直接使用不受支援，建議經 pdf2zh_next（也是 AGPL-3.0〔檔案〕）。
- **架構**〔檔案：docs/ImplementationDetails〕：PDF 解析（pdfminer 分支＋PyMuPDF → 中間表示 IL）→ 版面辨識（DocLayout-YOLO ONNX）→ 段落切分 →
  樣式與公式偵測（字型、特殊 Unicode、上下標等啟發式，可用正規式擴充）→ **IL 翻譯：公式與格式片段換成佔位符，翻譯後放回** →
  排版（先縮行距、再縮字、再向右側空白延伸）→ 字型對應 → 產生單語或雙語 PDF。
- **雙語輸出是「頁對頁」**（並排或交替頁）〔檔案〕，不是段落對段落的資料結構；RootThinks 閱讀器目前是段落對照（`seq_id`）。
- **翻譯後端只支援 OpenAI 相容 LLM**〔檔案：README、translator.py〕；可指到本機模型，否則段落文字會送到外部服務。
- **離線**：可預先下載資產（字型約 344 MB〔檔案：資產清單〕、版面模型、CMap）並打包成離線包；翻譯是否離線取決於 LLM 是否在本機。
- **已知限制**〔檔案：README〕：作者與參考文獻段落會合併、跨頁／跨欄段落與表格仍在路線圖、主要測試英翻中、排版效能自述不佳。
- **品質數字**：ACL 2026 demo 論文報告版面 BIoU 等指標〔摘要，原文未讀到；數字未核實，不引用〕。
- **AGPL 對 RootThinks 的意義**〔推論，非法律意見；gnu.org 未讀到〕：當成 Python 套件匯入通常構成合併作品，網路提供服務時須向使用者提供原始碼——
  影響最大。以獨立程序或獨立服務呼叫邊界較清楚，但該元件本身與修改仍須依 AGPL 提供原始碼。**任何形式的整合都需要 Owner 與法務核准。**

## 2. Immersive Translate（沉浸式翻譯）

- 官方網站全部無法連線；除 GitHub 檔案外都是〔摘要〕。
- 〔檔案：官方 Zotero 外掛 README〕其 PDF 翻譯使用 BabelDOC，需要網站授權碼並在雲端處理。BabelDOC README 也連到其線上服務。
- 〔摘要〕隱私政策：訂閱者上傳的 PDF 會加密傳給第三方 Mathpix 擷取內容；BabelDOC 功能的檔案「暫時保留、不永久儲存」。
  **意義**：整份 PDF 離開使用者裝置，可能經過 Funstory、Mathpix 與所選 LLM 供應商。
- 〔摘要〕支援二十多種翻譯服務、免費與付費頁數上限；價格來源互相矛盾，未核實。
- 服務條款的限制條款沒有讀到；**不能排除禁止逆向工程**。

## 3. REA（morluto/rea）

- 〔檔案：README、LICENSE、install.sh、package.json、SECURITY.md、DISCLAIMER.md〕MIT 授權的 MCP 伺服器＋代理技能，用來逆向二進位、應用程式與網站行為。
- **安裝會改代理設定**：寫入 `~/.claude.json` 的 `mcpServers` 與使用者層級的 `~/.claude/skills/`，讓「每個專案」都能呼叫；可能以 root 安裝 Hopper。
- **安全評估**〔推論〕：任意程式執行風險高（大型 npm 相依、以使用者權限執行目標程式、瀏覽器與封包擷取；其 SECURITY.md 自述「不是沙盒」）；
  提示注入風險高（逆向得到的內容直接進模型上下文，且技能對所有專案常駐）；資料外流風險中到高。
- **結論：不在 RootThinks 環境安裝 REA；不對 Immersive Translate 做逆向工程**（專有軟體；REA 自己的免責聲明也排除複製專有軟體；
  且所需技術已有開源的 BabelDOC 可讀，逆向只增加法律風險）。

## 4. 與 RootThinks 比較

| 面向 | RootThinks（Flow A＋B） | BabelDOC（自架） | Immersive Translate（雲端） |
|---|---|---|---|
| 版面 | 段落重排後雙語對照；不重新排版 PDF | 重新排進原框 | 宣稱保留（未獨立量測） |
| 公式 | 佔位符保護公式／URL／DOI（本輪補強，見 §5） | 啟發式偵測＋佔位符 | 宣稱保留 |
| 雙語對齊 | 段落對段落（`seq_id`） | 頁對頁 | 並排（粒度未核實） |
| 準確度 | **未量測** | 來源未提供可核實數字 | 未量測 |
| 隱私 | NLLB 本機；Google 快速翻譯會送出文字 | 取決於 LLM 端點 | 整份 PDF 上傳 |
| 授權 | **NLLB 權重 CC-BY-NC-4.0（非商業；見 §6）** | AGPL-3.0 | 專有 |

## 5. 本輪實作：翻譯保護層（獨立撰寫，無 AGPL 程式碼）

`app/core_pro/literature/translation_guard.py`，`HybridTranslator` 的保護／還原改為委派：

1. **基本遮罩與舊作法逐字相同**（40 句測試逐一比對），預設行為相容。
2. **容錯還原**：佔位符被加空白、改大小寫、改全形底線時仍能認回。
3. **不默默遺失**：仍找不到的公式／URL／DOI 補在譯文尾端並標示「翻譯時遺失、已補回的原文片段」。
4. **數字比對**：回報原文有、譯文沒有的數字（只回報，不改寫）。
5. **擴充保護**（引用標號、作者年份、上下標與化學式）**預設關閉**（`TRANSLATION_GUARD_EXTENDED=1` 開啟）：沒有在真 NLLB 上量測前不改變譯文。

量測（`scripts/translation_guard_benchmark.py`，40 句合成科學句；**模擬**佔位符損壞，不是任何真引擎的品質）：

| 損壞型態（基本遮罩，24 個片段） | 舊作法保留 | 新作法保留 | 舊作法無聲遺失句數 | 新作法無聲遺失句數 |
|---|---|---|---|---|
| 原樣保留 | 24 | 24 | 0 | 0（無誤報） |
| 加空白 | 0 | 24 | 20 | 0 |
| 大小寫＋全形底線 | 0 | 24 | 20 | 0 |
| 整個丟掉最後一個 | 4 | 4（20 句補回並標示） | 20 | 0 |
| 改動數字 | — | — | 無偵測 | 32／32 偵測到 |

擴充模式保護片段 24 → 57，同樣型態下新作法無聲遺失 0。**真引擎上佔位符實際損壞頻率未量測**（雲端沒有 torch、線上 Google 403）。

## 6. 需要 Owner 決定

1. **NLLB 授權**：`scripts/nllb-source.json` 記錄 `facebook/nllb-200-distilled-600M` 為 **cc-by-nc-4.0**（本專案自己的紀錄，已核對）。若 RootThinks 有商業用途，需法務確認。
2. 是否在可執行 NLLB 的環境量測佔位符真實損壞率，再決定是否開啟擴充保護。
3. 任何 BabelDOC／pdf2zh_next 整合（AGPL）、任何把文件或文字送到外部服務（Immersive Translate、Mathpix、雲端 LLM、既有 Google 快速翻譯）的決定。

## 7. 下一步建議（可獨立實作的概念）

段落級詞彙表與術語一致性檢查；參考文獻／作者段落分離與跨欄段落合併；翻譯快取（以原文、引擎、模型版本、詞彙表雜湊為鍵）；
翻譯品質量測工具（未翻譯段落比例、佔位符存活率、術語一致性）——先量測，再宣稱品質。

## 8. 無法核實

AGPL 與 FSF FAQ 原文、BabelDOC 論文表格與速度、版面模型大小與授權、Immersive Translate 所有官方頁面（功能、價格、完整隱私、服務條款）、
BabelDOC 的 issues、REA 的星數與技能內容、BabelDOC 是否有遙測（讀到的檔案中沒有看到，未全面稽核）。
