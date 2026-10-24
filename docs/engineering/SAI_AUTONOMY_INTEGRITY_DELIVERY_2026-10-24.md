# RootThinks 自主研究、研究完整性與恢復交付

日期：2026-10-24。起點 `6a965e851e03a9d7acefc3d48851d639c8595266`；隔離分支 `codex/sai-autonomy-integrity`。程式／測試凍結 `6c8e07174093c54ef5e9c2a45041c69173bb3ac3`。最後文件提交及遠端狀態由 Git 分支 tip 核對，避免在文件中自我引用未產生的 commit。

本輪交付是有界的工程實作與量測。全面安全、真實研究品質及正式環境驗收仍開放；沒有外部 auditor 對這輪作新的簽核。SAI v1.1.0 保持凍結，以 [v1.1.1](../spec/roothinks_SAI_v1.1.1_2026-10-24.md) 修訂自主、協作與恢復契約。[驗收矩陣](../ACCEPTANCE_MATRIX.md)逐項區分實作、已測及未驗。

## 交付內容

| 範圍 | 已交付及證據 | 保留界線 |
|---|---|---|
| P0-AUT | 無教授／PI 的正式研究、一般研究者註冊、自行申報學術背景、owner 雙方邀請、教授唯讀審閱、團隊章節權限、撤銷與自行退出。初始只 seed 一位 owner，三條真瀏覽器流程及 API 負向測試 | 職銜不等於機構認證；系統不寄信，owner 核對身分後另交一次性碼 |
| COL-01／02 | 署名不授權、啟動不補權；教授不能讀其他專案及全帳號活動；限定作者不能猜文獻 ID；最後 owner 保護；原意見／回覆／附件在撤權及還原後保留 | 全站滲透、所有舊 API CSRF、背景任務跨撤權時刻及多主機的全面模型仍未驗 |
| COC-05 | 來源／原 PDF／證據／主張／章節／稿件／原審閱影響圖；連續變更快照；作者接續、稿件重核及原教授重核分開追加；過時／矛盾／漏依賴／錯版拒絕，表單輸入保留 | 舊稿只推定依賴；沒有完整追蹤的章節持續明示。無全模組科學 lineage 保證 |
| REL-05 | 單機受控停機快照，原件及 JSON、SQLite／已提交 WAL、檔案與逐表指紋；全新目錄還原、pending 啟動阻擋、程序死亡解鎖、不可讀目錄明確失敗 | 非 live HA；不支援多主機／網路掛載／外部 DB／不配合鎖的 writer；未驗硬碟斷電。沒有內建異地保存、簽章及加密 |
| SCI-BASE | 兩份合成來源、六固定案例，真實確認 API→Socket→Task8 dispatch；人工答案不進 prompt；固定評分器及三種對抗控制 | provider 是受控替身，固定字串目錄不是語意驗證器，真文獻與真模型品質仍未量測 |

備份實際命令、故障處理及秘密邊界見 [runbook](RESEARCH_BACKUP_RUNBOOK.md)。本輪不把教授審閱或重新確認變成一般寫作、保存、修訂、匯出的必要條件。

## 提交

| Commit | 內容 |
|---|---|
| `a2065a6` | 自主研究、明確可撤銷邀請、署名與權限分離、教授隱私與歷史 |
| `e96a25e` | COC-05 影響圖及作者／稿件／原教授追加重核 |
| `c0b9bcb` | 限定作者可使用重新交付的當前證據快照 |
| `97dfcd6` | 一致停機備份與全新目錄還原、故障／程序／schema 測試 |
| `fb73d7a` | 合成科學基準、人工答案及評分敏感度 |
| `6a03ac8` | 限定作者初次定位可見章節，不固定開 Abstract |
| `767d594` | 重核快照包含當前來源／章節指紋，連續變更不能沿用舊畫面 |
| `811952a` | 重新載入證據時先同步變更報告，保留輸入及重置勾選 |
| `5414ce3` | 三條真瀏覽器流程、教授加入／移除與重核對抗情境 |
| `6c8e071` | 無法讀研究子目錄時拒絕不完整備份 |

沒有 main merge、force push、部署、正式資料操作或 LAVA-Cowork 上游變更。遠端查核與推送只使用 `personal`（SpadesZ/roothinks-1.0.1）。原 D: 工作樹仍為 `60cf9f7` 且乾淨。上輪 `d36cb9c` 與 `6a965e8` 的遠端分支已實際核對，不能以先前的「已 push」文字代替遠端觀測。

