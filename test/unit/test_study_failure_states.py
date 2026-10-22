# Roothinks source maintenance contract
# 目的：真實比較器／引擎的錯誤與降級，不得冒充科學比較成功。
# 驗證：python -m pytest test/unit/test_study_failure_states.py -q
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core_pro.study.study_matrix import MatrixEngine
from app.llm_service.matching_tasks.task_6comp import Comparator
from app.llm_service.matching_tasks.task_7qachat import TutorEngine


def test_provider_failure_stays_degraded_without_invented_dimensions():
    comparator = Comparator()
    comparator.bus = SimpleNamespace(dispatch_task=lambda *a, **k: (False, "synthetic provider offline"))
    result = comparator.compare_pair({"id": "a", "title": "A", "text": "laser optics"},
                                     {"id": "b", "title": "B", "text": "laser optics"}, "Results")
    assert result.get("error")
    assert result["quality_mode"] == "heuristic"
    assert not result.get("comparison_points"), "字詞重疊不能變成五維科學比較"


def test_malformed_model_result_is_failure():
    comparator = Comparator()
    comparator.bus = SimpleNamespace(dispatch_task=lambda *a, **k: (True, "not json"))
    result = comparator.compare_pair({"id": "a", "title": "A", "text": "x"},
                                     {"id": "b", "title": "B", "text": "y"}, "Results")
    assert result.get("error")
    assert result["status"] == "failed"


def test_all_comparisons_failed_is_not_success(tmp_path):
    engine = MatrixEngine(str(tmp_path))
    engine.comparator = SimpleNamespace(compare_pair=lambda *a: {"error": "offline"})
    result = engine.generate_matrix("P-p", [{"paper_id": "a", "content": "A"}, {"paper_id": "b", "content": "B"}])
    assert result.get("status") == "failed"
    assert result["success_count"] == 0
    assert not list(tmp_path.rglob("*.json")), "失敗不得取代既有研究成果"


def test_partial_comparison_reports_both_counts(tmp_path):
    engine = MatrixEngine(str(tmp_path))
    engine.comparator = SimpleNamespace(compare_pair=lambda a, b, c: {"error": "offline"} if b["id"] == "b" else {"analysis": "supported result"})
    result = engine.generate_matrix("P-p", [{"paper_id": p, "content": p} for p in ("a", "b", "c")])
    assert result.get("status") == "partial"
    assert result["success_count"] == 1 and result["failure_count"] == 1


def test_tutor_provider_failure_does_not_become_answer():
    engine = TutorEngine()
    engine.bus = SimpleNamespace(dispatch_task=lambda *a, **k: (False, "synthetic offline"))
    with pytest.raises(RuntimeError, match="provider"):
        engine.process_query("q", {})


def test_citation_index_error_is_not_no_results(monkeypatch):
    from app.core_pro.manuscript.citation_suggest import suggest_citations_for_paragraph
    def fail(**kwargs):
        raise OSError("synthetic index unavailable")
    monkeypatch.setattr("app.services.evidence_index_service.search_evidence", fail)
    with pytest.raises(OSError):
        suggest_citations_for_paragraph("P-p", "test evidence")
