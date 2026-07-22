# 檔案路徑: tests/test_paper_evidence_sync.py
# 產生時間: 2026-07-19
# 維護提醒: Flow A 粗索引 → Flow B 原子取代（stale 清除）契約；in-memory DB。

import json
import os

from flask import Flask

from app import db
from app.models import EvidenceSegment
from app.services.paper_evidence_sync import (
    STAGE_FLOW_A,
    STAGE_FLOW_B,
    build_segments_from_fusion,
    build_segments_from_reflow,
    sync_paper_evidence,
    sync_reflow_evidence,
)


def _app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_BINDS"] = {"manuscript": "sqlite:///:memory:"}
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


FUSION = {
    "paper_id": "paper-1",
    "content": [
        {
            "page": 1,
            "blocks": [
                {"id": "p1-b1", "type": "Title", "content": "Multi-threshold confidence framework"},
                {"id": "p1-b2", "type": "Body", "content": "We propose a general multi-confidence thresholding framework for ASR."},
            ],
        },
        {
            "page": 2,
            "blocks": [{"id": "p2-b1", "type": "Body", "content": "Experiments show consistent accuracy improvements across systems."}],
        },
    ],
}

REFLOW = {
    "meta": {"generation_mode": "task_5b_reflow"},
    "sections": [
        {
            "section_label": "Abstract",
            "confidence": 0.95,
            "source_block_refs": ["p1-b2"],
            "content_en": "We propose a general multi-confidence thresholding framework for speech recognition.",
            "content_zh": "本文提出一種通用的多信心閾值設定框架，以提升語音辨識系統的整體準確率。",
        },
        {
            "section_label": "Results",
            "confidence": 0.9,
            "source_block_refs": ["p2-b1"],
            "content_en": "The multi-threshold method consistently improves accuracy on real systems.",
            "content_zh": "多閾值方法在真實系統上穩定提升準確率。",
        },
    ],
}


def _write_artifacts(tmp_path, *, with_reflow: bool):
    paper_dir = tmp_path / "P1" / "literature" / "papers" / "paper-1"
    fusion_dir = paper_dir / "05_interprets" / "fusion"
    fusion_dir.mkdir(parents=True, exist_ok=True)
    (fusion_dir / "full_text.json").write_text(json.dumps(FUSION, ensure_ascii=False), encoding="utf-8")
    if with_reflow:
        reflow_dir = paper_dir / "06_translates" / "reflow"
        reflow_dir.mkdir(parents=True, exist_ok=True)
        (reflow_dir / "semantic_sections.json").write_text(json.dumps(REFLOW, ensure_ascii=False), encoding="utf-8")
    return str(paper_dir)


def test_fusion_segments_are_page_level_with_block_ids():
    segments = build_segments_from_fusion(FUSION)
    assert [s["segment_id"] for s in segments] == ["page-1", "page-2"]
    assert segments[0]["metadata"]["block_ids"] == ["p1-b1", "p1-b2"]


def test_reflow_segments_carry_source_block_refs():
    segments = build_segments_from_reflow(REFLOW)
    assert len(segments) == 2
    assert segments[0]["metadata"]["source_block_refs"] == ["p1-b2"]
    assert "多信心閾值" in segments[0]["text"], "雙語內文必須同時可檢索"


def test_flow_a_then_flow_b_atomic_replacement(tmp_path):
    app = _app()
    with app.app_context():
        db.create_all()
        paper_dir = _write_artifacts(tmp_path, with_reflow=False)

        # Flow A 粗索引
        result_a = sync_paper_evidence("P1", "paper-1", paper_dir, prefer="fusion")
        assert not result_a["skipped"] and result_a["stage"] == STAGE_FLOW_A
        rows = EvidenceSegment.query.filter_by(project_id="P1", paper_id="paper-1").all()
        assert {r.segment_id for r in rows} == {"page-1", "page-2"}

        # Flow B 精索引：舊的頁級 segments 必須全部被清除（不能只靠 content_hash）
        result_b = sync_reflow_evidence("P1", "paper-1", REFLOW)
        assert result_b["stage"] == STAGE_FLOW_B
        assert result_b["deleted"] == 2, "stale Flow A segments 必須被刪除"
        rows = EvidenceSegment.query.filter_by(project_id="P1", paper_id="paper-1").all()
        assert {r.segment_id for r in rows} == {"sec-1", "sec-2"}
        for row in rows:
            meta = json.loads(row.metadata_json)
            assert meta["stage"] == STAGE_FLOW_B


def test_rerun_with_changed_content_leaves_no_stale_rows(tmp_path):
    app = _app()
    with app.app_context():
        db.create_all()
        sync_reflow_evidence("P1", "paper-1", REFLOW)

        changed = json.loads(json.dumps(REFLOW))
        changed["sections"] = changed["sections"][:1]
        changed["sections"][0]["content_en"] = "Completely rewritten abstract content for the revised run."
        result = sync_reflow_evidence("P1", "paper-1", changed)
        assert result["deleted"] == 2 and result["indexed"] == 1

        rows = EvidenceSegment.query.filter_by(project_id="P1", paper_id="paper-1").all()
        assert len(rows) == 1
        assert "rewritten abstract" in rows[0].text


def test_sync_prefers_reflow_on_auto(tmp_path):
    app = _app()
    with app.app_context():
        db.create_all()
        paper_dir = _write_artifacts(tmp_path, with_reflow=True)
        result = sync_paper_evidence("P1", "paper-1", paper_dir, prefer="auto")
        assert result["stage"] == STAGE_FLOW_B


def test_sync_skips_when_no_artifacts(tmp_path):
    app = _app()
    with app.app_context():
        db.create_all()
        empty_dir = tmp_path / "P1" / "literature" / "papers" / "empty-paper"
        empty_dir.mkdir(parents=True)
        result = sync_paper_evidence("P1", "empty-paper", str(empty_dir))
        assert result["skipped"] is True


def test_other_papers_untouched_by_replacement(tmp_path):
    app = _app()
    with app.app_context():
        db.create_all()
        sync_reflow_evidence("P1", "paper-1", REFLOW)
        sync_reflow_evidence("P1", "paper-2", REFLOW)
        sync_reflow_evidence("P1", "paper-1", REFLOW)  # 重跑 paper-1
        assert EvidenceSegment.query.filter_by(project_id="P1", paper_id="paper-2").count() == 2
