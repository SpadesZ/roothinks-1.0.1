# 給下一輪（I14）的接手 prompt

你是 RootThinks Lead Engineer 與 Scientific Reliability Researcher。接續 I13。溝通及實作報告用繁體中文。

## 從哪裡接手

- 個人倉庫 `https://github.com/SpadesZ/roothinks-1.0.1.git`，起點為 `codex/sai-i13-gate-a-and-deps` 最新已推送提交。先 fetch 並核對 SHA。
- I13 最終**實作**凍結 `5b02f3c`；之後只有文件與證據。產品引擎 `advisory-rules-4` hash `4c2034bf…` 未變。
- 另建 `codex/` 隔離分支。LAVA-Cowork 不可碰；不 force push、不 merge main、不部署、不改正式資料。

## 必先讀

1. `AGENTS.md`、`docs/SAI.md`、`docs/ACCEPTANCE_MATRIX.md`（I13 段落）。
2. `docs/engineering/SAI_I13_DELIVERY_2026-10-25.md`、`SAI_I13_EVIDENCE_2026-10-25.json`、
   `SAI_I13_GATE_A_RESULT_2026-10-25.json`、`I13_GATE_A_PROTOCOL.md`。
3. 舊的負結果：`SAI_I11_SEMANTIC_NEGATIVE_RESULT_2026-10-24.json`、`I11_PROTOCOL.md`。

## 不可誤讀的 I13 結果

- **Gate A 未通過，是負結果，不要重跑或調參救它。** C1（VerbNet）與 C2（+PropBank）結果完全一樣：
  決定性 2／300（基線 1／300），新增的唯一真陽性 id 589 來自假 frame（形容詞 *aged* 被當動詞）。
  G6（TP 2 < 6）與 G7（決定性 frame 2/2 錯誤）都未過。
- **規則式語意路徑已兩次負結果（I11、I13），根因相同：沒有句法剖析。** 詞性 tagger 的錯誤
  （形容詞性分詞、名詞／動詞誤標、縮減關係子句、無 be 被動）是主要失敗來源。
- **PropBank 零效益**，不需要為它做法務。
- **Gate C 已完成**：`6b10247` 補宣告 httpx／sqlalchemy／werkzeug／python-socketio，鎖定零差異，
  Python 3.10 全新安裝與 `pip check` 通過。生產 Dockerfile 在本環境卡在 apt（代理對明文 HTTP 回 405）。
- **既有相依風險（未修）**：opencv-python 4.14 與 easyocr 帶入的 opencv-python-headless 5.0
  寫入同一個 `cv2/`；`python:3.10-slim` 是浮動標籤（現為 Debian 13）；torch 的生產 pin 在 Dockerfile。
- **I13 修了一個重大缺陷**：`dd456aa` 用了 Python 3.11 才有的 `hashlib.file_digest`，
  生產（Python 3.10）下每次上傳 PDF 都回 503。修正 `5b02f3c`。I08～I12 等舊 SAI 分支仍帶著這個缺陷（沒改舊分支）。
  **主機回歸跑 3.13 抓不到這類問題，每輪都要再跑一次 Python 3.10 回歸。**
- **科學能力四輪未變**：SUPPORT TP 1、CONTRADICT TP 0、UNKNOWN 299。真專家 0 人，holdout 未開封。

## 下一輪優先工作

1. **先等 Owner 對 Gate D 的決定**（見 I13 交付文件第八節）：
   - 選項 1：核准一次窄範圍、釘版本、驗 hash 的句法剖析模型取得，只用於隔離實驗，
     **重新預先登記**協議後再量測。
   - 選項 2：停止語意擴充，維持保守引擎，轉做專家驗證準備與工程項目。
   **沒有 Owner 核准，不要擴大網路權限，不要整合任何語意引擎。**
2. 若選項 1：照 I13 的做法——協議先提交、凍結基線、附加覆寫產生實驗引擎、G1 用 pytest 外掛替換
   產品模組、盲稽核封包不含 gold、稽核規則與改判全部揭露。
3. 可自主做的小項：第四節的相依風險只要記錄或提案，**動 requirements 或 Dockerfile 前先問 Owner**。
4. 最終 source 凍結後跑完整回歸，更新逐項驗收、交付文件與下一輪接手 prompt。

## 執行環境注意

- 完整回歸：`python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider`。
  I13 主機（Python 3.13、無 NLTK）：1,473 通過／50 略過／0 失敗。
  I13 Python 3.10.22：1,454 通過／51 略過／0 失敗／8 錯誤（全是環境：缺 libxcb、沒有 git）。
- Gate A 腳本：`scripts/clause_frame_experiment.py`、`scripts/clause_frame_evaluate.py`（`run`／`finalize` 兩階段）。
  需要 NLTK 的 `averaged_perceptron_tagger_eng`（JSON 版）、wordnet、verbnet，C2 另需 propbank。
- NLTK 的下載器會拒絕走代理。**不要設 `NLTK_ALLOW_PROXIED_URLOPEN`**；用 curl 取檔並核對
  index.xml 的 md5，再 `chmod -R go-w` 資料目錄。**不要用 pickle 版 tagger。**
- Docker：本容器沒有常駐 daemon。I13 以 `dockerd --host=unix:///tmp/i13-docker.sock --data-root=<暫存區>`
  自行啟動，HTTPS 走代理；apt（deb.debian.org 明文 HTTP）、PyTorch CPU index、HuggingFace 不可達。
  背景程序有 2 小時上限；到期後不要重啟，可改用 containerd 快照層 overlay 掛載 + `unshare -n` chroot 跑測試。
  代理 CA 只能放在暫存區的 build context，**絕不入庫**。
- SciFact dev 自公開資料重建：`https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz`
  再跑 `python scripts/scientific_benchmark.py prepare <data-dir> <new-output>`。
- 證據 hash 自 I10 起固定 LF。eventlet 0.36.1 在 Python 3.13 不可用（倉庫目標 3.10）。

## 交付

增量 commits，最終 SHA、完整回歸結果、可重現證據、改善及退步、明確未測量、
獨立自我稽核、待 Owner 決策及下一個決策閘門。推前驗秘密與保護路徑，推後核對遠端 SHA。
**若沒取得真人標註，科學驗收繼續保持未完成。**
