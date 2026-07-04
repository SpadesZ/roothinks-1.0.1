# 檔案路徑: tests/test_evidence_index_service.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: evidence segment indexing tests; use in-memory app fixtures.

from flask import Flask

from app import db
from app.services.evidence_index_service import index_paper_segments, search_evidence, upsert_evidence_segment
from app.services.evidence_types import EvidenceSourceType


def _app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_BINDS"] = {"manuscript": "sqlite:///:memory:"}
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def test_upsert_and_search_evidence():
    app = _app()
    with app.app_context():
        db.create_all()
        upsert_evidence_segment(
            project_id="P1",
            source_type=EvidenceSourceType.PAPER_SEGMENT.value,
            source_id="src",
            paper_id="paper-1",
            segment_id="s1",
            text="insulin resistance diabetes cohort",
            title="Diabetes Study",
        )
        rows = search_evidence("P1", "diabetes insulin", top_k=3)
        assert rows[0]["paper_id"] == "paper-1"
        assert rows[0]["source_type"] == EvidenceSourceType.PAPER_SEGMENT.value


def test_content_hash_avoids_duplicates():
    app = _app()
    with app.app_context():
        db.create_all()
        rows = index_paper_segments("P1", "paper-1", [{"id": "s1", "text": "same text"}, {"id": "s2", "text": "same text"}])
        assert len(rows) == 2
        assert rows[0].id == rows[1].id
