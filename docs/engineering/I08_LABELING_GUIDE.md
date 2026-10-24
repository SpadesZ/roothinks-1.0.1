# I08 專家標註交付說明

本輪只有材料與程序，**沒有任何專家判斷或專家成績**。所有 reviewer、expertise、label、reason、confidence、gold 及 adjudication 均留空。未指派保管人、未聯絡或冒稱聘用專家，也沒有把模型輸出填成 gold。

## 本機材料

程式工作樹的 `output/scientific-i08-expert-labeling/`（不進 Git）包含：

- `pilot-labeling.json`：六篇文獻／30 個候選，供程序試標。保留作者、DOI、授權、原 XML、完整摘要及各主張來源身分。
- `reviewer-1-unassigned.json`、`reviewer-2-unassigned.json`：已產生兩份各 30 案的 pilot 空白副本，去除變換提示與另一位審查者欄位，次序分別打亂；尚未指派任何人。
- `holdout.aesgcm`：六篇文獻／26 個候選，來源層與 pilot 無交集。三個抽樣層各兩篇，所有候選是原句或明示的機械變換，尚未有人判斷支持／矛盾。
- `evaluator-key.json`：只供保管人使用，不上傳 Git、不放進推論輸入、不在對話或報告中顯示。密鑰與資料目前同機保存，是防誤讀封存，不是對同權限實作者的強制隔離。
- `public-manifest.json`：文件數、候選數、來源分割、hash、抽樣來源及未完成標註狀態。實作者沒有解密真正 holdout，也沒有在它上面推論。

所有文章由[公開來源池](I08_PUBLIC_SOURCE_POOL.json)指定，透過 Europe PMC fullTextXML 驗 2022–2025 年、原始研究及 CC BY／CC0 URL。搜尋 503 時用固定書目 seed；抽樣層不等於經專家確認的領域。公開書目與搜尋摘要可能在選材時被看到，不能聲稱全新隱藏文獻。文章原文及候選的實際分割封存在資料包；重現評分應用同一份凍結包，不以重新亂數抽樣取代它。

## 交給真正審查者前

1. Owner 指定一位保管人、兩位具相關領域能力的獨立審查者及分歧裁決者，記錄真實身分／專長；程式不能替人認證資格。
2. 先用 pilot 試標，修正 rubric 的歧義；所有調整另版留歷史。Holdout 不給模型、不作門檻調整。正式品質門檻須在開封預測前依使用情境固定。
3. 每位審查者拿獨立副本，不看另一人的標註、模型結果、`candidate_origin`、`original_quote` 或變換種類。這些只屬保管人的來源追蹤，不是暗示答案的欄位。候選次序分開打亂，不能把同來源的機械變換稱為獨立研究樣本。
4. 保管人核對來源／主張 manifest、rubric 及案例 hash 後，才在獨立目錄匯出 holdout：

```text
python scripts/prepare_expert_holdout.py open <evaluator-folder> <new-custodian-export>
```

匯出的 `holdout-labeling.json` 是保管人母版，**不可直接交給審查者**。與 pilot 一樣，先去除變換提示與另一審查者欄位。以下轉換僅複製待審資料，沒有產生或推定答案；先在 pilot 執行，正式 holdout 由保管人執行：

```python
import copy, json, random
from pathlib import Path
master = json.loads(Path("pilot-labeling.json").read_text(encoding="utf-8"))
for slot in (1, 2):
    cases = [{"case_id": c["case_id"], "claim": c["claim"],
              "evidence_sources": c["evidence_sources"],
              "annotation": copy.deepcopy(c["annotations"][slot-1])}
             for c in master["cases"]]
    random.Random(slot).shuffle(cases)
    packet = {"version": master["version"], "sources": master["sources"],
              "cases": cases, "expert_labels_complete": False}
    with Path(f"reviewer-{slot}-unassigned.json").open("x", encoding="utf-8") as out:
        json.dump(packet, out, ensure_ascii=False, indent=2)
```

5. 兩位分別交回原始標註後才對照；裁決者保留兩份原件、理由及裁決，不覆寫。缺標、分歧未裁決或資格未確認列為未完成，不補造支持標籤。固定 gold hash 後才能比較模型預測。

## Rubric

每案標 `SUPPORT`／`CONTRADICT`／`INSUFFICIENT`／`MIXED`，指「在提供的證據及適用條件下」的關係，不是全世界真理；可記錄不確定性與拒判理由。沒有引用不等於主張為假；原句摘錄也可能是背景、假說、引述或被否定敘述，不能自動視為 SUPPORT。

證據定位用 `source_id`、摘要字元範圍及逐字原句；可用保存的完整 JATS XML 補查段落／章節。`source.text` 只正規化空白，不刪尾端條件，字元範圍以該文字為準。完整論文與摘要推論範圍分開，不能把全文新發現偷放到先前只取得摘要的推論輸入。

分開記必要條件（樣本、族群、濃度、溫度、波長、頻率、時間、參考平面、統計定義等）、是否保留條件、simulation／measurement／observational／mixed／unknown、因果／主客體、外推、缺引用、理由及信心。數值不同可能是不同樣本或指標；沒有顯著差異也不等於相反效果。機械變換的名稱不是答案。

試標採共同欄位約定，任何未填值都維持 null，不自動當作「否」：

| 欄位 | 約定 |
|---|---|
| `label` | SUPPORT／CONTRADICT／INSUFFICIENT／MIXED |
| `evidence_sentence_locators` | 陣列：source_id、scope、start／end、逐字 quote；全文另記原 XML 章節／段落定位 |
| `required_conditions` | 陣列：條件名稱、來源原句／定位、主張是否保留；不能只填關鍵字而省略條件值 |
| `condition_fidelity` | preserved／missing／changed／unknown，並以逐條條件說明支持總結 |
| `research_modality` | 分別填 claim 與 source 的 simulation／measurement／observational／mixed／unknown |
| 主客體／因果、外推、缺引用 | true／false／null；有疑義填 null 並說明 |
| `confidence` | low／medium／high；信心不是科學真實性或專家資格的證明 |
| `reason`／`uncertainty` | 必填可追溯理由，以及限制／資訊缺口；避免只寫結論 |

以上先由 pilot 驗可理解性；若需改欄位或定義，先另版固定，再開正式 holdout。不要在看到模型的正誤後修改 gold 定義。

以每個主張為判讀單位，並依論文群組報告結果及不確定性；不能把同一篇的多個變換當作獨立論文。分開報誤報、漏報、棄權、定位及條件一致率。真標註存在前，不計算專家一致率、kappa 或品質分數，也不宣告專家／科學驗收通過。

研究者在產品中的寫作、保存、修訂及匯出不受這些標註或日後分數限制。
