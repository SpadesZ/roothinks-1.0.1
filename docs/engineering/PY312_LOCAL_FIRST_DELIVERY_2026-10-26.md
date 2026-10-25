# LOCAL-FIRST Python 3.12 整合交付

**正式遷移建議：NO-GO。** 工程候選已完成建置與回歸；deep-translator 的已知供應鏈通報仍未關閉，Google 真 OAuth／Drive 尚無授權測試帳號，獨立外部審查仍待 Owner 指派。本輪沒有 merge main、部署、正式資料操作或啟動 cloud handoff。

本輪分支 `codex/py312-local-first-integration-20261025`。先讀並比較本機 `433eec4` 與雲端 `e64c4e7`；保留兩原分支與舊證據，選用本機已驗證的 bookworm／CPU 路線。正式產品凍結 **`84b0ea9b0bf47b16afc71b002458ae5097a21405`**；交付 commit 只補證據、文件及已獨立執行的驗證工具。最後 SHA 由 Git log／remote 給出，不把文件 SHA 說成當時完整測試 SHA。

## 依賴與確認修復

- 原 29 項差異全部逐項列明：[調和表](PY312_DEPENDENCY_RECONCILIATION.md)與[機器表](PY312_DEPENDENCY_RECONCILIATION.json)。整合為 113 個固定套件；CPU wheels 含官方來源與 SHA-256，單一 headless cv2，移除無產品呼叫的 layoutparser 相依鏈。
- 安全更新包括 Flask 3.1.3、Eventlet 0.40.3、Cryptography 50.0.0、Filelock 4.2.1、Pillow 13、CPU Torch 2.13／TorchVision 0.28、Transformers 5.10.4、protobuf 5.29.6 與相容 Google SDK。不是把原本 29 項直接覆寫雲端 lock。
- 第一個安全候選 Transformers 5.10.0 已被撤回。保留該輪，改用未撤回的 5.10.4；正式 build 產生安裝報告並拒絕任何 `is_yanked` wheel。113 個實際安裝 wheel 都有 SHA-256，沒有 CUDA 套件。
- Transformers 5 移除 translation pipeline 的相容性失敗已重現；改用同一 NLLB 的原生 generate、固定來源／目標語言、既有輸出上限與 batch。來源不無聲截斷，執行期只讀本地 safetensors、不執行模型 repo 程式，模型共享狀態用鎖保護。科學判斷規則未改。
- NLLB 七個公開來源檔都固定 SHA-256，原權重、嚴格 state、safetensors 及離線重載在正式 build 驗證。EasyOCR 三個公開權重也預先驗 archive／payload SHA-256，執行期下載停用；補上繁中 Tesseract 語言檔。
- Google SDK 不可用時原程式會 TypeError；跨專案／章節舊回應會被套用。紅測試後修正：公開 browser 設定由環境注入，未配置不載 SDK；只請求 `drive.file`、檢查就緒／token 過期／撤權、固定 URL 結構、拒絕舊研究脈絡。沒有讀 OAuth secret 或真 Drive 文件。
- 重現頁面 10 秒 load 超時，trace 顯示未使用的 Plotly／Mermaid 阻塞。Plotly 只留在 PAQ；Mermaid 首次繪圖才載固定 10.9.6＋SRI。解析錯誤原本直接插入 HTML，已用對抗紅測試重現並改純文字；真瀏覽器 SVG、錯誤安全與原稿不變另驗通過。

## 凍結驗證

| 項目 | 實際結果 | 界線 |
|---|---|---|
| 正式 Dockerfile | Python 3.12.15／linux-amd64／bookworm、固定 base digest、HTTPS／簽章 Debian snapshot、CPU wheels、真 NLLB/OCR、非 root，完整 build | 本機正式候選，未部署 |
| 正式 runtime | `sha256:3f7391eaa7146963e64e805744e9c17d7ff734aac8ef72d903d6877fbcc77920` | source 84b0ea9 |
| Verification image | `sha256:b2f70583ff63b5d918edd1bbb688719a6961d0b42ea0521e5c5b76ba78f4bc6a`，由上述 runtime 衍生 | 測試工具不進 runtime |
| 完整 Python regression | **1,506 passed／50 skipped／0 failed／0 errors**，797.51 秒，1,290 warnings | 四個 targets：test/unit、tests、test/integration_e2e、test/integration_smoke |
| 原八個環境錯誤 | 兩個原 cv2 collection 模組各 6 tests、六個 git scope tests 都執行通過 | 不是八個產品 bug |
| Linux CAS／crash／backup 負向案例 | 完整回歸含獨立 OS 程序、版本競爭、程序死亡、部分寫入、WAL、損毀／未完成 restore 拒絕 | 不代表斷電、多主機或網路磁碟驗收 |
| Node | 11 支行為腳本全通過，含 Google 失效／撤權與 Mermaid 錯誤注入 | 真 provider 另列 |
| 原三支瀏覽器 | autonomy 4 組情境、scientific、assets 都通過，原 assertions／10 秒 timeout 不改 | 獨立研究、選用教授、團隊；邀請／撤權／移除教授後保留歷史與真 DOCX |
| 有效 PDF | 真 browser XHR/CSRF、原件 hash、同 ID 恢復兩次、Poppler 文字／轉圖、Tesseract 讀出 300 K | 合成一頁；非完整真論文解析品質 |
| 真模型／workers | CPU-only、native NMS、cv2、Eventlet／gthread HTTP 登入／Socket polling／上傳／恢復、真 NLLB 單句／batch、EasyOCR 離線推論 | 不推定容量／品質 |
| 科學引擎 parity | 原 3.10、本機舊 3.12、整合 3.12 的 300 個已曝露公開 SciFact dev 預測逐 byte 相同、0 errors；最後映像再核對 | 未讀 sealed holdout，非新盲測或科學品質改善 |
| 回退 | 3.12 三流程資料停機備份／還原後，用原 3.10.20 image＋新隔離 sandbox 啟動；所有研究 DB rows、教授／撤權歷史、舊全文保留，可新增章節與認證匯出，匿名 401 | 只用合成資料；不是正式 cutover／正式資料 restore |

