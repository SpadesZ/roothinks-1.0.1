# Python 3.12 本機遷移驗證交付

日期：2026-10-25。分支 `codex/local-python312-validation`，I13 基準 `b7a7bdd8f3abaab566029e899b5e26e6d34cc6d5`。**僅本機提交，未 push／merge／部署。** 不讀正式研究資料、不改科學引擎、I14 或 LAVA-Cowork。

## 判定與邊界

**本機候選的環境相容性已有可重現證據；建議進入單一分支整合驗證，暫不核准正式切換。** 原 production Dockerfile、requirements、Compose、app 與 main.py 均未更動。這是候選環境驗證，不是科學可靠性或全站安全驗收。

本機與雲端有 29 項依賴差異及不同 Debian base。完整 build／CPU Torch／真 NLLB 的本機成功，不能自動填成雲端 Dockerfile 通過。具體順序與接手 prompt 見[整合方案](LOCAL_PY312_INTEGRATION_PLAN_2026-10-25.md)。

## 凍結版本與提交

| SHA | 內容 |
|---|---|
| bdc575e | 本機驗證契約與 Owner Gate |
| 18c9325 | Python 3.12 候選、hash lock、公開模型 commitment、runtime checks |
| fdea195 | 真瀏覽器資產核對與隔離 fixture adapter |
| d4b2963 | runtime probe 採 canonical distribution version lookup |
| 1df5d8d | 明示離線 Google SDK、容器 SQLite adapter、真 PDF／恢復、完整 regression runner |
| 最後交付 commit | 文件、驗收與 EasyOCR 公開權重 probe；精確 SHA 由 Git log／本機 bundle 給出 |

最後完整回歸的 source 為 **`1df5d8deacfd6ab51a341a60f6e5ca55751dc037`**；後續只有驗證工具與文件。app、科學引擎、Dockerfile 候選及 lock 均未再改。瀏覽器測的是同一產品 tree 的 d4b2963，當時使用的 hook 變更已納入 1df5d8d。不能把文件 commit 說成所有測試當時的 SHA。

## 本機驗收矩陣

| 項目 | 實測結果 | 未涵蓋 |
|---|---|---|
| 完整 Docker runtime | Python 3.12.15／bookworm／linux-amd64，apt、CPU wheel、真 NLLB bake、非 root，完整 build 通過 | 雲端 trixie Dockerfile、其他 CPU 架構 |
| 固定相依 | 109 套件版本與 wheel hashes、base digest、Debian snapshot；pip check 通過 | 套件全面安全稽核；固定來源日後仍需安全刷新 |
| CPU Torch／TorchVision | 2.6.0+cpu／0.21.0+cpu，無 CUDA，原生 NMS 正確 | GPU、效能／正式容量 |
| OpenCV | 單一 headless 4.11.0.86；PNG 往返通過 | 與現行雙 provider／NumPy 2.2.6 的數值逐值一致性 |
| NLLB 真權重 | 固定公開 revision／SHA-256，嚴格 state loading、safetensors、離線單句與 batch adapter 通過；缺模型 ready=False | 翻譯品質。tokenizer 警告仍未完全定位；12 個控制 token IDs 相同不等於全面正確 |
| EasyOCR 真權重 | SDK MD5 與下載 SHA-256 留存，en+ch_tra／en 離線載入及推論；英文固定控制讀對 | 雙語模型有英文字錯讀；繁中準確度、3.10 比較 NOT MEASURED |
| 兩種 Gunicorn worker | Eventlet 0.36.1 與 gthread：HTTP 登入、session、Socket polling 握手、PDF 登記、hash、恢復兩次均通過 | 真 provider、所有認證配置、負載與多主機 |
| 最後完整 Python 回歸 | **1,499 passed／50 skipped／0 failed／0 errors**，643.27 秒；1,291 warnings | 四個 targets 含 integration_smoke；50 個略過須照實保留，理由如下 |
| I13 八個環境錯誤 | 原兩個 cv2 collection 模組各 6 測試、原六個 git scope tests 都執行且通過 | 不是八個產品 bug，也不代表所有原環境相同 |
| 真瀏覽器三流程 | 獨立研究、選用教授、團隊限定寫作；加入／撤權／移除教授後保留歷史、保存與真 DOCX；原 assertions 不變 | Google OAuth／Drive SDK 不可用，相關功能未驗 |
| 科學建議自主性 | 原稿保留、無證據保存／匯出、章節隔離、來源變更、重核等原科學瀏覽器情境通過 | 科學品質／真專家 holdout NOT MEASURED |
| 有效 PDF | 一頁合成有效 PDF 真瀏覽器 XHR/CSRF 上傳、原件 byte hash、同 ID 恢復兩次、Poppler 文字／轉圖、Tesseract 正確讀取 300 K | 沒跑整份真論文的完整解析／翻譯管線；沒有授權私有論文樣本 |
| 部分失敗／歷史 | 完整回歸包含原件不可覆寫、DB/library 部分失敗、same-ID 恢復、hash tamper 等既有負向測試 | 正式資料 restore、斷電、多主機及雲端 volume |
| 回退 | 原 3.10 image 在新合成 fixture 可啟動；正式容器 ID/image 保持相同且 healthy；只停自建 QA 容器 | 未切換正式服務，也沒對正式資料做回滾演練 |

