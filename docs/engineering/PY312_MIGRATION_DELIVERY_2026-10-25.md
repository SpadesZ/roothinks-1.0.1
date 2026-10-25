# Python 3.12 遷移：交付與 Owner Gate

日期：2026-10-25。分支 `codex/py312-migration`，起點 `b7a7bdd`（I13 最新）。只推個人倉庫。
**生產 `Dockerfile`、`requirements.txt`、產品程式與科學引擎都沒改。** 遷移只新增檔案。

## 判定

- **建議進入「可連網環境的 staging build」**，不是直接上線。理由見第十節。
- Python 3.12 下：完整回歸 0 失敗；生產 gunicorn 指令（eventlet 與 gthread）實測登入、上傳、恢復都通過；
  四次真瀏覽器流程通過；科學引擎在 3.10／3.12／3.13 的預測逐位元相同。
- **8 個環境錯誤已查清**：2 個是驗證映像缺 `libgl1`（帶 libxcb），6 個是缺 `git`。補上後歸零。
  其中 6 個 git 錯誤在**官方測試映像（`Dockerfile.test`）也會發生**，建議補 git。
- **這個環境做不到的三件事**：Debian apt（代理 405）、PyTorch CPU index（代理 403）、HuggingFace（不可達）。
  所以生產式 `Dockerfile.py312` 沒有端到端建完，真 NLLB 權重沒有載入過。

## Owner 決策的執行結果

| 決策 | 執行 |
|---|---|
| 隔離的 Python 3.12 遷移；I14 語意研究獨立進行 | 獨立分支 `codex/py312-migration`。I14 接手文件已註明兩條線不要混 |
| 保留現行生產環境與所有分支邊界 | `Dockerfile` sha256 `9e5607c6…` 與 main、I13 相同；`requirements.txt` 未改；舊分支都沒動 |
| 驗證相依相容性（PyTorch CPU、TorchVision、Eventlet、OpenCV、NLLB、完整 Docker build） | 第二、三節。PyTorch CPU 與 NLLB 真權重受環境阻擋，已用最接近的替代驗證並寫明差異 |
| 3.12 完整回歸、瀏覽器流程、PDF 上傳／恢復、模型載入 | 第四節，全部執行 |
| 調查 8 個未解環境錯誤 | 第五節，根因已證實 |
| 釘可重現版本、準備回滾證據 | 第六、七節 |
| 不 merge、不部署、不改上游、不改科學引擎 | 全部遵守。科學引擎 hash `4c2034bf…` 不變 |

## 一、新增了什麼

| 檔案 | 用途 |
|---|---|
| `Dockerfile.py312` | 3.12 建置候選。與 `Dockerfile` 只差三處：基底 `python:3.12-slim@sha256:a0f57064…`、套件改用鎖定檔 `--require-hashes --no-deps` 安裝、torch 改在之後 `--no-deps` 安裝並 `pip check` |
| `requirements-py312.lock` | 119 個套件，全部有 hash。版本與今天的 3.10 解析相同，只少 3.10 才需要的 async-timeout、exceptiongroup、tomli |
| `requirements-py312.constraints.txt`、`scripts/py312_lock.py` | 重新產生鎖定檔用 |
| `test/unit/test_py312_lock.py` | 檢查鎖定檔涵蓋 `requirements.txt`、版本在範圍內、每項有 hash、不含 CUDA 套件、`Dockerfile.py312` 的釘法、生產 `Dockerfile` 仍是 3.10。變造鎖定檔會讓 5 項中的 3 項失敗 |
| `scripts/py312_runtime_check.py` | 映像內執行期檢查（直譯器、匯入、torch、OpenCV、eventlet、NLLB、easyocr、tesseract）。`--nllb-real` 給可連網環境驗真權重 |
| `scripts/py312_gunicorn_smoke.sh` | 用生產 gunicorn 指令啟動，經 HTTP 驗登入、上傳、恢復 |
| `docs/engineering/py312_verification/` | 本環境用的驗證 Dockerfile（V1／V2／V3）、torch PyPI 鎖定、瀏覽器執行腳本、I12 導流包裝 |

**為什麼不讓 3.12 自由解析**：自由解析時 numpy 2.2→2.5、pandas 2.3→3.1、SQLAlchemy 2.0→2.1、scipy、scikit-image 等
14 個套件會一起升級。那是兩件事綁成一件。這次只換直譯器，升級另做。

