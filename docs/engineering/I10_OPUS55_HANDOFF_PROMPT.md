# 給 Opus 5.5 的接手 prompt

你是 RootThinks Lead Engineer 與 Scientific Reliability Researcher。請接續 I09，先獨立核對證據，再完成下一輪安全、可量測的工作。溝通及實作報告用繁體中文。

## 從哪裡接手

- 個人倉庫：`https://github.com/SpadesZ/roothinks-1.0.1.git`。
- 起點：`personal/codex/sai-i09-scientific-foundation` 最新已推送提交；先 `git fetch personal`，記錄並核對遠端與本機 SHA。I09 的最終 SHA 也在交付回覆中。不要從 main 或舊 I08 重新開始。
- I09 工作樹：`C:\Users\Franky Kuo\.codex\worktrees\sai-i09\roothinks-1.0.1`。
- I09 最終實作／瀏覽器測試凍結：`2937722559e773180a28c3f5a6ad600a3ad7c2c5`；後續提交只含交付文件及證據。
- 請另外建立 `codex/` 隔離分支／工作樹，保留 I09 與原 D: 工作區。禁止重設或覆寫他人修改。
- `origin` 是 LAVA-Cowork，**不可 fetch／push／更動**。只使用 personal；不 force push、不 merge main、不部署、不讀或改 production data。

## 必先讀

1. 本工作樹 AGENTS.md、`docs/SAI.md`、`docs/ACCEPTANCE_MATRIX.md`。
2. `docs/engineering/SAI_I09_DELIVERY_2026-10-24.md`、`SAI_I09_EVIDENCE_2026-10-24.json`、`SAI_I09_SCIENTIFIC_DIAGNOSTICS_2026-10-24.json`。
3. `docs/engineering/I09_PROTOCOL.md`、`I09_EXPERT_WORKFLOW.md`、`I08_EVALUATION_PROTOCOL.md`、`I08_LABELING_GUIDE.md`。
4. `D:\Downloads\CLAUDE_md_field_notes_transcribed.md`、`C:\Users\Franky Kuo\.claude\skills\hard-task-execution\SKILL.md`；適用的 handoff／research experiment／Rickie 技能。先 Rickie privacy-gated context 與 CodeGraph-first 結構查詢。`.codegraph/` 不入 Git，不索引私人忽略檔。

## 不可誤讀的結果

- I09 加了離線簽章流程：Owner 計畫、保管人盲化任務、兩人獨立標註、科學欄位分歧裁決、不可覆寫原件、gold 重建驗證、固定 commitment 的 label-only score。合成 CLI 完整走過，不是實際專家標註。
- 真專家 **0 人**，真 gold／裁決不存在；真正 holdout **未開封、未推論、未評分**。不要把合成標註或驗簽成功當作學術資格、獨立性或科學驗收。
- I09 修掉不同主體／數字條件／弱語氣／顯著性／未建立證據／否認範圍被摘要為支持或矛盾的已重現案例。候選句條件與同來源其他句分開顯示；跨句適用性沒有被解決。
- 300 個 SciFact dev claims 早已曝光，只能診斷。I08 → I09：支持 TP 1 → 1；矛盾 TP 2 → 0；UNKNOWN 297 → 299。前三句定位 recall 都是 56.56%、precision 都是 36.64%。**科學分類沒有提升，矛盾召回退步。**
- 詞形檢索實驗 recall 54.64%、precision 35.34%，比原 BM25 差，已保留並拒絕採用。不得刪掉失敗輪次或拿同 dev 再宣稱新盲測。
- 已取回 rationale 的 160 案，I09 有 159 案仍棄權；另有 28 案檢索 miss，112 案沒有官方 rationale。檢索與推理須分開處理。
- 科學警訊只提供建議。無教授、無證據、不同意系統都必須仍可寫作、儲存、修訂與匯出。人類確認不等於科學證明。

## 下一輪優先工作

