# SAI 權限、恢復與段落審閱延伸週期

起點：`d36cb9c18c944ac0d9d3cac0912702f29790c096`。
分支：`codex/sai-verification-review`，沿用附加的隔離工作樹，不改原工作區。

Auditor 判定是 Batch A/B 工程切片的 CONDITIONAL PASS；完整安全、併發及科學驗收仍開放。本紀錄只用當輪實際證據更新狀態，不沿用上輪通過數當成本輪執行結果。

## 發布狀態核對

本輪實際 `git ls-remote --heads personal`：

- `refs/heads/codex/sai-v1.1.0-batch-a-b` = `d36cb9c18c944ac0d9d3cac0912702f29790c096`。
- `refs/heads/main` = `9655546eac2f2e43a0cabceee0bd75843b47dae2`。
- 本機原工作區 = `60cf9f7`，乾淨；實作工作樹起點 = `d36cb9c`，乾淨。

上輪交付報告的「未推送」是文件提交當時的歷史狀態；之後使用者明確要求 Push，遠端現已可核對。不能把這句歷史文字當成現在尚未發布，也不把本輪新分支誤稱已推送。

## 可執行工作清單

- [x] 核對實際本機／遠端 SHA 與隔離分支；凍結上輪歷史證據。
- [x] V1：API／Socket／DOCX／實際 Task8 dispatch 的章節邊界與伺服器 actor；修復引用、版本清單、圖片與留言越權。完整全站安全驗收仍開放。
- [x] V2：每種儲存使用 6 個 OS 程序 × 3 輪同版競爭；另外強制 Windows 別名混用。後段完整回歸仍重現私人草稿雙成功，新增對抗測試後修同檔案多把鎖根因，保存相關 36 項通過；最終完整回歸另記。程序在 canonical 發布前死亡的恢復亦已測。
- [x] V3：PDF 半途寫入、DB 與 library 失敗、來源指紋變動、連結 CAS 失敗、恢復重試及新 PDF 不被舊恢復切回。
- [x] W1：重用 append-only citation ledger 保存完整段落目的、主張、條件、禁止延伸、版本／段落指紋及伺服器取回的證據快照。
- [x] W2：指定段落決策的生成路徑完整保留核心內容；來源變動／未解矛盾／低預算明確停止。最終 provider prompt 指紋與 audit 實測一致。一般未選決策的舊路徑仍有待統一的裁切限制。
- [x] W3：Mentor 意見連到同一 ChapterComment；追加事件保存作者回覆、修訂定位、原教授決定與 CAS 版號。原意見及事件不可改寫／刪除；來源變動不可沿用舊來源結案。
- [x] W4：真瀏覽器完成作者確認與生成、教授 V1 意見、作者 V2 回覆、教授差異比對與確認；限定共同作者正／負路徑。過期版本、來源變動及冒名確認另由 API／dispatch 對抗測試驗證。見瀏覽器紀錄。
- [x] F：完整回歸 1,254 通過、0 失敗、0 略過；完成本代理重新自查與實作／已測／未驗矩陣。已測程式 SHA `aa3fbaebd3fe9f6bdf8f9d1e1e55ea291acc5e23`；最後文件提交與發布 SHA 另由 Git HEAD／遠端及交付訊息核對。

## 實作邊界

不合併 main、不部署、不讀寫正式資料、不操作 LAVA-Cowork 上游、不新增產品依賴或通用框架。既有原件、來源 ID、已存版本與審查意見必須保留。研究者明確確認與科學支持程度分開；不以 AI 偵測率或模型自評作驗收。

Rickie 本輪未回傳可用記憶，privacy gate 擋下的內容未繞過。程式探索先查 CodeGraph，原始碼及測試為實際行為依據。

## 重現與修復

