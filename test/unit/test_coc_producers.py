# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_coc_producers.py
# 子系統定位:
#   COC 第二刀（P0 Literature / P0 PAQ）的驗收證據。與 test_coc_provider_request.py
#   同一種形式：斷言對象是 dispatch_task 實際收到的字串，不是中間層回傳值。
# 主要責任:
#   1. PAQ：作者真正寫出來的 taxonomy_v1 / cube_v1 / PaqSurvey 必須到得了 provider。
#      （舊 reader 只找 taxonomy_manual_update.json，全站 0 個，等於整條 PAQ 斷鏈。）
#   2. Literature：人工 included 的文獻與**採用理由**到得了 provider；
#      excluded 一個字都不得出現；未經 screening 的 search_results 旁路必須封死。
# 明確不負責:
#   - 不驗證 provider 真的回話（dispatch_task 被攔截）。
#   - 不驗證 screening UI／遷移腳本本身（各有其測試）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只在 tmp_path（app fixture 的 sqlite URI 所在目錄即 data root）；不碰真實 data/。
# 不變量:
#   - 每一條「X 不在場」都配一個同資料、同路徑、只換一個變數的「X 在場」對照組。
#     否則 X 根本沒被產生時，否定斷言恆為真 —— 這個 repo 已經空過一次（HANDOFF §3.9）。
#   - context_text 一律給非空字串：Task8Drafter 的 grounding guard 會在無依據時
#     直接回 system_guard 而**完全不呼叫 dispatch_task**，那會讓所有否定斷言
#     因為「根本沒送出 request」而假綠。
# 相關 NOTE:
#   NOTE-013（只接受 included）、NOTE-018（PAQ canonical reader）、
#   NOTE-019（未經 screening 的搜尋結果不得進入寫作路徑）。
# 驗證:
#   python -m pytest test/unit/test_coc_producers.py -q
# ---------------------------------------------------------------------------
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "COCPRD-p"
BASE_PID = "COCPRD"
SECTION = "introduction"

# 指令與所有哨兵共用 "triage" 詞元：這個 repo 的檢索是 lexical 的，
# 沒有共同詞元時檢索一筆都不回，否定斷言會整組空過（HANDOFF §3.9 的教訓）。
USER_PROMPT = "請續寫 Introduction，聚焦 triage accuracy"

S_PAQ_LABEL = "SENTINEL PAQ AXIS LABEL triage"
S_PAQ_TAG = "SENTINEL PAQ AXIS TAG triage"
S_PAQ_CUBE = "SENTINEL PAQ CUBE HOVER triage"
S_PAQ_SURVEY = "SENTINEL PAQ SURVEY DB LABEL triage"

S_LIT_INCLUDED = "SENTINEL INCLUDED PAPER TITLE triage"
S_LIT_RATIONALE = "SENTINEL WHY THIS PAPER WAS INCLUDED triage"
S_LIT_EXCLUDED = "SENTINEL EXCLUDED PAPER TITLE triage"
S_LIT_EXCLUDED_NOTE = "SENTINEL WHY THIS PAPER WAS EXCLUDED triage"
S_LIT_CANDIDATE = "SENTINEL CANDIDATE NOT YET SCREENED triage"

S_SEARCH_KEYWORD = "SENTINEL UNSCREENED SEARCH KEYWORD triage"
S_SEARCH_APA = "SENTINEL UNSCREENED APA CITATION triage"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'prd.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'prd_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


# --------------------------------------------------------------------------
# seeding helpers
# --------------------------------------------------------------------------