## 重現、修復及失敗紀錄

- `autonomy-before` 3 失敗：無 PI 被拒、署名升權、撤權後從署名補權。修共用授權根因，保留既有明確 membership。
- `mentor-privacy-before` 1 失敗：研究者其他專案／筆記及登入活動可見。`professor-history-before` 1 失敗：移除教授後作者看不到原審閱。改為共享專案範圍與歷史可見、當下存取分離。
- `scoped-evidence-before` 2 失敗：限定共同作者能列舉未交付來源及猜 ID。改沿用本章明確交付的證據。`scoped-reconfirmation-before` 又重現舊指紋擋住後續新授權，改檢查任一當前有效快照；未交付來源仍拒絕。
- `invitation-last-owner-before` 1 失敗：撤銷已升為最後 owner 的受邀者，可使專案沒有 owner。共用撤權處補保護。
- `backup-wal-before` 1 失敗：WAL 快照出現未登記 sidecar。目標快照改用獨立 DELETE journal，來源仍 WAL。`backup-unreadable-before` 1 失敗：`os.walk` 靜默略過無法讀取子目錄，仍稱完整備份；改為傳出錯誤，且驗證來源不變、無完整 manifest。
- 瀏覽器重現團隊初次進站停在未獲授權 Abstract；對應 Node 紅測試確認後，改選可見章節。瀏覽器亦重現來源已變一次後再變，舊快照仍可送出；`repeated-source-change-before` 紅測試確認後補當前指紋。
- 快速「重新檢查」後「重載證據」曾保留舊快照而被後端 409 安全拒絕；改為重載時自行等待新報告，輸入不丟失。重核版本選單也一併更新，保留仍存在的原選擇。
- 中間 `repeated-source-change-fixed` 8 失敗來自新增匯入的區域變數遮蔽，診斷後修正，後續針對性 32 項通過。不是刪除失敗斷言。
- 舊 PI 自動升權、單方 MentorLink 及教授全帳號活動測試，依使用者 P0 修正改成負向契約；內部活動計算及原有跨角色拒絕檢查保留。原全模組 E2E assertion 沒有弱化。
- `backup-capability-before` 是功能尚未存在的 collection error；Linux 第一次是測試依賴 importer cache 的環境錯誤；Alembic smoke 第一次缺套件。這些不記為產品缺陷。Alembic／Mako 僅補到本輪 TEMP，未改產品 dependency 或共用環境。
- `autonomy-integrity-full-final` 在備份稽核發現上述新缺陷後由代理中止；非通過，也不把被停止的 exit code 當成 assertion 缺陷。修正後以新 label 重新執行完整回歸，原紀錄保留。

## 測試證據

最終完整回歸：**1,321 通過、0 失敗、1 略過，839.51 秒，1,213 個棄用警告**。JUnit 共 1,322 項、0 errors；略過是 Windows 無建立 symlink 權限，同案例在 Linux 有執行通過。結果由本輪機器證據固定；執行命令為 `python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider --tb=short --show-capture=no --junitxml <new-path>`。隔離啟動器停用 dotenv、清除主要 provider／auth／DB 環境值並使用合成資料。完整回歸只證明已涵蓋案例，不替代下述科學及正式環境驗收。

