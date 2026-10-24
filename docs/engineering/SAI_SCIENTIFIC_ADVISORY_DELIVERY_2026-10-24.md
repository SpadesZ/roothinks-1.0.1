# I07 交付：科學完整性建議

**判定：可量測的工程切片有條件通過；科學判讀品質未通過。** 作者可逐句核對來源、條件及警訊，追加自己的理由；系統不改結論、不用教授或科學建議封鎖寫作、保存、修訂及匯出。外部矛盾召回僅 3.1%，不能把這個版本當成可靠科學判定器。

程式 SHA：`ba2448014e234b4547e570c5d2448c647480285a`。起點：`52f728740cba7b30bb7c18636deed3a31c4a521d`；隔離分支：`codex/sai-scientific-advisory`。後續文件提交與遠端 SHA 以 Git 為準；本文件不自我引用尚未產生的提交。

## 增量提交及根因

| 提交 | 交付 |
|---|---|
| `f0a18be` | 在引擎與外部 gold 開封前固定產品及盲測契約 |
| `4f5faf8` | session API CSRF、專案選單 ACL、Socket 排隊／結果／廣播撤權及登出撤銷 |
| `1810a83` | 原文與來源逐句對照、部分條件／模擬／否定警訊、不可變報告、追加作者回應、生成後建議 |
| `58fab86` | 固定與測試 SciFact 準備、推論、評分程式，推論不讀 gold |
| `8f8e558` | 分析中撤權再驗、過時回應恢復、版面遮擋、XHR 上傳 CSRF、科學歷史備份回歸與控制報表 |
| `ba24480` | 生成報告在切換章節後不能被誤標為另一章的建議 |

I06 的檢查證明來源／版本／條件字串可傳遞，沒有判定輸出主張是否受證據支持。重新執行原觀測，兩個未支持敘述及一個缺條件敘述仍能輸出。此為原流程的科學能力缺口；本輪不靠刪除原稿使它「通過」。

新純規則引擎使用原句與字元 offset，提供前三個文字候選、來源 ID／定位／指紋、未明示條件、可能數值差異、模擬誤寫為實測、部分方向／否定與外推警訊。只判讀最相近候選；不把不同來源的條件合成同一實驗。未知明示棄權，文字對齊也不是科學證明。上限明確報錯而不截尾。沒有新 dependency 或新 DB schema，重用 `RevisionLog`。

報告保存被比較的原文及來源快照，來源由伺服器取得；客戶端不能改來源原句、定位、actor 或 `scientific_proof`。來源變更時保留歷史報告、標示過時；舊檢視回應回 409。作者重新載入仍保留未送出的理由，可針對明示的歷史快照回應。沒有自動改稿或核准狀態。

生成後比較僅使用已選人類決策中的證據／條件，沒有選證據時明示未知；比較或報告保存故障會標示 unavailable，保留原始模型輸出。手動報告可比較未保存原文；它不是目前稿件的認證。稿件變更後須重新選原文比較，不宣稱全部稿件自動追蹤。

## 先重現的安全與可靠性缺陷

| 重現 | 修補與證據 |
|---|---|
| `i07-security-before` 六失敗：未共享專案列舉、缺 CSRF、排隊後／provider 返回前撤權仍 dispatch／交付、登出舊 Socket 仍保存 | 專案清單過濾；unsafe session API 驗 CSRF；dispatch 前與 provider 後重驗當下 ACL；登出用追加紀錄持久撤銷 session；綜合指定回歸 94 通過 |
| `i07-broadcast-before` 一失敗：撤權後仍收 peer update | 廣播逐一重驗當下 membership、章節／全文與登出狀態，不信任舊 room |
| `i07-analysis-revoke-before` 一失敗：分析中撤權仍回 201、新增報告 | 共用保存函式在分析後重驗當下章節權限；拒絕且不新增報告 |
| XHR 控制重現缺 `csrf_token` | 唯一既有 XHR 上傳 handler 加入同源 token；Node 與真瀏覽器登記／原件保留通過；不為此放寬後端 CSRF |
| 新 API 首輪三失敗：不存在的 section validator 類型 | 重用既有 `_safe_section`；保留原授權及狀態斷言 |
| 新 UI：工作列蓋住自主性說明、舊報告可能跨章誤標 | 修顯示層級、真瀏覽器檢查說明未被遮住；跨章提示先切回原章，不呈現錯章報告 |

