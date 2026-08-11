# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_literature_screening_gate.py
# 子系統定位:
#   NOTE-020 的驗收：文獻納入／排除是 PI/Co-PI 的學術判斷，
#   必須有角色門檻，而且每一筆決策都要留下「誰、什麼時候、哪一批」。
# 主要責任:
#   1. 沒有 actor 就不准改 screening_status（結構上擋死，不是靠呼叫端自律）。
#   2. 有 actor 時決策 provenance 三個欄位要落地。
#   3. merge_candidates 永遠只產生 candidate，不得自動 included。
# 明確不負責:
#   - 不驗 HTTP 層的角色判定（require_workspace_role 在 AUTH_MODE!=session 時
#     一律放行，那是全站既有慣例，另有 test_mentor_scope 等測試涵蓋）。
#     這裡驗的是**即使繞過 HTTP 層，service 層仍然擋得住沒有主體的決策**。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   只在 tmp_path。
# 不變量:
#   - 「擋下來」的斷言都配一個「同一個操作帶了 actor 就會成功」的對照組。
# 相關 NOTE:
#   NOTE-013（三分狀態）、NOTE-020（screening 決策需要 actor 與角色門檻）。
# 驗證:
#   python -m pytest test/unit/test_literature_screening_gate.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "LITGATE-p"


@pytest.fixture()
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCK_ROOT", str(tmp_path / "locks"))
    (tmp_path / "locks").mkdir(exist_ok=True)
    from app.services.literature_library import LiteratureLibrary

    lib = LiteratureLibrary(str(tmp_path))
    lib.merge_candidates(PID, [{"title": "A triage paper", "doi": "10.1/GATE"}])
    return lib


def _only_entry_id(lib) -> str:
    return list(lib.load(PID)["entries"].keys())[0]


class TestSearchResultsNeverBecomeIncluded:
    def test_merge_candidates_always_lands_as_candidate(self, library):
        """
        遷移既有論文／搜尋結果時只能產生 candidate。

        擁有者的裁示：批次標成 included 等於偽造「作者已審核」。
        """
        entry = list(library.load(PID)["entries"].values())[0]
        assert entry["screening_status"] == "candidate"
        assert entry.get("screening_decided_by", "") == "", \
            "還沒有人做決定，就不該有決策者"

    def test_merge_never_overwrites_a_human_decision(self, library):
        """重跑遷移不得把作者已經做過的判斷洗掉。"""
        entry_id = _only_entry_id(library)
        library.update_entry(PID, entry_id, {"screening_status": "excluded"},
                             actor="pi@example.org")
        library.merge_candidates(PID, [{"title": "A triage paper", "doi": "10.1/GATE"}])
        assert library.load(PID)["entries"][entry_id]["screening_status"] == "excluded", \
            "重跑遷移把作者的排除決定覆蓋掉了"


class TestScreeningRequiresAnActor:
    def test_screening_change_without_actor_is_rejected(self, library):
        """沒有主體的決策紀錄事後無法歸屬，等於沒有紀錄 —— 直接擋掉。"""
        entry_id = _only_entry_id(library)
        with pytest.raises(ValueError, match="actor"):
            library.update_entry(PID, entry_id, {"screening_status": "included"})

        assert library.load(PID)["entries"][entry_id]["screening_status"] == "candidate", \
            "被拒絕的更新仍然改到了狀態"

    def test_screening_change_with_actor_records_provenance(self, library):
        """
        *** 上一條的對照組。 ***
        同一個操作、只多帶一個 actor 就必須成功，否則「擋下來」可能只是
        因為這條路徑根本不能用。
        """
        entry_id = _only_entry_id(library)
        entry = library.update_entry(
            PID, entry_id, {"screening_status": "included",
                            "screening_note": "與本文 triage 主題直接相關"},
            actor="pi@example.org", batch_id="BATCH-2026-08-11",
        )

        assert entry["screening_status"] == "included"
        assert entry["screening_decided_by"] == "pi@example.org"
        assert entry["screening_batch_id"] == "BATCH-2026-08-11"
        assert entry["screening_decided_at"], "決策時間沒有落地"

    def test_non_screening_updates_do_not_need_an_actor(self, library):
        """
        門檻只套在 screening —— 補 metadata、寫閱讀筆記不該被擋。
        擋過頭會讓一般編輯連整理書目都不能做。
        """
        entry_id = _only_entry_id(library)
        entry = library.update_entry(PID, entry_id, {
            "reading_status": "read",
            "reading_note": "第 3 節的 triage 流程可引用",
            "venue": "JAMIA",
        })
        assert entry["reading_status"] == "read"
        assert entry["venue"] == "JAMIA"

    def test_actor_cannot_be_forged_through_the_patch(self, library):
        """
        決策者不得由 patch 帶入：開放的話任何人都能宣稱某個 PI 做過這個決定。
        """
        entry_id = _only_entry_id(library)
        entry = library.update_entry(
            PID, entry_id,
            {"screening_status": "included", "screening_decided_by": "someone-else@evil"},
            actor="pi@example.org",
        )
        assert entry["screening_decided_by"] == "pi@example.org", \
            "patch 裡的 screening_decided_by 覆蓋了真實 actor"
