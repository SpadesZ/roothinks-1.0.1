# I13 交付：Gate A 負結果、Gate C 相依補宣告、Gate B 授權核對

日期：2026-10-25。起點 `29aefb0`（I12 最新已推送提交）。
隔離分支 `codex/sai-i13-gate-a-and-deps`。**產品引擎未被動到**（`advisory-rules-4`，hash `4c2034bf…`）。

**判定：**
- **Gate A：未通過。** C1、C2 兩個候選都沒過 G6 與 G7，不進 Gate B。保留為負結果。
- **Gate C：完成。** 四個直接相依已補宣告，鎖定結果零差異，Python 3.10 全新安裝、`pip check`、完整回歸都已執行。
  生產 Dockerfile 在本環境卡在 apt 層（環境限制，見第二節）。
- **Gate B：依決策延後。** 只做了版本別授權核對，沒有整合、沒有打包 PropBank、沒有擴大網路。
- **確認並修正 1 個重大缺陷**：Python 3.10（生產執行環境）下**每次上傳 PDF 都失敗**（503）。
  原因是 `dd456aa` 用了 3.11 才有的 `hashlib.file_digest`。只在 SAI 分支線，**不在 main**。修正 `5b02f3c`。

## Owner 決策的執行結果

| 決策 | 執行 |
|---|---|
| Gate A 核准：預先登記、隔離的科學評估；量測決定性誤報、召回、棄權、否定／條件錯誤、跨子句假 frame；報分母、退步與失敗案例；SciFact dev 只是開發證據 | **完成。** 協議在量測前提交（`87f7b39`）。結果見第一節與 [機器證據](SAI_I13_GATE_A_RESULT_2026-10-25.json)。dev 已曝光，結果只是開發證據 |
| Gate B 延後：不整合、不打包 PropBank I、不擴網路；核對版本別授權（含 NLTK tagger 資料）；81 MB 與 68 句/秒只是初步量測 | **照辦。** 授權核對見第三節。三項禁止事項都沒發生。81 MB／68 句/秒本輪沒有重新量測，維持「初步」 |
| Gate C 核准：在獨立 commit 修正未宣告的直接相依；驗證全新安裝、Docker build、相容性、完整回歸 | **完成。** 獨立 commit `6b10247` 只改 `requirements.txt`。驗證見第二節與第五節 |
| 研究者自主：科學警示永不阻擋寫作、儲存、匯出 | **維持。** G2 自主性檢查兩個候選都通過；產品引擎沒改 |
| holdout 保持密封、專家驗證保持待定 | **維持。** 沒讀 evaluator key、沒解密 holdout、真專家 0 人 |
| 自主調查已確認的重大缺陷 | **確認 1 個並已修正**（Python 3.10 上傳全部失敗，第四節）。另發現 3 個既有風險，都不是本輪造成，未自行修改 |
| 保留負結果與分支邊界；不 merge main、不部署、不碰 LAVA-Cowork | **維持。** I08～I12 分支與 main 的遠端 SHA 推送前後都已核對 |

## 一、Gate A：子句範圍關係框架（預先登記）

### 做了什麼

協議 [I13_GATE_A_PROTOCOL.md](I13_GATE_A_PROTOCOL.md) 在任何量測前提交。候選只在凍結基線回傳
`unsupported_sentence_structure` 或 `different_arguments_or_conditions` 時才介入，其他判定與
保守閘門一律不覆寫。規則寫死：子句切分、每子句恰好一個謂詞、被動、否定、同族／同詞根比對。

- **C1**：謂詞詞表 = VerbNet 2.1 ∪ 基線關係族動詞。
- **C2**：C1 再加 PropBank。

實驗引擎用「凍結原始碼 + 附加覆寫」產生，不改產品檔案。引擎 hash：
基線 `4c2034bf…`、C1 `12e2ca59…`、C2 `97df998f…`（改完檔頭後重新產生，hash 不變）。

### 結果（SciFact dev 300 筆；gold SUPPORT 124／CONTRADICT 64／NEI 112）