- Windows 備份最後針對性：18 通過、1 略過；略過為該 Windows 帳號無建立符號連結權限。Linux 同一 symlink 案例有執行。
- Linux 最後一輪：25 通過、2 略過；略過為 Windows 專用路徑別名案例。Python 3.10.20、SQLite 3.46.1、WSL2 Linux 6.6.87.2，容器無網路、根檔案系統唯讀，僅掛載程式副本與合成證據。包含多程序 CAS、程序硬退出、備份及教授撤權後還原。
- JavaScript：全部 5 支本輪變更 JS 語法檢查；7 支既有／新增 Node 行為腳本（含 PI、稿件草稿／輸入邊界、章節定位、文獻決策、教授競態及 Study 筆記）。
- 三流程瀏覽器 run：`autonomy-2026-10-23T19-59-00-026Z`，4 組垂直斷言、0 pageerror。初始只有研究者；教授後來透過真註冊與邀請加入。圖、DOM snapshot、真 DOCX 及 receipt 在本機 `output/playwright/<run>/`，媒體不強制加入 Git。
- 瀏覽器以 `811952a` 的產品程式及後來提交為 `5414ce3` 的同一情境腳本執行；後續只修備份目錄掃描，不影響該介面證據。來源理由變更以瀏覽器同 session 的真 API 補充注入，其餘邀請、寫作、回覆、重核與匯出均操作真介面。
- 兩份下載 DOCX 通過 ZIP CRC、必要 OOXML package 與正文檢查；未在 Microsoft Word 應用程式中作視覺驗收。限定作者全文匯出後端為 403；畫面仍可見 2C／匯出按鈕，是後續可用性待辦。
- 合成舊 schema 從 Alembic `0002_create_evidence_segments` 經真 wrapper 升至 `0004_project_invitations`，再 upgrade 一次；保留原人類決策、integrity_check 為 ok。沒有對正式 DB 執行 stamp 或 migration。

原始 receipt、XML 與雜湊清單見[機器證據](SAI_AUTONOMY_INTEGRITY_EVIDENCE_2026-10-24.json)。中間 dirty tree 的 receipt 不當成只靠該 SHA 就能重建的完整版本。

## 科學基準的實際結果

凍結程式 `6c8e071`；6 請求，5 接受、1 缺證據拒絕。接受者來源定位 5/5；作者輸入條件／禁止延伸保存並進最終 prompt 5/5。來源必要條件詞覆蓋 18/19，與「有沒有裁切作者輸入」分開。

**2/2 未受支持的控制敘述仍被保留輸出，1/1 缺條件控制敘述也仍輸出。** 系統沒有冒稱獨立科學驗證。三種評分器突變（改定位、刪 prompt 條件、未知敘述）被量測辨識。這證明可追溯管線與測量工具的有限能力；沒有前後對照、真 provider 或真文獻盲測，不能宣稱科學品質、作者聲音或 AI 偵測率改善。

完整合成觀測、人工答案與資料雜湊另存 [科學基準證據](SAI_SCIENTIFIC_BASELINE_2026-10-24.json)，協定見 [protocol](SCIENTIFIC_BENCHMARK_PROTOCOL.md)。

## 分開進行的自我稽核與剩餘風險

源碼／資料流稽核與執行結果分開檢查：重用 WorkspaceMember／ChapterAssignment、MentorLink、RevisionLog、版本 manifest、FileLock 及標準 SQLite backup；沒有另建權限或審查框架。查到的連續變更、章節預設與不可讀目錄問題均留下先失敗再修復證據。未以計數宣稱全需求通過。

仍開放的界線：

1. 全站外部滲透、所有舊 API CSRF、正式反向代理／origin 配置、跨撤權時刻的背景任務及多主機交易語意。
2. 檔案／JSON／DB 之間沒有跨儲存體全域交易。快照與生成前重新驗指紋，可拒絕可觀測的過時脈絡；不宣稱阻止任意外部 writer 在檢查與使用之間改資料。來源改變會再失效。
3. 舊稿與舊意見缺依賴時只顯示推定／未追蹤；未提供自動科學回填。變更圖含原件雜湊與全量掃描，大型研究庫延遲未量測。
4. 備份須完整停機；外部 DB 配置拒絕，不自動搬資料。舊程序、網路磁碟、多主機、斷電、加密／簽章／異地保存及容量／健康門檻未驗。`.env` 與啟動所需秘密不包含於研究快照。
5. 自行申報背景及未驗證 Email 不是機構身分證明。安全授權依 owner 核對、對應登入帳號及另行交付的高熵一次性碼；邀請不代表自動升權或機構認證。
6. 全模組研究者／教授可用性、行動版、鍵盤與輔具、真 OCR／LLM、正式文獻科學品質及作者性仍開放。不得把合成 happy path 當成真實研究驗收。

## 建議下一階段

先以小型獲授權文獻集及盲測答案，量測「生成新增主張／適用條件漂移／缺證據」。以作者可見的對照與明確確認呈現警訊，不自動改稿或新增教授門檻。同時補全站 API／Socket／背景任務的撤權與 CSRF 稽核。正式部署前另外安排一致備份、既有 schema 複本、秘密配置與還原演練；這些需要獨立的正式環境授權。
