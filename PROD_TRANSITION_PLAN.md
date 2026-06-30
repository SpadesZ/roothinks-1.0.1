# Roothinks 現狀轉上線規劃書 V3（完整版）

版本: v3  
更新日期: 2026-04-09  
適用範圍: `roothinks`（Docker Compose 部署）

## 1. 現況基線（As-Is）

1. 系統目前可運行，容器健康。
2. 當前為解卡模式（開發可用，非正式）:
   - `API_AUTH_ENABLED=0`
   - `CORS_ALLOWED_ORIGINS=http://127.0.0.1:10000,http://localhost:10000`
3. 目前主要缺口在「正式基礎設施與上線運維路徑」，而非功能不可用。

## 2. 上線目標（To-Be）

1. 安全目標:
   - API/Socket 啟用 Bearer 驗證與 ACL（`API_AUTH_ENABLED=1`）
   - 僅允許正式來源 CORS
   - 正確處理代理後真實 IP（ProxyFix + rate limit）
2. 架構目標:
   - base/dev/prod compose 分離
   - prod 不掛載整包原始碼（不使用 `.:/app`）
   - 僅保留資料持久化卷（`/app/data`）
3. 運維目標:
   - 啟用 Gunicorn access log
   - 具備部署前顯式 migration
   - 具備可驗證回滾流程

## 3. 關鍵決策（V3）

1. Compose 分離:
   - `docker-compose.yml` 作為 base（無 `.:/app`）
   - `docker-compose.override.yml` 作為開發覆蓋（專放 `.:/app`）
   - `docker-compose.prod.yml` 作為正式覆蓋（僅保留 `/app/data`）
2. 服務模型:
   - Web API/Socket 服務維持 eventlet 路線
   - OCR/PDF 等 CPU-heavy 任務規劃拆到背景 worker（Celery/RQ + Redis）
3. Migration 策略:
   - 部署前顯式執行 `python fix_db_schema.py`
   - 啟動路徑只保留輕量 guard，不把重 migration 壓在 app 冷啟動
4. 代理策略:
   - 對外環境必須有 Nginx/Caddy/LB 代理 TLS
   - Flask 啟用 `ProxyFix`，且只在可信代理環境啟用
5. DB 路徑策略（上線穩定度）:
   - SQLite 僅作過渡方案，不作最終高併發上線目標
   - 若仍維持 SQLite，連線池採保守值並搭配單 Web worker
   - 正式高負載上線建議切 PostgreSQL，再使用 `pool_size=5/max_overflow=10`
6. Lock 路徑策略:
   - lock 檔不寫在 bind mount 的 data 根目錄
   - 使用 `LOCK_ROOT`（Linux 預設 `/tmp/roothinks-locks`，Windows 預設 `%TEMP%/roothinks-locks`）
7. Worker 策略:
   - 硬條件是「Web 與 OCR/PDF 任務分離」
   - `eventlet -> gthread` 屬暫時緩解，不取代任務分離本身

## 4. 分階段計畫

## Phase A: 凍結與備援（D0）

1. 建立備份:
   - `data/roothinks.db`
   - `data/manu_core.db`
2. 產出回滾包:
   - 前一版映像 tag
   - 前一版 env/compose
   - DB 快照
3. 凍結功能變更，只允許上線相關修正。

## Phase B: 環境與 Compose 切分（D1）

1. 建立環境檔:
   - `env.dev`
   - `env.prod`（不提交真實 secret，提交 `env.prod.template`）
2. 建立 compose 三層:
   - base: `docker-compose.yml`（不含 `.:/app`）
   - dev: `docker-compose.override.yml`（放 `.:/app`、`.env`、dev workaround）
   - prod: `docker-compose.prod.yml`（僅 `./data:/app/data`）
3. 明確部署命令:
   - 開發: `docker compose up -d`（自動載入 `docker-compose.override.yml`）
   - 正式: `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file env.prod up -d --build`

## Phase C: 認證授權與前端注入（D1~D2）

1. 前端建立統一 API client:
   - fetch 一律注入 `Authorization: Bearer <token>`