| 指標 | 基線 | C1 | C2 |
|---|---:|---:|---:|
| 決定性判定 | 1／300 | 2／300 | 2／300 |
| 決定性判定正確 | 1／1 | 2／2 | 2／2 |
| 決定性誤報（gold SUPPORT／NEI 上判 CONTRADICT，或 gold CONTRADICT／NEI 上判 SUPPORT） | 0 | 0 | 0 |
| 跨極性錯誤（SUPPORT↔CONTRADICT） | 0 | 0 | 0 |
| SUPPORT 召回 | 1／124（0.8%） | 2／124（1.6%） | 2／124（1.6%） |
| CONTRADICT 召回 | 0／64 | 0／64 | 0／64 |
| 棄權（UNKNOWN） | 299／300 | 298／300 | 298／300 |
| 與基線不同的判定 | — | 1 | 1 |
| 相對基線的退步 | — | 0 | 0 |
| 涉及否定的決定性判定 | 0 | 0 | 0 |
| 有條件警示的決定性判定 | 0 | 0 | 0 |

**C1 和 C2 的結果完全一樣。PropBank 沒有帶來任何決定性效益。**

唯一變動是 id 589（UNKNOWN → SUPPORT，gold 也是 SUPPORT）。但它**對的原因是錯的**：
主張與來源都含 "middle-aged adults"，tagger 把形容詞 *aged* 標成動詞，產生假 frame
`主詞 middle｜謂詞 age｜受詞 adult`，兩邊對上就判 SUPPORT。主張真正的謂詞
*do not increase the risk* 因為同子句還有 *uses*，被「每子句一個謂詞」的紀律排除。

### 閘門

| 閘門 | C1 | C2 | 說明 |
|---|---|---|---|
| G1 產品對抗控制在候選下通過（硬） | 通過 | 通過 | 47 個產品控制 + 1 個替換生效探針，48/48 |
| G2 研究者自主（硬） | 通過 | 通過 | |
| G3 定位結果與基線相同（硬） | 通過 | 通過 | |
| G4 零跨極性決定性錯誤（硬） | 通過 | 通過 | |
| G5 決定性精確度 ≥ 0.80 | 通過 | 通過 | **分母只有 2，沒有意義** |
| **G6 SUPPORT TP + CONTRADICT TP ≥ 6** | **未過** | **未過** | 2 < 6 |
| **G7 稽核：決定性 frame 錯誤 ≤ 10%，抽樣跨子句 ≤ 20%** | **未過** | **未過** | 決定性 frame 2/2 錯誤（100%）；抽樣跨子句 C1 3/40、C2 2/40 |
| G8 新否定／條件控制 100%（硬） | 通過 | 通過 | 34 通過 + 2 strict xfail（xfail 是同詞根 block 案例，不是否定／條件控制） |

### 盲稽核（G7）

稽核封包不含 gold 標籤，seed 13 固定抽 40 個 frame。我先只看子句判，再用完整來源句複核。

| | CORRECT | CROSS_CLAUSE | WRONG_ROLE |
|---|---:|---:|---:|
| C1 決定性 frame（2） | 0 | 0 | 2 |
| C2 決定性 frame（2） | 0 | 0 | 2 |
| C1 抽樣（40） | 15 | 3（7.5%） | 22 |
| C2 抽樣（40） | 15 | 2（5.0%） | 23 |

否定範圍錯誤另外標註（不算進 G7）：C1 抽樣 3/40、C2 抽樣 2/40。例如 *unable to suppress* 沒被當成否定；
*not* 修飾形容詞或其他子句時仍套到謂詞上。這些 frame 這次都沒有變成決定性判定。

**失敗原因（系統性，不是個案）：**
1. 形容詞性分詞與連字號複合詞被標成動詞：*aged、estimated、so-called、K63-linked、angiotensin-converting、adjusted、membrane-attached、protein-coding、teaching*。
2. 名詞被標成動詞（*mean、follow up、like*），或反過來動詞被標成名詞（*blocks、promotes*）。
3. 停用詞表含 *we*，主詞只剩 *here* 這種副詞。
4. `\w+` 斷詞拆開連字號（*under-represented → under*）。
5. 無 *be* 的被動與縮減關係子句沒偵測到，角色顛倒。
6. *who、if* 不是切分詞；逗號切分會切斷括號內的並列，真主詞落到別段。