def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _seed_paq_files() -> None:
    """
    寫入作者實際會產生的兩個檔案。

    路徑與欄位都照真實資料抄（`data/A22E9W/paq/paq/`）：
    taxonomy_v1.json = {axis_labels{x,y,z}, axis_tags{x,y,z:[...]}, summary}
    cube_v1.json     = {voxels:[{x,y,z,val,hover}], summary}
    """
    from app.core_pro.manuscript.manuscript_io import _get_data_root

    paq_dir = os.path.join(_get_data_root(), BASE_PID, "paq", "paq")
    _write_json(os.path.join(paq_dir, "taxonomy_v1.json"), {
        "axis_labels": {"x": S_PAQ_LABEL, "y": "Y axis", "z": "Z axis"},
        "axis_tags": {"x": [S_PAQ_TAG], "y": ["y1"], "z": ["z1"]},
        "summary": "taxonomy summary",
    })
    _write_json(os.path.join(paq_dir, "cube_v1.json"), {
        "voxels": [{"x": 0, "y": 0, "z": 0, "val": 3, "hover": S_PAQ_CUBE}],
        "summary": "cube summary",
    })


def _seed_search_results() -> None:
    """
    未經任何 screening 的搜尋結果，放在舊 reader 真的會去讀的位置。

    真實資料的 search_results.json 落在 `<pid>-p/`，而舊 reader 讀的是
    `<base_pid>/` —— 也就是這條旁路目前**剛好**因為路徑不符而沒有觸發。
    這裡刻意種在 reader 讀得到的位置，讓旁路確實成立，
    否則「旁路已封死」的斷言會因為路徑不符而假綠。
    """
    from app.core_pro.manuscript.manuscript_io import _get_data_root

    _write_json(os.path.join(_get_data_root(), BASE_PID, "search_results.json"), {
        "timestamp": 0.0,
        "date_str": "2026-08-11",
        "results": {
            "keywords": [S_SEARCH_KEYWORD],
            "apa_citations": [S_SEARCH_APA],
            "papers": [{"title": S_LIT_CANDIDATE, "doi": "10.1/CAND", "year": 2024}],
            "reasoning": "raw search reasoning",
        },
    })


def _seed_library(*, included: bool, doi: str = "10.1/SENT") -> None:
    """
    同一批資料、同一條路徑，只切換 screening_status —— 這是否定斷言的對照組。

    included=True  → 應到得了 provider（含採用理由 screening_note）
    included=False → excluded，一個字都不得出現
    """
    from app.services.evidence_index_service import _resolve_data_root
    from app.services.literature_library import LiteratureLibrary

    lib = LiteratureLibrary(_resolve_data_root())
    title = S_LIT_INCLUDED if included else S_LIT_EXCLUDED
    note = S_LIT_RATIONALE if included else S_LIT_EXCLUDED_NOTE
    lib.merge_candidates(PID, [{"title": title, "doi": doi, "year": 2025}])
    key = list(lib.load(PID)["entries"].keys())[-1]
    # actor 是必填（NOTE-020）：沒有主體的納入／排除決策無法歸屬。
    lib.update_entry(PID, key, {
        "screening_status": "included" if included else "excluded",
        "screening_note": note,
    }, actor="pi@example.org", batch_id="TEST-BATCH")


def _capture_prompt(monkeypatch) -> str:
    """
    跑一次真實生成路徑，回傳 dispatch_task 實際收到的所有 prompt 串接結果。

    context_text 給非空字串是必要的：grounding guard 會在「只有題目」時直接
    回 system_guard 且**不呼叫 dispatch_task**，那會讓否定斷言因為沒有 request
    而假綠（本檔不變量第 2 條）。
    """
    from app.core_pro.manuscript.coc_bundle import build_coc_bundle
    from app.llm_service.matching_tasks import task_8drafter
    from app.llm_service.matching_tasks.task_8drafter import Task8Drafter

    captured: list[str] = []

    def fake_dispatch(task_id, prompt, **kwargs):
        captured.append(str(prompt))
        return {"ok": True, "text": "drafted."}

    monkeypatch.setattr(task_8drafter, "dispatch_task", fake_dispatch)

    bundle = build_coc_bundle(
        PID, SECTION,
        readable_sections=[SECTION],
        can_read_current_section=True,
        include_paper=True,
        formal_pid=PID,
    )

    drafter = Task8Drafter()
    monkeypatch.setattr(drafter, "_detect_intent", lambda prompt, **_k: "draft")
    # coc_items / coc_notes 要照 handler 的方式往下傳（manuscript_routes 約 L1526）。
    # 少了它們，audit 的觸發條件不成立、provenance 也不會落地 ——
    # 那會讓這個測試「證明了 prompt 有內容，卻沒證明 audit 記得住來源」。
    drafter.process_request(
        USER_PROMPT,
        context_text=bundle["text"] or "作者目前的草稿：triage accuracy 尚待補述。",
        pid=PID, title="COC Producers", section=SECTION,
        coc_items=bundle.get("items") or [],
        coc_notes=bundle.get("notes") or [],
    )
    return "\n".join(captured)


