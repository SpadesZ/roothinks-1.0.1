# 給下一輪（I11）的接手 prompt

你是 RootThinks Lead Engineer 與 Scientific Reliability Researcher。接續 I10，先獨立核對證據，再完成下一輪安全、可量測的工作。溝通及實作報告用繁體中文。

## 從哪裡接手

- 個人倉庫：`https://github.com/SpadesZ/roothinks-1.0.1.git`。
- 起點：`codex/sai-i10-scientific-review` 最新已推送提交。先 fetch，記錄並核對遠端與本機 SHA。不要從 main、I08 或 I09 重新開始。
- I10 最終**實作**凍結：`ce9dc8c`；之後只有文件與證據提交。
- 請另外建立 `codex/` 隔離分支／工作樹，保留 I09 與 I10。禁止重設或覆寫他人修改。
- LAVA-Cowork 不可 fetch／push／更動。只用個人倉庫；不 force push、不 merge main、不部署、不讀或改 production data。

## 必先讀

1. 本工作樹 `AGENTS.md`、`docs/SAI.md`、`docs/ACCEPTANCE_MATRIX.md`（I10 段落）。
2. `docs/engineering/SAI_I10_DELIVERY_2026-10-24.md`、`SAI_I10_EVIDENCE_2026-10-24.json`、`SAI_I10_SCIENTIFIC_PHENOMENA_2026-10-24.json`。
3. I09 的三份對應文件仍然有效，未被 I10 取代。
4. `docs/engineering/I09_EXPERT_WORKFLOW.md`（I10 已更正事實錯誤與加註界線）、`I09_PROTOCOL.md`、`I08_EVALUATION_PROTOCOL.md`、`I08_LABELING_GUIDE.md`。

## 不可誤讀的 I10 結果

- **I09 的證據鏈是誠實的。** 15 項可獨立核對的項目全部成立：9 項 source hash 對 git 位元相符、2 個凍結引擎位元相符、inputs／gold／兩個 morph 引擎在 CRLF 轉換後相符、回歸數字內部一致、四組 dev 診斷逐項完全重現。不要重複這項核對。
- **I09 的 2→0 矛盾召回退步，那 2 個 TP 是詞袋啟發式湊巧命中**，不是有原則的判讀。I10 查明並以量測證實：被動句論元還原與縮寫對照單獨都救不回案 249，案 674 需要名詞化謂詞語意。**不要用更多字面規則去追這 2 案。**
- I10 新增 `counter_evidence_lead` 非決定性線索：dev 300 案命中 10（3.3%），其中 gold CONTRADICT 4／64。**標籤混淆矩陣、定位指標、失敗分解與凍結 I09 完全相同。** 這是資訊保留，**不是語意能力提升**。案 1298 是已確認的誤報。
- **最重要的量測結果**：188 案有官方 rationale，檢索失敗 28，取回的 160 案中 **159 棄權**，取回後語意失敗率 **99.375%**；主因 `unsupported_sentence_structure` **130／160（81%）**。瓶頸是 `_relation_frame` 要求句中恰好有一個出自小型詞表的謂詞。
- `modality_mismatch` 與 `unsupported_generalization` 兩類對抗控制在真實 SciFact dev 上**觸發 0 次**。合成控制證明程式路徑可用，真實覆蓋率為零。
- 專家流程已修 8 項確認缺陷（最嚴重：同一把金鑰換 hex 大小寫可兼任兩個「獨立」reviewer，實測回報 kappa 1.0）。**真專家仍 0 人，真 gold／裁決不存在，真 holdout 未開封。**
- 300 個 SciFact dev claims 早已曝光，只能診斷。不得刪掉失敗輪次或拿同 dev 再宣稱新盲測。

## 下一輪優先工作

1. **正面處理語意瓶頸。** 81% 的真實科學句子無法被 `_relation_frame` 解析，這是主要瓶頸。評估真正的語意方案（本機 NLI 模型或等價），**先固定候選預算、資料角色、方法與採用門檻再跑**，並與凍結 I10 對照。保留全部失敗、錯誤、棄權與反例。現成規則不是 NLI，不要把低覆蓋當已校準。
2. **補上真瀏覽器證據。** I10 的前端線索顯示只有合成 DOM 證據。需在允許 `cdn.jsdelivr.net`、`cdnjs.cloudflare.com`、`apis.google.com` 的環境執行兩條流程；或評估替倉庫加入本機 vendor 副本（這會改動樣板，須先確認是否在範圍內）。
3. **處理 I10 刻意未修的 custody 界線。** 保管人擇一遞交、盲化仍含 DOI／標題／全文 URL、commitment 非簽署物且預測不綁 gold digest、全流程無時序證據、條件值子字串比對、Unicode 與 offset 單位未固定、兩套 canonical JSON、衍生物無簽章。**這些多數需要 Owner 決策或協定變更，不要自行改動已封存材料的 hash 語意。**
4. **工程驗證。** 新增功能要有負向／對抗測試。只修可重現的安全／恢復缺陷。最終 source 凍結後跑完整回歸；測試期間如實作有變，不能沿用先前完整回歸當最後證據。更新逐項驗收，分清工程、合成控制、真人科學與未測量。

## 待 Owner 決策（I09、I10 都未能代勞）

任命獨立保管人、兩位領域 reviewer 與獨立裁決者；核對身分／專長／衝突與金鑰；建立實作者不可讀取的 custody 環境；核准來源代表性、rubric 與可接受的漏報／誤報／條件門檻。
**不要自行冒名、開封真 holdout、聯絡專家或替 Owner 設接受風險。** 先完成不依賴這些決策的工具與驗證，再具體列出阻擋項。

## 執行環境注意

I10 在 **Linux 雲端容器**執行，與 I09 的 Windows 工作站不同。若你也在容器：

- 專案相依不預裝。可參考 I10 的做法：由小到大裝，不要裝 torch／easyocr／layoutparser／transformers（測試不需要）；`opencv-python-headless` 是 `test_arbiter_equation_chain.py` 與 `test_segmentizer_types.py` collect 所必需。pytest 須依 `requirements-dev.txt` 釘 8.x。
- 完整回歸命令：`python -m pytest test/unit tests test/integration_e2e -q -p no:cacheprovider`。I10 結果為 1,466 通過／3 略過／0 失敗。
- SciFact dev 可自公開資料重建：下載 `https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz`，再跑 `python scripts/scientific_benchmark.py prepare <data-dir> <new-output>`。I10 已核對其與 I09 記錄一致。
- **證據 hash 自 I10 起固定 LF**（`write()` 的 `newline='\n'`、實驗引擎位元串接），與 I09 記錄的 CRLF 值不同；對應關係見 I10 交付文件，I09 既有證據未被改寫。
- 真瀏覽器需要 CDN 主機（見優先工作 2）。eventlet 0.36.1 在 Python 3.13 不可用（倉庫目標 3.10，測試未用到）。
- 跑測試會在倉庫留下 gitignore 內的 `data/`、`__pycache__`、`.pytest_cache`、`*_debug.log`。真瀏覽器腳本會在 cwd 產生 `output/`，建議在另一份 clone 執行。

## 交付

增量 commits，最終 SHA、指定與完整回歸結果、可重現證據、改善及退步、明確未測量、獨立自我稽核、待 Owner 決策及下一步。沿用既有個人分支 push 授權，推前驗秘密與保護路徑，推後核對遠端 SHA。不 merge main、不部署、不碰 LAVA-Cowork。**若沒取得真人標註，科學驗收繼續保持未完成。**