2. WebSocket 一律透過 auth payload 傳 token。
3. 套用至 Dashboard/PAQ/Literature/Study/Manuscript 全模組。
4. 啟用並驗證:
   - `API_AUTH_ENABLED=1`
   - `API_BEARER_TOKENS`
   - `TOKEN_ACL_MAP_JSON`
5. 驗證 ACL:
   - 無 token -> 401
   - 有 token 無權限 pid -> 403
   - 有 token 有權限 pid -> 200

## Phase C2: 系統高併發與穩定度調優（D2，Go/No-Go Blockers）

### P0（上線前必解）

1. DB 連線池擴容與釋放紀律（`app/__init__.py`）
   - 解除 `pool_size=1/max_overflow=0` 的硬瓶頸
   - 全背景任務強制 `commit()/rollback()` + `session.remove()` 路徑
   - 若仍使用 SQLite: 保守配置 + 單 Web worker
   - 若切 PostgreSQL: `pool_size=5/max_overflow=10/pool_timeout=30`
2. FileLock 路徑與權限修正（`app/security.py`）
   - 全部 lock 檔改到 `LOCK_ROOT`，不直接落在 `/app/data`
   - 啟動時做 lock 目錄建立與可寫自檢
3. Web/OCR 工作負載分離（`Dockerfile`/compose）
   - Web 容器只處理 API/Socket
   - OCR/PDF 由背景 worker（Celery/RQ/獨立進程）執行
   - 避免 `WORKER TIMEOUT` 與 `SIGKILL`

### P1（第二階段）

4. Task 3 改非同步
   - `/api/literature/search` 改 `202 + task_id`
   - 前端 Polling 或 Socket 接收完成事件
5. 全域背壓（Backpressure）
   - 單 PID 併發上限（例如 5）
   - 超額直接 `429 Too Many Requests`，禁止無限排隊

### P2（收尾）

6. API Key 加密雜訊清理
   - 一次性 migration 清理舊格式 token
   - 消除 log 中 decrypt fail 噪音，避免誤判事故

## Phase D: 基礎設施與可觀測性強化（D2）

1. Gunicorn 存取日誌:
   - 加入 `--access-logfile -`
   - 建議同時設定 `--error-logfile -`
2. ProxyFix 與真實 IP:
   - 在 `app/__init__.py` 條件式啟用 `werkzeug.middleware.proxy_fix.ProxyFix`
   - 透過 env 控制 `x_for/x_proto/x_host` 信任層級
   - 驗證 Flask-Limiter 的來源 IP 為真實 client IP
3. CORS/Origin:
   - prod 僅允許正式網域，禁止 `localhost`

## Phase E: 工作負載隔離（D2~D3）

1. 定義 OCR/PDF 背景任務路徑:
   - Web 請求只投遞任務，不在請求執行重 CPU 任務
2. 引入背景 worker:
   - Celery 或 RQ（使用既有 Redis）
3. Web 服務保持回應與 WS 穩定，避免單 worker 被 CPU 任務鎖住。

## Phase F: 上線 Runbook（D3）

1. 部署前:
   - `python deploy_preflight.py --env-file env.prod`
   - （Windows 一鍵）`.\deploy_preflight.ps1 -EnvFile env.prod`
   - preflight 全綠後再進入 migration
2. migration:
   - `python fix_db_schema.py`（顯式 migration）
   - migration 成功紀錄入 release note
3. 佈署:
   - 使用 prod compose + prod env 啟動
4. 上線後 60 分鐘監控:
   - 401/403/500 比率
   - API latency
   - WebSocket 連線成功率
   - 背景任務排隊與耗時

## 5. 環境變數清單（Prod 必填）

