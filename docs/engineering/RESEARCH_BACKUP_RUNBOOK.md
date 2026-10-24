# 研究資料備份與全新目錄還原

適用 SAI v1.1.1 的單機本地 SQLite 資料根目錄。請先在合成資料演練；本輪沒有對正式資料執行以下操作。

## 操作前

1. 核對 main DB、manuscript bind、原 PDF、研究檔、決策、版本、圖像及審閱附件的實際根目錄。不得把程式目錄或任意單一 DB 當成整份研究資料。
2. 全面停止使用該根目錄的 app／worker／外部寫入程序。新版本的 runtime lease 會互斥，但舊程序及不配合鎖的工具不在保證內。
3. 備份目錄及還原目錄都必須不存在，且與來源互不包含。保留原資料，不就地還原。
4. 備份包含私密研究資料與帳號紀錄；使用有存取限制的磁碟，不上傳 Git。環境設定、`.env` 及外部秘密不納入快照；若啟動需要原加密金鑰，另依既有秘密管理方式提供，不貼進報告或版控。

## 命令

在 repository 根目錄，以已安裝 `filelock` 的 Python 執行。以下路徑均為替換用例，並非正式資料位置。

```powershell
python app/research_backup.py backup C:\RootThinksTest\research C:\RootThinksBackups\snapshot-001
python app/research_backup.py verify C:\RootThinksBackups\snapshot-001
python app/research_backup.py restore C:\RootThinksBackups\snapshot-001 C:\RootThinksTest\restored-001
```

每一步都要確認 exit code 為零且 JSON `complete=true`。`verify` 驗檔案長度與 SHA-256、目錄清單、DB schema、逐表筆數／內容、SQLite integrity 及外鍵。WAL 中已提交的資料會被納入獨立 DB 快照，來源 WAL 模式不改。

## 故障及復原

活動程序、root 外 DB 配置、無權讀取的子目錄、symlink／junction、路徑逃逸、損壞 DB 或不一致 manifest 都必須明確失敗。不要忽略 stderr，也不要把只有部分檔案的目錄標作成功。

失敗後保留來源、備份與部分還原目錄作診斷。修正原因後，使用另一個全新目的地重試；工具不自動刪除或覆寫舊目錄。未完成還原的 pending marker 會阻止 app 啟動補建空 DB，不要自行刪 marker 來強迫啟動。

完整還原後，先在隔離環境配置 DB／資料路徑及必要的既有秘密，再啟動 app。抽查原 PDF、作者條件、兩版稿件、原教授意見／回覆、撤權狀態及附件。移除的教授不得因舊署名、MentorLink 或重啟而恢復存取。

## 保證界線

本工具沒有提供排程、遠端備援、簽章、加密、網路檔案系統、多主機或真實斷電保證。manifest 是完整性證據，不是來源真偽認證。對任意不配合鎖的外部 writer，必須由操作者確保完整停機。本輪已測程序硬退出，不等於驗了硬碟斷電及正式掛載。
