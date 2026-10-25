# Python 3.12 遷移的驗證檔案（不是生產用）

這些檔案只用來在**受限網路**的驗證環境重現 2026-10-25 的結果。生產候選是 repo 根目錄的 `Dockerfile.py312`。

| 檔案 | 用途 |
|---|---|
| `Dockerfile.p312v1` | 由 `Dockerfile.py312` 機械轉換：拿掉 apt、torch 改 PyPI 同版號（hash 釘住）、拿掉 NLLB 下載、加測試 stage。基底仍是 Debian `python:3.12-slim`（3.12.15） |
| `Dockerfile.p312v2` | Ubuntu 24.04（Python 3.12.3）＋ 生產 `Dockerfile` 的 apt 套件 ＋ git ＋ 同一份鎖定，不裝 torch。用來證明 8 個環境錯誤的根因 |
| `Dockerfile.p312v3` | V2 ＋ torch（PyPI 同版號）。用來驗 OpenCV、easyocr 真模型、tesseract／poppler |
| `torch-pypi.lock` | PyPI torch 2.6.0、torchvision 0.21.0 與其 CUDA 相依的 hash 鎖定（只給驗證用） |
| `run_browser.sh`、`py-in-fx.sh` | 在 V1 容器啟動 `sai_browser_fixture.py`，用主機 Chromium 跑 I12 的四次流程 |
| `vendor-route.cjs` | I12 的 Playwright 導流包裝：只把樣板引用的 CDN 資產導到本機的 npm 真品，記錄每一個請求與失敗 |

## 重現步驟（摘要）

1. build context 用乾淨匯出：`git archive HEAD | tar -x -C <ctx>`，再把 `torch-pypi.lock` 與這裡的 Dockerfile 放進 `<ctx>`。
2. 若環境有 TLS 攔截代理，CA 以 `--secret id=ca,src=<ca.crt>` 掛載；**不要 COPY 進映像**。build 加 `--network host`。
3. `docker build -f Dockerfile.p312v1 --target test -t p312-v1:test <ctx>`（V2、V3 同理，V3 依賴 `p312-v2:test`）。
4. 回歸：`docker run --rm --network none -e APP_ENV=test -v <ctx>/test:/app/test:ro <image> python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider --continue-on-collection-errors`。
5. 執行期檢查：`docker run --rm <image> python scripts/py312_runtime_check.py --work /tmp/c [--easyocr-dir /tmp/e --allow-download]`。
6. 生產指令：`docker run --rm --network none [-e GUNICORN_WORKER_CLASS=gthread] <image> sh scripts/py312_gunicorn_smoke.sh`。
7. 瀏覽器：設 `P312_WORK`（含 `ctx/` 與一份 git clone `browser-repo/`）與 `I12_BROWSER_DIR`（含 `vendor/` 與 `vendor-route.cjs`），執行 `sh run_browser.sh`。
   vendor 資產的建法見 `SAI_I11_DELIVERY_2026-10-24.md`。
