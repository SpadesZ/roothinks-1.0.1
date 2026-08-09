# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_literature_flowb_refactor.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 literature flowb refactor 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_literature_flowb_refactor.py -q
from pathlib import Path


def _create_dev_app(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    from app import create_app

    return create_app({"TESTING": True})


def test_run_translation_wrapper_delegates_to_bflow_module(monkeypatch):
    app = _create_dev_app(monkeypatch)
    from app.core_pro.literature import literature_routes

    called = {}

    def _fake_impl(deps):
        called["deps"] = deps
        return literature_routes.jsonify({"status": "success", "message": "delegated"})

    monkeypatch.setattr(literature_routes, "run_translation_impl", _fake_impl)

    with app.test_request_context(
        "/api/literature/run_translation",
        method="POST",
        json={"pid": "TESTPID", "paper_ids": ["paper_1"]},
    ):
        resp = literature_routes.run_translation()

    payload = resp.get_json()
    assert payload["status"] == "success"
    assert payload["message"] == "delegated"
    assert "deps" in called
    assert called["deps"].DATA_ROOT == literature_routes.DATA_ROOT
    assert callable(called["deps"]._run_flowb_subprocess)
