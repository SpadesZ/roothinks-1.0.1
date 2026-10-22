# Roothinks source maintenance contract
# 目的：原檔不能被 overwrite 或同名併發上傳破壞；合成 PDF，不執行 OCR／LLM。
# 驗證：python -m pytest test/unit/test_upload_preservation.py -q
import io
from concurrent.futures import ThreadPoolExecutor

import pytest

PID = "UPLOAD-p"


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    from app import create_app, db
    from app.models import Project
    from app.core_pro.literature import literature_routes as routes
    monkeypatch.setenv("FLASK_ENV", "development")
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
                      "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'manu.db'}"}})
    monkeypatch.setattr(routes, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(routes, "literature_library_service", None)
    with app.app_context():
        db.create_all()
        db.session.add(Project(project_id=PID, name="Synthetic", status="formal"))
        db.session.commit()
    yield app, routes, tmp_path
    with app.app_context():
        db.session.remove()
        for engine in db.engines.values():
            engine.dispose()


def upload(app, content, **extra):
    return app.test_client().post('/api/literature/upload', data={
        "pid": PID, "file": (io.BytesIO(content), "paper.pdf"), **extra}, content_type="multipart/form-data")


def test_explicit_overwrite_cannot_destroy_original(uploads):
    app, routes, root = uploads
    first = upload(app, b"%PDF-original-human-source")
    assert first.status_code == 200
    with app.app_context():
        origin = routes._get_literature_paper_dir(PID, first.json['paper_id'], for_write=False)
    from pathlib import Path
    source = Path(origin) / '00_origins' / 'source.pdf'
    original = source.read_bytes()
    overwrite = upload(app, b"%PDF-different-source", overwrite="1")
    assert overwrite.status_code == 409
    assert source.read_bytes() == original


def test_concurrent_names_keep_distinct_originals(uploads):
    app, routes, root = uploads
    payloads = [f"%PDF-source-{n}".encode() for n in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda content: upload(app, content), payloads))
    assert all(result.status_code == 200 for result in results)
    ids = [result.json['paper_id'] for result in results]
    assert len(set(ids)) == len(payloads)
    from pathlib import Path
    with app.app_context():
        saved = [(Path(routes._get_literature_paper_dir(PID, pid, for_write=False)) / '00_origins' / 'source.pdf').read_bytes() for pid in ids]
    assert set(saved) == set(payloads)


def test_database_failure_is_not_upload_success(uploads, monkeypatch):
    app, routes, root = uploads
    def fail():
        raise RuntimeError("synthetic commit failure")
    monkeypatch.setattr(routes.db.session, "commit", fail)
    response = upload(app, b"%PDF-preserve-on-database-failure")
    assert response.status_code == 503
    assert response.json['status'] == 'error'
    assert response.json['original_saved'] is True
    from pathlib import Path
    with app.app_context():
        source = Path(routes._get_literature_paper_dir(PID, response.json['paper_id'], for_write=False)) / '00_origins' / 'source.pdf'
    assert source.read_bytes() == b"%PDF-preserve-on-database-failure"
