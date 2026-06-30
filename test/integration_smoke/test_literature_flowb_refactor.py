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
