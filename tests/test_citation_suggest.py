# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 citation suggest 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_citation_suggest.py -q
# 檔案路徑: tests/test_citation_suggest.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: citation suggestion regression tests; keep fixtures small and deterministic.

from flask import Flask

from app import db
from app.core_pro.manuscript.citation_suggest import detect_citation_needed, suggest_citations_for_paragraph
from app.services.evidence_index_service import upsert_evidence_segment
from app.services.evidence_types import EvidenceSourceType


def _app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_BINDS"] = {"manuscript": "sqlite:///:memory:"}
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def test_detect_citation_needed():
    assert detect_citation_needed("previous studies have been reported in 42% of cases")
    assert not detect_citation_needed("This paragraph is only a transition.")


def test_suggest_citations_uses_real_evidence_only():
    app = _app()
    with app.app_context():
        db.create_all()
        upsert_evidence_segment(
            project_id="P1",
            source_type=EvidenceSourceType.PAPER_SEGMENT.value,
            source_id="src",
            paper_id="paper-1",
            segment_id="s1",
            title="Insulin Study",
            text="insulin resistance was associated with diabetes progression",
        )
        suggestions = suggest_citations_for_paragraph("P1", "insulin resistance has been reported", top_k=3)
        assert suggestions[0]["paper_id"] == "paper-1"
        assert suggestions[0]["segment_ids"] == ["s1"]
