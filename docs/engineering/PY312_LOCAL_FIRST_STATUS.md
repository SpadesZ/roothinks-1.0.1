# LOCAL-FIRST Python 3.12 整合進度

Owner 於 2026-10-25 核准本機主導、單一隔離整合分支完成後 push personal。禁止 main merge、部署、LAVA-Cowork、科學規則變更與 sealed holdout。不得自行啟動 cloud handoff。

- 基準：本機 `433eec4`，雲端 `e64c4e7`，兩者已讀；原分支及證據保留。
- 分支：`codex/py312-local-first-integration-20261025`。
- 本輪 QA：`D:\Documents\RootThinksValidation\py312-integration-20261025`；舊 QA 不覆寫。

## 可驗交付清單

1. [x] 29 項依賴逐項調和；113 套件、wheel/model hashes，詳見 PY312_DEPENDENCY_RECONCILIATION。
2. [ ] 正式映像已有完整成功證據；查出 Transformers 5.10.0 被撤回，改 5.10.4 並加 build-time yanked guard，待最後重建。
3. [ ] OCR／翻譯固定控制、tokenizer 警告、Google 失敗／就緒／撤權路徑；先重現再修工程問題。
4. [ ] 最終凍結完整 regression、三研究流程、auth／PDF／export、科學引擎 parity、隔離 rollback。
5. [ ] 安全與秘密掃描、驗收、自我稽核與可重現交接；清楚區分自我稽核與獨立外部審查。
6. [ ] 僅 push personal 的本輪整合分支，核對遠端 SHA，停 Owner Gate。

## 首批調查

本機 lock 是主要候選；不直接套雲端 29 項變動。先以 inventory／advisory 掃描判斷必修版本。Google 是未配置原型，OAuth 不可用不能冒充成功。科學驗收不因工程測試通過而升級。

## 2026-10-26 05:18 實際續作點

- 沒有其他 QA 程序在跑，接續未完成項；未 push。
- `9b73030` 正式 runtime `6505cba…`、verification `6be4ca9…` 完整建置。
- 首輪完整 regression：1,504 passed／50 skipped／1 failed（新增測試的 torchvision 預期 hash 少抄最後一碼，已依官方 index 補正；不放寬斷言）。原失敗保存 regression-frozen/。
- 自主三流程首輪在 goto load 10 秒超時；資產／科學 browser 通過，需乾淨重驗與調查，不改 timeout。
- 300 公開已曝露 SciFact dev 在原 3.10、本機舊 3.12、整合 3.12 byte-identical；不讀 sealed holdout。
- 12 翻譯控制：舊 numeric exact 10/12，新 11/12；語義條件仍須人工審閱。EasyOCR 雙語錯讀仍在；加入 chi_tra 後 Tesseract 三張合成圖 CER=0，非獨立科學驗收。
- pip-audit 95 筆降到 1 筆（deep-translator PYSEC-2022-252，官方資料明列 1.11.4 受影響且無修復版，不得逕自忽略）。Gitleaks 本輪歷史 0；Bandit findings 待逐項分流，不能稱全面無漏洞。