略過：34 個 NLTK POS／VerbNet、13 個 WordNet、2 個 Windows 路徑別名（本輪 Linux）、1 個歷史 commit 32e1949 在乾淨 context 中不可取得。未下載 I14 的語意資源，也未放寬 assertions。

## 主要映像與資料保全

- base：`python:3.12-slim-bookworm@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0`；snapshot `20261024T000000Z`，保持 apt 簽章；Check-Valid-Until 只為固定歷史 snapshot 關閉。
- 完整 runtime：`sha256:b0b93fb77ea99b306723d8795431cb0ee7c0e54b980b79fb224d71211f317017`。
- 最後 verification：`sha256:abf80355d80247bc3a802f26361155211d8d9d9d5a9be9d81ae95f3c72976ba8`，source 1df5d8d。
- 正式容器保留：`5a7d0832977066954bea86266c53e33513b0b285d9664d3b3493f9995cf05537`；舊 image `sha256:47d74fc67670303dfa0c3916ed00bbfe935c20890eddf3a0446679b253f4baf9`。僅讀容器 ID/image/health，未 exec 正式 app 或讀 env/data。
- source context 只拷 tracked files，排除 `.env`、data、papers、models、logs、output、`.git`、`.codegraph`；下載模型全部是公開指定來源。模型原權重不入 Git。
- NLLB 原權重約 2.46 GB，revision `f8d333a098d19b4fd9a8b18f94170487ad3f821d`；既有來源 CC-BY-NC-4.0。本輪沒有重新授予用途許可。

## 失敗與更正

所有失敗輪保存在 QA，沒有覆寫成成功。重要項目：初次 lock 仍含兩套 cv2，改成單一 provider；probe 查 distribution 名稱修正；公開 Google SDK 403，改為明示 SDK 不可用控制，未偽造 SDK；混用同一 fixture 稿件導致 browser 狀態污染，改新 fixture 後原 autonomy assertions 通過。change-impact 等待逾時沒有改產品或斷言硬過，未證明是 3.12 產品回歸。

有效 PDF probe 第一輪誤引入未安裝且產品沒使用的 pdfplumber；改用既有 Poppler 後通過，原失敗保留。Windows `TEMP`／`TMP` 需同設；SQLite 寫入保持在 Linux 容器。打錯 image tag 與 baseline 直接唯讀 /app 的失敗都保留；後者改在新 tmpfs sandbox 啟動，沒有碰原 image 的正式資料。

一度把工具畫面省略的 command 參數誤認為紀錄缺漏；直接讀 JSON 驗證原先 46 個 argv 都在。不存在原始 receipt 遺失參數的缺陷。新 runner 是清楚的重現入口；已啟動的最後凍結重跑正常完成後才列入驗收。

清理工具初次假設容器停止後仍可 inspect，但三個測試站原本使用 `--rm`，停止後已自動移除。後續改以精確名稱查存活狀態；確認三者皆不存在、正式容器保持原 ID/image/healthy。沒有執行 Docker 全域清理或刪除正式資料。

## 證據與重現

機器證據：[LOCAL_PY312_EVIDENCE_2026-10-25.json](LOCAL_PY312_EVIDENCE_2026-10-25.json)。原始 QA：`D:\Documents\RootThinksValidation\py312-20261025`，包含每輪 receipt／stdout、JUnit、資產 SRI/integrity、模型 commitment、screenshots 與 source manifests。雜湊清單由 SHA256SUMS.json 固定，不拷原始私有研究檔。

建置（PowerShell；新輸出目錄不可已存在）：

```powershell
python validation/python312/local_build.py stage D:\Documents\RootThinksValidation\py312-reproduce
python validation/python312/local_build.py build D:\Documents\RootThinksValidation\py312-reproduce --target verification --label v1
```

再對實際 image ID 執行 `python validation/python312/regression.py sha256:<image-id> D:\Documents\RootThinksValidation\py312-regression-reproduce`。runner 留完整 argv，以 `--network none`、2 CPU／4GB、新合成 DB 與唯一 evidence bind 執行四個 test targets，不掛正式 volume。

Runtime checks：`python validation/python312/runtime_checks.py capabilities <new-json>` 與 `model <new-json>` 應在候選容器執行。EasyOCR：先 `python validation/python312/easyocr_probe.py download <new-public-model-dir>`，再在同候選 `--network none` 以唯讀 bind 執行 `check <container-model-dir>`。確切已執行命令與 input hashes 見 JSON 及 QA receipts。使用現有原 browser CJS 與本機 hook；Google SDK 阻擋、assets manifest 與 host/container synthetic root 環境變數須如 receipt/context 指定。

## 自我稽核與下一步

可重現的工程證據已完成；沒有讓測試數代替研究品質。沒有獨立人類 reviewer，本節是 agent 自我稽核。也沒有測正式研究資料、完整部署切換、provider 品質、壓力容量、真專家標註或 fresh blind holdout。

Owner 下一步只需選：**建議准許第三條隔離分支整合與重驗**，或暫緩 migration。兩者都不等於授權上線。整合前不要並行推同一 GitHub 分支；所有上傳／部署仍需新的明確授權。