## 二、相依相容性

| 項目 | 結果 | 依據 |
|---|---|---|
| 全部 119 個鎖定套件 | **通過**：cp312 可安裝（含 hash 檢查），`pip check` 無問題 | V1 build |
| PyTorch 2.6.0 / TorchVision 0.21.0 | **通過**（PyPI 同版號）：Conv2d、`torchvision.ops.nms` 正確 | V1、V3 執行期檢查 |
| PyTorch **CPU 版**（`+cpu`） | **未驗**：PyTorch index 被代理 403。只釘版本、未釘 hash | 第八節風險 1 |
| Eventlet 0.36.1 | **通過**：green WSGI 往返、`eventlet.green.ssl`；生產 gunicorn eventlet worker 實測通過 | V1 |
| OpenCV（opencv-python 4.14.0.94） | **通過**（有 libgl1 時）：imdecode、cvtColor、threshold。沒有 libgl1 時匯入失敗（缺 libxcb） | V3 通過；V1 失敗（無 apt） |
| easyocr 1.7.2（真模型，從 GitHub 下載） | **通過**：照 `literature_stacka` 載入 en＋ch_tra（`craft_mlt_25k.pth`、`chinese.pth`）並完成推論；英文單語（fallback，`english_g2.pth`）正確讀出 `ROOTHINKS 2026` | V3。雙語模型讀合成英文字得到 `R0oTH\|\|/K$ 2026`，見第九節第 7 點 |
| tesseract 5.3.4 / poppler 24.02（pytesseract、pdf2image） | **通過**：正確讀出 `ROOTHINKS 2026`；PDF 轉圖 1 頁 | V3（Ubuntu 套件版本，非 Debian） |
| NLLB 載入路徑 | **通過**：原封不動的 `scripts/bake_nllb.py` 在 3.12 跑完，產出 safetensors；`NLLBTranslator` 載入成功並回傳翻譯結果 | V1（合成小權重，只驗路徑） |
| NLLB **真權重** | **未驗**：HuggingFace 不可達 | 第八節風險 2 |
| 3.12 移除的 API（distutils、imp、asyncore、ssl.wrap_socket 等） | **未使用**：掃描 app、scripts、migrations、test 皆無 | 靜態掃描 |
| 科學引擎 | **行為相同**：SciFact dev 300 筆預測在 3.10.22、3.12.15、3.13.17 **逐位元相同**（predictions sha256 `a7a22bcf…`） | 第四節 |

## 三、Docker 建置

| 建置 | 結果 |
|---|---|
| `Dockerfile.py312`（生產式） | **卡在 `[2/11] apt-get update`**：`deb.debian.org` 走明文 HTTP，代理回 405。另測 22 個 Debian HTTPS 來源（deb／security.debian.org 與 20 個鏡像）全部不可達 |
| PyTorch CPU index | `pip download torch==2.6.0+cpu`：代理 `Tunnel connection failed: 403 Forbidden` |
| V1：Debian `python:3.12-slim`（Python 3.12.15），去掉 apt、torch 用 PyPI、去掉 NLLB 下載 | **建置成功**，`pip check` 通過。映像 `ec4a88fb…` |
| V2：Ubuntu 24.04（Python 3.12.3）＋ 生產的 apt 套件 ＋ git，不裝 torch | **建置成功**。apt 走 archive.ubuntu.com HTTPS |
| V3：V2 ＋ torch | **建置成功**，`pip check` 通過。前兩次在解壓時磁碟滿而失敗，清掉 V1 映像後第三次成功 |

代理 CA 一律用 BuildKit `--secret` 掛載，**不寫入任何映像層、不入庫**。

## 四、測試結果

| 環境 | 完整回歸（test/unit、tests、test/integration_e2e） |
|---|---|
| 主機 Python 3.13.17（`95bb679`，產品環境：無 NLTK、未設憑證） | **1,478 通過／50 略過／0 失敗**（I13 為 1,473；多的 5 個是鎖定檔測試） |
| V1 Debian Python 3.12.15（torch、無系統函式庫） | **1,459 通過／51 略過／0 失敗／8 錯誤**（8 個都是環境：第五節） |
| V2 Ubuntu Python 3.12.3（系統函式庫 ＋ git、無 torch） | **1,477 通過／51 略過／0 失敗／0 錯誤** |
| V3 Ubuntu Python 3.12.3（系統函式庫 ＋ git ＋ torch） | **1,477 通過／51 略過／0 失敗／0 錯誤** |
| 回滾演練：Python 3.10.22（I13 驗證映像，同一個 commit `60b672f`） | **1,459 通過／51 略過／0 失敗／8 錯誤**（與 V1 完全相同） |

