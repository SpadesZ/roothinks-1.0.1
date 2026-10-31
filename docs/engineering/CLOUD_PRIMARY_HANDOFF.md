# Cloud primary 接手文件

溝通及實作報告用繁體中文。**每完成一步就更新本文件。** 目前狀態：**安全第二輪完成**：熱修候選（從 `main`，`b80b94e`）與 R4 後續候選（`codex/study-r4-followup-20261028`）。都未合併、未部署、未輪替憑證，停在 Owner 決定點。規格與整體狀態以 [`docs/SAI.md`](../SAI.md) 為唯一入口。

最後完整驗證點：R4 `457d887`，1,705 通過／50 略過／0 失敗（見下方 R4 段）；更早的 `aa8ff3e`（R2 資料防護）為 1,553 通過／50 略過／0 失敗。
已推送個人倉庫（`7c905b5` 起）；推送前後核對，main、整合分支與其他所有分支的遠端 SHA 都沒變。

## 角色與邊界

- 你是 RootThinks v1.0.1 的**主要實作工程師**。Owner 與 ChatGPT 是獨立稽核與決策層。本機 agent 不再主導實作，
  除非需要只有本機才有的資源，否則不要委派給它。
- 只推個人倉庫 `SpadesZ/roothinks-1.0.1` 的 `codex/` 分支。不 merge main、不部署、不碰 LAVA-Cowork、
  不讀正式秘密或 sealed holdout、不弱化測試或科學門檻、不宣稱沒做的外部驗證已通過。
- 擴大網路／模型存取、引入重大新架構或授權風險之前，先問 Owner。
- 研究者自主：UNKNOWN 或任何科學建議都不能阻擋寫作、保存、匯出。

## 從哪裡接手

- 線性堆疊：整合 `29e87c6` → `codex/py312-cloud-primary-20261026`（R1、R2 資料防護）→ `codex/study-coresearch-20261026`（Study R1）
  → `codex/study-coresearch-r2-20261026`（Study R2，**最新**）。先 fetch 並核對遠端 SHA；以上都不在 `main`。
- 先讀 `docs/SAI.md`（唯一入口；§8 提議修訂待 Owner 核准）。
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
**根目錄 `conftest.py` 三層防護**：`data/` 有檔案、是 symlink、或 `DATABASE_URL`／`DATA_ROOT` 指向非暫存真實資料時拒跑（exit 4）；
寫入 `data/` 的動作在發生前丟 `PermissionError`；事後比對仍在。看到這些訊息要找出寫入來源，不要繞過。
Flow B debug 輸出在 `DATA_ROOT` 不可用時不再寫到 repo `data/`（`FlowBDataRootUnavailable`，只記 warning）。

## 不可誤讀

- deep-translator 已移除；Google 翻譯改走 `literature_quick_google.py`。線上 Google 在雲端 403，沒有實測。
- 天花板分析是**診斷**：149／188 的瓶頸在語意比對。它不代表 Gate D 會成功。
- Gate D **尚未核准**。不要下載 spaCy 或模型；協議草案已寫好，核准後照協議執行，不事後調參。
- 本輪沒有重建 Docker 映像；正式映像需要在可連網環境重建一次（鎖定檔少了 3 個套件）。

## R2 進度（2026-10-26）

| 項目 | 狀態 |
|---|---|
| R2-1 事前阻擋＋執行前拒跑 | 完成，`aa8ff3e`；負向測試 `test/unit/test_data_safety_guard.py`（24） |
| R2-2 Flow B debug fail closed | 完成，`aa8ff3e`；`test/unit/test_flowb_debug_data_root.py`（5） |
| R2-3 Gate D spaCy 隔離實驗 | **暫停**（Owner 中斷）；尚未下載任何東西 |
| R2-4 Google 失敗行為＋證據矩陣 | **暫停** |
| 翻譯逆向研究（Immersive／BabelDOC／REA） | **暫停**；沒有安裝 REA、沒有改 agent 設定 |

