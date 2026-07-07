# 檔案路徑: roothinks/test/unit/test_fusion_merge.py
# 產生時間: 2026-07-05 04:30 +08:00
# 版本: v1.0
# 模組定位:
#   task_5interpret._simple_merge(v1.1)合併規則的單元測試。
# 主要責任:
#   1. heading 樣式短 Body(標題/章節名)不被併入正文。
#   2. 真正的正文段落(句號結尾)仍會合併。
#   3. is_page_noise 塊與非 Body type 不參與合併。
# 維護提醒:
#   - 用 CTC 論文首頁的真實結構(title/authors/Abstract標題/abstract正文)
#     縮小版作為案例。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_fusion_merge.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.llm_service.matching_tasks.task_5interpret import Interpreter


def _b(content, btype="Body", **extra):
    d = {"type": btype, "content": content, "bbox": [0, 0, 100, 100]}
    d.update(extra)
    return d


def test_title_authors_abstract_stay_separate():
    # CTC 首頁縮影:title(無句號)、Abstract 標題、abstract 正文(句號結尾)
    blocks = [
        _b("Connectionist Temporal Classification: Labelling Unsegmented Sequence Data"),
        _b("Abstract"),
        _b("Many real-world sequence learning tasks require the prediction of sequences. " * 3),
        _b("Another full paragraph of the introduction that ends with a period. " * 3),
    ]
    merged = Interpreter()._simple_merge(blocks)
    # title 與 "Abstract" 標題必須獨立;兩個正文段可以互併
    contents = [m["content"] for m in merged]
    assert any(c.startswith("Connectionist") and "Abstract" not in c for c in contents)
    assert any(c.strip() == "Abstract" for c in contents)
    assert len(merged) == 3  # title / Abstract 標題 / (正文1+正文2 合併)


def test_real_paragraphs_still_merge():
    blocks = [
        _b("First paragraph of body text that is definitely long enough and ends with a period. " * 2),
        _b("Second paragraph continues the discussion and also ends with a period. " * 2),
    ]
    merged = Interpreter()._simple_merge(blocks)
    assert len(merged) == 1


def test_page_noise_and_non_body_not_merged():
    blocks = [
        _b("Running Header Text That Repeats", is_page_noise=True),
        _b("A full body paragraph that ends properly with punctuation. " * 3),
        _b("x^2 + y^2", btype="Equation"),
        _b("Another body paragraph that ends with a period as well. " * 3),
    ]
    merged = Interpreter()._simple_merge(blocks)
    assert len(merged) == 4  # 全部保持獨立(中間隔著 Equation)