**生產指令實測**（`scripts/py312_gunicorn_smoke.sh`，無網路；V1 與 V3 各跑 eventlet、gthread，四次全部 passed）：

| worker | 登入頁 | session 登入 | 首頁 | Socket.IO 握手 | PDF 上傳 | 原件 sha256 | 恢復 ×2 | 原件未變 |
|---|---|---|---|---|---|---|---|---|
| eventlet（`Dockerfile`／`docker-compose.yml` 預設） | 200 | 通過 | 200 | 200 | 200 | 相符 | 200、200，同一 paper_id | 是 |
| gthread（`docker-compose.prod.yml` 預設） | 200 | 通過 | 200 | 200 | 200 | 相符 | 200、200，同一 paper_id | 是 |

**真瀏覽器**（伺服器在 V1 容器內，Python 3.12.15；主機 Chromium；資產導流沿用 I12 已驗真的 npm 檔案）：

| 流程 | 結果 | 檢查數 | 與 I12 比較 |
|---|---|---:|---|
| 自主性 | passed | 4 | 相同 |
| 科學建議 | passed | 8 | 相同 |
| 資產（不服務 plotly） | passed | 4 | 相同；`cdn.plot.ly` 失敗 3 次，與 I12 相同（未授權網域，照實保留） |
| 資產（服務已驗真 plotly） | passed | 4 | 相同；外部失敗 0 |

**PDF 上傳／恢復**：完整回歸內的 `test_upload_preservation`（10 項，含 3.10 缺陷的回歸測試）與端到端使用者旅程都通過；
另有上面的生產指令實測。

**警告**：3.12 新增的 DeprecationWarning 只有兩類——`datetime.utcnow()`（2 處）與 protobuf upb 的 metaclass（2 處）。不是錯誤。

## 五、8 個環境錯誤

| 錯誤 | 根因 | 證據 | 正式環境會不會發生 |
|---|---|---|---|
| `test_arbiter_equation_chain`、`test_segmentizer_types` 收集失敗 | 驗證映像跳過 apt，`opencv-python` 找不到 `libxcb.so.1`（`libgl1` 會帶進來） | V2 裝了 `libgl1` 後這兩個模組通過 | **不會**：生產 `Dockerfile` 有裝 `libgl1` |
| `test_source_contract_scope` 6 項 setup 失敗 | 映像沒有 `git` 執行檔 | V2 裝了 git 後 6 項通過 | **會**：`Dockerfile.test` 從執行期映像衍生，沒有 git |

**建議（未自行修改，等 Owner）**：在 `Dockerfile.test` 加
`RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*`。
這只影響測試映像，不影響執行期映像。本環境無法在 Debian 上驗證 apt，所以沒有直接改。

## 六、釘版本

- 基底映像以 digest 釘住：`python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`（Debian 13、Python 3.12.15）。
- Python 套件：`requirements-py312.lock` 全部 hash；以 `--require-hashes --no-deps` 安裝，不會偷偷多裝。
- torch／torchvision：釘版本 `2.6.0+cpu`／`0.21.0+cpu`，**沒有 hash**（PyTorch index 不可達）。第一次在可連網機器建置時，
  用 `pip download --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.6.0+cpu torchvision==0.21.0+cpu` 再 `pip hash` 補上。
- 重新產生鎖定檔：見 `scripts/py312_lock.py` 檔頭。`test_py312_lock.py` 會擋住不一致。

## 七、回滾證據

- **回滾方法**：繼續用未改動的 `Dockerfile`。遷移只新增檔案，所以不需要 revert 任何東西。
  上線後要回滾，就重新部署前一版 3.10 映像（建議上線前把目前生產映像加上 rollback tag）。
- 生產 `Dockerfile` sha256 `9e5607c6…`，與 main（`9655546`）、I13（`b7a7bdd`）相同。
- 3.10 基底目前的 digest：`python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`（2026-10-06）。
  生產 `Dockerfile` 用浮動標籤，**今天的 3.10 環境本身並不可完全重現**；建議另案也釘 digest。