| 證據標籤 | 重現 | 修復後的可觀察行為 |
|---|---|---|
| channels-before | 13 紅項中，11 項為產品缺口；其餘為匿名圖片合法登入導向與測試漏填必填標題 | 引用／修訂／圖片按章節過濾；引用寫入驗 write ACL；actor 由 session 決定；不同位置 PID 不一致回 400；撤銷章節權限後不能改舊留言。ACL 回歸 203 項通過 |
| process-stress / process-repeat | Windows 在持鎖寫入時仍間歇回 WinError 5；一次重跑曾綠，不當作排除問題 | 原鎖內最多六次短重試，只處理 Windows 暫時拒絕。永久失敗不刪舊 canonical。跨程序／當機／永久拒絕及既有 CAS 回歸 17 項通過 |
| upload-before | 半份串流直接留為 source.pdf；補登記 API 不存在 | 完整 staging、flush/fsync、exclusive hard-link 才發布；保存 hash／pending manifest；補登記同 ID；恢復不重寫 PDF，不自動納入，不切回舊 PDF。9 項通過 |
| workflow-baseline | 11 項新流程案例未實作，回 404；另重現教授可提交不存在版本 | 版本／段落／證據連結、來源變更檢查、角色與確認、回覆定位及差異比對 |
| blocked-state-before | 低預算時模型未 dispatch，但 worker 仍回 job_done | 模型錯誤改發 job_error，不保存假成功聊天紀錄，不發 job_done |
| ledger-shape-before | 語法正確但形狀損壞的 ledger 被當成新空紀錄覆寫 | 讀／寫均明確報錯，原 bytes 保留；一般查詢不建立目錄 |
| workflow-expanded | E2E 隔離到 tmp 後，Study 從原碼 data 路徑讀到不一致資料 | Study 全部同源儲存路徑沿用 resolve_data_root；E2E 原斷言保留，測試不再使用 repo/data |
| full-regression | 第一輪 1,241 通過、4 項失敗：輕量 Study Flask fixture 無 DB URI | 補齊 fixture 的隔離 DB URI，原故障狀態／保存斷言保留；相關 16 項通過；最後完整結果另記 |
| final-full-regression / lock-alias-before | 後一輪 1,251 通過、1 項失敗：同一草稿版號有兩個 OS 程序回成功；強制 Windows 別名混用再現兩成功，同一檔案三種拼法得到三把鎖 | `aa3fbae` 在計算鎖指紋前重用前綴正規化與 normcase；別名程序、鎖身分與其他保存測試共 36 通過。沒有降低唯一成功斷言；post-alias-full-regression 為最後完整證據 |

`vertical-final-targeted` 因執行環境中斷，沒有完成 receipt，不列通過。重新執行 `vertical-resumed`：115 通過。此後新增的 ledger 形狀測試及最後完整回歸以最後證據檔為準。

`post-alias-full-regression` 在固定程式 `aa3fbae` 完成：**1,254 通過、0 失敗、0 略過**，pytest 摘要 838.53 秒、1,187 個棄用警告。JUnit 與 receipt 均確認沒有失敗／略過；保留警告原文及檔案指紋。JavaScript 為 7 個語法檢查與 6 支 Node 行為檢查，全數成功；這些 JS 在最後鎖修正前後沒有變更。證據 JSON 保存紅項、中斷與最後綠項，不以一次綠色結果抹除前面發現。

## 自我稽核與剩餘風險

- 本輪是同一實作代理重新沿授權、來源、保存、最終請求與 UI 路徑自查，沒有假稱另一位 auditor 已簽核。
- COL-01 證據涵蓋指定手稿／引用／圖片／匯出／模型通道及本輪恢復端點。全站所有模組、CSRF／瀏覽器威脅模型與正式部署配置尚未完成安全驗收。
- 程序壓測在 Windows 本機檔案系統；未驗多主機、網路檔案系統、主機斷電、所有保存階段當機或長期高負載。hard-link 不支援時上傳會失敗，不能假成功；正式掛載環境需再驗。
- Windows 鎖指紋現在正規化別名；未來更新同一資料目錄的 Windows 執行程序時，需要一起停妥舊程序再啟動，不能讓新舊鎖命名規則混跑。本輪沒有部署。
- 已執行 CodeGraph sync，但部分結構查詢仍返回舊版本片段；沒有把它當 runtime 證據。關鍵判定另以目前工作樹原碼、實際 OS 程序及 dispatch 重現核對。
- 新審閱表只新增資料，不重建原表。已驗實際 SQLAlchemy Table 建立在合成舊庫保存原留言；現有啟動 create_all 使用同一路徑。Alembic wrapper 未實際執行：本環境沒有 Alembic，未新增依賴或碰正式 DB。
- 舊 MentorReviewItem 若沒有版本／ChapterComment 連結，保留並標「未標版本／舊意見」，不自動推定歷史版本。舊意見自動移轉與跨版本重新錨定仍未實作。
- 段落前置決策可從已確認納入的文獻閱讀筆記帶入；其他 Study 決策類型、整份研究所有段落的自動覆蓋、COC-05 全域影響傳播仍待後續。
- COC-04 的嚴格「完整保留或阻擋」適用於明確選用已確認段落決策的路徑。一般舊生成、附件裁切與 upstream 固定字數的統一治理尚未全部完成；不宣稱所有 prompt 都已完整保留所有限制。
- 未量測真實 OCR／LLM、來源提示注入抵抗、科學主張支持程度、條件互斥的語意辨識、作者聲音或 AI 偵測率。人類確認是研究者意圖，教授結案是審閱狀態，兩者均不等於科學正確性驗收。
- 來源新舊以索引段落內容、定位、library 版號及來源登記指紋核對；不是每次生成都重新解析原始 PDF。正式原件備份還原與離線竄改偵測仍需驗。
- 本機瀏覽器已觀察教授欄位可操作，但窄欄閱讀長意見仍會捲動；尚未做正式教授／研究員可用性、螢幕閱讀器或完整 200% 放大驗收。

下一工程階段建議：先做 COC-05 的來源／主張／版本影響清單與明確重新確認，再做 REL-05 備份還原及 Linux 正式掛載上的程序／當機驗證；科學驗收用固定人工答案與條件保留樣本獨立評估。