# --------------------------------------------------------------------------
# P0 PAQ
# --------------------------------------------------------------------------

class TestPaqReachesProvider:
    """
    NOTE(NOTE-018): writer 寫 taxonomy_v1 / cube_v1 / PaqSurvey，
    reader 卻只找 taxonomy_manual_update.json（全站 0 個）。
    """

    def test_taxonomy_and_cube_files_reach_provider(self, app, monkeypatch):
        """作者跑完 PAQ Task1/Task2 後，labels / tags / voxels 必須進得了 request。"""
        with app.app_context():
            _seed_paq_files()
            prompt = _capture_prompt(monkeypatch)

        assert prompt, "沒有攔截到任何 provider request"
        assert S_PAQ_LABEL in prompt, "PAQ axis_labels 沒有進入 provider request"
        assert S_PAQ_TAG in prompt, "PAQ axis_tags 沒有進入 provider request"
        assert S_PAQ_CUBE in prompt, "PAQ cube voxels 沒有進入 provider request"

    def test_manual_update_still_wins_when_present(self, app, monkeypatch):
        """
        對照組兼相容性守衛：舊檔名仍存在時必須優先（那是作者的人工修改），
        且不得因為改用 canonical reader 而讀不到。
        """
        from app.core_pro.manuscript.manuscript_io import _get_data_root

        with app.app_context():
            _seed_paq_files()
            _write_json(
                os.path.join(_get_data_root(), BASE_PID, "paq", "taxonomy_manual_update.json"),
                {"axis_labels": {"x": "SENTINEL MANUAL OVERRIDE triage"},
                 "axis_tags": {"x": ["manual tag triage"]}},
            )
            prompt = _capture_prompt(monkeypatch)

        assert "SENTINEL MANUAL OVERRIDE triage" in prompt, \
            "人工修改的 taxonomy 沒有優先於 AI 產生的版本"

    def test_paq_survey_db_reaches_provider_without_files(self, app, monkeypatch):
        """
        沒有檔案、只有 PaqSurvey 資料表時也要讀得到 ——
        DB 是兩條 writer 路徑（手動與 AI）唯一都會寫的地方。
        """
        from app import db
        from app.models import PaqSurvey, Project

        with app.app_context():
            project = Project(project_id=PID, name="COC Producers")
            db.session.add(project)
            db.session.flush()
            db.session.add(PaqSurvey(
                project_ref_id=project.id,
                axis_labels={"x": S_PAQ_SURVEY},
                axis_tags={"x": ["survey tag triage"]},
                cube_data=[{"x": 0, "y": 0, "z": 0, "val": 1, "hover": "survey voxel triage"}],
            ))
            db.session.commit()
            prompt = _capture_prompt(monkeypatch)

        assert S_PAQ_SURVEY in prompt, "PaqSurvey 資料表的 taxonomy 沒有進入 provider request"


# --------------------------------------------------------------------------
# P0 Literature
# --------------------------------------------------------------------------