CSRF fetch 傳輸只加同源 unsafe header，保留 Request、正文、取消訊號與 credentials。beacon 用表單 token 及原 JSON。登出撤銷不依賴會被閒置統計自動結束的 `UserSession`。限定作者仍不能藉報告猜來源、讀未交付證據、隱藏章節或全文匯出。

## 外部盲測：結果與界線

來源：Wadden 等（2020），*Fact or Fiction: Verifying Scientific Claims*，[AI2 SciFact](https://github.com/allenai/scifact)。Claims／證據標註 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；摘要來自 S2ORC、[ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/)；倉庫程式 Apache 2.0。[官方授權](https://github.com/allenai/scifact/blob/master/LICENSE.md)，blob `18a738e109d7e3b28935a75d2134c5ccd602f403`。本輪沒有改動或發布原資料集，正文及 gold 留新 TEMP。

完整使用 dev 的 **300 claims／283 篇引用文獻**，不按標籤或結果挑樣本。推論只取得 claim、cited_doc_ids 的摘要及原句 offset，不取得 label／rationale／gold evidence 來源選擇。Gold 由原資料集作者既有標註固定；不是本實作者撰寫。先固定引擎及評分工具，再隔離 gold、產生全部預測並記錄 hash，最後評分。沒有在開封後調整引擎或重報盲測。這是公共開發集的程序性 label-blind，不能稱新隱藏測試或新聘專家獨立重標。

| 官方 gold \ 預測 | 文字支持 | 可能矛盾 | 未知 | 錯誤 |
|---|---:|---:|---:|---:|
| SUPPORT | 1 | 0 | 123 | 0 |
| CONTRADICT | 0 | 2 | 62 | 0 |
| NEI | 0 | 0 | 112 | 0 |

| 指標 | 支持 | 矛盾 |
|---|---:|---:|
| TP／FP／FN／TN | 1／0／123／176 | 2／0／62／236 |
| Precision | 100%（僅 1 個陽性） | 100%（僅 2 個陽性） |
| Recall | 0.806% | 3.125% |
| FPR／FNR | 0%／99.194% | 0%／96.875% |

明確判斷覆蓋率 **1%**，未知 **297／300**，推論錯誤 0。高 precision 來自極少數明確輸出，不能宣稱有效判讀；UNKNOWN 是棄權，不能把 112 個 NEI／UNKNOWN 當作全數正確偵測未支持。

對 188 個有官方 rationale 的主張，使用官方 rationale 聯集作句子定位量測。首候選 precision **48.68%**、recall **25.14%**（TP 92、FP 97、FN 274）；前三候選 precision **34.69%**、recall **53.55%**（TP 196、FP 369、FN 170）。不是主張語意支持的 accuracy，也不以 gold 選句。共同引用不當作獨立文獻重複計數，沒有因果改善或顯著性主張。

固定 I06 合成控制：外推、缺溫度、模擬／實測混淆、無證據共 **4／4** 產生預期警訊；六例原文字串不變，來源 quote offset 保持精確。明示的 19 個條件詞比較為 **TP 6、FP 0、FN 0、TN 13**。分母排除無可用來源的條件比較，逐例保留在機器證據。這僅是事先固定合成詞表；SciFact 沒有本產品所有條件／模擬標籤，**獨立人工條件與模擬 gold 仍缺**。控制報表器在外部評分後補寫，沒有更改既有控制、引擎或外部評分程式。額外 15 項引擎測試涵蓋方向、否定、模擬負例、數值、提示注入、來源條件分離、上限及因果反向棄權。

主要 hash：archive `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`；inputs `0fde332e274aee1de8083b4895fa9e6368568a95add06dc97fdbc1e55e17221b`；predictions `e77efb443153b09468689bc36ddfdb484d7c6a12223072f54ad0a17b2b1f3448`。各資料檔、gold、引擎及 runner hash 見機器證據。

## 實際回歸範圍

- 完整 pytest 在乾淨 `58fab86` 執行：**1,352 通過、0 失敗、1 略過**，887.22 秒，1,265 個棄用警告。略過為 Windows 帳號不能建立 symlink；I06 的 Linux 對應案例有歷史證據，本輪未重跑 Linux 安全測試。
- 完整回歸之後的保存前撤權／歷史還原等修補，在乾淨 `8f8e558` 執行指定回歸 **43 通過**。另一次較廣指定回歸 72 通過。它們重疊，**不得相加，也不聲稱完整回歸在最終 SHA 執行**。
- **9 支 Node 行為檢查**通過；變更 JS 語法檢查通過。原始 before／失敗／修復 receipts 與 hash 保留；其中測試路徑拼錯、未實作前 collection error、測試誤期待 CSRF 400／beacon `ok`，均區分工具／契約錯誤，沒有移除拒絕斷言。
- 真瀏覽器於最終程式 `ba24480`、新啟動的離線 localhost fixture 驗三流程：無教授建研究／兩版／真 DOCX；教授註冊、邀請接受、版本審閱、作者回應、撤權及保留歷史；限定團隊章節保存、全文拒絕與退出保留歷史。
- 同版科學瀏覽器驗逐句來源、原稿不變、來源變更 409、重新載入保留理由、追加回應、無證據仍保存／匯出、390px 面板、生成報告跨章提示，以及 CSRF XHR 原件保存／登記。合成上傳控制不宣稱完成 PDF 解析或科學內容驗證。
- 真 schema 備份還原比較科學報告及回應 payload 逐字相同，保留其他研究／決策／教授撤權與歷史。既有檔案、DB、WAL、部分失敗與還原不可假成功回歸維持。

重現（在隔離、停 dotenv、清 provider／auth／DB 環境、離線模型及全新測試目錄執行）：

```text
python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider --tb=short --show-capture=no --junitxml <new-evidence.xml>
node test/js/test_csrf_transport.cjs
node test/js/test_origins_upload_csrf.cjs
python scripts/scientific_control_report.py <new-controls.jsonl>
```

外部資料從[官方 archive](https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz)取得，先核對上述 hash，只取 `corpus.jsonl`、`claims_dev.jsonl` 至公開資料 TEMP。使用 `58fab86` 的凍結工具依序執行；`prepare` 目的目錄與所有輸出都必須不存在：

```text
python scripts/scientific_benchmark.py prepare <data-dir> <new-blind-dir>
python scripts/scientific_benchmark.py predict <new-blind-dir>/inputs.jsonl <new-blind-dir>/predictions.jsonl
python scripts/scientific_benchmark.py score <new-blind-dir>/evaluator-only-gold.jsonl <new-blind-dir>/predictions.jsonl <new-blind-dir>/metrics.jsonl
```

瀏覽器用已安裝 Playwright／Chromium 執行 `sai_browser_fixture.py --autonomy`、`sai_autonomy_browser.cjs`、`sai_scientific_browser.cjs`；後者的 `SAI_SYNTHETIC_ROOT` 必須指向該次合成站 TEMP，並以 `SAI_TEST_PYTHON` 指定測試 Python。不使用正式站或研究檔案。

## 自我稽核、限制及下一階段

1. 本輪是同一實作者的自我稽核，沒有冒稱新獨立專家或第二代理審查。外部 gold 獨立於本實作，但資料公開且領域以生醫摘要為主。
2. 文字規則無法可靠建立語意支持、處理跨句論證、跨來源矛盾、跨指標數值、單位換算、完整中文條件、因果及全文證據。99% 棄權與 96.9% 矛盾漏報已實測，科學品質驗收維持未通過。
3. 規則警訊可能是不同條件／不同指標或鄰文限制，作者有權不同意。人類回應、教授確認、低誤報與來源 hash 都不是科學證明。
4. 指定通道重驗不等於跨主機原子撤權，也不能追回撤權前已下載內容。本輪未做全面滲透、分散式 Socket、多主機、真斷電、正式站容量或全部舊頁可用性驗收。
5. 手動比較可用未保存原文，僅保存原文及報告，沒有將所有建議自動連結至全部稿件版本。現有 COC-05 人類決策依賴仍沿用；未追蹤範圍不能稱完整 lineage。
6. 下一工程階段應先由獨立領域審查者固定真實研究的條件／模擬／因果 gold、保留集及可接受誤報漏報，再接現有模型介面的語意建議和校準棄權、重做盲測。專家 gold／保留集的取得及真實模型執行須另有明確資料及資源授權；不能用這批已開封 dev 成績作新的盲測。

只提交程式、規格、測試與合成／公開評估聚合證據。原 D: 工作區保持 `60cf9f7` 且乾淨；沒有讀取或修改正式研究資料、merge main、部署或操作 LAVA-Cowork 上游。推送僅至既有授權的 `personal` 新分支；遠端核對由最終交付訊息記錄。
