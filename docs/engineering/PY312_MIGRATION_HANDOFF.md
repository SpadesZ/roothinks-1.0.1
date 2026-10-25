# Python 3.12 遷移：接手文件（進行中）

溝通及實作報告用繁體中文。本文件每完成一步就更新。

## 範圍與邊界

- Owner 決策：隔離的 Python 3.12 遷移是下一個工程優先；I14 離線語意研究另行獨立進行。
- 分支 `codex/py312-migration`，起點 `b7a7bdd`（I13 最新）。只推個人倉庫 `SpadesZ/roothinks-1.0.1`。
- **不 merge、不部署、不改上游（LAVA-Cowork）、不改科學引擎**（`scientific_advisory.py` hash `4c2034bf…`）。
- 生產 `Dockerfile`（python:3.10-slim）與 `requirements.txt` **不動**。遷移只新增檔案，回滾 = 繼續用 `Dockerfile`。
- 停在 Owner Gate：給遷移建議、剩餘風險、可獨立重現的證據。

## 已提交

| commit | 內容 |
|---|---|
| `60b672f` | `Dockerfile.py312`（基底以 digest 釘住）、`requirements-py312.lock`（119 套件、全部 hash）、`requirements-py312.constraints.txt`、`scripts/py312_lock.py`、`test/unit/test_py312_lock.py` |
| `8fade05` | `scripts/py312_runtime_check.py`、`scripts/py312_gunicorn_smoke.sh` |

## 目前結果（進行中）

- 3.12 鎖定版本與 3.10 現行解析相同，只少 async-timeout、exceptiongroup、tomli（3.10 才需要）。
  若讓 3.12 自由解析，numpy、pandas、SQLAlchemy、scipy 等 14 個套件會跳新版 → 刻意不這樣做。
- 生產式 build（`Dockerfile.py312`）卡在 apt：代理對 deb.debian.org 回 405。PyTorch CPU index 403、HuggingFace 不可達。
- 驗證映像（只在暫存區）：V1 = Debian python:3.12-slim、無 apt、torch 用 PyPI 同版號；V2 = Ubuntu 24.04 + 生產系統套件 + git、無 torch；V3 = V2 + torch。
- V1：`pip check` 通過；torch／torchvision、eventlet、NLLB 載入路徑通過；cv2 缺 libxcb（無 apt）。
- **生產 gunicorn 指令實測（V1）**：eventlet 與 gthread 都通過——登入、首頁、Socket.IO、PDF 上傳（sha256 相符）、恢復兩次冪等。
- **V2 完整回歸 1,477 通過／0 失敗／0 錯誤** → 8 個環境錯誤全部消失：2 個是缺 libgl1（帶 libxcb），6 個是缺 git。
- 3.10 回滾演練（同 commit `60b672f`）：1,459 通過／0 失敗／8 個相同環境錯誤。

## 待做

V1 完整回歸、V3 執行期檢查（easyocr 真模型、tesseract）與完整回歸、四次真瀏覽器流程、交付文件與證據 JSON、推送。

## 執行環境注意

- 自行啟動 dockerd：`dockerd --host=unix:///tmp/p312-docker.sock --data-root=<暫存區>/dockerd-data`，HTTPS 走代理；
  build 要 `--network host` 才連得到 127.0.0.1 的代理。代理 CA 用 `--secret` 掛載，**不寫入任何層、不入庫**。
  apt 讀 secret 需 `mode=0444`（apt 以 `_apt` 使用者下載）。
- 背景程序有 2 小時上限；到期不要重啟同一個任務。
