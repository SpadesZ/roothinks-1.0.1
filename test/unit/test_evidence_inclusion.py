# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_evidence_inclusion.py
# 子系統定位:
#   Evidence 檢索的「人工篩選有效力」護欄（NOTE-013）。
# 主要責任:
#   1. excluded 的論文硬阻擋：任何 scope 都不得回傳。
#   2. writing scope 下，未篩選（unknown/candidate）的論文不得作為寫作依據。
#   3. included 的論文在 writing scope 下正常可用。
#   4. **人類自己的內容（study_note / paq_note / manuscript_note）不受文獻篩選影響。**
#      這條是最容易寫錯的：一起濾掉等於把 COC 要保住的作者決策刪掉。
#   5. 讀不到 library 時 fail-closed（視為全部 unknown），不得例外放行。
# 明確不負責:
#   - 不驗證 COC bundle 組裝（見 test_coc_bundle.py）。
#   - 不驗證排序與配額邏輯（既有 _apply_per_paper_quota 的行為不在本檔範圍）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只在 tmp_path 下建立 evidence 與 <pid>/literature/library.json；不碰真實 data/。
# 不變量:
#   - 斷言以「哨兵字串有沒有出現在檢索結果」為準，不綁定實作細節。
# 相關 NOTE:
#   NOTE-013（Manuscript evidence 只接受 included，excluded/unknown 不得靜默注入）。
# 驗證:
#   python -m pytest test/unit/test_evidence_inclusion.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "EVSCRN-p"
QUERY = "triage accuracy"


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db

    application = create_app({
        "TESTING": True,
        "AUTH_MODE": "none",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'ev.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'ev_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _index(project_id, source_type, source_id, text, paper_id=None):
    from app import db
    from app.services.evidence_index_service import upsert_evidence_segment
    upsert_evidence_segment(
        project_id=project_id, source_type=source_type, source_id=source_id,
        text=text, paper_id=paper_id, title="Triage study",
    )
    db.session.commit()


def _set_screening(paper_id, status):
    """把某篇論文標成 included / excluded，走既有的 LiteratureLibrary。"""
    from app.services.evidence_index_service import _resolve_data_root
    from app.services.literature_library import LiteratureLibrary

    lib = LiteratureLibrary(_resolve_data_root())
    lib.merge_candidates(PID, [{"title": f"paper {paper_id}", "doi": f"10.1/{paper_id}"}])
    entries = lib.list_entries(PID)
    entry_id = entries[-1]["id"] if entries and "id" in entries[-1] else None
    if entry_id is None:                       # schema 沒有 id 就用 key
        entry_id = list(lib.load(PID)["entries"].keys())[-1]
    lib.update_entry(PID, entry_id, {"paper_id": paper_id, "screening_status": status},
                     actor="pi@example.org")


def _search(scope):
    from app.services.evidence_index_service import search_evidence
    return search_evidence(project_id=PID, query=QUERY, top_k=20, inclusion_scope=scope)


class TestExcludedIsHardBlocked:
    def test_excluded_paper_never_appears_in_any_scope(self, app):
        sentinel = "SENTINEL_EXCLUDED_PAPER_MUST_NOT_BE_CITED"
        with app.app_context():
            _index(PID, "paper_segment", "seg-ex", f"triage accuracy {sentinel}", paper_id="PX")
            _set_screening("PX", "excluded")

            for scope in ("any", "writing"):
                texts = " ".join(str(r) for r in _search(scope))
                assert sentinel not in texts, f"excluded 論文在 scope={scope} 仍被回傳"


class TestWritingScopeRequiresIncluded:
    def test_unknown_paper_is_not_usable_for_writing(self, app):
        """從未篩選過的論文不得成為正式寫作依據（fail-closed）。"""
        sentinel = "SENTINEL_UNREVIEWED_PAPER"
        with app.app_context():
            _index(PID, "paper_segment", "seg-unk", f"triage accuracy {sentinel}", paper_id="PU")
            assert sentinel not in " ".join(str(r) for r in _search("writing"))
            # 但探索用途仍看得到，資料不會從系統消失。
            assert sentinel in " ".join(str(r) for r in _search("any"))

    def test_included_paper_is_usable_for_writing(self, app):
        sentinel = "SENTINEL_INCLUDED_PAPER"
        with app.app_context():
            _index(PID, "paper_segment", "seg-inc", f"triage accuracy {sentinel}", paper_id="PI")
            _set_screening("PI", "included")
            assert sentinel in " ".join(str(r) for r in _search("writing"))


class TestHumanContentIsNotScreened:
    def test_study_and_paq_notes_survive_writing_scope(self, app):
        """
        文獻篩選只約束論文。作者自己的筆記若被一起濾掉，
        等於把 COC 要保住的東西刪光 —— 這是本次最容易寫錯的一條。
        """
        with app.app_context():
            _index(PID, "study_note", "note-1", "triage accuracy SENTINEL_STUDY_NOTE")
            _index(PID, "paq_note", "paq-1", "triage accuracy SENTINEL_PAQ_NOTE")
            _index(PID, "manuscript_note", "mn-1", "triage accuracy SENTINEL_MANUSCRIPT_NOTE")
            # 同時放一篇未篩選論文，證明論文被擋掉而筆記沒有。
            _index(PID, "paper_segment", "seg-x", "triage accuracy SENTINEL_PAPER", paper_id="PZ")

            texts = " ".join(str(r) for r in _search("writing"))

        assert "SENTINEL_STUDY_NOTE" in texts
        assert "SENTINEL_PAQ_NOTE" in texts
        assert "SENTINEL_MANUSCRIPT_NOTE" in texts
        assert "SENTINEL_PAPER" not in texts, "未篩選論文應被擋，此處若在代表過濾沒生效"


class TestFailClosedOnLibraryError:
    def test_library_failure_blocks_papers_instead_of_allowing(self, app, monkeypatch):
        """讀不到篩選狀態時必須擋下，不得因為「查不到」而放行。"""
        sentinel = "SENTINEL_PAPER_WHEN_LIBRARY_BROKEN"
        with app.app_context():
            _index(PID, "paper_segment", "seg-b", f"triage accuracy {sentinel}", paper_id="PB")
            _set_screening("PB", "included")

            import app.services.evidence_index_service as svc

            def boom(*_a, **_k):
                raise RuntimeError("library unavailable")

            monkeypatch.setattr(svc, "_resolve_data_root", boom)
            assert sentinel not in " ".join(str(r) for r in _search("writing"))