這些都源自「詞性標註 + 規則」沒有句法剖析。I11（WordNet 被動／名詞化）與 I13 兩次規則式嘗試
都卡在同一個根因。

### 必須誠實說明的地方

- 稽核者是實作者本人，不是專家。四個類別預先固定；**優先序與 WRONG_ROLE 的細項是稽核中寫下的操作化規則**，兩個變體一致套用。
- C1 R14、R17 在完整句複核後由 CORRECT 改判 WRONG_ROLE。
- 事後敏感度（不影響閘門）：若把 4 個「不及物動詞＋附加語受詞」改判 CORRECT，C1 抽樣變 19／18／3；G7 仍因決定性 frame 2/2 錯誤不過。
- 同詞根 *block* 案例的 strict xfail 是**看到失敗之後才標的**；預期結果沒改。
- 稽核封包含 SciFact 來源句，依 AGENTS.md 不入 Git。已記錄封包 sha256，可用腳本重建核對。

## 二、Gate C：補宣告直接相依

`6b10247` 只改 `requirements.txt`，補上 `httpx`、`sqlalchemy`、`werkzeug`、`python-socketio`。
下界沿用上游約束（取自 openai、Flask-SQLAlchemy、Flask、Flask-SocketIO），不比現有解析多加限制。

| 驗證 | 結果 |
|---|---|
| 解析差異（`uv pip compile --python-version 3.10`，修改前 vs 修改後） | 都是 143 個套件，**版本零差異** |
| 全新安裝（`python:3.10-slim`，Python 3.10.22，Debian 13） | 成功。httpx 0.28.1、SQLAlchemy 2.0.55、Werkzeug 3.1.9、python-socketio 5.17.0、openai 1.109.1、Flask 3.0.3、torch 2.6.0 |
| `pip check` | `No broken requirements found.` |
| 匯入煙霧測試 | 五個套件全部可匯入 |
| 生產 Dockerfile build | **卡在 `[2/10] apt-get update`**：`deb.debian.org` 走明文 HTTP，代理回 405 Method Not Allowed。PyTorch CPU index 與 HuggingFace（NLLB 預載）也不可達（000）。這是環境限制，不是本次修改造成 |
| 替代驗證 | 用驗證映像重現 Python 相依層（跳過 apt、PyTorch CPU index、NLLB）。torch 改從 PyPI 裝同版號 2.6.0（CUDA 變體，生產是 `+cpu`） |
| Python 3.10 完整回歸 | 見第五節 |

驗證用 Dockerfile 與代理 CA 只放在暫存區，**沒有入庫**。

## 三、Gate B：版本別授權核對（只核對，不整合）

來源：NLTK 3.10.4 的 `nltk_data` index.xml（sha256 `97dce5e7…`）。本機每個 zip 的 md5 都與索引的 checksum 相符。

| 資源 | 版本 | 授權（索引欄位與內附檔案） | 產品可用性 |
|---|---|---|---|
| averaged_perceptron_tagger_eng | JSON 版（md5 `729e2255…`，5.5 MB） | MIT（textblob-aptagger，Copyright 2013 Matthew Honnibal） | 授權可用；**訓練資料來源無法核實**（github 403） |
| averaged_perceptron_tagger | pickle 版（md5 `05c91d60…`） | MIT | **不要用**：pickle 可執行任意程式碼，改用 JSON 版 |
| WordNet | 3.0（md5 `b3f38606…`） | Princeton：任何用途可用，所有副本須附版權聲明 | 可用，須附聲明 |
| VerbNet | 2.1（md5 `427dac60…`） | 索引寫 "Distributed with permission of the author."；README 沒有授權條文 | Owner 提供「上游有商用授權，受其條款約束」；**上游網站在本容器不可達（000），我無法核實條款** |
| PropBank | 1.0（md5 `2397782c…`） | "Distributed with Permission"；建在 Penn Treebank（WSJ）上 | **不打包**（Owner 決策）。Gate A 也顯示它零效益 |
| treebank 樣本 | — | 只限非商業用途 | 不可用於產品 |
| punkt | — | 索引授權欄為 None | 授權不明，不可假設可用 |
| FrameNet 1.7 | — | CC BY 3.0 | 847 MB，排除 |

