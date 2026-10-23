# SAI 作者與教授瀏覽器驗證

2026-10-23，內建 Chromium 瀏覽器，背景分頁；`127.0.0.1:8893`，僅合成帳號 `@example.invalid`。站台由 `test/integration_e2e/sai_browser_fixture.py` 建立在新的暫存 SQLite／資料目錄；LLM 使用可觀測替身，無真實研究檔案或 provider 金鑰。

## 實際操作與結果

1. 作者登入，選 Introduction V0.1，開「主張與審閱」。帶入已確認閱讀筆記，填段落、目的、適用條件及禁止延伸。
2. 填入未解溫度矛盾後按確認，畫面明確拒絕，原輸入仍在。清除已解矛盾再確認，決策回讀包含 300 K、同一校正面及不得外推其他樣本。
3. 以選用決策送出 Drafter 指令，真實 Socket 收到生成、使用脈絡清單及完成訊息；未自動寫入正文。模型替身僅證明流程與契約，不能證明科學成稿品質。
4. 切換教授帳號，選作者、V1、指定段落主張，留下校正面及適用範圍意見。意見固定標 V1。
5. 切回作者，選被審閱版本 V1，看到同一意見；回覆連結 V2，貼上 V2 真實修訂段落。狀態變成 responded、版號 1。
6. 切回原教授，按「比對作者修訂」，實際看到 V1 → V2 的新增校正／限制文字；填決定理由再按「確認已處理」，狀態 confirmed、版號 2。原意見、作者回覆、教授決定均保留。
7. 改以限定共同作者登入，只列出 Introduction；可讀該章，沒有全文內容或 Results。直接瀏覽 Results 的 paragraph/context API 回 Forbidden，無私人哨兵文字。

來源變更、過期章節版本、假 actor、同時回覆、自行確認、撤銷 MentorLink、低預算及附件存取另以真實 API／Socket／Task8 dispatch 測試驗證，未冒稱全部都在瀏覽器中注入。

## 保存畫面（本機）

既有 `.gitignore` 排除 PNG；依儲存庫邊界未強制加入忽略檔。以下是本機合成畫面，GitHub 上提供重跑 fixture、操作紀錄及畫面 SHA-256，沒有上傳圖片二進位檔：

- `evidence/sai-professor-confirmed.png`：教授確認、原始主張／條件與回覆歷史。
- `evidence/sai-scoped-workspace.png`：限定共同作者的合法章節與空全文。
- `evidence/sai-scoped-denied.png`：直接存取其他章節仍被拒絕。

期間發現並修復教授送出意見後跳回最新版的問題，現在保留目前選取版本。瀏覽器主要流程後新增的來源變動結案檢查與 worker 錯誤終態由最後回歸測試驗證。最後檔案的 SHA-256 另記 evidence manifest；不以畫面取代安全或科學驗收。
