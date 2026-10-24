# I09 專家評估交付與保管

本輪沒有聘請、指派、聯絡或冒稱任何專家。I08 真保留集仍未解密、未推論、未評分。以下工具以合成資料完成程序驗證。**不等於科學效度驗收。**

## 信任及角色

Owner 在工具外核對真人身分、領域能力、利益衝突與公開金鑰；將核准計畫簽署後交給保管人。每人自己產生並保管私鑰，不能由實作者替真實專家持有。Owner、保管人、兩位 reviewer 與 adjudicator 的金鑰不得重複；四位執行者的 ID 不能重複。外部已核對的 Owner 公鑰必須透過 `--owner-public` 傳入，不接受待驗檔自我宣告信任根。

簽章只證明對應持鑰者簽署。程式不能判定真人資格、利益衝突、是否串通或真的沒看過模型答案。仍需獨立人員與外部權限隔離。

## 可執行工具

`python scripts/expert_evaluation.py --help` 顯示完整參數。輸出一律為新檔／新目錄，已有檔案直接失敗。所有 SHA 指此工具 canonical JSON 的 SHA-256（UTF-8、sort_keys、compact separators），不是排版後檔案的 bytes hash。原始檔 bytes hash 可另外由保管人記錄。

1. 各人執行 `keygen --output <private-key.json>`。不傳送私鑰；公鑰由 Owner 另行核對。Linux 建檔 mode 0600；Windows 存取控制及獨立保管需由 Owner 設定，不能把 chmod 當強制隔離。
2. Owner 從 [空白計畫](I09_EXPERT_PLAN_TEMPLATE.json) 填入真實角色、專長、公鑰、資料及 rubric hash、事先固定的接受門檻。rubric 使用 I08 指南與本文件的補充，不以模型結果決定標註。`sign-plan --input <config> --owner-public <public> --private-key <owner-key> --output <signed-plan>`。
3. 保管人核對既有 I08 manifest、密文與明文承諾後，才在獨立環境開封。I09 不替舊材料重新抽樣，不搬動或刪除舊密鑰。未來封存可用 `prepare_expert_holdout.py prepare <new-data-dir> --key-file <separate-custodian-dir/key.json>`；開封用 `open <data-dir> <new-export> --key-file <custodian-key>`。外部檔案路徑不等於獨立 custody，manifest 仍保持 `independent_custodian_assigned=false`。
4. `assign --input <master> --plan <signed-plan> --owner-public <public> --private-key <custodian-key> --output <new-assignments-dir>`。核對整份母版 hash。每位取得不同亂序的簽署任務，逐案只有可見來源、主張、provenance 與空白標註；不含機械變換、另一人答案、原始候選提示或模型預測。每案 hash 綁定完整可見資料。不得把保管人母版直接給 reviewer。
5. Reviewer 填 `{"assignment": <原簽署任務>, "filled": {"annotations": [...], "declarations": ...}}`，用 `submit --input <filled> --plan ... --owner-public ... --private-key <reviewer-key> --output <signed-submission>`。annotations 每案含 case_id、case_sha256、annotation。獨立性、沒看過預測、衝突已揭露三項聲明都必須為 true；聲明本身不證明事實。
6. 保管人 `disputes --assignment <r1-task> <r2-task> --submission <r1> <r2> --plan ... --owner-public ... --output <new-disputes>`。標籤以外的證據定位、條件、模態、因果或外推分歧也須裁決。未完成不得產生 gold。
7. 裁決者填 decisions 陣列：case_id、case_sha256、annotation、reason。`adjudicate` 使用相同 assignment／submission 參數，另給 `--input <decisions> --private-key <adjudicator-key> --output <signed-resolution>`。決定綁定兩份原標註 hash。重交標註需新輪次及新裁決。
8. 保管人 `freeze` 使用相同 review 參數，另給 `--adjudication <signed-resolution>`（無分歧時省略）、`--private-key <custodian-key> --output <new-gold>`。保留原任務、原兩份標註、理由、裁決與最後 gold。`verify-gold --gold <gold> --plan ... --owner-public ... --output <new-verification>` 再驗所有簽章並重建結果，連保管人另簽一份擅改 gold 也會拒絕。
9. Gold 固定後，由獨立保管人保留 commitment，再讓實作方提交一次預測及 engine hash，固定預測 commitment 後才開放評分。預測 JSON：plan_sha256、engine_sha256、predictions（case_id、case_sha256、label）。`score --gold <gold> --input <predictions> --gold-sha256 <externally-fixed> --prediction-sha256 <externally-fixed> --plan ... --owner-public ... --output <new-score>`。拒絕缺案、重複、改案、錯誤計畫及改動承諾。

`score` 只計 label confusion、TP／FP／FN、棄權、錯誤與雙人一致率；**沒有實作條件、模態、定位的專家成績或機率校準**。缺少那些測量不得宣稱品質達標。相同來源的多個候選不是獨立研究樣本；輸出另列來源數。單一支援標籤或高一致率也不代表科學真理。

## Annotation 契約

每案必填 I08 指南的 12 欄：label、evidence_sentence_locators、required_conditions、condition_fidelity、research_modality、causal_or_argument_reversal、unsupported_generalization、missing_citation、uncertainty、reason、confidence。

- label：SUPPORT／CONTRADICT／INSUFFICIENT／MIXED；支持、矛盾、混合必須有定位。沒有引用不等於為假。
- locator：source_id、start、end、quote，精確對應該案可見的 source.text 字元。只接受整數位置及逐字原句。全文資訊不能偷渡進 abstract-only 案例；如需擴大範圍，另建版本及計畫。
- required_conditions：每項含 name、value、locator、claim_status。條件值必須出現在該定位原句；claim_status 為 preserved／missing／changed／unknown。
- condition_fidelity：同上四值；總結 preserved 不得同時含缺失或改變的條件。空條件不證明沒有條件，由專家明確寫理由。
- research_modality：claim／source 各為 simulation／measurement／observational／mixed／unknown。
- 三個科學旗標為 true／false／null。reason、uncertainty 必填；confidence 為 low／medium／high，不當成真實機率。

案例／任務、原件、rubric 或模型有變動，一律建立新輪次，不能覆寫先前結果。

## Owner 尚待決定

1. 指定並核對保管人、兩名領域 reviewer、獨立裁決者與各自金鑰，完成實作者無權讀取的真正保管環境。
2. 核准來源代表性、rubric、誤報／漏報／定位／條件門檻與正式盲測程序，再開封保留集。

工具與材料均可先備妥；以上兩項不能由代理偽造或替 Owner 決定。