81 MB 與 68 句/秒是 I12 的初步量測，本輪沒重測，**不是生產核准**。

## 四、重大缺陷調查

### 確認並修正：Python 3.10 下上傳 PDF 全部失敗

- **怎麼發現的**：Gate C 的 Python 3.10 完整回歸。`test_upload_preservation` 8 項與端到端使用者旅程 1 項失敗，
  全部回 503「原件寫入未完成；未登記為可處理 PDF」。
- **根因**：`app/core_pro/literature/literature_batch_routes.py` 兩處用 `hashlib.file_digest`（Python 3.11 才加入）。
  生產 Dockerfile 是 `python:3.10-slim`。在 3.10 上，發布原件時拋出 `AttributeError`，被 `except Exception` 接住後回 503；
  上傳恢復（`recover_upload`）也一樣失敗。主機回歸跑在 Python 3.13，所以之前從沒看到。
- **影響範圍**：`dd456aa`（2026-10-23）引入。遠端分支中 I08～I12 與 `codex/sai-scientific-advisory`、`codex/sai-autonomy-integrity`、`codex/sai-verification-review` 都含有它；
  **不在 main**（`9655546`），也不在 `codex/sai-v1.1.0-20261023`、`codex/sai-v1.1.0-batch-a-b`。
  若這條分支線未修就合併並部署，研究者將**無法上傳任何 PDF**。
- **修正**：`5b02f3c`，改成 1 MiB 分塊 sha256（與 `research_backup._hash` 同寫法）。雜湊結果不變。
- **測試**：新增 `test_upload_and_recovery_work_on_python310_without_file_digest`，移除 `hashlib.file_digest` 後
  檢查上傳與恢復都成功。修正前在主機 3.13 上**重現失敗**，修正後 3.13 與 3.10.22 都通過。
- **同類掃描**：用 Python 3.10 編譯 `app/`、`scripts/`、`migrations/`、`evaluation/` 等 155 個檔案，語法錯誤 0；
  掃描 3.11+ 才有的 API（`datetime.UTC`、`tomllib`、`typing.Self`、`StrEnum`、`ExceptionGroup`、`TaskGroup` 等），
  只找到這兩處。`research_backup.py` 的 `is_junction` 有 `hasattr` 保護，沒問題。
- **舊分支都沒有改**（保留分支邊界）；修正只在 I13 分支。

### 既有風險（未自行修改）

發現 3 個既有風險，都不是本輪造成：

1. **兩個 OpenCV 套件裝進同一個 `cv2/`。** `requirements.txt` 要 `opencv-python<5.0`（得 4.14.0.94），
   `easyocr` 又帶入 `opencv-python-headless` 5.0.0.93。兩者都寫 `cv2/`，結果取決於安裝順序。
   驗證映像最後是 4.14.0.94。目前沒看到壞掉，但這是不穩定的組合。
2. **基底映像是浮動標籤。** `python:3.10-slim` 現在是 Debian 13（trixie）、Python 3.10.22。
   生產 build 會隨上游變動。沒有證據顯示已經壞掉。
3. **PyPI 解析的 torch 與生產不同。** 只看 PyPI 時 Python 3.10 會解到 torch 2.14.1，
   生產用的是 PyTorch CPU index 的 2.6.0+cpu。生產的 pin 在 Dockerfile，不在 requirements.txt。

## 五、回歸結果

最終 source 凍結 `5b02f3c`（之後只有文件）。

