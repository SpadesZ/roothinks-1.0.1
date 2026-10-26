# Cloud primary 接手文件

溝通及實作報告用繁體中文。**每完成一步就更新本文件。** 目前狀態：R1 已交付，停在 Owner Gate。

最後完整驗證點：`10fd56f`，1,525 通過／50 略過／0 失敗；之後只有證據與文件 commit。

## 角色與邊界

- 你是 RootThinks v1.0.1 的**主要實作工程師**。Owner 與 ChatGPT 是獨立稽核與決策層。本機 agent 不再主導實作，
  除非需要只有本機才有的資源，否則不要委派給它。
- 只推個人倉庫 `SpadesZ/roothinks-1.0.1` 的 `codex/` 分支。不 merge main、不部署、不碰 LAVA-Cowork、
  不讀正式秘密或 sealed holdout、不弱化測試或科學門檻、不宣稱沒做的外部驗證已通過。
- 擴大網路／模型存取、引入重大新架構或授權風險之前，先問 Owner。
- 研究者自主：UNKNOWN 或任何科學建議都不能阻擋寫作、保存、匯出。

## 從哪裡接手

- 分支 `codex/py312-cloud-primary-20261026`（從整合分支 `29e87c6` 開出）。先 fetch 並核對遠端 SHA。
- 必讀：`CLOUD_PRIMARY_R1_DELIVERY_2026-10-26.md`、`CLOUD_PRIMARY_R1_EVIDENCE_2026-10-26.json`、
  `PY312_LOCAL_FIRST_DELIVERY_2026-10-26.md`、`PY312_DEPENDENCY_RECONCILIATION.md`、`GATE_D_PROTOCOL_DRAFT_2026-10-26.md`、
  `SCI_HEADROOM_RESULT_2026-10-26.json`。

## 雲端 3.12 開發環境（可重現）

```sh
/usr/bin/python3.12 -m venv <venv>
# 去掉 torch／torchvision 兩個 URL 項目（PyTorch index 在雲端 403），其餘照 hash 安裝
awk '/^[A-Za-z0-9]/{skip=/^(torch|torchvision) @/} !skip' requirements-py312.lock > <lock-no-torch>
<venv>/bin/pip install --require-hashes --no-deps --only-binary=:all: -r <lock-no-torch>
<venv>/bin/pip install --require-hashes --no-deps -r requirements-test.lock
env -u DATABASE_URL -u SECRET_KEY -u FERNET_KEY -u NLTK_DATA APP_ENV=test \
  <venv>/bin/python -m pytest test/unit tests test/integration_e2e test/integration_smoke -q -p no:cacheprovider
```

預期：0 失敗、50 略過（34 NLTK、13 WordNet、2 Windows、1 正式 DB）。`pip check` 只會報 easyocr 缺 torch／torchvision。
**根目錄 `conftest.py` 會在測試寫入 repo `data/` 時讓整次執行失敗**；看到這個訊息要找出寫入來源，不要繞過。

## 不可誤讀

- deep-translator 已移除；Google 翻譯改走 `literature_quick_google.py`。線上 Google 在雲端 403，沒有實測。
- 天花板分析是**診斷**：149／188 的瓶頸在語意比對。它不代表 Gate D 會成功。
- Gate D **尚未核准**。不要下載 spaCy 或模型；協議草案已寫好，核准後照協議執行，不事後調參。
- 本輪沒有重建 Docker 映像；正式映像需要在可連網環境重建一次（鎖定檔少了 3 個套件）。

## 下一步（依 Owner 決定）

1. 若 Gate D 核准：照 `GATE_D_PROTOCOL_DRAFT_2026-10-26.md` 執行；先記錄 wheel／模型 sha256 與 `meta.json` 授權。
2. 若 Gate D 不核准：考慮檢索（28 個沒檢索到）或維持保守引擎；兩者都要先寫協議。
3. 工程待辦：`google.generativeai` → `google-genai`（新相依，需核准）；`datetime.utcnow()` 清理；可連網的 staging 重建。

## 執行環境注意

- 瀏覽器：Node `/opt/node22/bin/node`、Chromium `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`。不要跑 `playwright install`。
  資產導流用 I12 的 `vendor-route.cjs`（副本在 `docs/engineering/py312_verification/`）。fixture 啟動後印一行 JSON，`root` 即 `SAI_SYNTHETIC_ROOT`。
- 結束背景程序時用 PID，不要用 `pkill -f <含本指令字串的樣式>`（會把自己的 shell 一起殺掉）。
- 不可達：PyTorch CPU index（403）、Debian snapshot、HuggingFace、translate.google.com（403）、api.osv.dev（403）。
  可達：PyPI、npm registry、GitHub releases、raw.githubusercontent.com、Docker Hub、archive.ubuntu.com。