1. `FLASK_ENV=production`
2. `FLASK_DEBUG=0`
3. `SECRET_KEY=<strong-random>`
4. `FERNET_KEY=<valid-fernet-key>`
5. `API_AUTH_ENABLED=1`
6. `API_BEARER_TOKENS=<comma-separated>`
7. `TOKEN_ACL_MAP_JSON=<json>`
8. `CORS_ALLOWED_ORIGINS=<prod-domains-only>`
9. `SOCKETIO_MESSAGE_QUEUE=redis://...`
10. `RATELIMIT_STORAGE_URI=redis://...`
11. LLM provider secrets（與 dev 分離）
12. `LOCK_ROOT=<writable-lock-dir>`
13. `DB_POOL_SIZE=<profile-based>`
14. `DB_MAX_OVERFLOW=<profile-based>`
15. `DB_POOL_TIMEOUT=30`

## 6. 驗證清單（Staging -> Prod）

1. 安全驗證:
   - 無 token API = 401
   - 越權 pid = 403
   - 授權 pid = 200
   - WebSocket 無 token 拒連
2. 代理驗證:
   - Access log 可見真實 client IP
   - Rate limit 不會把所有請求判成同一 IP
3. 穩定性驗證:
   - 大 PDF/OCR 任務進行中，其他 API 仍可回應
   - 無大面積 502/504 timeout
   - 連續同時上傳 5 份大型 PDF 時，非重任務 API P95 < 2s
   - WebSocket 在重任務期間可維持連線與回應
   - 日誌不得再出現:
     - `QueuePool limit of size 1 overflow...`
     - `Permission denied ... .lock`
4. 功能驗證:
   - Dashboard 專案列表正常
   - PAQ 主流程正常（載入、保存、歷史紀錄）
   - Literature history 正常
   - Study 對話列表正常
   - Manuscript WS 正常

## 7. Go/No-Go 標準

Go:

1. 核心驗證全綠（安全、代理、穩定性、功能）。
2. `Phase C2` 的 P0 項目全數完成且驗收通過。
3. P1 項目至少完成 Task3 非同步或具等效保護機制。
4. 重負載驗收（5 份大 PDF）達標，且 Web/API/Socket 不被拖垮。
5. 日誌無 DB pool 與 lock 權限錯誤。
6. 有完整回滾包並演練成功。

No-Go:

1. 任一 P0 未完成。
2. 任一安全驗證失敗。
3. 代理後真實 IP 未正確識別。
4. OCR/重任務壓力下 API 或 WS 出現 502/504 或長時間不可用。
5. 日誌仍出現 DB pool 耗盡或 lock 權限錯誤。

## 8. 風險註記（V3）

1. `gthread` 可降低單 worker 鎖死風險，但不是任務分離的替代方案。
2. SQLite 在高併發與重任務下風險仍高，應作過渡，不應作長期終態。
3. lock 路徑改為 `LOCK_ROOT` 後，需在每個部署環境做可寫驗證。

## 9. 回滾策略

1. 切回前一版 image + env + compose。
2. 還原 DB 快照。
3. 執行 smoke test:
   - Dashboard
   - Literature
   - Study
   - Manuscript
4. 回滾後保留故障窗口日誌與時間軸，作為下一輪修正依據。

## 10. 交付物（V3）

1. `PROD_TRANSITION_PLAN.md`（本文件）
2. `env.prod.template`
3. `docker-compose.prod.yml`
4. `deploy_preflight.py`（上線前一鍵檢查）
5. `deploy_preflight.ps1`（Windows 一鍵入口）
6. 上線/回滾 runbook（命令級）
7. Staging 驗證報告（含 401/403/500、延遲與 5-PDF 壓測統計）

## 11. 現狀版 -> 上線版檔案規劃（As-Is -> To-Be）

1. `docker-compose.yml`
   - As-Is: base compose（共用設定）
   - To-Be: 維持 base 角色，不放 `.:/app`
2. `docker-compose.override.yml`（新增）
   - As-Is: 不存在
   - To-Be: dev 專用覆蓋檔（`.:/app`、`.env`、Windows dev workaround）
3. `docker-compose.prod.yml`（新增）
   - As-Is: 不存在
   - To-Be: 正式環境覆蓋檔，移除原始碼掛載，僅保留 `./data:/app/data`
4. `.env`
   - As-Is: 本機實際運行環境（可能含暫時解卡設定）
   - To-Be: 僅 dev 使用，不作正式部署來源