- 同一個 commit 在 3.10 與 3.12 的回歸結果完全相同（1,459／51／0／8）。
- 科學引擎預測在 3.10 與 3.12 逐位元相同。

## 八、剩餘風險

1. **PyTorch `+cpu` 的 cp312 wheel 沒在這裡裝過。** 驗證用的是 PyPI 同版號（CUDA 變體）。API 相同，但二進位不同。
2. **真 NLLB 權重沒載入過。** 只驗了 bake 腳本與載入程式碼在 3.12 能跑。`--nllb-real` 已備好給 staging 用。
3. **生產式映像沒有端到端建完**（apt、PyTorch index、HuggingFace）。映像大小與建置時間未知。
4. **Debian 的 apt 套件在 3.12 基底上沒裝過。** 但 3.12 與目前 3.10 基底都是 Debian 13（trixie），套件清單相同，風險低。
5. **eventlet 0.36.1 在 3.12 可用，但在 3.13 不可用**（I12 已知）。下一次升級前要換掉 eventlet；`docker-compose.prod.yml` 已預設 gthread。
6. **OpenCV 兩個套件寫同一個 `cv2/`**（opencv-python 4.14 與 easyocr 帶入的 headless 5.0）。I13 已記錄，本次未改，鎖定檔把現況固定下來。
7. 官方測試映像缺 git（第五節）。
8. 生產沒有裝 tesseract 的 `chi_tra` 語言檔，繁中 tesseract 會走英文 fallback。與 3.12 無關，3.10 也一樣。
9. 基底以 digest 釘住後，**安全更新要手動刷新 digest**。

## 九、獨立自我稽核

1. **最大的缺口是三個外部來源不可達。** 我用最接近的替代驗證，並在每一處寫明差異，沒有把替代結果說成正式結果。
2. **V2／V3 是 Ubuntu 的 Python 3.12.3，不是正式基底的 3.12.15。** 它們只用來證明 8 個錯誤的根因與系統函式庫相關功能；正式基底的結果看 V1。
3. **NLLB 用合成小權重。** 只證明程式路徑可跑，不代表翻譯品質；不能拿來說「NLLB 已驗證」。
4. **生產指令實測用的是 docker-compose.gcp.yml 的認證模式**（session、無 Bearer）。其他部署組合沒逐一測。
5. **鎖定檔與 3.10 版本相同是刻意的選擇**，代價是沒有順便拿到套件的安全更新。
6. 隔離 dockerd 用完即停；驗證映像都只在暫存區，沒推到任何 registry。
7. **easyocr 檢查在看到第一次結果後改過。** 第一次：雙語模型下載、載入、推論都成功，但把合成英文字讀成 `R0oTH||/K$ 2026`，
   我原本要求讀出 `ROOTHINKS` 所以判失敗。改成「雙語模型要有輸出 ＋ 英文模型要讀對」後通過。原始失敗結果保留在證據檔。
   這證明 3.12 上的載入與推論可跑，但**沒有證明繁中辨識品質**；3.10 上同一張圖的結果沒有比較過。

## 十、Owner Gate

**請決定下一步（2 選 1）：**

1. **（我建議）核准在可連網的 staging 機器建置 `Dockerfile.py312`**，並在那裡跑：
   `python -m pytest`（用 `Dockerfile.test`，`BASE_IMAGE` 指向 3.12 映像，建議先補 git）、
   `python scripts/py312_runtime_check.py --work /tmp/c --nllb-real --easyocr-dir /app/data/aimodels/easyocr --allow-download`、
   `sh scripts/py312_gunicorn_smoke.sh`（eventlet 與 gthread 各一次）、四次瀏覽器流程，並補上 torch `+cpu` 的 hash。
   全部通過後再決定是否切換。理由：依 PEP 619，Python 3.10 的上游支援到 2026-10 結束，而 3.12 在這裡能測的部分全部通過、沒有發現需要改產品程式的地方。
2. **暫緩**，繼續用 3.10。代價：3.10 已到支援期尾端，之後的安全修補與套件 wheel 會越來越少。

下一輪接手文件：[PY312_MIGRATION_HANDOFF.md](PY312_MIGRATION_HANDOFF.md)。