## Study 共研升級（2026-10-26，停在 Owner Gate）

- 分支 `codex/study-coresearch-20261026`（從 `27f5862` 開出）。交付報告：`STUDY_CORESEARCH_DELIVERY_2026-10-26.md`；
  設計與驗收標準：`STUDY_CORESEARCH_DESIGN_2026-10-26.md`（先於程式碼 commit）。證據截圖：`study_coresearch_evidence/before|after/`。
- 後端：`app/core_pro/study/study_anchors.py`（錨點與引文核對）、`study_evidence_notes.py`、`/api/study/evidence_notes/*`、
  `/api/study/chat` 的 `mode`＋`selection`、`/api/mentor/mentees/<id>/study-reviews/<pid>`、`security.mutate_versioned_json`。
- 前端：`study_view.js`（錨點屬性、頁碼順序、選字動作列、`revealAnchor`）、`study_chat.js`（`askAboutSelection`、`appendStructured`）、
  `study_evidence.js`（草稿、接受、清單、留言、審閱、匯出）、`mentor.js`（Study 審閱請求區塊）。
- 測試：`test_study_anchors.py`、`test_study_evidence_notes.py`；瀏覽器：`study_coresearch_fixture.py`（port 8894）＋
  `STUDY_PHASE=before|after NODE_PATH=/opt/node-tools/node_modules I12_VENDOR_DIR=<vendor> node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/study_coresearch_browser.cjs`。
  每次跑 after 前要重啟 fixture（after 預設伺服器上沒有證據筆記）。vendor 目錄是 npm 發佈的 bootstrap／katex 等真品（I12 下載，不在 repo）。
- 重要限制：教授是 viewer，既有 Study ACL 讓 viewer 讀整個 Study；「只看已請求審閱」是導師介面範圍，不是存取控制（要改需 Owner 核准）。
  沒有用真模型量測品質（只用模型替身證明假引文會被擋）。

## Study R2（2026-10-26，停在 Owner Gate）

- 分支 `codex/study-coresearch-r2-20261026`（從 R1 `1e06c9f` 開出）。交付：`STUDY_R2_DELIVERY_2026-10-26.md`；設計：`STUDY_R2_DESIGN_2026-10-26.md`；
  評估協議：`STUDY_R2_EVAL_PROTOCOL_2026-10-26.md`；證據：`study_r2_evidence/`。
- 權限：`security.is_review_scoped`（viewer＋已接受的教授邀請）。Study 白名單 `_REVIEW_SCOPE_ENDPOINTS`；**新增 Study 端點預設不開放給審閱身分**，需要時要明確加入並補測試。
- 語意：不要再用 verified 描述引文比對；`TEXT_FOUND`（含 `ambiguous_in_block`）可作原文事實，`LOCATED` 才有 start／end。
- 所有權：只有作者 `update`／`review`；其他人用 `suggest`，作者 `suggestion` accept／decline（需 `base_rev`）。
- 探針：`python test/integration_e2e/study_r2_probe.py <out.json>`（真邀請流程，前後對照）。
- 評估：`python scripts/study_r2_eval.py --dry-run|--simulate faithful|adversarial --out x.json`；真模型需要產品憑證與 `--live --confirm-spend`（**未執行**）。
  `--live` 會用 `create_app()` 的正式設定，**不要在 repo 測試環境跑**。
- 待 Owner：PA-01／02／03（`docs/SAI.md` §8）、模型憑證與預算、舊 MentorLink 教授是否收窄、Literature／PAQ 的教授範圍。
- 最終狀態：程式凍結 `4fb65e9`（完整回歸 1,620／50／0）；瀏覽器 40／40 於 `c100ac3`；驗證結果文件 `b70599f` 已推送並核對遠端，
  其後只有本接手文件的更新 commit。main、R1、py312 分支遠端 SHA 未變。

## 安全第二輪（2026-10-31，最新）