5. `env.prod.template`（新增）
   - As-Is: 不存在
   - To-Be: 正式環境模板（含 `LOCK_ROOT`、`DB_POOL_*`、`GUNICORN_*`、Redis 密碼 URI）
6. `app/__init__.py`
   - As-Is: 固定 DB pool 參數、無 `LOCK_ROOT` 啟動自檢
   - To-Be: DB pool 參數由 env 控制 + `LOCK_ROOT` 啟動可寫檢查
7. `app/security.py`
   - As-Is: lock 檔寫在資料檔旁 (`*.lock`)
   - To-Be: lock 檔統一寫入 `LOCK_ROOT`（避免 bind mount 權限問題）
8. `app/services/context_chain_service.py`
   - As-Is: 使用資料檔旁 lock
   - To-Be: 共用 `LOCK_ROOT` lock path
9. `app/llm_service/matching_tasks/task_3search.py`
   - As-Is: task3 cache lock 寫在資料檔旁
   - To-Be: task3 cache lock 改用 `LOCK_ROOT`
10. `app/core_pro/literature/literature_routes.py`
   - As-Is: 預設批次執行緒數偏高
   - To-Be: 預設 `LITERATURE_MAX_WORKERS=1`（穩定度優先），並加強 DB session 釋放
11. `Dockerfile`
    - As-Is: 固定 gunicorn 命令，不易切 profile
    - To-Be: `GUNICORN_*` 參數化 + access/error log 啟用 + `LOCK_ROOT` 目錄建立
12. `deploy_preflight.py`（新增）
    - As-Is: 無上線前自動阻擋檢查
    - To-Be: 檢查 secrets、compose 合併、volume、data 可寫、redis 密碼、DB dry-run
13. `deploy_preflight.ps1`（新增）
    - As-Is: 無 Windows 一鍵 preflight
    - To-Be: `.\deploy_preflight.ps1` 即可啟動完整 preflight

正式部署命令（V3）:
`docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file env.prod up -d --build`

## 12. 本地 Demo 穩定化補充（2026-04-10）

目的:
1. 解決本地 `Task4` CPU 密集流程造成 `eventlet` worker 卡死、`WORKER TIMEOUT`、Auto-heal 重跑循環。
2. 僅針對開發/展示環境調整，不改動正式部署路徑。

套用範圍:
1. 只在 `docker-compose.override.yml`（dev overlay）生效。
2. `docker-compose.prod.yml` 與 `env.prod` 不採用本段調校值。

dev overlay 調整（已落地）:
1. Gunicorn:
   - `GUNICORN_WORKER_CLASS=gthread`
   - `GUNICORN_WORKERS=1`（dev 先鎖單 worker，避免 Manuscript polling 在多 worker 下出現 `Invalid session`）
   - `GUNICORN_THREADS=8`
   - `GUNICORN_TIMEOUT=900`
   - `GUNICORN_GRACEFUL_TIMEOUT=90`
2. 容器資源:
   - `cpus: 4.0`
   - `mem_limit: 4g`
3. LLM 逾時/重試:
   - `GOOGLE_LLM_TIMEOUT_SEC=60`
   - `GOOGLE_LLM_MAX_RETRIES=1`
   - `GOOGLE_LLM_RETRY_BACKOFF_SEC=1`
4. 背景執行策略（dev）:
   - `LITERATURE_USE_DIRECT_THREAD=1`（dev 直接背景 thread，避開本地 executor 佇列異常）
   - `LITERATURE_ANALYZING_STALE_SEC=300`（analyzing 超時自動轉可重試）
5. Task4 Auto-heal 防循環:
   - `TASK4_AUTOHEAL_MAX_RETRY=1`
   - `TASK4_AUTOHEAL_COOLDOWN_SEC=900`
   - `LITERATURE_OCR_READY_RATIO=1.0`
   - `LITERATURE_STAGE_READY_RATIO=1.0`
   - 在 `app/core_pro/literature/literature_routes.py` 加入 auto-heal state/cooldown gate，並將 OCR/Fix 完成判定改為「按頁覆蓋率」避免「只跑到第 1 頁也被判完成」。
