# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_routes_smoke.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 routes smoke 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_routes_smoke.py -q
from pathlib import Path


def test_literature_and_study_routes_register(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    from app import create_app

    app = create_app({"TESTING": True})
    routes = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/literature" in routes
    assert any(r.startswith("/api/literature") for r in routes)
    assert "/api/literature/run_translation" in routes
    assert "/study" in routes
    assert any(r.startswith("/api/study") for r in routes)
    assert "/submit" in routes


def test_study_media_kind_accepts_equation(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    from app.core_pro.study.study_routes import _normalize_media_kind

    kind, folder, type_name = _normalize_media_kind("equation")
    assert kind == "equation"
    assert folder == "body"
    assert type_name == "Equation"