| 環境 | 結果 |
|---|---|
| 主機（Python 3.13，產品環境：無 NLTK 資料、未設 provider／DB 憑證） | **1,473 通過／50 略過／0 失敗**，1,523 項、206.5 秒。比 I12 多 1 個通過（新的上傳測試）與 34 個略過（Gate A 測試在無 NLTK 時略過） |
| Python 3.10.22 驗證映像（無網路命名空間、`git archive` 匯出的乾淨樹） | **1,454 通過／51 略過／0 失敗／8 錯誤**，224.3 秒。8 個錯誤全是環境：2 個 cv2 測試模組缺 `libxcb`（apt 層被擋），6 個 `test_source_contract_scope` 因映像沒有 `git` |
| Python 3.10.22，修正前（`9a87725`） | 1,444 通過／51 略過／**9 失敗**／8 錯誤。9 個失敗全是上傳 503（`hashlib.file_digest`），修正後歸零 |
| G1（候選引擎替換產品引擎，跑 47 個產品科學控制） | C1 48/48、C2 48/48 |
| Gate A 對抗控制（有 NLTK） | 34 通過、2 strict xfail |
| Gate A 對抗控制（無 NLTK，驗證退回基線） | 2 通過、34 略過 |

## 六、剩餘風險

- **科學能力仍未提升**：產品引擎 SUPPORT TP 1、CONTRADICT TP 0、UNKNOWN 299。已四輪不變。
- **規則式語意路徑已兩次負結果**（I11、I13），根因相同：沒有句法剖析。再調規則不會解決。
- **生產 Dockerfile 沒有端到端 build 成功**（apt 層被代理擋），cv2 相關兩個測試模組在驗證映像中因缺系統函式庫無法收集。
- **主機回歸跑在 Python 3.13，而生產是 3.10。** 本輪的上傳缺陷就是這樣漏掉的。建議往後每輪都跑一次 3.10 回歸。
- VerbNet 商用條款無法在本容器核實；tagger 訓練資料來源無法核實。
- 第四節的三個既有相依風險。
- 真人專家 0 人；holdout 未開封；科學驗收仍未完成。

## 七、獨立自我稽核

我反過來檢查自己的工作，以下是最可能被挑戰的地方：

1. **稽核者就是實作者。** 雖然封包不含 gold，但我知道協議與門檻。G7 結論不依賴邊界案例：決定性 frame 2/2 都是明顯的假謂詞，換人判也很難改變結果。
2. **操作化規則在稽核中才寫下。** 已在證據檔揭露。敏感度分析顯示放寬規則也不會讓 G7 通過。
3. **G6 未過與規則無關。** 只有 1 個新決定性判定，這是計數，不靠人工判斷。
4. **G5 的 1.000 沒有意義**（分母 2），我沒有把它當成正面證據。
5. **strict xfail 是事後標的。** 預期結果沒改，失敗原因寫在 reason 裡，並在測試檔頭與 commit 說明。
6. **Gate C 的 Docker 驗證是替代方案**，不是生產 Dockerfile 本身。差異（跳過 apt、torch 變體不同）已列出。
7. **上傳缺陷的修正超出 Gate C 原本的範圍。** Owner 授權「自主調查已確認的重大缺陷」，而且它會讓上傳全部失敗，所以我修了。修正是獨立 commit、雜湊結果不變、有先失敗後通過的測試。
8. **隔離 dockerd 到了 2 小時背景上限後停止。** 我沒有重啟它，改用已建好的映像層以 overlay 掛載、在無網路命名空間中跑 Python 3.10 回歸。映像層與 build 時相同。

## 八、下一個決策閘門

**Gate D（需要 Owner 決定）：規則式語意路徑要不要停？** 兩個選項：

1. **（我建議）核准一次「窄範圍、釘版本、驗 hash」的句法剖析模型取得**，只用於隔離實驗，
   沿用同一套預先登記方法（新協議、同樣的 G1～G8 精神）。I13 的失敗原因幾乎都是剖析器能解的
   （形容詞性分詞、名詞／動詞誤標、縮減關係子句、被動角色）。代價：要擴大網路權限
   （spaCy 模型在 github releases、stanza 在 HuggingFace，目前都不可達），並需要模型授權核對。
2. **停止語意擴充**，接受產品引擎維持高棄權（299/300）的保守狀態，下一輪改做專家驗證準備與工程項目。
   代價：科學關係偵測短期不會進步；真正的科學驗收仍要等 Owner 指派專家。

不管選哪個，Gate B 對 PropBank 的問題已可關閉：它在本實驗零效益，不需要為它做法務。

下一輪接手文件：[I14 接手 prompt](I14_HANDOFF_PROMPT.md)。
