# 安全穩定化第二輪：熱修候選＋R4 後續候選

日期：2026-10-31。**兩個獨立結果，都沒有合併、沒有部署，沒有讀正式日誌，沒有輪替任何憑證。**
規格與整體狀態以 [`docs/SAI.md`](../SAI.md) 為唯一入口。

| 結果 | 分支 | 基底 | 內容 |
|---|---|---|---|
| 安全熱修候選 | `codex/security-hotfix-log-isolation-20261027`（`b80b94e`） | `main`（`9655546`） | H1／H2（第一輪）＋H6 調查工具安全化＋H7 網址 token。詳見該分支的 `SECURITY_HOTFIX_LOG_ISOLATION_2026-10-27.md` §6 |
| R4 後續候選 | `codex/study-r4-followup-20261028` | R4 `22d8d22` | H6／H7 移植＋F1 限定編輯邊界 |

## 1. H6 調查工具本身會外洩

- **問題**：v1 的報告附「遮罩後樣本」，但遮罩只處理 email 與部分金鑰格式。
- **負向控制**：用 10 個隨機秘密做成對抗日誌。v1 洩漏 4 個：
  - 路徑段落；
  - 格式錯的 IP 欄位；
  - 提示詞；
  - 論文標題。
- **v2**：報告**完全不含日誌原文**，只有驗證過格式的欄位、內建端點常數和類別計數。
  - 另外統計「網址帶 token 的存取行數」和「已存稿件中帶 token 的網址數」，只給數量。
  - 同一組對抗日誌在 v2 洩漏 0 個。

## 2. H7 Manuscript 把 token 放進網址

### 範圍
- 圖片 `<img src>` 與 Word 匯入的網址帶 `?access_token=`。
- 圖片網址會被**存進稿件 HTML**，所以 token 會出現在：
  - 版本檔、匯出；
  - 協作者的畫面；
  - 存取日誌、代理紀錄。
- 只影響用 Bearer token 的部署（prod compose）。純 session 的 GCP 部署，瀏覽器沒有 token。

### 修補（相容）

| 項目 | 改動 |
|---|---|
| 前端 | 不再產生 token 網址 |
| session 成員 | 用 cookie 讀圖 |
| 只有 token 的用戶端 | 用標頭換限定專案路徑的 HttpOnly 圖片票證：12 小時過期、不含 token、token 移除即失效 |
| 已存稿件 | 存檔與讀取都去除 token；舊檔本身不改寫 |
| 存取日誌 | gunicorn 存取日誌遮罩 token |
| 網址 token 相容 | 預設仍接受；`API_QUERY_TOKEN_ALLOWED=0` 可關閉 |

### 更正
我曾懷疑 `main` 的圖片路由在 session 模式缺少成員檢查。紅測試證明 `main` 的全域模組守衛**本來就擋非成員**，所以這**不是漏洞**，不列為發現。

## 3. F1 限定編輯（coauthor）讀到 Study 私人筆記

- **問題**：被指派一章的 coauthor 進不了 Study，但可以經由 `/manuscript/api/provenance?section=<該章>` 讀到連結的 Study 證據筆記全文，包括研究者假說和 AI 解讀。
- **修補**：凡是進不了 Study 的人，只看得到「這段連到一則證據筆記」。
  - 段落文字、類型、需要重新核對的提示照常顯示；
  - coauthor 仍可在自己的章節寫作，也可標記自由寫作；
  - 匯出仍需要全篇讀取權。
- **GET 權限矩陣加入 coauthor 角色**（92 條路由 × 7 個角色，1,064 次請求；探測帶 `section=`）：
  - 修補前 1 個發現；
  - 修補後 0 個；
  - 其他路由上 coauthor 只讀得到自己那一章的稿件。

## 4. 驗證

| 項目 | 熱修分支 | R4 後續分支 |
|---|---|---|
| 新增／移植測試 | `test_log_exposure_audit.py`（4）、`test_url_credentials.py`（9，舊程式 7 個失敗） | 同左＋`test_paragraph_provenance.py` coauthor（修補前失敗） |
| 完整回歸 | 3.10 與 3.12：1,123 通過、1 失敗，失敗清單與未修補的 `main` 逐行相同 | 見 §6 |
| 真瀏覽器 | token 與 session 模式都通過（圖片從不帶 token 的網址載入，所有請求網址都不含 token）；舊程式在 token 模式失敗 | 同一測試在 R4 程式上兩種模式都通過；完整共研旅程 58／58 |

## 5. 仍需 Owner 決定

1. **token 輪替**：曾被寫進存取日誌或稿件 HTML 的 token 應視為已暴露。先用 `log_exposure_audit.py --manuscript-data <副本> --access <存取日誌副本>` 統計範圍，再決定輪替。工程端不能自行輪替。
2. **舊稿件清理**：是否改寫已存稿件，把舊 token 從檔案中移除。
3. **關閉網址 token 的時間點**：設定 `API_QUERY_TOKEN_ALLOWED=0`。
4. **合併與部署熱修**。部署前確認有 admin 帳號；部署後在授權的日誌副本上跑調查工具。
5. PA-01～04 仍是提議。F1 的遮蔽依據的是既有的「coauthor 只進 Manuscript」規則，**不是新政策**。

## 6. R4 後續分支回歸

（填入）
