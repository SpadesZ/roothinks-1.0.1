# 資料庫遷移演練（R7，2026-11-02）

在一次性的代表性資料上，從 `main`（`9655546`）的 schema 升級到 R 堆疊。**沒有碰任何正式資料。**

- 腳本：`scripts/repro/migration_rehearsal.py --main-tree <main checkout> --out <報告.json>`
- 證據：`study_r7_evidence/migration-rehearsal.json`
- 結果：**13／13 項檢查通過**（腳本所在提交 `c96e4d9`；報告格式修正後在 `052fb40` 重跑，結果相同）

## 1. 「代表性資料」是什麼

- 用 `main` 自己的模型建立兩顆資料庫：`roothinks.db` 和 `manu_core.db`。
- 核心資料是有意義的：
  - 6 個帳號：owner、editor、viewer、coauthor、教授、admin
  - 2 個專案，以及成員、章節指派、導師關係、論文
  - Manuscript 的預設章節設定
- 其他資料表各補 3 列合成資料，外鍵指向既有的列，JSON 欄位填合法的 JSON。
- 結果：`main` 的 17 張表、Manuscript 的 5 張表，全部有資料。
- 這**不是**正式資料的副本。正式資料庫的大小、舊格式資料、Postgres 都沒有演練。

## 2. 三種情境與結果

| 情境 | 步驟 | 結果 |
|---|---|---|
| A：alembic 已在 `main` 的 head（`0002`） | 先部署新程式（啟動時跑 `fix_db_schema`＋`create_all`），再跑 `alembic upgrade head` | 既有 17＋5 張表的每一列都不變；新增 3 張表（`academic_profiles`、`project_invitations`、`chapter_review_events`）；alembic 到 `0004`；第二次啟動和第二次 upgrade 不改任何資料 |
| A：升級後的應用程式 | 登入，呼叫所有只帶 `<pid>` 的 GET API 路由（56 條） | 0 個 5xx；教授邀請可以寫入新表 |
| A：回滾（只換程式） | 不還原資料庫，直接換回 `main` 的程式 | 能啟動；45 條路由都沒有 5xx。多出的資料表會被忽略 |
| A：回滾（還原資料庫） | `alembic downgrade` | 依設計拒絕執行（不刪歷史）；從備份還原後，逐表雜湊和升級前相同 |
| B：資料庫從沒用過 alembic | 直接跑 `alembic upgrade head` | **會失敗**：`0002` 要建立的表已經存在。失敗後既有資料不變 |
| B：正確步驟 | 先 `alembic stamp 0002_create_evidence_segments`，再 `upgrade head` | 成功，既有資料不變 |
| C：順序相反 | 先 `alembic upgrade head`，再部署新程式 | 成功，既有資料不變，沒有 5xx |

## 3. 演練發現的問題

1. **`alembic.ini` 的預設網址不是應用程式的資料庫。**
   - `alembic.ini` 寫的是 `sqlite:///./instance/roothinks.db`。應用程式用的是 `data/roothinks.db`（或 `DATABASE_URL`）。
   - 執行 alembic 時沒有設 `DATABASE_URL`，就會改到另一顆（可能是空的）資料庫，而正式資料庫完全沒有被升級。
   - 這次沒有修改 `alembic.ini`，因為正式環境怎麼設定要由 Owner 決定。下面的步驟一律明確設 `DATABASE_URL`。
2. **沒有 `alembic_version` 的資料庫直接 upgrade 會失敗**（情境 B）。要先確認版本，再決定要不要 stamp。
3. **專案流程狀態頁在 `main` 上可能 500。**
   - 當舊的 PAQ 問卷欄位型別不對（例如是字串，不是物件），`/api/project/workflow/<pid>` 會整頁 500。
   - R7 已修：異常值視為「尚未填寫」。紅測試是 `test_workflow_status_r7.py`。

## 4. 正式環境的建議步驟（Owner 執行；工程端沒有執行）

1. 停止寫入（維護時段），備份 `data/roothinks.db`、`data/manu_core.db` 和整個 `data/` 目錄。
2. 確認版本：`sqlite3 data/roothinks.db "select version_num from alembic_version"`。
   - 結果是 `0002_create_evidence_segments`：照情境 A 做。
   - 沒有這張表：先確認 `evidence_segments` 表存在，再執行 `alembic stamp 0002_create_evidence_segments`（情境 B）。
   - 其他結果：停下來，不要升級。
3. 部署新程式並啟動（啟動時會建立新表）。
4. 執行 `DATABASE_URL=sqlite:////<絕對路徑>/data/roothinks.db alembic upgrade head`。**一定要設 `DATABASE_URL`。**
5. 檢查：
   - `alembic_version` 是 `0004_project_invitations`。
   - 3 張新表存在。
   - 用 owner 帳號登入，打開專案、Manuscript、Study、文獻庫。
6. 需要回滾時有兩種做法：
   - 直接換回 `main` 的映像（演練證明可以在升級後的資料庫上執行）。
   - 從第 1 步的備份還原（`downgrade` 會拒絕執行）。

## 5. 沒有演練的部分

- 正式資料庫的實際內容、大小和舊格式資料。
- Postgres（如果正式環境用 `DATABASE_URL` 指向 Postgres）。
- 多個 worker 同時啟動時的 `fix_db_schema` 鎖競爭。
- 檔案型資料（`data/<pid>/…` 的 JSON）。R 堆疊讀舊格式時是相容的，但這次沒有用正式資料演練。