6. 狀態顯示自修復（dev-safe）:
   - 若 DB 仍是 `analyzing`，但 `fixed/summary` 產物已完整，`status API` 直接回報 `gold_ready`，避免 UI 卡死。
7. CV pipeline 單頁超時保底（dev）:
   - `LITERATURE_STACKA_TIMEOUT_SEC=120`
   - `LITERATURE_STACKB_TIMEOUT_SEC=300`
   - `app/core_pro/literature/literature_cvpipeline.py` 增加單頁 timeout 與 StackB fallback，避免整份文件因單頁卡住而中斷。

注意:
1. 此補充屬「開發穩定化」，不是正式上線最終架構。
2. 正式上線仍以「Web/OCR 分離（背景 worker）」為終態，不以 gthread 取代任務分離。

## 13. 上線切回多 Worker 的穩定方案（2026-04-11）

目標:
1. 在保留多 worker 吞吐量的前提下，避免 Manuscript Socket 再出現 `Invalid session`、`socket.io 400/500`。
2. 讓 `chat_message -> job_queued -> job_progress -> job_done` 事件鏈在高併發下可持續穩定。

根因摘要:
1. 若使用 polling，且請求在多 worker 間跳轉（無 sticky session），同一 `sid` 會被不同 worker 視為未知，觸發 `Invalid session`。
2. worker class 與 Socket async model 不一致（例如 gthread + eventlet 路徑混用）會導致握手錯誤與連線異常。

上線必備條件（P0）:
1. Web 與 OCR/PDF 任務分離:
   - Web 容器只做 API/Socket。
   - OCR/重 CPU 任務由獨立 worker（Celery/RQ）處理。
2. Socket 水平擴充一致性:
   - 啟用 `SOCKETIO_MESSAGE_QUEUE=redis://...`。
   - 反向代理對 `/socket.io` 啟用 sticky session（cookie 或 ip_hash）。
3. 連線策略一致:
   - 若採 websocket-first：代理需完整設定 `Upgrade/Connection`。
   - 若保留 polling fallback：必須有 sticky session，否則不得開多 worker。

建議上線配置（多 worker 穩定版）:
1. App/Env:
   - `SOCKETIO_MESSAGE_QUEUE=redis://<redis-host>:6379/1`
   - `RATELIMIT_STORAGE_URI=redis://<redis-host>:6379/0`
   - `API_AUTH_ENABLED=1`
2. Gunicorn/Socket（推薦 Profile A）:
   - `GUNICORN_WORKER_CLASS=eventlet`
   - `SOCKETIO_ASYNC_MODE=eventlet`
   - `GUNICORN_WORKERS=2~4`（依 CPU 與壓測結果調整）
3. 代理:
   - `/socket.io` 開啟 sticky session
   - `proxy_read_timeout >= 120s`
   - `proxy_send_timeout >= 120s`
   - websocket upgrade header 正確轉發

過渡配置（若暫不切 eventlet，僅先回多 worker）:
1. 可用 `gthread + threading`，但要同時滿足:
   - `/socket.io` sticky session 已啟用
   - `SOCKETIO_MESSAGE_QUEUE` 指向 Redis
   - Staging 壓測通過後才可由 `workers=1` 升到 `workers>1`
2. 若 sticky 尚未完成，禁止升 worker，維持 `workers=1`。

驗收門檻（Go 條件）:
1. 連續 30 分鐘壓測下，`/socket.io` 不得出現連續型 `Invalid session`。
2. Manuscript 事件鏈成功率:
   - `job_queued` 到 `job_done` 成功率 >= 99%
   - `job_error` 可追蹤且有明確錯誤碼
3. 高峰期間（同時多用戶）:
   - 無大面積 400/500 握手錯誤
   - Web/API 回應不被重任務拖垮

