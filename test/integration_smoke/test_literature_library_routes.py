# 檔案路徑: test/integration_smoke/test_literature_library_routes.py
# 產生時間: 2026-07-19
# 維護提醒:
#   完全隔離：DB 與 DATA_ROOT 都指向 tmp_path，並重置 library singleton，
#   絕不觸碰真實 data/。驗證 library API + citation API 端到端契約。

from pathlib import Path


def _make_app(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(project_root))
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)

    data_root = tmp_path / "data"
    data_root.mkdir()
    main_db = data_root / "roothinks_lib_test.db"
    manu_db = data_root / "manu_lib_test.db"

    from app import create_app, db

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{main_db}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{manu_db}"},
        }
    )

    # 把 literature 模組的 DATA_ROOT 指到 tmp，並清掉 library singleton，
    # 確保 route 端讀寫都落在隔離資料夾。
    from app.core_pro.literature import literature_routes

    monkeypatch.setattr(literature_routes, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(literature_routes, "literature_library_service", None)

    with app.app_context():
        db.drop_all()
        db.create_all()
    return app, data_root


def test_library_import_list_update_export(monkeypatch, tmp_path):
    app, _ = _make_app(monkeypatch, tmp_path)
    pid = "LIBR1-p"

    from app import db
    from app.models import Project

    with app.app_context():
        db.session.add(Project(project_id=pid, name="Lib Test", status="formal"))
        db.session.commit()

    client = app.test_client()

    # 匯入 CSL + normalized 混合
    resp = client.post(
        "/api/literature/library/import",
        json={
            "pid": pid,
            "items": [
                {"title": "CSL One", "DOI": "10.1/one", "issued": {"date-parts": [[2021]]}},
                {"title": "Norm Two", "authors": "A. Author", "year": 2019},
            ],
        },
    )
    assert resp.get_json()["added"] == 2

    # 列表
    entries = client.get(f"/api/literature/library?pid={pid}").get_json()["entries"]
    assert len(entries) == 2
    target = next(e for e in entries if e["title"] == "CSL One")
    assert target["sources"] == ["import"]

    # 更新 screening 狀態
    upd = client.post(
        "/api/literature/library/update",
        json={"pid": pid, "entry_id": target["entry_id"], "patch": {"screening_status": "included"}},
    ).get_json()
    assert upd["status"] == "success" and upd["entry"]["screening_status"] == "included"

    # 匯出 BibTeX（included scope）——只含 CSL One，且不捏造缺失欄位
    export = client.get(f"/api/literature/library/export?pid={pid}&format=bibtex&scope=included")
    body = export.get_data(as_text=True)
    assert "10.1/one" in body
    assert "Norm Two" not in body


def test_library_main_table_hides_provenance_and_row_upload(monkeypatch, tmp_path):
    """Provenance 留在 API；主表不顯示，也不保留已取消入口的死程式。"""
    app, _ = _make_app(monkeypatch, tmp_path)
    client = app.test_client()

    html = client.get("/literature", query_string={"pid": "LIBR1-p"}).get_data(as_text=True)
    js = client.get("/static/js/literature_library.js").get_data(as_text=True)

    assert ">來源</th>" not in html
    assert 'colspan="4"' in html
    assert "e.sources" not in js
    assert "triggerUpload" not in js
    assert "libHiddenUpload" not in js


def test_library_update_rejects_unknown_paper_link(monkeypatch, tmp_path):
    app, _ = _make_app(monkeypatch, tmp_path)
    pid = "LIBR2-p"

    from app import db
    from app.models import Project

    with app.app_context():
        db.session.add(Project(project_id=pid, name="Lib Test", status="formal"))
        db.session.commit()

    client = app.test_client()
    client.post(
        "/api/literature/library/import",
        json={"pid": pid, "items": [{"title": "Solo", "DOI": "10.2/solo"}]},
    )
    entry_id = client.get(f"/api/literature/library?pid={pid}").get_json()["entries"][0]["entry_id"]

    resp = client.post(
        "/api/literature/library/update",
        json={"pid": pid, "entry_id": entry_id, "patch": {"paper_id": "ghost_paper"}},
    )
    assert resp.status_code == 404


def test_upload_links_library_entry_and_handles_duplicate_names(monkeypatch, tmp_path):
    import io

    app, data_root = _make_app(monkeypatch, tmp_path)
    pid = "LIBR4-p"

    from app import db
    from app.models import Project

    with app.app_context():
        db.session.add(Project(project_id=pid, name="Upload Test", status="formal"))
        db.session.commit()

    client = app.test_client()
    client.post(
        "/api/literature/library/import",
        json={"pid": pid, "items": [{"title": "Linked Paper", "DOI": "10.4/link"}]},
    )
    entry_id = client.get(f"/api/literature/library?pid={pid}").get_json()["entries"][0]["entry_id"]

    def _upload():
        return client.post(
            "/api/literature/upload",
            data={
                "pid": pid,
                "entry_id": entry_id,
                "file": (io.BytesIO(b"%PDF-1.4 minimal"), "linked_paper.pdf"),
            },
            content_type="multipart/form-data",
        ).get_json()

    first = _upload()
    assert first["status"] == "success"
    assert first["linked_entry_id"] == entry_id
    paper_id = first["paper_id"]

    # library entry 現在連結到該 paper_id
    entries = client.get(f"/api/literature/library?pid={pid}").get_json()["entries"]
    assert entries[0]["paper_id"] == paper_id

    # 同名再上傳：預設配發新 paper_id，不覆蓋
    second = _upload()
    assert second["status"] == "success"
    assert second["paper_id"] != paper_id


def test_citation_suggest_and_decision_ledger(monkeypatch, tmp_path):
    app, data_root = _make_app(monkeypatch, tmp_path)
    pid = "LIBR3-p"

    from app import db
    from app.models import Project
    from app.services.evidence_index_service import upsert_evidence_segment
    from app.services.evidence_types import EvidenceSourceType

    with app.app_context():
        db.session.add(Project(project_id=pid, name="Cite Test", status="formal"))
        db.session.commit()
        upsert_evidence_segment(
            project_id=pid,
            source_type=EvidenceSourceType.PAPER_SEGMENT.value,
            source_id="src",
            paper_id="paper-1",
            segment_id="sec-1",
            text="insulin resistance has been reported in multiple cohorts",
            title="Diabetes",
        )

    client = app.test_client()
    suggest = client.post(
        "/manuscript/api/citation/suggest",
        json={"pid": pid, "text": "insulin resistance has been reported", "section": "introduction"},
    ).get_json()
    assert suggest["success"] is True
    assert suggest["citation_needed"] is True
    assert any(s["paper_id"] == "paper-1" for s in suggest["suggestions"])

    # 人確認後才記錄決策
    decide = client.post(
        "/manuscript/api/citation/decide",
        json={
            "pid": pid,
            "section": "introduction",
            "status": "accepted",
            "paper_id": "paper-1",
            "claim_text": "insulin resistance has been reported",
            "segment_ids": ["sec-1"],
        },
    ).get_json()
    assert decide["success"] is True

    decisions = client.get(f"/manuscript/api/citation/decisions?pid={pid}").get_json()
    assert decisions["count"] == 1
    assert decisions["decisions"][0]["section_id"] == "introduction"

    # sidecar 落在隔離 data root
    ledger = data_root / pid / "manuscript" / "citation_ledger.json"
    assert ledger.exists()
