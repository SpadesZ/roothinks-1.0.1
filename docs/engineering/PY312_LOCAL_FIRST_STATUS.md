# LOCAL-FIRST Python 3.12 整合進度

Owner 於 2026-10-25 核准本機主導、單一隔離整合分支完成後 push personal。禁止 main merge、部署、LAVA-Cowork、科學規則變更與 sealed holdout。不得自行啟動 cloud handoff。

- 基準：本機 `433eec4`，雲端 `e64c4e7`，兩者已讀；原分支及證據保留。
- 分支：`codex/py312-local-first-integration-20261025`。
- 本輪 QA：`D:\Documents\RootThinksValidation\py312-integration-20261025`；舊 QA 不覆寫。

## 可驗交付清單

1. [ ] 29 項依賴逐項調和；lock、wheel/model hashes、資安掃描與相依圖。
2. [ ] 正式 Dockerfile／測試映像使用唯一 pinned Python 3.12／CPU Torch／真 NLLB；完整 build。
3. [ ] OCR／翻譯固定控制、tokenizer 警告、Google 失敗／就緒／撤權路徑；先重現再修工程問題。
4. [ ] 最終凍結完整 regression、三研究流程、auth／PDF／export、科學引擎 parity、隔離 rollback。
5. [ ] 安全與秘密掃描、驗收、自我稽核與可重現交接；清楚區分自我稽核與獨立外部審查。
6. [ ] 僅 push personal 的本輪整合分支，核對遠端 SHA，停 Owner Gate。

## 首批調查

本機 lock 是主要候選；不直接套雲端 29 項變動。先以 inventory／advisory 掃描判斷必修版本。Google 是未配置原型，OAuth 不可用不能冒充成功。科學驗收不因工程測試通過而升級。
