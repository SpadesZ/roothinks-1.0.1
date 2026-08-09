# Roothinks source maintenance contract
# 檔案路徑: test/integration_smoke/test_workflow_status.py
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 workflow status 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/integration_smoke/test_workflow_status.py -q
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


def _seed_project(app, pid="WFLOW1-p"):
    """批次端點的測試也要有專案存在——沒有的話會被 `if not project: continue`
    靜默略過，回傳空 dict（第一版就是這樣誤判成端點壞掉）。"""
    # db / Project 在這個檔案裡是函式內 import（見 _make_app），這裡照做。
    from app import db
    from app.models import Project
    with app.app_context():
        db.session.add(Project(project_id=pid, name="Batch Test",
                               research_title="Batch", status="formal"))
        db.session.commit()


def _module_pages(client):
    """五個模組頁的 HTML。"""
    return {
        "paq": client.get("/paq/WFLOW1-p").get_data(as_text=True),
        "literature": client.get("/literature", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True),
        "study": client.get("/study/project/WFLOW1-p", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True),
        "manuscript": client.get("/manuscript/", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True),
        "submit": client.get("/submit", query_string={"pid": "WFLOW1-p"}).get_data(as_text=True),
    }


def test_module_pages_no_longer_duplicate_the_workflow_strip(monkeypatch, tmp_path):
    """[契約變更] 五模組進度改在 dashboard 一覽，模組頁不再重複掛載。

    原本每個模組頁頂部都有一條五模組狀態列，但那些頁面的導覽列本來就有
    同樣五個模組的按鈕——同一組資訊重複兩次，還佔掉垂直空間。
    進度改為顯示在 dashboard 的每張專案卡上，一眼可比較所有專案。
    """
    app, _data_root = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    for name, html in _module_pages(client).items():
        assert 'data-workflow-status' not in html, f"{name} 仍掛著重複的狀態列"
        assert 'workflow_status.js' not in html, f"{name} 仍載入無掛載點的狀態腳本"


def test_dashboard_renders_per_project_workflow(monkeypatch, tmp_path):
    """進度改由 dashboard.js 逐張專案卡渲染。"""
    app, _data_root = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert 'dashboard.js' in html

    js = client.get("/static/js/dashboard.js").get_data(as_text=True)
    assert 'data-workflow-card' in js
    assert '/api/project/workflow?pids=' in js
    assert 'WORKFLOW_STATUS_LABELS' in js
    assert "complete: '完成'" in js
    assert 'aria-label=' in js


def test_workflow_batch_endpoint_returns_requested_projects(monkeypatch, tmp_path):
    """批次端點：逐張卡各打一次的話，9 個專案就是 9 次請求。"""
    app, _data_root = _make_app(monkeypatch, tmp_path)
    _seed_project(app)
    client = app.test_client()

    res = client.get("/api/project/workflow", query_string={"pids": "WFLOW1-p"})
    payload = res.get_json()
    assert res.status_code == 200
    assert payload["success"] is True
    assert "WFLOW1-p" in payload["projects"]
    assert payload["projects"]["WFLOW1-p"]["order"] == [
        "paq", "literature", "study", "manuscript", "submit"
    ]


def test_workflow_batch_skips_unknown_pids_without_failing(monkeypatch, tmp_path):
    """不存在或無權限的 pid 靜默略過，不能讓整批失敗。"""
    app, _data_root = _make_app(monkeypatch, tmp_path)
    _seed_project(app)
    client = app.test_client()

    res = client.get("/api/project/workflow",
                     query_string={"pids": "WFLOW1-p,NO-SUCH-PID"})
    payload = res.get_json()
    assert res.status_code == 200
    assert set(payload["projects"]) == {"WFLOW1-p"}


def test_workflow_batch_empty_input_is_ok(monkeypatch, tmp_path):
    app, _data_root = _make_app(monkeypatch, tmp_path)
    client = app.test_client()
    res = client.get("/api/project/workflow")
    assert res.status_code == 200
    assert res.get_json()["projects"] == {}
