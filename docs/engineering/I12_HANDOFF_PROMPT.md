# 給下一輪（I12）的接手 prompt

你是 RootThinks Lead Engineer 與 Scientific Reliability Researcher。接續 I11，先獨立核對證據，再完成下一輪安全、可量測的工作。溝通及實作報告用繁體中文。

## 從哪裡接手

- 個人倉庫：`https://github.com/SpadesZ/roothinks-1.0.1.git`。
- 起點：`codex/sai-i11-semantic-and-browser` 最新已推送提交。先 fetch，記錄並核對遠端與本機 SHA。
- I11 最終**實作**凍結：`780e1e1`；之後只有文件與證據提交。I10 基線 `9deaa94` 未被動到。
- 請另外建立 `codex/` 隔離分支／工作樹。禁止重設或覆寫他人修改。
- LAVA-Cowork 不可 fetch／push／更動。只用個人倉庫；不 force push、不 merge main、不部署、不讀或改 production data。

## 必先讀

1. 本工作樹 `AGENTS.md`、`docs/SAI.md`、`docs/ACCEPTANCE_MATRIX.md`（I11 段落）。
2. `docs/engineering/SAI_I11_DELIVERY_2026-10-24.md`、`SAI_I11_EVIDENCE_2026-10-24.json`、`SAI_I11_SEMANTIC_NEGATIVE_RESULT_2026-10-24.json`、`I11_PROTOCOL.md`。
3. I09 與 I10 的交付文件仍然有效，未被取代。特別是 I10 的 custody 稽核與 `SAI_I10_SCIENTIFIC_PHENOMENA_2026-10-24.json`。

## 不可誤讀的 I11 結果

- **真瀏覽器驗證已完成且通過**（兩條流程、12 項 check、零錯誤）。方法是從 npm 取得同版本資產，
  用 Playwright routing 只導那三個 CDN 網域；樣板宣告的 5 個 SRI 全部相符，瀏覽器自己也驗過。
  **不要重做這件事**，除非你改動了前端或樣板。vendor 做法與包裝見 I11 交付文件。
  仍未驗證：CDN 實際出網與 TLS 鏈、`apis.google.com` 的 Drive 轉接、KaTeX／Split.js／plotly。
- **語意實驗已量測且不採用。** 不要重跑同一條路。已曝光 dev 300 案上
  標籤、定位、棄權原因與完整報告**逐案位元相同**，效果門檻 130 → ≤115 未達標（實測 130）。
- **瓶頸的精確形狀**：161 個首候選句中 93 句（57.8%）在 WordNet 擴充後仍然零謂詞，
  主張側 94 句（58.4%）同樣零謂詞；擴充詞表同時把多謂詞句從 17 推到 20，
  被單一謂詞紀律否決得更多，兩者互相抵銷。唯一一次還原成功是**跨子句的假 frame**，
  真實語料還原精確度 0／1。**更大的謂詞詞表無法修好這個架構。**
- WordNet 反義關係已量測為不可用（8 個關鍵謂詞只有 2 個正確），不要再試。
- `causal`／`role` 在 WordNet 沒有到 `cause` 的派生連結，I10 案 674 仍無法解析。不要為單一案例硬加詞條。
- 科學分類能力自 I08 起**沒有任何提升**：SUPPORT TP 1、CONTRADICT TP 0、UNKNOWN 299。
  I10 加的是反證線索（資訊保留），I11 的語意實驗是負結果。不要把這些說成能力改善。

## 下一輪優先工作

1. **語意能力需要新的資源路徑，不是新的規則。** 本環境 HuggingFace 與 github.com 被擋，
   PyPI 與 npm 可達。先**量測並回報**哪些真正的剖析／語意資源可從 PyPI 或 npm 取得
   （例如 PyPI 上是否有自帶模型的剖析器、npm 上的 NLP 套件），再據此設計。
   若結論是必須放寬網路或改用授權模型，**明確列為 Owner 決策並停在那裡**，不要自行開放網路。
   任何新實驗一律：先固定協議與採用門檻、先寫對抗案例、凍結基線對照、保留負結果。
2. **單一謂詞紀律與跨子句假 frame。** I11 證實鬆綁覆蓋會讓跨子句假 frame 生效。
   若要處理多謂詞句，必須同時有子句邊界判定，否則會製造無聲誤判。這是真實風險，有案例（837）可重現。
3. **補齊 I11 未驗證的瀏覽器範圍**（若在範圍內）：`apis.google.com` 的 Drive 轉接路徑、
   KaTeX／Split.js／plotly 所在頁面。注意 plotly 在第四個網域 `cdn.plot.ly`，
   Owner 只核准三個網域，**擴大範圍需先取得 Owner 同意**。
4. **custody 協定界線**（見 I10 稽核）多數需 Owner 或協定決策，不要自行變更已封存材料的 hash 語意。
5. **工程驗證。** 新增功能要有負向／對抗測試與真瀏覽器操作（方法已備好）。
   只修可重現的安全／恢復缺陷。最終 source 凍結後跑完整回歸。更新逐項驗收。

## 待 Owner 決策

專家任命與正式 holdout 評估**依 Owner 決策延後中**；工具與空白材料已備妥。
不要自行冒名、開封 holdout、聯絡專家或設接受門檻。
I10 列出的 custody 協定界線同樣待決。

## 執行環境注意

- Linux 雲端容器。專案相依不預裝；由小到大裝，不要裝 torch／easyocr／layoutparser／transformers。
  `opencv-python-headless` 是兩個測試檔 collect 所必需。pytest 依 `requirements-dev.txt` 釘 8.x。
- 完整回歸：`python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider`。
  I11 結果：產品環境 1,470 通過／16 略過；有 WordNet 1,483 通過／3 略過；均 0 失敗。
- 真瀏覽器：Node `/opt/node22/bin/node` v22.22.0、Playwright 1.63.0、
  Chromium `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`。**不要跑 `playwright install`。**
  fixture 綁 `127.0.0.1:8893`，啟動後印一行 JSON（`root` 即 `SAI_SYNTHETIC_ROOT`）。
  導流包裝與 vendor 建法見 I11 交付文件；**不要改產品碼或既有 assertion**。
  腳本會在 cwd 產生 `output/`，請在另一份 clone 執行。
- SciFact dev 可自公開資料重建：`https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz`
  再跑 `python scripts/scientific_benchmark.py prepare <data-dir> <new-output>`。
- 證據 hash 自 I10 起固定 LF。WordNet 資料不入 Git，為實驗專用選用資源。
- NLTK 自身的下載器會拒絕走代理（SSRF 防護）。**不要設 `NLTK_ALLOW_PROXIED_URLOPEN`**；
  改用 curl 取檔後讓 NLTK 只讀本機檔案（I11 的做法），並 `chmod -R go-w` 資料目錄。
- eventlet 0.36.1 在 Python 3.13 不可用（倉庫目標 3.10，測試未用到）。
- `httpx` 被 `app/llm_service/adapter/llm_google.py:26` 與 `llm_openrouter.py:14` 在模組層直接 import，
  但 `requirements.txt` 未列，只靠 `openai` 間接帶入。I10／I11 都未修（屬 Docker build 範圍）。

## 交付

增量 commits，最終 SHA、指定與完整回歸結果、可重現證據、改善及退步、明確未測量、
獨立自我稽核、待 Owner 決策及下一步。沿用既有個人分支 push 授權，推前驗秘密與保護路徑，
推後核對遠端 SHA。**若沒取得真人標註，科學驗收繼續保持未完成。**
