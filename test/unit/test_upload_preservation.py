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


def test_pdf_first_creates_candidate_and_hash(uploads):
    app, routes, root = uploads
    response = upload(app, b'%PDF-candidate-source')
    assert response.status_code == 200
    assert response.json['linked_entry_id']
    with app.app_context():
        entries = routes.get_literature_library().list_entries(PID)
    assert len(entries) == 1
    assert entries[0]['paper_id'] == response.json['paper_id']
    assert entries[0]['screening_status'] == 'candidate'
    import hashlib
    assert entries[0]['uploaded_sources'][0]['sha256'] == hashlib.sha256(b'%PDF-candidate-source').hexdigest()


def recover(app,paper_id,**extra):
    return app.test_client().post('/api/literature/recover_upload',json={'pid':PID,'paper_id':paper_id,**extra})


def test_partial_stream_failure_never_publishes_source(uploads,monkeypatch):
    from werkzeug.datastructures import FileStorage
    app,routes,root = uploads
    app.config['PROPAGATE_EXCEPTIONS'] = False
    def partial(self,target,*args,**kwargs):
        target.write(b'%PDF-incomplete')
        raise OSError('synthetic interrupted stream')
    monkeypatch.setattr(FileStorage,'save',partial)
    response = upload(app,b'%PDF-complete')
    assert response.status_code == 503
    assert response.json['original_saved'] is False
    assert not list(root.glob('**/source.pdf'))
    with app.app_context():
        assert routes.Paper.query.count() == 0


@pytest.mark.parametrize('stage',['db','library'])
def test_registration_failure_can_recover_same_identity_without_rewriting(uploads,monkeypatch,stage):
    from pathlib import Path
    from app.services.literature_library import LiteratureLibrary
    app,routes,root = uploads
    with monkeypatch.context() as patch:
        def fail(*a,**k):
            raise RuntimeError('synthetic registration failure')
        if stage == 'db':
            patch.setattr(routes.db.session,'commit',fail)
        else:
            patch.setattr(LiteratureLibrary,'register_upload',fail)
        response = upload(app,b'%PDF-recover-original')
    assert response.status_code == 503 and response.json['original_saved']
    paper_id = response.json['paper_id']
    source = next(root.glob('**/source.pdf'))
    before = (source.read_bytes(),source.stat().st_mtime_ns)
    one = recover(app,paper_id)
    two = recover(app,paper_id)
    assert one.status_code == two.status_code == 200
    assert one.json['paper_id'] == two.json['paper_id'] == paper_id
    assert (source.read_bytes(),source.stat().st_mtime_ns) == before
    with app.app_context():
        assert routes.Paper.query.filter_by(pid=PID,paper_id=paper_id).count() == 1
        rows = routes.get_literature_library().list_entries(PID)
        assert len(rows) == 1 and rows[0]['screening_status'] == 'candidate'


def test_recovery_rejects_changed_original_and_missing_manifest(uploads):
    app,routes,root = uploads
    response = upload(app,b'%PDF-immutable')
    source = next(root.glob('**/source.pdf'))
    source.write_bytes(b'%PDF-tampered')
    assert recover(app,response.json['paper_id']).status_code == 409
    (source.parent/'metadata.json').unlink()
    assert recover(app,response.json['paper_id']).status_code == 409


def test_link_failure_preserves_candidate_and_recovery_never_reactivates_old_pdf(uploads):
    app,routes,root = uploads
    with app.app_context():
        library = routes.get_literature_library()
        library.merge_candidates(PID,[{'title':'Search identity','doi':'10.1234/recovery'}])
        target = next(e for e in library.list_entries(PID) if e.get('doi'))
        library.update_entry(PID,target['entry_id'],{'reading_note':'newer revision'},actor='author',base_rev=0)
    failed = upload(app,b'%PDF-first',entry_id=target['entry_id'],base_rev='0')
    assert failed.status_code == 503 and failed.json['original_saved']
    first_id = failed.json['paper_id']
    with app.app_context():
        rows = library.list_entries(PID)
        assert next(e for e in rows if e.get('paper_id')==first_id)['screening_status']=='candidate'
    linked = recover(app,first_id,entry_id=target['entry_id'],base_rev=1)
    assert linked.status_code == 200
    second = upload(app,b'%PDF-second')
    assert second.status_code == 200
    with app.app_context():
        current = library.load(PID)['entries'][target['entry_id']]
        updated = library.update_entry(PID,target['entry_id'],{'paper_id':second.json['paper_id']},actor='author',base_rev=current['_rev'])
    assert recover(app,first_id).status_code == 200
    with app.app_context():
        now = library.load(PID)['entries'][target['entry_id']]
        assert now['paper_id']==second.json['paper_id'] and now['_rev']==updated['_rev']
    assert len(list(root.glob('**/source.pdf')))==2
