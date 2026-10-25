# 給下一輪（I13）的接手 prompt

你是 RootThinks Lead Engineer 與 Scientific Reliability Researcher。接續 I12。溝通及實作報告用繁體中文。

## 從哪裡接手

- 個人倉庫 `https://github.com/SpadesZ/roothinks-1.0.1.git`，起點為 `codex/sai-i12-browser-and-semantic-probe` 最新已推送提交。先 fetch 並核對 SHA。
- I12 最終**實作**凍結 `567e630`；之後只有文件與證據。I10（`9deaa94`）與 I11（`16f1be3`）基線未被動到。
- 另建 `codex/` 隔離分支。LAVA-Cowork 不可碰；不 force push、不 merge main、不部署、不改正式資料。

## 必先讀

1. `AGENTS.md`、`docs/SAI.md`、`docs/ACCEPTANCE_MATRIX.md`（I12 段落）。
2. `docs/engineering/SAI_I12_DELIVERY_2026-10-25.md`、`SAI_I12_EVIDENCE_2026-10-25.json`。
3. `I11_PROTOCOL.md`（預先固定的門檻仍有效）、`SAI_I11_SEMANTIC_NEGATIVE_RESULT_2026-10-24.json`、`SAI_I10_SCIENTIFIC_PHENOMENA_2026-10-24.json`。

## 不可誤讀的 I12 結果

- **瀏覽器範圍已擴大並通過**：四次執行全 passed。新增 `sai_assets_browser.cjs` 驗 study 頁面的
  Split.js（實測 2 個 gutter）與 KaTeX（實測產生 `.katex` 節點）。plotly 已從 npm 取得並以樣板
  SRI 驗真（6/6 相符）。**不要重做**，除非改了前端或樣板。
- **`cdn.plot.ly` 這個網域未授權**。I12 的做法是把請求導向本機已驗真的 npm 資產，
  **對該網域沒有發出任何連線**。要改這個邊界必須先問 Owner。
- **I11 的證據有兩處錯誤已更正**：plotly 其實每條流程都請求且失敗；`accounts.google.com/gsi/client`
  是第四個外部網域且在本容器可達。根因是 I11 只記錄被導流的請求。I12 的包裝已改為記錄全部請求與失敗。
- **語意資源可行性已做完，結論是「有路但 margin 薄」**。不要重跑可達性探測：
  HuggingFace 與 github.com 不可達；spaCy 模型是 soft-404；stanza 模型只在 HuggingFace。
  可得的是 NLTK 語料（wordnet／verbnet／propbank／perceptron tagger，最小集約 81 MB）
  與 npm 的 wink-nlp／compromise（MIT）。
- **關鍵數字**：謂詞詞表 48 → 4,744，來源句零謂詞 68.9% → 1.2%。但 Python 路徑
  （產品語言）只有 **39.0%** 的子句是單一謂詞、**44.4%** 仍有多謂詞；JS 的 wink 是 48.1%／13.7%。
  **同一想法兩種實作差 9～30 個百分點 → 效果對實作細節高度敏感，不可假設。**
- **案 674 與案 249 所有候選都未解**（名詞化需 WordNet 派生關係、縮寫對照沒有候選提供）。
- **科學能力三輪未變**：SUPPORT TP 1、CONTRADICT TP 0、UNKNOWN 299。
- **確認 P0 共 0 個。** `httpx`／`sqlalchemy`／`werkzeug`／`socketio` 是未宣告的直接相依，
  但都由已宣告套件傳遞保證，今天不會壞。

## 下一輪優先工作（照 I12 建議的閘門順序）

1. **閘門 A：可量測的誤報預算。** 不改產品，用凍結基線做離線實驗：
   VerbNet/PropBank ＋ NLTK 詞性 ＋ 子句切分 ＋ 子句內規則論元連結。
   **先預先登記採用門檻**，必須包含 gold SUPPORT／NEI 上的決定性誤報上限，
   以及跨子句假 frame 的人工抽查比例與抽查方法。再在已曝光 dev 上量測。
   參考 `scripts/semantic_resource_probe.py` 與 I12 的子句切分量測。
   **失敗是可接受的結果**，保留為負結果即可。不要為了過門檻調參或硬寫個案。
2. **不要在閘門 A 之前整合任何語意引擎。** I11 已證實鬆綁單一謂詞紀律會讓跨子句假 frame
   生效（案 837 精確度 0/1），而擴充後有 4,744 個謂詞在推動它。
3. **待 Owner 的三個決定**（見 I12 交付文件第七節）：PropBank／VerbNet 授權是否可用於產品、
   是否接受 81 MB 新資料相依與 68 句/秒延遲、是否開放 HuggingFace 以取得真 NLI。
   **不要自行開放網路權限。**
4. **可自主做的小項**：若 Owner 同意閘門 C，把 `httpx` 等四個直接相依補進 `requirements.txt`
   （會動到 Docker build，I12 未自行做）。
5. **工程驗證。** 新功能要有負向／對抗測試與真瀏覽器操作（方法已備好，見 I12 交付文件）。
   最終 source 凍結後跑完整回歸。更新逐項驗收。

## 執行環境注意

- 完整回歸：`python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider`。
  I12：產品環境 1,470 通過／16 略過／0 失敗。
- 真瀏覽器：Node `/opt/node22/bin/node` v22.22.0、Playwright 1.63.0、
  Chromium `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`。**不要跑 `playwright install`。**
  導流包裝與 vendor 建法見 I12 交付文件；**不要改產品碼或既有 assertion**。
  腳本會在 cwd 產生 `output/`，請在另一份 clone 執行。
- NLTK 的下載器會拒絕走代理（SSRF 防護）。**不要設 `NLTK_ALLOW_PROXIED_URLOPEN`**；
  改用 curl 取檔後讓 NLTK 只讀本機檔案，並 `chmod -R go-w` 資料目錄。
  FrameNet 1.7 解壓後 847 MB，**不要裝**。
- SciFact dev 自公開資料重建：`https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz`
  再跑 `python scripts/scientific_benchmark.py prepare <data-dir> <new-output>`。
- 證據 hash 自 I10 起固定 LF。eventlet 0.36.1 在 Python 3.13 不可用（倉庫目標 3.10）。

## 交付

增量 commits，最終 SHA、完整回歸結果、可重現證據、改善及退步、明確未測量、
獨立自我稽核、待 Owner 決策及下一個決策閘門。推前驗秘密與保護路徑，推後核對遠端 SHA。
**若沒取得真人標註，科學驗收繼續保持未完成。**