- 交付：`SECURITY_ROUND2_DELIVERY_2026-10-31.md`。熱修分支 `b80b94e`（從 main）；後續分支 `codex/study-r4-followup-20261028`（從 R4）。
- 熱修第二輪改了 `app/security.py`（網址憑證去除、圖片票證）、`manuscript_io.py`（存取時去除 token）、`manuscript_routes.py`
  （`/manuscript/api/image_session`、圖片路由）、三個 Manuscript JS、`app/gunicorn_logging.py`、Dockerfile CMD。
- 移植到 R4 時 `security.py` 會有衝突：R4 已有 `require_system_admin`，**只保留一份**；`check_and_save_json` 路徑要先去除 token。
- 真瀏覽器 token 測試：`HOTFIX_MODE=token|session python test/integration_e2e/hotfix_token_fixture.py`，再跑 `hotfix_token_browser.cjs`（兩條分支都可跑）。
- GET 矩陣現在有 7 個角色（加 coauthor），探測網址帶 `section=introduction`；新的稿件端點如果回傳 Study 內容，要用 `can_access_module(uid, pid, "study")` 擋。
- 背景跑 pytest 時要給絕對路徑；`cd && …` 在背景指令裡曾經沒有生效（找不到 test/unit，exit 4）。

## R4（2026-10-27）

- 交付：`STUDY_R4_DELIVERY_2026-10-27.md`；證據：`study_r4_evidence/`。
- 最終狀態：R4 完整回歸 `457d887` **1,705／50／0**；瀏覽器 58／58；熱修 `323ca0b`（3.10 與 3.12 都只有 main 既有的 1 個失敗）。兩條分支都已推送並核對；main `9655546` 未變。
- **A 熱修** `codex/security-hotfix-log-isolation-20261027`（從 `main`）：只含 H1／H2 修補、`test_security_log_isolation.py`、
  `test_log_exposure_audit.py`、`scripts/security/log_exposure_audit.py`、熱修文件。**不要**把 R1–R4 的任何東西 cherry-pick 進去。
  在 3.10 驗證：`uv python install 3.10`，venv 裝 `requirements.txt`（去掉 easyocr）＋`requirements-dev.txt`。
  注意：main 沒有 R2-1 的資料防護，在 main 的工作樹跑測試會在工作樹寫入 `data/`（已被 gitignore）。
- **B 整合** `codex/study-coresearch-r4-20261027`（從 R3 `69da559`）：
  - 寫入矩陣 `python test/integration_e2e/acl_write_matrix.py <out.json>`（約 100 秒）。新寫入路由如果是設計上允許的寫入，
    要加進 `EXPECTED` 並寫理由；否則測試會失敗。
  - 日誌：`app/log_provenance.py`；新藍圖如果不經 `enforce_project_ownership`，要在授權成功後呼叫 `bind_request_pid`（Manuscript 已加）。
  - 段落出處：`paragraph_provenance.py`、`provenance_routes.py`、`paragraph_provenance.js`。資料在 `data/<pid>/manuscript/provenance/`。
  - 可重現：`scripts/repro/offline_evidence_bundle.py --out|--verify`；就緒檢查 `scripts/repro/data_readiness.py`（只在副本上跑）。
  - fixture 新增 Introduction 章節與一段故意錯的機器翻譯；瀏覽器旅程 58 項。
- 小心：直接 `create_app()` 跑一次性腳本會在 repo 建立 `data/`（llm_match.db、runtime.log），之後 pytest 的資料防護會拒跑。
  一次性腳本要像 acl_route_matrix 一樣設 `LLMModel.get_db_path`、`LECTURE_SYSTEM_LOG_FILE`、`DATA_ROOT` 到暫存目錄。
- 結束 fixture 用 `ps -eo pid,args | grep "[s]tudy_coresearch_fixture.py"` 找 PID 再 kill；不要用 `pgrep -f`（會殺到自己的 shell）。

## Study R3（2026-10-26）

