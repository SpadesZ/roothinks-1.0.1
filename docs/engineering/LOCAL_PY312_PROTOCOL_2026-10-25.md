# 本機 Python 3.12 遷移驗證契約

使用者最新界線：**僅本機隔離環境，不 push、不 merge、不部署、不動上游**；雲端代理與 I14 科學研究獨立進行。GitHub 可唯讀核對，最後交付整合方案並停在 Owner Gate。

基準：personal `codex/sai-i13-gate-a-and-deps`，`b7a7bdd8f3abaab566029e899b5e26e6d34cc6d5`。本機分支 `codex/local-python312-validation`。原 D: 工作區與所有先前工作樹保留。科學引擎、原 production Dockerfile、Compose 與正式資料不得修改。

## 可驗成果

1. linux/amd64 Python 3.12 base 以 digest 固定，完整 runtime build 包含系統相依、CPU torch／torchvision、NLLB safetensors 及非 root 啟動；記錄所有失敗與 image IDs。
2. 由 I13 相依宣告生成本機候選 lock，固定完整版本與 wheel hashes。只保留單一 headless OpenCV provider；真實 import、TorchVision NMS、Eventlet Gunicorn worker、OpenCV 影像、NLLB 離線載入及翻譯分別測試。
3. 全新 runtime／test image 執行 pip check、Python 3.12 完整回歸、六個 git 範圍測試及兩個 CV 模組，核對 I13 八個環境錯誤。不得排除錯誤來宣告通過。
4. 真瀏覽器三研究流程、科學建議不阻擋寫作／保存／匯出、PDF 上傳與部分失敗恢復。模型測試用公開固定 revision，不接 provider 或研究秘密。
5. 保存原 source／engine hash、既有容器 image ID／狀態的唯讀 metadata、新 image／lock／模型 commitment、完整命令與 raw QA；回退僅涉及候選容器，不操作正式環境。
6. 完成後唯讀核對 GitHub 最新雲端 branch，給檔案重疊、共用基準、整合順序、重驗條件與 Owner 決策；任何合併／推送／啟用由 Owner 或雲端代理另行執行。

## 基線缺口及採用界線

I13 Python 3.10：1,454 passed／51 skipped／8 errors；2 個 cv2 collection errors（libxcb），6 個 git setup errors。I13 的替代 image 不是完整生產 Dockerfile。原始碼同時宣告 GUI OpenCV 與 easyocr 引入的 headless OpenCV，可能覆寫同一 cv2 namespace。

候選如出現數值／模型載入差異、完整 build 不通、環境錯誤未清、原件／歷史或授權回歸，建議不得遷移。只有環境相容性證據不等於科學引擎品質、翻譯品質或正式容量驗收。NLLB 公開權重有其授權與使用範圍，不由本輪重新授權。

所有測試在專用新 QA 目錄與無正式 volume 的候選容器內；不複製 ignored 私人資料、`.env`、papers、runtime DB、既有模型或 logs。資產導流沿用既有 browser 規範，不新增 `cdn.plot.ly` 存取。