50 個略過：34 個 POS／VerbNet、13 個 WordNet、2 個 Windows 路徑別名（本輪 Linux）、1 個歷史 commit 不在乾淨 context。沒有下載 I14 資源補數目或刪失敗測試。

研究 browser 的資料改放 Linux 原生容器檔案系統；原件斷言仍透過受限 adapter 讀同一真原件 bytes。NTFS bind 的延遲與失敗輪保留，**不宣稱 Windows bind 的 latency／耐久性已驗收**。

## 品質與 tokenizer

舊 4.57.6 的本地 config 版本判定有區間缺口：只有 config 版本 ≤4.57.2 才會檢查非 Mistral model_type，而 ≥5 才直接跳過；本輪 bake 的 4.57.6 NLLB config 因而誤觸 Mistral 警告。本輪沒有設 fix flag 或靜音。新版正常載入，12 個固定控制的 token IDs 舊／新完全相同。這只排除這組控制的 token drift，不能替所有輸入背書。

12 個翻譯控制的嚴格數字「順序」一致由 10/12 到 11/12；但翻譯允許重排語序，**數字 multiset 其實兩邊都是 12/12**。因此沒有數字保存品質提升的證據；語義、條件／因果／模擬與實測仍須人類審閱。原始結果保留，沒有重訂門檻稱通過。

三張固定合成圖：EasyOCR 雙語舊／新 CER 相同（英文 0.538、條件句 0.083、模態句 0）；仍有 O／0 錯讀。補 chi_tra 後 Tesseract 三圖 CER=0，舊環境缺繁中語言時兩張中文 CER=1。這證明缺件修復，**不是實際論文 OCR 或科學品質驗收**。

## 安全、秘密與獨立審查

- pip-audit：原 95 筆／10 套件降為 **1 筆／1 套件**。保留 **PYSEC-2022-252／deep-translator 1.11.4**；官方資料明列此版本且沒有修復版。固定 hash 與 code inspection 不能消除此通報；正式啟用前需移除／替換或由 Owner 完成風險決策。
- Gitleaks：工程程式提交 history 為 0；加入證據後，完整 b7a7bdd..tip 掃描有 5 個誤報，都是公開 NLLB tokenizer／special-token 檔的 SHA-256，與實際 build manifest 一致。乾淨 context 另有 1 個診斷字串誤報，定位 `deploy_preflight.py` 的「SECRET_KEY missing/weak/placeholder」。沒有確認的 credential 洩漏；保留原 scanner 非零輸出，不加寬鬆 allowlist 或移除 hash 證據。
- Bandit findings 不抹掉：既有識別／CAS 的 SHA1 與可配置 UniMERNet `shell=True` 仍列風險。未重寫歷史 ID；候選沒有設定該外部命令。沒有重現跨 HTTP 的命令注入；啟用外部 shell 模板前仍需安全審查。NLLB manifest 七檔實際全走 SHA-256，舊 Git-blob SHA1 fallback 不是本輪模型驗證路徑。
- Python／秘密 scanner 是獨立工具，**不是獨立人類稽核**。本輪完成自我稽核與可重現機器掃描，外部 auditor 尚未審查；不把自己審自己寫成獨立通過。也未做全面滲透、OS CVE 或負載驗收。

## 保留的失敗

原始資料都在 `D:\Documents\RootThinksValidation\py312-integration-20261025`。包含 resolver 約束失敗、舊 translation pipeline 不相容、5.10.0 被撤回、BuildKit 裸 image ID 當 FROM 的失敗、原瀏覽器 load／NTFS timeout、diagram probe 在 workspace 尚未完成 bootstrap 就注入導致其 DOM 被還原，以及新 hash 測試少抄一碼。後者依官方 digest 修正，仍要求精確 64 碼相等。沒有改原瀏覽器斷言換成功。

## Owner Gate

**建議先請獨立 auditor 審查此分支，並處理 deep-translator；目前不准正式遷移。** Google 真帳號 OAuth／Picker／撤權、真論文品質、正式容量與正式資料還原仍未驗。雲端接手指令見[交接文件](PY312_LOCAL_FIRST_CLOUD_HANDOFF.md)；本 agent 不替 Owner 啟動交接。