class TestLiteratureScreeningGovernsProvider:
    """
    NOTE(NOTE-013) / NOTE(NOTE-019)。
    included / excluded 兩個測試共用同一份 seeding 程式碼，只差一個布林值 ——
    這樣「excluded 不在場」才有同資料同路徑的對照。
    """

    def test_included_paper_and_rationale_reach_provider(self, app, monkeypatch):
        """人工 included 的文獻＋作者寫的採用理由，都要到得了 provider。"""
        with app.app_context():
            _seed_library(included=True)
            prompt = _capture_prompt(monkeypatch)

        assert prompt, "沒有攔截到任何 provider request"
        assert S_LIT_INCLUDED in prompt, "人工 included 的文獻沒有進入 provider request"
        assert S_LIT_RATIONALE in prompt, "作者的採用理由（screening_note）沒有傳遞"

    def test_excluded_paper_and_its_note_never_reach_provider(self, app, monkeypatch):
        """
        對照組是上面那一條：同一份 seeding、同一條組裝路徑，只把
        screening_status 從 included 換成 excluded。
        """
        with app.app_context():
            _seed_library(included=False)
            prompt = _capture_prompt(monkeypatch)

        assert prompt, "沒有攔截到任何 provider request"
        assert S_LIT_EXCLUDED not in prompt, "被排除的文獻標題出現在正式寫作 request"
        assert S_LIT_EXCLUDED_NOTE not in prompt, "被排除文獻的註記也不得出現"


