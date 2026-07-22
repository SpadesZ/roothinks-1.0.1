from pathlib import Path


def _make_app(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)

    data_root = tmp_path / "data"
    data_root.mkdir()
    main_db = data_root / "roothinks_workflow_test.db"
    manu_db = data_root / "manu_workflow_test.db"

    from app import create_app, db

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        }
    )
    with app.app_context():
        db.drop_all()
        db.create_all()
    return app, data_root


def test_workflow_status_reports_five_module_readiness(monkeypatch, tmp_path):
    app, data_root = _make_app(monkeypatch, tmp_path)
    pid = "WFLOW1-p"

    from app import db
    from app.models import Project, PaqSurvey

    with app.app_context():
        project = Project(project_id=pid, name="Workflow Test", research_title="Workflow Research", status="formal")
        db.session.add(project)
        db.session.flush()
        db.session.add(
            PaqSurvey(
                project_ref_id=project.id,
                axis_labels={"x": "Method", "y": "Domain", "z": "Level"},
                axis_tags={"x": ["A"], "y": ["B"], "z": ["C"]},
                cube_data=[{"x": "A", "y": "B", "z": "C"}],
            )
        )
        db.session.commit()

    paper = data_root / pid / "literature" / "papers" / "paperA"
    (paper / "03_recognizes").mkdir(parents=True)
    (paper / "05_interprets").mkdir(parents=True)
    (paper / "06_translates" / "fusion").mkdir(parents=True)
    (paper / "06_translates" / "reflow").mkdir(parents=True)
    (paper / "03_recognizes" / "text_1_fixed.json").write_text('{"blocks":[]}', encoding="utf-8")
    (paper / "05_interprets" / "summary.json").write_text('{"abstract_zh":"ok"}', encoding="utf-8")
    (paper / "06_translates" / "fusion" / "full_text_trans.json").write_text('{"content":[]}', encoding="utf-8")
    (paper / "06_translates" / "reflow" / "semantic_sections.json").write_text('{"sections":[{}]}', encoding="utf-8")

    study_dir = data_root / pid / "study"
    study_dir.mkdir(parents=True)
    (study_dir / "latest_matrix.json").write_text('{"matrix_id":"m1"}', encoding="utf-8")
    (study_dir / f"{pid}_note.json").write_text('{"notes":"ready"}', encoding="utf-8")

    block_dir = data_root / pid / "manuscript" / "block" / "introduction"
    block_dir.mkdir(parents=True)
    (block_dir / "intro.json").write_text('{"content":"draft"}', encoding="utf-8")

    client = app.test_client()
    payload = client.get(f"/api/project/workflow/{pid}").get_json()

    assert payload["success"] is True
    assert payload["order"] == ["paq", "literature", "study", "manuscript", "submit"]
    assert payload["modules"]["paq"]["status"] == "complete"
    assert payload["modules"]["literature"]["done"] == 1
    assert payload["modules"]["study"]["status"] == "complete"
    assert payload["modules"]["manuscript"]["status"] == "working"
    assert payload["modules"]["submit"]["status"] == "ready"
    assert payload["modules"]["submit"]["next_action"] == "產生單欄/雙欄投稿版型"


def test_submit_status_waits_for_manuscript_before_ready(monkeypatch, tmp_path):
    app, data_root = _make_app(monkeypatch, tmp_path)
    pid = "WFLOW1-p"

    from app import db
    from app.models import Project, PaqSurvey

    with app.app_context():
        project = Project(project_id=pid, name="Workflow Test", research_title="Workflow Research", status="formal")
        db.session.add(project)
        db.session.flush()
        db.session.add(
            PaqSurvey(
                project_ref_id=project.id,
                axis_labels={"x": "Method", "y": "Domain", "z": "Level"},
                axis_tags={"x": ["A"], "y": ["B"], "z": ["C"]},
                cube_data=[{"x": "A", "y": "B", "z": "C"}],
            )
        )
        db.session.commit()

    paper = data_root / pid / "literature" / "papers" / "paperA"
    (paper / "03_recognizes").mkdir(parents=True)
    (paper / "06_translates" / "fusion").mkdir(parents=True)
    (paper / "06_translates" / "reflow").mkdir(parents=True)
    (paper / "03_recognizes" / "text_1_fixed.json").write_text('{"blocks":[]}', encoding="utf-8")
    (paper / "06_translates" / "fusion" / "full_text_trans.json").write_text('{"content":[]}', encoding="utf-8")
    (paper / "06_translates" / "reflow" / "semantic_sections.json").write_text('{"sections":[{}]}', encoding="utf-8")

    study_dir = data_root / pid / "study"
    study_dir.mkdir(parents=True)
    (study_dir / "latest_matrix.json").write_text('{"matrix_id":"m1"}', encoding="utf-8")
    (study_dir / f"{pid}_note.json").write_text('{"notes":"ready"}', encoding="utf-8")

    payload = app.test_client().get(f"/api/project/workflow/{pid}").get_json()

    assert payload["modules"]["manuscript"]["status"] == "empty"
    assert payload["modules"]["submit"]["status"] == "blocked"
    assert payload["modules"]["submit"]["next_action"] == "先在 Manuscript 保存草稿"


def test_paq_page_exposes_workflow_status_mount(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    html = client.get("/paq/WFLOW1-p").get_data(as_text=True)

    assert 'data-workflow-status' in html
    assert 'data-focus="paq"' in html
    assert '/static/js/workflow_status.js' in html


def test_literature_page_exposes_workflow_status_mount(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    html = client.get("/literature", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True)

    assert 'data-workflow-status' in html
    assert 'data-focus="literature"' in html
    assert 'workflow_status.js' in html


def test_study_page_exposes_workflow_status_mount(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    from app import db
    from app.models import Project

    with app.app_context():
        db.session.add(Project(project_id="WFLOW1-p", name="Workflow Test", status="formal"))
        db.session.commit()

    client = app.test_client()

    html = client.get("/study/project/WFLOW1-p").get_data(as_text=True)

    assert 'data-workflow-status' in html
    assert 'data-focus="study"' in html
    assert 'workflow_status.js' in html


def test_manuscript_page_exposes_workflow_status_mount(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    from app import db
    from app.models import Project

    with app.app_context():
        db.session.add(Project(project_id="WFLOW1-p", name="Workflow Test", status="formal"))
        db.session.commit()

    client = app.test_client()
    html = client.get("/manuscript/", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True)

    assert 'data-workflow-status' in html
    assert 'data-focus="manuscript"' in html
    assert 'workflow_status.js' in html


def test_csp_keeps_frame_ancestors_locked_while_allowing_drive_frames(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/")

    csp = resp.headers.get("Content-Security-Policy", "")

    assert "frame-src 'self' https://content.googleapis.com https://accounts.google.com" in csp
    assert "frame-ancestors 'none'" in csp


def test_submit_page_exposes_workflow_status_mount(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    html = client.get("/submit", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True)

    assert 'data-workflow-status' in html
    assert 'data-focus="submit"' in html
    assert 'workflow_status.js' in html
    assert '投稿版型輸出' in html
    assert '雙欄' in html
    assert '單欄' in html
    assert 'href="/submit?pid=' in client.get("/paq/WFLOW1-p").get_data(as_text=True)
