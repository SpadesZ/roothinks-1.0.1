# R7 雙語閱讀器：實測缺陷與修正（2026-11-02）

合成論文：`synthetic_r7_reading`（4 頁、37 個區塊：25 個正文類、4 個公式、1 圖、1 表、6 個頁首尾雜訊）。
量測：`test/integration_e2e/reading_ui_r7_browser.cjs`，四種寬度 desktop 1440×900／laptop 1280×800／tablet 820×1180／phone 390×844。
截圖：`before/` 與 `after/` 同名，`<寬度>-<01-top|02-abstract|03-methods-url|04-equations|05-equations-fallback|06-figure|07-table|08-references|09-selection>.png`。
結果 JSON：`before-json/reading_ui_r7_result.json`（89 項 42 過／47 不過）→ `after-json/reading_ui_r7_result.json`（89／89 過，0 頁面錯誤）。

| # | 缺陷 | 證據（修正前 → 修正後） | 修正 |
|---|------|------------------------|------|
| 1 | 長網址／DOI／檔名被 `.bilingual-section{overflow:hidden}` **裁掉看不到**（不是捲動，是消失） | 被裁的內容寬度：desktop 11px、laptop 67px、tablet 228px、phone 273px；溢出元素數 4／12／22／36 → 全部 0。`laptop-03-methods-url`、`tablet-08-references`（`…PhysRevB.101.16` 被切） | study.css：閱讀器文字 `overflow-wrap:anywhere`（不插連字號，複製仍完整）、欄 `min-width:0` |
| 2 | 數字檢查把 DOI 尾碼當成遺失數字（R4 誤報） | `laptop-03-methods-url`：「⚠ 原文的數字 1234567890123 在譯文中找不到」，但譯文有同一 DOI → 修正後無此提示；第一篇的 0.5 提示仍只有 1 則 | study_view.js `_translationCheckHtml` 比對前移除網址／DOI |
| 3 | 章節標題、論文標題、小標題的**譯文完全不顯示** | 有譯文的正文類區塊 23 個，只有 14 個顯示譯文（9 個不見：MainTitle、1. 緒論、2. 方法、2.1…、3. 結果、4. 討論與結論、參考文獻、碎片） → 23/23 | 標題下顯示譯文＋「機器翻譯」標籤，帶 `data-side="translation"` 錨點 |
| 4 | 小標題（Subtitle/SubSubtitle）與短碎片**沒有原文錨點** | 無錨點正文區塊 4 個（Abstract、2. Methods、2.1…、`(n = 12)`）→ 0 | 小標題改在段落渲染器內產生並帶 `data-side/data-ref/data-page`；短碎片仍黏到上一列，但有自己的錨點與譯文 |
| 5 | 短碎片（< 12 字）黏到上一列時**譯文被丟掉** | `（n = 12）` 不在畫面 → 顯示在同一列譯文欄（`tablet-04-equations`） | 同上 |
| 6 | KaTeX 不支援的指令（OCR 帶出的 `\SI`）畫成紅字、`4.2e6` 被排成 `4.2·e·6`，數值被誤讀 | 紅字錯誤節點 16 個（每種寬度）→ 0；改顯示原始 LaTeX＋OCR 文字並說明（`*-05-equations-fallback`） | `throwOnError:true`，失敗時 `_equationFallbackHtml` |
| 7 | 介面殘留英文 | `Page 1…4`、`<Failed to OCR Equation>`、`reason: latex_low_confidence` → 「第 N 頁」、「公式辨識失敗：請以上方原文切塊圖片為準」、「原因代碼：…」（原哨兵字留在 title） | study_view.js |
| 8 | 圖說／表說的機器翻譯不顯示 | 圖 1 有 content_zh 但 captionZh=false → 顯示並標「機器翻譯」（`*-06-figure`） | `_captionTranslationHtml` |
| 9 | 表格圖片無限縮小、窄螢幕讀不到數字 | 縮放比 tablet 0.24、phone 0.09 → 0.36／0.36（區塊內左右捲動，`phone-07-table`）；desktop/laptop 不變 0.45/0.40 | 表格圖放進可聚焦捲動區，最小寬度＝min(原圖寬, 720px) |
| 10 | 寬公式／表格捲動區無法用鍵盤聚焦 | 會捲動但不可聚焦：tablet 1、phone 2 → 0；公式與表格捲動區 4 個皆 `tabindex=0 role=region aria-label` | study_view.js＋`:focus-visible` 外框 |
| 11 | 平板：閱讀窗格 535px 仍左右並排，每欄文字約 214px | 堆疊列 0/16 → 16/16（依閱讀窗格寬度的 container query，< 600px 上下排列） | study.css `@container study-reader` |
| 12 | 手機：閱讀窗格 234px、AI 家教 98px，「研究筆記」被擠成直書（`phone-02-abstract`） | 閱讀窗格 234 → 340px；AI 家教 98×796 → 340×303，上下排列 | study.css `@media (max-width:767.98px)` 蓋掉 Split.js inline width |
| 13 | 手機上下排列時第 2 列以後的譯文沒有「機器翻譯」標示 | 未標示的譯文 3 段 → 0 | 窄窗格時非首列譯文欄 `::before` 標「機器翻譯」（偽元素，不進選字／錨點文字） |
| 14 | 選字動作列在 390px 被推出視窗左緣 | left −2px（inViewport=false）→ 8px；三個按鈕皆可見、不蓋住選取 | `_showSelectionToolbar` 先夾右再夾左；CSS `flex-wrap`＋`max-width:calc(100vw-16px)` |
| 15 | 沒有跳頁；長論文只能一路捲 | 無 → 黏在閱讀窗格頂端的「跳到頁 1 2 3 4」，跳到 `data-page-marker` 並把焦點移過去 | `_renderPageNav`＋document 委派 click |
| 16 | `text-align:justify` 在中英混排與 `$\kappa$` 長字串旁把字距拉開 | `desktop-01-top`：「降 幅 34.6% 。 應 變 係 數」；`phone-02-abstract`：「We    report    Raman-」→ 靠左 | study.css `text-align:start !important`（inline style 寫死 justify） |
| 17 | 缺譯段落只有灰色小字、無機器可辨識標記 | 文字改為「此段落沒有機器翻譯，請以原文為準」，加 `data-translation-missing` | study_view.js |

不變（量測確認）：四種寬度整頁 `scrollWidth == clientWidth`（修正前後皆無整頁水平溢出）；並排列原文／譯文頂端差 0px、高度差 0px；每列 `data-ref` 原文＝譯文；原文逐字保留（0 段被改動，不做 NFKC）；簡體字與大陸用語 0。

未修正（理由）：
- 行內數學 `$\kappa$`、`$d\kappa/d\varepsilon = …$` 以原始 LaTeX 顯示。用 KaTeX 排版會把一個文字節點拆成多個（含 MathML annotation），`_rangeForQuote` 在每個文字節點後補空白，跨公式的引文就對不到伺服器錨點；為保住錨點與「符號不得改動」而不排版。
- 文獻清單的「Ready／Wait」徽章與「Loading papers...」是英文：在 study_core.js／study.html 文獻清單（非閱讀器、非本工作擁有），留給擁有者。
- 論文標題在閱讀器頁首與第一張卡片重複出現：既有設計，不影響閱讀。
- 表格內容（Table 區塊的 OCR 文字）仍以一段文字顯示在圖下：管線沒有 HTML／Markdown 表格，無結構可渲染。
