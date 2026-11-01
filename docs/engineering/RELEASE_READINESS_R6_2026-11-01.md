# 發佈就緒評估：安全熱修＋R 堆疊（R6，2026-11-01）

本文件回答兩件事：熱修能不能乾淨併入 R 堆疊，以及要按什麼順序發佈。
**沒有合併到 `main`、沒有部署、沒有動正式資料、沒有輪替憑證。** 下面列出的 Owner 動作，都要由 Owner 授權後才能做。

## 1. 分支與 SHA

| 項目 | 分支 | SHA |
|---|---|---|
| 正式基準 | `main` | `9655546`（未變） |
| 安全熱修（從 `main` 開出） | `codex/security-hotfix-log-isolation-20261027` | `b3b935e` |
| R5 整合（R5＋熱修） | `codex/integration-hotfix-r5-20261101` | `24ae014` |
| R6 開發 | `codex/study-r6-20261101` | `e52e2b5（程式；之後只改文件）` |
| **R6 整合（R6＋熱修）** | `codex/integration-hotfix-r6-20261101` | `6c7b4ca（程式；之後只合併文件）` |

## 2. 熱修併入 R 堆疊

- 有 5 個檔案衝突：`Dockerfile`、`app/security.py`、`literature_bflow.py`、`manuscript_io.py`、`manuscript_routes.py`。
  - 5 個都保留 R 堆疊那一邊，因為 R 堆疊已經有相同或更嚴的修補。
  - 每個 helper 只保留一份：`require_system_admin`、`strip_html_url_credentials`、`image_ticket_cookie_secure`、`RedactingLogger`。
  - `cmd_load_chat` 保留 R 堆疊的版本：它同時檢查章節和審閱範圍。
  - 日誌過濾保留 R 堆疊的結構化出處欄位 `[pid=…]`。它比熱修的「文字裡找 pid」更嚴：沒有標記的舊行一律不給。
- 熱修測試 `test_security_log_isolation.py` 的日誌夾具改成帶出處欄位的格式。
  - 原本的斷言全部保留，另外加了三條排除檢查：偽造提及、全站行、沒有標記的舊行。
  - 拿掉過濾後，有 4 個測試會失敗。
- 對照熱修相對 `main` 新增的每一行：
  - 30 個檔案中有 19 個和熱修完全相同。
  - 對不到的 20 行，都是被 R 堆疊更嚴的實作取代。逐行說明見整合工作樹的熱修文件 §10。
- H9（章節結構只有 editor 以上能改）在熱修和 R6 是分別修的。整合時只保留一份檢查。

## 3. 測試證據

| 範圍 | 結果 |
|---|---|
| 熱修 `b3b935e`，Python 3.10 完整回歸 | 1,143 通過、1 失敗。失敗的是 `test_user_journey_roothinks`，和未修補的 `main` 逐行相同 |
| 熱修 H9 紅測試 | 修補前 viewer 和 coauthor 兩個測試失敗；修補後 3／3 通過（3.12 與 3.10） |
| R5＋熱修（`eb017ad`），Python 3.12 完整回歸 | 1,751 通過、50 略過、1 失敗（說明見下方） |
| R5＋熱修，Python 3.10 熱修測試 | 30／30 通過；`test_security_log_isolation.py` 13／13 |
| R6 整合，Python 3.12 完整回歸 | （執行中；完成後更新） |
| 熱修真瀏覽器（token／session／`HOTFIX_SECURE=1`），在 R6 整合樹上執行 | token 4／4、session 3／3、`HOTFIX_SECURE=1` 4／4（熱修分支 `b3b935e` 上也是同樣結果） |
| 秘密掃描（熱修差異、合併差異、整合樹對 `main` 的全部差異） | 0 筆；沒有追蹤 `.env`、`data/`、log、模型、`.codegraph` 或 CA 檔 |

R5＋熱修那 1 個失敗的原因：`test_data_safety_guard.py::test_symlinked_data_and_real_databases_are_refused` 假設 repo 不在 `/tmp` 底下。
- 整合工作樹放在暫存目錄（`/tmp/...`）裡，所以這個假設不成立。
- 純 R5 放在同一個位置也一樣失敗。同一份程式碼放到 `/tmp` 以外，這個檔案 24／24 通過。
- 所以這是測試環境的問題，不是合併造成的。

