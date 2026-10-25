# Python 3.12 遷移：接手文件

溝通及實作報告用繁體中文。本文件每完成一步就更新。**目前狀態：停在 Owner Gate，等待決定。**

## 範圍與邊界

- Owner 決策：隔離的 Python 3.12 遷移是下一個工程優先；I14 離線語意研究另行獨立進行（I14 接手文件已註明兩條線不要混）。
- 分支 `codex/py312-migration`，起點 `b7a7bdd`（I13 最新）。只推個人倉庫 `SpadesZ/roothinks-1.0.1`。
- **不 merge、不部署、不改上游（LAVA-Cowork）、不改科學引擎**（`scientific_advisory.py` hash `4c2034bf…`）。
- 生產 `Dockerfile`（python:3.10-slim，sha256 `9e5607c6…`，與 main 相同）與 `requirements.txt` **沒動**。
  遷移只新增檔案，回滾 = 繼續用 `Dockerfile`。

## 必讀

1. `PY312_MIGRATION_DELIVERY_2026-10-25.md`（結果、風險、Owner Gate）。
2. `PY312_MIGRATION_EVIDENCE_2026-10-25.json`（機器證據）。
3. `py312_verification/README.md`（如何在受限網路重現）。

## 已提交

| commit | 內容 |
|---|---|
| `60b672f` | `Dockerfile.py312`、`requirements-py312.lock`（119 套件全部 hash）、`requirements-py312.constraints.txt`、`scripts/py312_lock.py`、`test/unit/test_py312_lock.py` |
| `8fade05` | `scripts/py312_runtime_check.py`、`scripts/py312_gunicorn_smoke.sh` |
| `95bb679` | 執行期檢查加 `--nllb-real` |
| `97a2d59` | easyocr 檢查分成「雙語載入」與「英文準確度」（看到第一次結果後才改，已揭露） |
| 之後 | 交付文件、證據、驗收矩陣、`py312_verification/`、本文件 |

## 結果摘要（不可誤讀）

- 完整回歸：主機 3.13 **1,478／50／0**；V1 Debian 3.12.15 **1,459／51／0／8 錯誤**；V2 Ubuntu 3.12.3＋系統函式庫＋git **1,477／51／0／0**；
  V3（＋torch）**1,477／51／0／0**；3.10 回滾演練同 commit **1,459／51／0／8**。
- 8 個環境錯誤：2 個缺 `libgl1`（libxcb），6 個缺 `git`。生產映像有 libgl1；**官方 `Dockerfile.test` 沒有 git**，建議補（未自行改）。
- 生產 gunicorn 指令（eventlet、gthread）在 V1 與 V3 都通過：登入、上傳、恢復兩次冪等。
- 真瀏覽器四次流程在 3.12.15 伺服器上全部 passed，與 I12 相同。
- 科學引擎預測在 3.10／3.12／3.13 逐位元相同。
- **沒做到**：生產式 `Dockerfile.py312` 端到端 build（apt 405）、PyTorch `+cpu` wheel（403）、真 NLLB 權重（HuggingFace 不可達）。
- 鎖定檔版本**刻意**與 3.10 相同；自由解析會升 14 個套件，那是另一件事。

## Owner Gate（待決）

1. （建議）核准在可連網的 staging 機器建置 `Dockerfile.py312` 並跑交付文件第十節列的檢查，補 torch `+cpu` 的 hash。
2. 暫緩，繼續用 3.10。

Owner 核准前**不要**：改 `Dockerfile`、改 `Dockerfile.test`、合併、部署、推任何映像。

## 執行環境注意

- 自行啟動 dockerd：`dockerd --host=unix:///tmp/p312-docker.sock --data-root=<暫存區>/dockerd-data`，HTTPS 走代理；
  build 要 `--network host` 才連得到 127.0.0.1 的代理。代理 CA 用 `--secret` 掛載，**不寫入任何層、不入庫**。
  apt 讀 secret 需 `mode=0444`（apt 以 `_apt` 使用者下載）；容器內 appuser 要讀 CA 也需要 0644 的副本。
- 背景程序有 2 小時上限；到期不要重啟同一個任務。
- 磁碟：PyPI torch（CUDA 變體）一層約 5.3 GB，匯出加解壓要約 13 GB 空間；同時保留多個含 torch 的映像會塞滿磁碟。
- 瀏覽器：Node `/opt/node22/bin/node`、Chromium `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`，**不要跑 `playwright install`**。
