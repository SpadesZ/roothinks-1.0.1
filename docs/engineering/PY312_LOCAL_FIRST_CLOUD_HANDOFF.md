# Cloud／Auditor 接手指令（需 Owner 另行啟動）

先讀本輪交付、驗收矩陣與 PY312_DEPENDENCY_RECONCILIATION.json。工程凍結 84b0ea9；最終文件 SHA 由 personal `codex/py312-local-first-integration-20261025` 取得。不得把本文件視為部署或 cloud handoff 授權。

## 重現（乾淨、無研究秘密的 Linux／Docker）

1. 取得隔離分支，檢查工作樹乾淨與科學引擎 hash。保留 e64c4e7、433eec4 與 main；禁止 force-push、merge main、讀 production 或 sealed holdout。
2. 使用 repo 的 tracked-only staging，不在原始私人工作區直接 `COPY .`：

```text
python validation/python312/local_build.py stage /tmp/rt-py312-audit
python validation/python312/local_build.py build /tmp/rt-py312-audit --dockerfile Dockerfile --target runtime --label audit1
python validation/python312/local_build.py build /tmp/rt-py312-audit --dockerfile Dockerfile.test --target verification --label audit1 --base-image sha256:<剛產出的 runtime iid>
python validation/python312/regression.py sha256:<verification iid> /tmp/rt-py312-regression-audit
```

每個輸出目錄／label 必須新建，失敗不可覆寫。BASE_IMAGE 由工具解析本機 tag，核對相同 config ID，避免 BuildKit 把裸 SHA 當遠端 repository。完整 build 要可達固定 Debian snapshot、PyPI、官方 CPU wheel、固定 HF／GitHub 模型檔；保留 TLS 與 apt 簽章。需數 GB 空間與建置模型記憶體，別在正式 VM 直接試。

3. 對同一 runtime 分別 `--network none` 執行 `scripts/py312_runtime_check.py capabilities <new-json>`、`model <new-json>`、`scripts/py312_gunicorn_smoke.sh`（eventlet／gthread 各一次）。保留 `/opt/runtime-install-report.json` 與 `/opt/models/*/*manifest.json`，核對全部實際 wheel hash、無 yanked、CPU-only、模型檔 commitment。不得以合成 NLLB 代替真權重。
4. 用 verification 的 Noto font 先 `quality_diagnostic.py render <new-dir>`，舊／新各以相同像素跑 `ocr`；`translation` 只讀固定 quality_controls.json。保留原始輸出、numeric sequence 與 multiset 兩種口徑；沒有專家就不填語義 gold。
5. 跑全部 `test/js/*.cjs`。原 `sai_autonomy_browser.cjs`、`sai_scientific_browser.cjs`、`sai_assets_browser.cjs` 與 `pdf_browser.cjs` 不改 assertions。fixture 用全新 Linux `/tmp`，僅 publish localhost:8893；host `TEMP/TMP` 指定全新 QA parent，`SAI_SYNTHETIC_ROOT` 為其同名子目錄。

```text
docker run -d --name roothinks-py312-local-audit-browser -p 127.0.0.1:8893:8893 --entrypoint python <verification-image> validation/python312/browser_fixture.py
docker logs roothinks-py312-local-audit-browser
```

從 fixture 唯一 JSON 讀取 `/tmp/roothinks-sai-browser-*`，不能猜 root。每支有寫入的流程另開新 fixture。完成後只 stop 自己的容器；需要 backup 時先停機，再 cp 該合成 tmp 到新 QA 目錄，最後 rm 該容器，不做全域 prune。
6. `browser_hook.cjs` 使用 browser_assets.py 下載並驗 npm integrity／SRI 的真資產 manifest；設定 `PY312_ASSET_MANIFEST`、新 `PY312_BROWSER_REQUEST_LOG`、`PY312_BROWSER_CONTAINER`（限 roothinks-py312-local- 前綴）、`PY312_CONTAINER_SYNTHETIC_ROOT`、`PY312_LINUX_FIXTURE=1`、`SAI_BROWSER_EXECUTABLE`、`NODE_PATH`、`SAI_TEST_PYTHON`。原件 bytes／SQLite 指令留在同一 Linux 容器。Google 未配置時不應下載 SDK；不要用假 SDK 冒充真 OAuth。
7. 用 `browser_optional.cjs` 驗真 Mermaid SVG／錯誤純文字／Google 未配置仍可寫作。先等 Socket 連線並切到 introduction、等原稿載入，不能在 workspace bootstrap 前注入測試資料；需 `PY312_EXTRA_EVIDENCE` 指定新 QA 目錄。舊 timeout 記錄保留。
8. 公開已曝露 SciFact dev 300 筆只驗遷移 parity；用 scientific_benchmark.py predict，比較整份輸出 hash `a7a22bcfa8438daec9e1871ec5b8159e38db5e57c7a42c53ec5d214fd347d09e`。資料下載來源／SHA 在本輪機器證據；不得取 claims_test 或專案 sealed holdout，也不能把 parity 稱準確度。
9. 三流程 fixture 停機後，透過 app/research_backup.py backup／restore 到新目錄，再以舊 3.10 image、乾淨 source、獨立 tmpfs sandbox 執行 `validation/python312/rollback_probe.py`。它只接受 /source 的已掃描 source 和 /evidence/rollback-restored 合成還原資料；不讀舊 image 的 /app/data 或 .env。不可為了複製本機證據而匯出可能含秘密的舊正式 image；遠端舊 image／一致備份須由 Owner 另案確認。
10. 重跑 pip-audit、Bandit、Gitleaks，逐項判斷而不刪 scanner findings。deep-translator 通報、外部 shell 命令、舊 SHA1 與未驗 Google 必須如實列出。只有 Owner 可以決定下一步；本輪 GO/NO-GO 是 NO-GO。

## 可直接給接手 agent 的 prompt

> 你是 RootThinks 的獨立 auditor。先驗 personal 隔離分支的 exact HEAD、凍結 84b0ea9、Docker 與模型／wheel commitment。以此文件重現，不相信測試數或交付敘述；原 failures、兩原分支及未驗事項都保留。重點審 deep-translator PYSEC-2022-252、選用 Google OAuth／Drive 的權限與失效邊界、Mermaid 載入與錯誤注入、Linux CAS／restore／撤權歷史、實際非 yanked CPU 模型映像。科學引擎不可改，科學建議不得阻擋作者寫作保存匯出。不要部署、merge main、動 LAVA-Cowork、讀 production／sealed holdout 或自行啟動其他 agent。交付可否關閉各風險的證據與 NO-GO／條件建議，停在 Owner Gate；未有新授權不要 push 或開始 cloud implementation。
