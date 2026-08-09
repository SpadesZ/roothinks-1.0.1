# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 evidence index service 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_evidence_index_service.py -q
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


def test_cjk_substring_search_hits():
    app = _app()
    with app.app_context():
        db.create_all()
        upsert_evidence_segment(
            project_id="P1",
            source_type=EvidenceSourceType.PAPER_SEGMENT.value,
            source_id="src",
            paper_id="paper-zh",
            segment_id="s1",
            text="本文提出多信心閾值設定框架以提升語音辨識準確率",
            title="語音辨識",
        )
        rows = search_evidence("P1", "多信心閾值", top_k=3)
        assert rows and rows[0]["paper_id"] == "paper-zh"


def test_search_returns_beyond_first_1000_segments():
    app = _app()
    with app.app_context():
        db.create_all()
        # 早期索引一個唯一目標段，之後灌入 1100 個雜訊段。
        upsert_evidence_segment(
            project_id="P1",
            source_type=EvidenceSourceType.PAPER_SEGMENT.value,
            source_id="target",
            paper_id="paper-target",
            segment_id="target",
            text="unicornmarker rare token needle",
            title="Target",
        )
        noise = [
            {"id": f"n{i}", "text": f"generic filler sentence number {i} about unrelated topics"}
            for i in range(1100)
        ]
        index_paper_segments("P1", "paper-noise", noise)
        rows = search_evidence("P1", "unicornmarker needle", top_k=5)
        # 舊的 limit(1000) 會讓最早索引的 target 被截斷而消失；移除後必須仍可召回。
        assert any(r["paper_id"] == "paper-target" for r in rows)