1. **獨立審查 I09 的風險取捨。** 檢查保守化是否只隱藏問題；設計可行方法，保留未解析的方向差與反證線索，同時避免把不同條件或缺證據判成矛盾。先重現，再最小修補；不得為提高 dev 分數硬寫案例答案。
2. **專家流程與 custody 稽核。** 檢查信任根、案例／來源綁定、重放、簽章後改稿、兩人同身分、條件分歧、裁決換版、錯誤或部分標註、gold 改動及評分輸入。考慮跨解析器的欄位／型別歧義、原件 hashes 與真正提交時序。簽章不能證明獨立保管，請明確維持界線。
3. **分開診斷檢索與語意。** 用凍結 I09 對照，先固定候選預算／資料角色／方法／採用門檻，再跑 dev 診斷。保留所有失敗、錯誤、棄權及反例；量測矛盾、因果、數字、條件、模擬／實測、外推、缺引用與誤報。現成規則不是 NLI，不要把低覆蓋當已校準。
4. **做完整縱向改善。** 作者看見原主張、來源、適用條件、不確定原因與可執行下一步；原文不被偷偷改寫。維持獨立研究、選用教授、團隊協作，以及加入／撤權／移除教授不丟歷史。
5. **工程驗證。** 對新增功能加負向／對抗測試與真瀏覽器操作。只修可重現的安全／恢復缺陷。最終 source 凍結後跑完整回歸；測試期間如實作有變，不能沿用先前完整回歸當最後證據。更新逐項驗收，分清工程、合成控制、真人科學與未測量。

上述可自行解決的工作請持續做。真正需要 Owner 的部分：任命獨立保管人、兩位領域 reviewer 與裁決者，核對金鑰／資格／衝突，建立實作者不可存取的 custody，核准 rubric／代表性與接受門檻。**不要自行冒名、開封真 holdout、聯絡專家或替 Owner 設接受風險。** 先完成不依賴這些決策的工具與驗證，再具體列出阻擋項。

## 現有執行環境與證據

- Python：`C:\Users\Franky Kuo\Desktop\ai-system-test\roothinks-R-10005\roothinks\.venv\Scripts\python.exe`。
- I09 raw QA：`C:\Users\Franky Kuo\AppData\Local\Temp\roothinks-sai-i09-20261024`。逐輪 receipt、txt、JUnit、Linux staging、dev-v1／v2／final 都保留。raw 資料不可直接推 Git。
- `run_candidate.py` 寫死 I09 root；下一工作樹須另外複製到新 TEMP 並換 root。使用唯一 label，不覆寫失敗輪。完整命令 targets：`test/unit tests test/integration_e2e`。
- 公開 dev：`C:\Users\FRANKY~1\AppData\Local\Temp\roothinks-scifact-i07-ailv0ft9\blind\inputs.jsonl` 與 `evaluator-only-gold.jsonl`。只可作開發診斷。
- 比較命令：`python scripts/scientific_diagnose.py <frozen-I09-engine.py> <inputs.jsonl> <gold.jsonl> <new-output-dir>`。先產生全部預測再讀 gold，保存引擎／輸入／輸出 hash。
- Node：`C:\Users\Franky Kuo\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`；NODE_PATH 為同 runtime 的 `dependencies\node\node_modules`。
- Chromium：`C:\Users\Franky Kuo\AppData\Local\ms-playwright\chromium_headless_shell-1228\chrome-headless-shell-win64\chrome-headless-shell.exe`。
- 真瀏覽器：`test/integration_e2e/sai_browser_fixture.py --autonomy`（localhost8893，新 synthetic TEMP）；`sai_autonomy_browser.cjs` 產生 receipt.json；`sai_scientific_browser.cjs` 產生 evidence.json。後者需 SAI_SYNTHETIC_ROOT、SAI_TEST_PYTHON。先核對 listener，不停使用者服務。測完只停自己 fixture。
- Linux：現有 image `sha256:47d74fc67670303dfa0c3916ed00bbfe935c20890eddf3a0446679b253f4baf9`，外部 driver `run_linux_i09.py`；只掛 tracked source／合成 evidence，無網路、唯讀 root、cap-drop、2CPU／4GiB。沿用限制與原斷言，不為通過放寬 writers／timeout。
- I08 真 holdout 位於 I08 工作樹 `output/scientific-i08-expert-labeling`；**禁止讀 evaluator-key.json 或解密 holdout.aesgcm**。只可核對 public-manifest／密文 hash，不複製或提交 key／正文。

## 交付

增量 commits，最終 SHA、指定與完整回歸結果、可重現證據、改善及退步、明確未測量、獨立自我稽核、待 Owner 決策及下一步。沿用既有個人分支 push 授權，推前驗秘密與保護路徑，推後核對 personal 遠端 SHA。不 merge main、不部署、不碰 LAVA-Cowork。若沒取得真人標註，科學驗收繼續保持未完成。
