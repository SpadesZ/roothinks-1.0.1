# 本機／雲端 Python 3.12 整合方案

本機只交付，不 push。2026-10-25 唯讀核對 personal GitHub：雲端 `codex/py312-migration` 為 `e64c4e74a7a5bae6537297f31bbd0dfccdc4f6d9`；main 為 `9655546eac2f2e43a0cabceee0bd75843b47dae2`。當次 connector 查詢沒有 open PR。本輪沒有改遠端設定、分支、PR 或 LAVA-Cowork。

## 建議

**由一個整合 agent 負責，從雲端 migration 建第三條隔離分支。先整合驗證工具與證據，再選唯一候選環境重驗。** 不直接把本機依賴 lock 覆蓋雲端 lock，也不因檔案能合併就宣告環境驗收完成。

兩邊共用 `b7a7bdd8f3abaab566029e899b5e26e6d34cc6d5`。base、本機與雲端的 `app/` Git tree 都是 `be98782f6ad4bbf819baf62f5449c0ecf984bb88`。本機先前新增檔與雲端新增檔沒有路徑衝突；本輪補的 `docs/ACCEPTANCE_MATRIX.md` 是共同修改，應人工保留兩份紀錄。I14 不納入本輪整合。

## 真正差異

| 項目 | 雲端 e64c4e7 | 本機候選 | 整合含義 |
|---|---|---|---|
| 系統 | Python 3.12.15／Debian trixie；原 apt 來源 | Python 3.12.15／bookworm；HTTPS、簽章及固定 snapshot | 不能互換 OS 證據；決定後重建 |
| lock | 119 套件，盡量保留當日 3.10 解析 | 109 套件；單一 headless provider；省略未引用 layoutparser 與其相依 | 29 項新增／移除／版本差異；完整列表在本機證據 JSON |
| NumPy | 2.2.6 | 1.26.4 | 本機不是單純換直譯器，不能聲稱數值相同 |
| OpenCV | GUI 4.14.0.94 與 headless 5.0.0.93 共寫 cv2 | 僅 headless 4.11.0.86 | 建議單獨處理重複 provider；須驗所有 CV 路徑 |
| 其他例子 | SQLAlchemy 2.0.55、SciPy 1.15.3、NetworkX 3.4.2 | 2.1.6、1.17.1、3.7 | 不把本機全面解析當作雲端相容性證明 |
| CPU Torch | 2.6.0+cpu／0.21.0+cpu 未直接安裝、未 hash | cp312 CPU wheel 真安裝與 SHA-256，NMS 通過 | 移植 CPU wheel URL／hash；不移植 CUDA 替代證據 |
| NLLB | 合成小權重路徑；公開真權重不可達 | 固定公開 revision、原權重 hash、完整 bake、離線 adapter 通過 | 移植來源 commitment 與檢查；未測翻譯品質 |
| Docker | 生產式完整 build 被網路阻擋；另有替代環境 | 完整本機 runtime build 通過 | 補足另一條可建置方案；不代表雲端 Dockerfile 已建完 |
| EasyOCR | 真模型與合成英文控制；雙語錯字已揭露 | 同 SDK 真權重；本機 CPU 離線載入／推論，英文控制通過、雙語亦有錯字 | 品質未驗，兩邊都不能當繁中驗收 |

## 執行順序

1. Owner 或雲端 agent 取得本機 Git bundle，先 `git bundle verify`，確認 prerequisite `b7a7bdd`；只 fetch bundle 到本機 review ref。不要覆寫兩條既有分支，不 force-push。若 GitHub HEAD 已改變，先重算共同基準與差異。
2. 在第三條分支保留雲端工具，加入 `validation/python312/`、本機交付與證據。合併驗收矩陣時兩份測量各自標 SHA、image、OS，不能加總成一次回歸。
3. **建議先沿用雲端的保守版本，逐項加入本機的 CPU wheel hash、固定 NLLB 來源、乾淨 tracked context、測試 image 的 git 與完整命令紀錄。** 再獨立處理 cv2 衝突；layoutparser 是否移除，以所有呼叫點及可驗工作流程為依據。不要整包套用本機 29 項變動。
4. 選定 Debian base、apt snapshot 與唯一 lock，產生新 immutable image。先前兩邊證據只能作診斷；整合映像須重跑 pip check、CPU/NMS、OpenCV、兩種 worker HTTP／Socket／PDF／恢復、真 NLLB 離線載入、EasyOCR、完整 Python 回歸與三支原瀏覽器 assertions。
5. 只在固定合成資料做數值與科學引擎跨版本一致性比較。若 I14 另改引擎，分開驗收，不能引用本輪相同 app tree 當作新引擎證據。真專家、holdout、OCR／翻譯品質仍保持未驗狀態。
6. 記錄失敗、skips、image ID、model／lock hashes 與 exact argv。Owner 另行決定是否允許 staging。這份方案沒有授權 main merge、遠端寫入、正式資料操作或部署。

## 回退與停止條件

目前正式容器與舊 image 未變，沒有切換，所以本輪回退就是停止自己的 QA 容器。原 Python 3.10 image 的合成 fixture 可啟動；沒有對正式資料演練 restore。後續若有新授權才規劃 cutover：記住**實際舊 image ID**，不可用重新 build 的浮動 `python:3.10-slim` 代替。任何備份／restore 仍需一致快照與另案驗收。

整合後若出現原件／版號／ACL 回歸、NLLB 不能離線載入、CPU native ops 失敗、無法完整 build，或依賴版本沒有唯一來源，停止遷移。不得刪資料、換弱斷言或以人工確認代替技術驗證。

## 可交接 prompt

> 接手 RootThinks Python 3.12 本機／雲端整合。先讀 LOCAL_PY312_DELIVERY_2026-10-25.md、LOCAL_PY312_EVIDENCE_2026-10-25.json 與本方案。雲端基準 e64c4e7；本機工具凍結 1df5d8d，最終本機交付 SHA 見 Git bundle/ref。先驗 bundle、GitHub HEAD 與現場工作樹。只在第三條隔離分支做整合，由你一個 agent 擁有此分支；不要同時推既有 migration 分支。保留兩邊證據與失敗；兩邊 29 項依賴差異不可直接當等價。建議保留雲端保守版本，再加入本機 CPU wheel hashes、NLLB 固定來源、tracked-only context、git 測試 stage 和完整 argv runner；cv2 衝突另以最小可驗變更處理。對唯一最終映像跑完整 regression、原瀏覽器 assertions、有效 PDF／恢復、兩 worker、CPU Torch、真 NLLB 與 EasyOCR。不得改科學引擎、I14、LAVA-Cowork、main、正式資料或部署；未另獲 Owner 授權不要 push。科學建議維持 advisory，教授仍選用。交付 exact SHA／image／commands／失敗與未驗，停在 Owner Gate。
