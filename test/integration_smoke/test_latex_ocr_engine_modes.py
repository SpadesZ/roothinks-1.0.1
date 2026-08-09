# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_latex_ocr_engine_modes.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 latex ocr engine modes 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_latex_ocr_engine_modes.py -q
import numpy as np
from pathlib import Path


def _dummy_image():
    img = np.full((64, 180, 3), 255, dtype=np.uint8)
    return img


def test_latex_ocr_auto_prefers_unimernet(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    monkeypatch.setenv("LITERATURE_LATEX_OCR_ENABLE", "1")
    monkeypatch.setenv("LITERATURE_LATEX_ENGINE", "auto")

    from app.core_pro.literature.literature_latex_ocr import LatexOCREngine

    engine = LatexOCREngine()
    calls = {"llm": 0}

    def fake_unimernet(**_kwargs):
        return {
            "ok": True,
            "payload": {
                "latex": r"x^2 + y^2 = z^2",
                "confidence": 0.91,
                "source": "unimernet_cmd",
                "is_equation": True,
                "reasons": ["mock_unimernet"],
            },
        }

    def fake_llm(**_kwargs):
        calls["llm"] += 1
        return {
            "ok": True,
            "payload": {
                "latex": r"x+y",
                "confidence": 0.88,
                "source": "llm_task_4cv",
                "is_equation": True,
                "reasons": ["mock_llm"],
            },
        }

    monkeypatch.setattr(engine, "_extract_with_unimernet", fake_unimernet)
    monkeypatch.setattr(engine, "_extract_with_llm_task", fake_llm)

    out = engine.extract_latex(
        region_image=_dummy_image(),
        seq_id="eq1",
        page_num=1,
        bbox=[10, 10, 100, 40],
        page_w=180,
        page_h=64,
    )

    assert out["source"] == "unimernet_cmd"
    assert out["latex"] == r"x^2 + y^2 = z^2"
    assert calls["llm"] == 0


def test_latex_ocr_auto_falls_back_to_llm(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    monkeypatch.setenv("LITERATURE_LATEX_OCR_ENABLE", "1")
    monkeypatch.setenv("LITERATURE_LATEX_ENGINE", "auto")

    from app.core_pro.literature.literature_latex_ocr import LatexOCREngine

    engine = LatexOCREngine()

    monkeypatch.setattr(engine, "_extract_with_unimernet", lambda **_kwargs: {"ok": False, "reason": "u_fail"})
    monkeypatch.setattr(
        engine,
        "_extract_with_llm_task",
        lambda **_kwargs: {
            "ok": True,
            "payload": {
                "latex": r"\\frac{a}{b}",
                "confidence": 0.86,
                "source": "llm_task_4cv",
                "is_equation": True,
                "reasons": ["mock_llm"],
            },
        },
    )

    out = engine.extract_latex(
        region_image=_dummy_image(),
        seq_id="eq2",
        page_num=2,
        bbox=[10, 10, 100, 40],
        page_w=180,
        page_h=64,
    )

    assert out["source"] == "llm_task_4cv"
    assert out["latex"] == r"\\frac{a}{b}"


def test_latex_ocr_unimernet_mode_returns_failed_marker_without_tesseract(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    monkeypatch.setenv("LITERATURE_LATEX_OCR_ENABLE", "1")
    monkeypatch.setenv("LITERATURE_LATEX_ENGINE", "unimernet")

    from app.core_pro.literature.literature_latex_ocr import LatexOCREngine

    engine = LatexOCREngine()

    monkeypatch.setattr(engine, "_extract_with_unimernet", lambda **_kwargs: {"ok": False, "reason": "u_fail"})
    monkeypatch.setattr(engine, "_extract_with_llm_task", lambda **_kwargs: {"ok": False, "reason": "llm_fail"})

    out = engine.extract_latex(
        region_image=_dummy_image(),
        seq_id="eq3",
        page_num=3,
        bbox=[10, 10, 100, 40],
        page_w=180,
        page_h=64,
    )

    assert out["source"] == "failed_ocr"
    assert out["latex"] == ""
    assert bool(out.get("equation_failed")) is True
    assert out.get("marker") == "<Failed to OCR Equation>"
    assert "u_fail" in " ".join(out.get("reasons", []))