class TestUnscreenedSearchResultsBypassIsSealed:
    """
    NOTE(NOTE-019): `_load_upstream_context` 的 `[Literature Hints]` 直接讀
    search_results.json 的 keywords / apa_citations，**完全不經過 screening**。
    那條路徑繞過作者的納入判斷，等於 NOTE-013 在這一側沒有效力。
    """

    def test_raw_keywords_and_apa_do_not_reach_provider(self, app, monkeypatch):
        with app.app_context():
            _seed_search_results()
            prompt = _capture_prompt(monkeypatch)

        assert prompt, "沒有攔截到任何 provider request"
        assert S_SEARCH_KEYWORD not in prompt, \
            "未經 screening 的搜尋關鍵字經由 [Literature Hints] 旁路進入 request"
        assert S_SEARCH_APA not in prompt, \
            "未經 screening 的 APA citation 經由 [Literature Hints] 旁路進入 request"
        assert S_LIT_CANDIDATE not in prompt, \
            "搜尋結果裡的論文標題未經人工納入即進入寫作 request"

    def test_audit_records_producer_provenance_with_fingerprint(self, app, monkeypatch):
        """
        provider 收到內容只是一半；audit 要能反查「當時讀到的是哪一版」。

        斷言對象是真的落地的 audit sidecar 檔案，不是中間層回傳值：
        source_type / source_id 要在，**fingerprint 不得是 None** ——
        `coc_source_items` 曾經把它寫死成 None，audit 看起來有 provenance
        但什麼都查不回來。
        """
        import glob as _glob

        from app.core_pro.manuscript.manuscript_io import _get_data_root

        with app.app_context():
            _seed_paq_files()
            _seed_library(included=True)
            # 再種一篇 excluded：被擋下的**數量**必須留痕，作者才知道
            # 「還有東西沒被採用」。只有 included 時不會產生任何統計 note，
            # 那樣的斷言驗不到「被擋下的有沒有被記錄」。
            _seed_library(included=False, doi="10.1/SENT-EXCLUDED")
            _capture_prompt(monkeypatch)
            audit_dir = os.path.join(_get_data_root(), "manuscript_context_audit", PID, SECTION)
            files = sorted(_glob.glob(os.path.join(audit_dir, "*.json")))

        assert files, f"沒有寫出任何 audit sidecar（{audit_dir}）"
        with open(files[-1], "r", encoding="utf-8") as f:
            audit = json.load(f)

        # **不要把 coc_sources 收斂成 {source_type: source} 的 dict。**
        # 同一個 source_type 會有多筆（N 篇 included 文獻），dict 只會留下最後一筆，
        # 前面那些的缺陷會被後面覆蓋掉 —— 本檔第一版就是這樣把一筆
        # 沒有 fingerprint 的合成彙總來源藏了起來。逐筆檢查才看得到。
        sources = list(audit.get("coc_sources") or [])
        by_type: dict[str, list[dict]] = {}
        for s in sources:
            by_type.setdefault(str(s.get("source_type")), []).append(s)

        assert "paq_note" in by_type, f"audit 沒有記錄 PAQ 來源：{list(by_type)}"
        assert "literature_included" in by_type, \
            f"audit 沒有記錄 included 文獻來源：{list(by_type)}"

        missing = [s for s in sources if not s.get("fingerprint")]
        assert not missing, (
            "有來源沒有 fingerprint，audit 無法反查來源版本："
            f"{[(m.get('source_type'), m.get('source_id')) for m in missing]}"
        )

        # 被擋下的數量要留痕，作者才知道「有東西沒被採用」。
        notes_blob = " ".join(str(n) for n in audit.get("coc_notes") or [])
        assert "literature" in notes_blob, f"文獻的排除統計沒有進 audit notes：{notes_blob}"
        # 但**標題與註記不得外流**：留痕的是數量，不是被排除的內容本身。
        assert S_LIT_EXCLUDED not in notes_blob, "被排除文獻的標題經由 audit notes 外流"
        assert S_LIT_EXCLUDED_NOTE not in notes_blob, "被排除文獻的註記經由 audit notes 外流"

    def test_multiple_included_papers_produce_one_item_each(self, app, monkeypatch):
        """
        N 篇 included 文獻 = N 筆來源，不多不少，而且每一筆都要有 fingerprint。

        回歸守衛：舊實作在一個 block 只放得下一筆 item，於是多來源時另外合成
        一筆 `literature:Nitems` 彙總 —— 那筆**沒有 fingerprint、對應不到任何
        真實文獻**，等於在 audit 裡憑空多一筆查不回去的證據。
        同一個缺陷還有第二面：`_strip_coc_section` 只認 `b["item"]` 單數，
        去重一觸發就把逐篇來源整批丟掉，只留下那筆假的。
        """
        from app.core_pro.manuscript.coc_bundle import build_coc_bundle
        from app.core_pro.manuscript.manuscript_routes import _strip_coc_section
        from app.services.evidence_index_service import _resolve_data_root
        from app.services.literature_library import LiteratureLibrary

        with app.app_context():
            lib = LiteratureLibrary(_resolve_data_root())
            for n in (1, 2, 3):
                lib.merge_candidates(PID, [{"title": f"Paper {n} triage", "doi": f"10.1/M{n}"}])
            for key in list(lib.load(PID)["entries"]):
                lib.update_entry(PID, key, {"screening_status": "included"},
                                 actor="pi@example.org")

            bundle = build_coc_bundle(
                PID, SECTION,
                readable_sections=[SECTION],
                can_read_current_section=True,
                include_paper=True,
                formal_pid=PID,
            )
            # 去重（前端未存草稿取代伺服器版本）之後，逐篇來源必須still在。
            stripped = _strip_coc_section(bundle, SECTION)

        for label, items in (("bundle", bundle["items"]), ("去重後", stripped["items"])):
            lit = [i for i in items if i.get("source_type") == "literature_included"]
            assert len(lit) == 3, f"{label}: 應有 3 筆 included 文獻來源，實際 {len(lit)}"
            no_fp = [i.get("source_id") for i in lit if not i.get("fingerprint")]
            assert not no_fp, f"{label}: 有來源沒有 fingerprint（合成的假來源？）：{no_fp}"
            ids = [i.get("source_id") for i in lit]
            assert len(set(ids)) == 3, f"{label}: source_id 不是逐篇唯一：{ids}"

    def test_same_paper_reaches_provider_once_a_human_includes_it(self, app, monkeypatch):
        """
        *** 上一條的對照組，不可省略。 ***

        同一批搜尋結果仍在原處；差別只有「有沒有人把它標成 included」。
        少了這一條，「旁路已封死」可能只是因為文獻通道整條都沒接通。
        """
        with app.app_context():
            _seed_search_results()
            _seed_library(included=True)
            prompt = _capture_prompt(monkeypatch)

        assert S_LIT_INCLUDED in prompt, \
            "人工 included 之後仍到不了 provider —— 文獻通道根本沒接通"
        assert S_SEARCH_KEYWORD not in prompt, \
            "旁路仍然開著（included 與否都不該讓原始關鍵字進入）"