## 4. 建議的發佈順序

1. **熱修先發佈。** Owner 審查 `b3b935e`，合併到 `main`，再依 §5 設定部署。
   - 熱修直接從 `main` 開出，不依賴任何提議修訂（PA）。
2. **R 堆疊之後再發佈。** 用整合分支 `codex/integration-hotfix-r6-20261101`，不要直接合併 `codex/study-r6-20261101`。
   - 整合分支已經包含熱修。`main` 含熱修之後再合併，就不會有衝突，也不會出現重複的修補。
   - 這一步要等 PA-01～04 的決定。R 堆疊用到了 PA-01（教授審閱範圍）和 PA-04（editor 不能刪除論文）。
3. 合併前，在正式資料副本上跑 `scripts/repro/data_readiness.py`（只讀），然後在乾淨環境重跑完整回歸和瀏覽器旅程。

## 5. 只有 Owner 能做的事（工程端不能代做）

| # | 動作 | 為什麼 | 工具或文件 |
|---|---|---|---|
| 1 | 核准合併熱修並部署 | 修補 H1／H2／H6／H7／H8／H9 | 熱修文件 |
| 2 | 部署前確認至少有一個 `system_role=admin` 帳號；需要讀全站日誌的機器 token，要在 `TOKEN_ACL_MAP` 寫明 `system_role: admin` | H1 之後全站日誌只給管理員 | — |
| 3 | 正式環境設 `SESSION_COOKIE_SECURE=1`，並走 HTTPS | 本專案沒有 ProxyFix：TLS 在反向代理終止時，`request.is_secure` 是 False | `deploy_preflight.py` 會 FAIL |
| 4 | 在授權的日誌和稿件副本上跑暴露調查，再決定是否輪替 token | 兩個日誌外洩從 2026-06-30 就存在；過去的暴露範圍未知 | `scripts/security/log_exposure_audit.py` |
| 5 | 依 runbook 重疊輪替 token，再設 `API_QUERY_TOKEN_ALLOWED=0` | 網址裡的 token 會進入存取日誌 | `RUNBOOK_QUERY_TOKEN_SHUTOFF.md`、`query_token_shutoff_check.py` |
| 6 | 決定是否在維護時段清理舊稿件裡的 token | 舊稿件檔可能還有 token 原文 | `RUNBOOK_MANUSCRIPT_CREDENTIAL_CLEANUP.md`（只在合成資料上執行過） |
| 7 | 核准或拒絕 PA-01～04 | 擋住 R 堆疊的發佈 | `docs/SAI.md` §8 |
| 8 | 決定舊段落出處要不要重新連結 | R6 以前的連結如果當時文字就出現多處，位置無法得知，現在會標「位置不明確」、不算覆蓋。不會自動改資料 | §6 |

## 6. 資料遷移與相容性

- **段落出處（R6）不需要資料遷移。**
  - 新連結會記錄位置（`anchor`）。
  - 舊連結在讀取時，由當時的 S.Ver 文字推導位置，不寫回。
  - 當時文字就出現多處的舊連結，會標「位置不明確」，不算覆蓋。研究者可以按「選這段」重新連結，或撤回舊連結。
- 結構化日誌出處：R 堆疊上線後，沒有出處欄位的舊日誌行，不會出現在專案層的除錯快照裡。全站日誌只給管理員。
- 資料庫：R 堆疊相對 `main` 有兩個 Alembic 遷移。
  - `0003_chapter_review_events` 和 `0004_project_invitations` 只新增資料表（`checkfirst=True`），不改既有資料表。
  - downgrade 會刻意拒絕執行，不刪歷史。所以回滾要靠部署前的資料庫備份。
  - 這兩個遷移只在合成 SQLite 上驗證過。合併前，要在正式資料庫副本上演練 upgrade，並準備備份還原的步驟。
  - R6 本身沒有新增資料庫遷移，也沒有改 `ManuSectionConfig` 的格式。
