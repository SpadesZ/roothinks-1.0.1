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


def test_study_media_kind_accepts_equation(monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))

    from app.core_pro.study.study_routes import _normalize_media_kind

    kind, folder, type_name = _normalize_media_kind("equation")
    assert kind == "equation"
    assert folder == "body"
    assert type_name == "Equation"