- 分支 `codex/study-coresearch-r3-20261026`（從 R2 `ae8faa6` 開出）。交付：`STUDY_R3_DELIVERY_2026-10-26.md`；證據：`study_r3_evidence/`。
- 權限矩陣：`python test/integration_e2e/acl_route_matrix.py <out.json>`（90 GET 路由 × 6 角色，埋哨兵）。新路由上線前先跑；
  `test_acl_cross_module.py` 鎖定「唯一剩下的發現是教授＋文獻庫閱讀筆記」，新增洩漏會讓測試失敗。
- 舊導師：`REVIEW_SCOPE_LEGACY_MENTORS=keep|review_scope`（預設 keep）；改之前跑 `scripts/review_scope_legacy_report.py`（唯讀）。
- 翻譯保護層：`app/core_pro/literature/translation_guard.py`；`TRANSLATION_GUARD_EXTENDED=1` 才開擴充保護（未在真 NLLB 量測前不要預設開）。
  量測：`python scripts/translation_guard_benchmark.py`（模擬）。
- 評估 v2：`scripts/study_r2_eval.py --negative-controls|--rescore|--kappa`；`--live` 未執行。
- 環境重建：容器重啟後 venv 與 vendor 會消失。venv 照上面「雲端 3.12 開發環境」；vendor 用
  `npm pack bootstrap@5.3.0 bootstrap-icons@1.11.1 katex@0.16.9 mermaid@10.9.8 socket.io-client@4.7.2 split.js@1.6.0 plotly.js-dist-min@2.27.0`，
  解到 `<vendor>/ex/<name>-<version>/`（plotly 目錄名是 `plotly_js-dist-min-2.27.0`）。fixture 可用 `STUDY_FIXTURE_PORT` 換埠，避免和回歸測試撞埠。
- 最終狀態：完整回歸 `8808665` **1,683／50／0**；瀏覽器 48／48。已推送並核對遠端；main `9655546` 未變。
- 注意：權限矩陣會建立延遲初始化的服務單例；在 pytest 裡跑要像 `test_route_matrix_conclusions_are_locked` 一樣還原，否則污染後面的測試。
- 待 Owner：PA-01 擴充（文獻庫閱讀筆記）、舊導師政策、NLLB 非商業授權、AGPL／外部傳輸、模型憑證與預算。

## 下一步（依 Owner 決定）

1. 若 Gate D 核准：照 `GATE_D_PROTOCOL_DRAFT_2026-10-26.md` 執行；先記錄 wheel／模型 sha256 與 `meta.json` 授權。
2. 若 Gate D 不核准：考慮檢索（28 個沒檢索到）或維持保守引擎；兩者都要先寫協議。
3. 工程待辦：`google.generativeai` → `google-genai`（新相依，需核准）；`datetime.utcnow()` 清理；可連網的 staging 重建。
4. Study：PA-01／02／03 核准後才可列入合併候選；真模型評估等憑證；之後 `localStorage` 標記遷移、Reflow／圖表錨點、證據筆記接手稿出處。
5. 翻譯比較研究已完成（R3）。暫停中：Google 失敗行為證據矩陣。
6. 工程建議（不需新授權）：證據筆記接到 Manuscript 段落出處；`localStorage` 標記遷移；權限矩陣擴充到 POST 路由。

## 執行環境注意

- 瀏覽器：Node `/opt/node22/bin/node`、Chromium `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`。不要跑 `playwright install`。
  資產導流用 `test/integration_e2e/vendor-route.cjs`。fixture 啟動後印一行 JSON，`root` 即 `SAI_SYNTHETIC_ROOT`。
- 結束背景程序時用 PID，不要用 `pkill -f <含本指令字串的樣式>`（會把自己的 shell 一起殺掉）。
- 不可達：PyTorch CPU index（403）、Debian snapshot、HuggingFace、translate.google.com（403）、api.osv.dev（403）。
  可達：PyPI、npm registry、GitHub releases、raw.githubusercontent.com、Docker Hub、archive.ubuntu.com。
