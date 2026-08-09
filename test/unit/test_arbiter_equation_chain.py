# Roothinks source maintenance contract
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 檔案路徑: roothinks/test/unit/test_arbiter_equation_chain.py
# 產生時間: 2026-07-05 01:50 +08:00
# 版本: v1.0
# 模組定位:
#   arbiter IoU 縫隙修復、latex_ocr 重試鏈、task_4cv 公式修正策略的單元測試。
# 主要責任: 重現並驗收 arbiter equation chain 的成功、失敗與回歸邊界。
#   1. IoU 縫隙 B block:內容不同 → rescue 撿回;內容重複 → 不重複補入。
#   2. latex_ocr:重試開啟時首輪失敗會做放大重試;關閉時不重試。
#   3. task_4cv:Equation 不進純文字修正;低信心公式標 needs_equation_review。
# 維護提醒:
#   - arbiter 測試用 tmp_path 構造 stack_a/b JSON,不碰真實 data/。
#   - latex_ocr 測試 monkeypatch _extract_with_llm_task,不觸網。
# 驗證方式:
#   - .venv/Scripts/python -m pytest test/unit/test_arbiter_equation_chain.py -q
# ------------------------------------------------------------------------------
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.literature.literature_arbiterlogic import Arbiter
from app.core_pro.literature.literature_latex_ocr import LatexOCREngine
from app.llm_service.matching_tasks.task_4cv import SemanticCorrector


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _block(seq_id, bbox, content, btype="Body", score=0.8):
    return {
        "seq_id": seq_id,
        "bbox": bbox,
        "type": btype,
        "content": content,
        "score": score,
        "page": 1,
        "reading_order": 1,
    }


def test_iou_gap_block_with_distinct_content_is_rescued(tmp_path):
    # A 骨架 block(無 seq 對應),B 有一個 IoU~0.15 的縫隙 block,內容完全不同。
    a = [_block("", [0, 0, 100, 100], "alpha beta gamma delta")]
    # bbox 讓 IoU 落在 0.1~0.2:B [0,80,100,180] 與 A 交集 20x100,聯集 180x100 → IoU≈0.125
    b = [_block("", [0, 80, 100, 180], "totally different equation fragment")]
    pa, pb, po = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "o.json"
    _write(pa, a)
    _write(pb, b)

    Arbiter().arbitrate(str(pa), str(pb), str(po))
    out = json.loads(po.read_text(encoding="utf-8"))

    contents = [blk["content"] for blk in out]
    assert "totally different equation fragment" in contents, "縫隙 B block 應被 rescue 補回"
    sources = {blk.get("source") for blk in out}
    assert "stack_b_rescue" in sources


def test_iou_gap_block_with_duplicate_content_not_duplicated(tmp_path):
    a = [_block("", [0, 0, 100, 100], "alpha beta gamma delta epsilon")]
    b = [_block("", [0, 80, 100, 180], "alpha beta gamma delta")]  # 高 Jaccard 子集
    pa, pb, po = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "o.json"
    _write(pa, a)
    _write(pb, b)

    Arbiter().arbitrate(str(pa), str(pb), str(po))
    out = json.loads(po.read_text(encoding="utf-8"))

    dup = [blk for blk in out if blk["content"] == "alpha beta gamma delta"]
    assert not dup, "內容高度重疊的 B block 不應重複補入"


def test_latex_retry_upscale_invoked_on_failure(monkeypatch):
    eng = LatexOCREngine()
    eng.enabled = True
    eng.engine_mode = "llm"
    eng.retry_enabled = True
    eng.unimernet_task_id = ""
    eng.unimernet_cmd = ""

    calls = {"n": 0}

    def fake_llm(*, image_path, seq_id, page_num, bbox, page_w, page_h):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "reason": "llm_failed:boom"}
        return {"ok": True, "payload": {"latex": "x^2", "latex_confidence": 0.9}}

    monkeypatch.setattr(eng, "_extract_with_llm_task", fake_llm)
    img = np.full((40, 120, 3), 255, dtype=np.uint8)
    out = eng.extract_latex(region_image=img, seq_id="p1_body_9", page_num=1)

    assert calls["n"] == 2, "首輪失敗後應做一次放大重試"
    assert out.get("latex") == "x^2"
    assert out.get("equation_retry") == "upscale"


def test_latex_retry_disabled_single_attempt(monkeypatch):
    eng = LatexOCREngine()
    eng.enabled = True
    eng.engine_mode = "llm"
    eng.retry_enabled = False
    eng.unimernet_task_id = ""
    eng.unimernet_cmd = ""

    calls = {"n": 0}

    def fake_llm(**kwargs):
        calls["n"] += 1
        return {"ok": False, "reason": "llm_failed:boom"}

    monkeypatch.setattr(eng, "_extract_with_llm_task", fake_llm)
    img = np.full((40, 120, 3), 255, dtype=np.uint8)
    out = eng.extract_latex(region_image=img, seq_id="p1_body_9", page_num=1)

    assert calls["n"] == 1, "重試關閉時只應呼叫一次"
    assert out.get("equation_failed") is True


def test_equation_excluded_from_textual_fix():
    sc = SemanticCorrector()
    eq_block = {"type": "Equation", "content": "garbled formula text here", "is_equation": True}
    body_block = {"type": "Body", "content": "normal paragraph text"}
    assert sc._is_textual_block(eq_block) is False
    assert sc._is_textual_block(body_block) is True


def test_low_confidence_equation_flagged(tmp_path):
    sc = SemanticCorrector()
    assert sc._is_low_confidence_equation(
        {"type": "Equation", "equation_source": "tesseract_fallback", "latex_confidence": 0.35}
    ) is True
    assert sc._is_low_confidence_equation(
        {"type": "Equation", "equation_source": "llm_task_4cv", "latex_confidence": 0.99}
    ) is False
    assert sc._is_low_confidence_equation({"type": "Body", "content": "text"}) is False