回退策略（若切回多 worker 後異常）:
1. 立即將 `GUNICORN_WORKERS` 降回 `1`（保留 threads）。
2. 保留 `SOCKETIO_MESSAGE_QUEUE` 與代理 log，定位 sticky/session 問題後再二次升級。
3. 僅在 Staging 重新驗收通過後，再恢復 `workers>1`。

## 14. LLM 429 配額穩定化（Task4/Task5/Task6，2026-04-11）

目標:
1. 降低多 worker / 多任務同時搶同一把 Gemini key 造成的 429 連撞。
2. 遇到 429 時改為「可預期等待」而非秒級重試風暴。
3. 將可調參數下放到 env，支援 demo 與 prod 不同配額策略。

已落地實作:
1. 全域節流（Google Adapter 層）:
   - 檔案: `app/llm_service/adapter/llm_google.py`
   - 維度: `(api_key_hash, model_name)`。
   - 配額維度:
     - RPM（每分鐘 request）
     - TPM（每分鐘 token，估算）
     - RPD（每日 request，可選）
   - 後端:
     - 優先 Redis（跨 worker 一致）
     - Redis 不可用時降級為本機記憶體節流（不中斷服務）
2. 429 動態退避:
   - 解析錯誤訊息中的 `retry in Xs` / `Retry-After`，以 provider 回傳秒數為主進行 sleep。
   - 新增 `GOOGLE_LLM_MAX_BACKOFF_SEC`，限制單次退避上限，避免無界等待。
3. 日配額耗盡快速止血:
   - 針對 `GenerateRequestsPerDay...` / `per day` 類訊號判定為 daily quota exhausted。
   - 直接停止無效重試，回傳清晰錯誤，避免持續打爆 provider。

本地 dev overlay 新增參數（已寫入 `docker-compose.override.yml`）:
1. `GOOGLE_LLM_MAX_BACKOFF_SEC=180`
2. `GOOGLE_LLM_RATE_LIMIT_ENABLED=1`
3. `GOOGLE_LLM_GLOBAL_RPM_LIMIT=12`
4. `GOOGLE_LLM_GLOBAL_TPM_LIMIT=25000`
5. `GOOGLE_LLM_GLOBAL_RPD_LIMIT=0`（預設不啟用本地日上限；若要預先保護可設定 18~20）
6. `GOOGLE_LLM_QUOTA_WAIT_MAX_SEC=180`
7. `GOOGLE_LLM_QUOTA_POLL_SEC=1`

新增可調 env（prod/staging 可依配額調整）:
1. `GOOGLE_LLM_RATE_REDIS_URL`（可覆蓋 Redis 位置；未設則回退 `RATELIMIT_STORAGE_URI` / `SOCKETIO_MESSAGE_QUEUE`）
2. `GOOGLE_LLM_GLOBAL_RPM_LIMIT`（0 表示不限制）
3. `GOOGLE_LLM_GLOBAL_TPM_LIMIT`（0 表示不限制）
4. `GOOGLE_LLM_GLOBAL_RPD_LIMIT`（0 表示不限制）
5. `GOOGLE_LLM_EST_OUTPUT_TOKENS`（預估輸出 token，預設 700）
6. `GOOGLE_LLM_EST_IMAGE_TOKENS`（每張圖估算 token，預設 1200）
7. `GOOGLE_LLM_QUOTA_WAIT_MAX_SEC`
8. `GOOGLE_LLM_QUOTA_POLL_SEC`
9. `GOOGLE_LLM_MAX_BACKOFF_SEC`

上線建議:
1. Redis 節流務必啟用（避免多 worker 各算各的）。
2. 若使用 Gemini Free Tier，建議先配置:
   - `GOOGLE_LLM_GLOBAL_RPM_LIMIT=10~12`
   - `GOOGLE_LLM_GLOBAL_TPM_LIMIT` 依 prompt 實測逐步上調
   - `GOOGLE_LLM_GLOBAL_RPD_LIMIT=18~20`（可選）
3. 若仍出現 daily quota exhausted，屬 provider 日額度耗盡，需:
   - 換 key / 升級方案 / 等配額重置（不是 retry 可解）。
