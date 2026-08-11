# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_coc_provider_request.py
# 子系統定位:
#   COC 第一刀的驗收證據：斷言的對象是「真正送給 provider 的那個字串」，
#   不是中間層的回傳值。
# 主要責任:
#   擁有者指定的四個強證據，全部在 dispatch_task 實際收到的 prompt 上檢查：
#     1. 前一輪 2A 的人類哨兵出現在下一輪 request。
#     2. 真實 S.Ver 的內容取代固定 0.1（V0.2 在場、V0.1 不在場）。
#     3. excluded／unknown 文獻哨兵不存在於正式寫作 request。
#     4. 另一個專案與 stale ContextChain 的哨兵不存在。
# 明確不負責:
#   - 不驗證 provider 真的回話（dispatch_task 被攔截，不打外部 API）。
#   - 不驗證 socket handler 的執行緒派送（_CHAT_EXECUTOR 非同步，另行以瀏覽器驗收）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只在 tmp_path 下建立版本檔、chat 歷史與 library；不碰真實 data/。
# 不變量:
#   - 這些是「在場／不在場」的證據，不是實作字串比對；換組裝方式也應通過。
#   - 攔截點固定在 task_8drafter 匯入的 dispatch_task —— 那是離 provider 最近、
#     且仍拿得到完整 prompt 的位置。
# 相關 NOTE:
#   NOTE-012（伺服器端組裝）、NOTE-013（只接受 included）、NOTE-014（stale fail-closed）。
# 驗證:
#   python -m pytest test/unit/test_coc_provider_request.py -q
# ---------------------------------------------------------------------------
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "COCE2E-p"
OTHER_PID = "COCOTH-p"
SECTION = "introduction"

S_HISTORY = "SENTINEL_2A_HUMAN_KEEP_PASSIVE_VOICE"
S_V02 = "SENTINEL_LATEST_VERSION_V02_BODY"
S_V01 = "SENTINEL_OLD_VERSION_V01_BODY"
S_EXCLUDED = "SENTINEL_EXCLUDED_PAPER_TEXT"
S_UNKNOWN = "SENTINEL_UNREVIEWED_PAPER_TEXT"
S_OTHER_PROJECT = "SENTINEL_OTHER_PROJECT_TEXT"

# 檢索是 lexical 的：查詢與證據文字必須有共同詞元，否則什麼都不會回。
# 所有哨兵證據都帶 "triage"，指令也帶，讓「不在場」的斷言真的有被檢索過。
USER_PROMPT = "請依前面的討論續寫 Introduction，聚焦 triage accuracy"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'e2e.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'e2e_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _seed(app):
    """建立哨兵資料：兩個版本、2A 歷史、excluded/unknown 論文、另一個專案。"""
    from app import db
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO, _get_data_root
    from app.services.evidence_index_service import _resolve_data_root, upsert_evidence_segment
    from app.services.literature_library import LiteratureLibrary

    # 兩個版本：V0.1 舊、V0.2 最新
    ManuscriptIO.save_block_version(pid=PID, section=SECTION, title="T", content=f"<p>{S_V01}</p>")
    ManuscriptIO.save_block_version(pid=PID, section=SECTION, title="T",
                                    content=f"<p>{S_V02}</p>", from_ver="0.1")

    # 2A 歷史
    chat_dir = os.path.join(_get_data_root(), PID, "manuscript", "chat")
    os.makedirs(chat_dir, exist_ok=True)
    with open(os.path.join(chat_dir, f"chat_{SECTION}.json"), "w", encoding="utf-8") as f:
        json.dump([{"role": "user", "content": S_HISTORY, "type": "text"}], f, ensure_ascii=False)

    # 文獻：一篇 excluded、一篇從未篩選
    upsert_evidence_segment(project_id=PID, source_type="paper_segment", source_id="ex",
                            text=f"triage {S_EXCLUDED}", paper_id="PEX", title="Excluded paper")
    upsert_evidence_segment(project_id=PID, source_type="paper_segment", source_id="unk",
                            text=f"triage {S_UNKNOWN}", paper_id="PUNK", title="Unreviewed paper")
    # 另一個專案的內容
    upsert_evidence_segment(project_id=OTHER_PID, source_type="paper_segment", source_id="oth",
                            text=f"triage {S_OTHER_PROJECT}", paper_id="POTH", title="Other project")
    db.session.commit()

    lib = LiteratureLibrary(_resolve_data_root())
    lib.merge_candidates(PID, [{"title": "Excluded paper", "doi": "10.1/PEX"}])
    key = list(lib.load(PID)["entries"].keys())[-1]
    lib.update_entry(PID, key, {"paper_id": "PEX", "screening_status": "excluded"},
                     actor="pi@example.org")


def _capture_provider_prompt(app, monkeypatch):
    """跑一次生成，回傳 dispatch_task 實際收到的 prompt。"""
    from app.core_pro.manuscript.coc_bundle import build_coc_bundle
    from app.llm_service.matching_tasks import task_8drafter
    from app.llm_service.matching_tasks.task_8drafter import Task8Drafter

    captured = {}

    def fake_dispatch(task_id, prompt, **kwargs):
        captured.setdefault("prompts", []).append(str(prompt))
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
    # 指令必須與證據文字有共同詞元（triage），否則 lexical 檢索一筆都不會回，
    # 「排除的論文不在場」就會因為根本沒檢索而空過 —— 見 test_retrieval_is_live。
    drafter.process_request(
        USER_PROMPT,
        context_text=bundle["text"],
        pid=PID, title="COC E2E", section=SECTION,
    )
    return "\n".join(captured.get("prompts", [])), bundle


class TestRetrievalIsActuallyLive:
    def test_unknown_paper_is_retrievable_in_any_scope(self, app):
        """
        自我驗證護欄。

        「excluded／unknown 不在 request 裡」這種否定斷言，在檢索根本沒回東西時
        會空過。本輪就踩到：指令與證據沒有共同詞元，any scope 也拿到 0 筆，
        於是把 writing scope 的過濾誤判成有效。
        這個測試先證明「同一批資料在 any scope 撈得到」，
        下面的否定斷言才有意義。
        """
        from app.services.evidence_index_service import search_evidence

        with app.app_context():
            _seed(app)
            any_hits = search_evidence(project_id=PID, query=USER_PROMPT, top_k=20,
                                       inclusion_scope="any")
            writing_hits = search_evidence(project_id=PID, query=USER_PROMPT, top_k=20,
                                           inclusion_scope="writing")

        any_blob = " ".join(str(h) for h in any_hits)
        writing_blob = " ".join(str(h) for h in writing_hits)

        assert S_UNKNOWN in any_blob, "檢索在 any scope 都撈不到，否定斷言會空過"
        assert S_UNKNOWN not in writing_blob, "writing scope 沒有擋掉未篩選論文"
        assert S_EXCLUDED not in any_blob, "excluded 應該在任何 scope 都被硬擋"


class TestProviderRequestCarriesHumanContext:
    def test_four_acceptance_sentinels(self, app, monkeypatch):
        with app.app_context():
            _seed(app)
            prompt, bundle = _capture_provider_prompt(app, monkeypatch)

        assert prompt, "沒有攔截到任何 provider request"

        # ① 前一輪 2A 的人類指令必須在場
        assert S_HISTORY in prompt, "2A 歷史沒有進入真正送出的 request"

        # ② 真實 S.Ver：最新版在場、舊版不在場
        assert bundle["s_ver"] == "0.2"
        assert S_V02 in prompt, "最新版內容沒有進 request"
        assert S_V01 not in prompt, "讀到了 V0.1 —— 又退回前端寫死的 0.1"

        # ③ excluded 與未篩選的文獻都不得作為寫作依據
        assert S_EXCLUDED not in prompt, "被排除的論文出現在正式寫作 request"
        assert S_UNKNOWN not in prompt, "未篩選的論文被當成寫作依據"

        # ④ 跨專案資料不得外洩
        assert S_OTHER_PROJECT not in prompt, "另一個專案的內容出現在 request"


class TestStaleChainDoesNotReachProvider:
    """
    NOTE-014 的驗收測試。

    原本只測 _collect_context_chain_candidates 的回傳值（中間層），
    review 判定 Contradicted：那個斷言證明不了「stale 內容沒有進真正送出的 request」。

    改寫重點：
    1. 在 tmp_path 植入 context_chain.json（含 stale L2/L3 與 fresh L1）。
    2. 讓 Evidence Index 無命中（不種任何 segment），迫使 retrieve_paragraph_context
       走 fallback_context_chain 路徑，讓 stale 擋截邏輯有機會被觸及。
    3. 攔截 dispatch_task —— 離 provider 最近的位置，確保 stale 哨兵不在最終 prompt。
    4. 對照組：把同一份 chain 的 L2/L3 改為 stale=False，確認哨兵這次進得了 prompt，
       讓否定斷言有意義。
    """

    # 哨兵字串故意用空格分隔，確保 lexical tokenizer 能把 "triage" 切出來
    # 作為獨立 token，避免底線讓整個字串被視為單一 token 而無法命中查詢。
    S_STALE_SUMMARY = "SENTINEL STALE SUMMARY triage"
    S_STALE_BRIEF = "SENTINEL STALE BRIEF triage"
    S_FRESH_L1 = "SENTINEL FRESH L1 triage"

    def _make_chain(self, tmp_path, *, stale: bool) -> None:
        """在 tmp_path 寫入一個 context_chain.json，stale 控制 L2/L3 的 stale 旗標。"""
        import json as _json
        chain = {
            "project_id": PID,
            "schema_version": 1,
            "layers": {
                "L1": {
                    "stale": False,
                    "claims": [
                        {
                            "claim_id": "c1",
                            "title": self.S_FRESH_L1,
                            "text": self.S_FRESH_L1,
                        }
                    ],
                },
                "L2": {
                    "stale": stale,
                    "summary_packets": [
                        {
                            "packet_id": "p1",
                            "text": self.S_STALE_SUMMARY,
                        }
                    ],
                },
                "L3": {
                    "stale": stale,
                    "project_brief": self.S_STALE_BRIEF,
                },
            },
        }
        pid_dir = tmp_path / PID
        pid_dir.mkdir(parents=True, exist_ok=True)
        (pid_dir / "context_chain.json").write_text(
            _json.dumps(chain, ensure_ascii=False), encoding="utf-8"
        )

    def _run_with_chain(self, app, monkeypatch, tmp_path, *, stale: bool) -> str:
        """植入 chain 後跑一次生成，回傳 dispatch_task 收到的 prompt。

        設計決策：
        - 在 tmp_path 植入 context_chain.json，並讓 _fallback_context_chain_search
          使用 tmp_path 的 ContextChainService，確保讀到的是測試資料。
        - 用 monkeypatch 讓 _load_upstream_context 回傳一個最小研究筆記，
          讓 grounding guard（has_grounding check）通過，測試才能走到 dispatch_task。
          這個 patch 不影響「stale 擋截有沒有效」的斷言本身：
          stale 擋截發生在 fallback_context_chain_search，在 upstream context 之後。
        - 不種任何 EvidenceSegment，迫使走 fallback_context_chain 路徑。
        """
        from app.llm_service.matching_tasks import task_8drafter
        from app.llm_service.matching_tasks.task_8drafter import Task8Drafter
        from app.services.context_chain_service import ContextChainService
        from app.core_pro.manuscript import manuscript_ruling as mruling

        self._make_chain(tmp_path, stale=stale)

        captured: list[str] = []

        def fake_dispatch(task_id, prompt, **kwargs):
            captured.append(str(prompt))
            return {"ok": True, "text": "drafted."}

        monkeypatch.setattr(task_8drafter, "dispatch_task", fake_dispatch)

        # _load_upstream_context 讀真實 data/，測試環境沒有筆記，
        # 導致 source_manifest 只有 context_chain_item（如果 fallback 有回），
        # 但如果 fallback 回來空，grounding guard 會直接擋住。
        # 最小化 mock：讓 upstream 回傳一行研究筆記讓 grounding check 通過，
        # 不影響 stale 的斷言（stale 擋截在 retrieval 層，已與 upstream 無關）。
        monkeypatch.setattr(
            mruling.ManuscriptRuling,
            "_load_upstream_context",
            staticmethod(lambda pid: "[Study Notes]\nbase_note_for_grounding_check"),
        )

        # ContextChainService 要讀到我們種的 chain。
        chain_svc = ContextChainService(data_root=str(tmp_path))

        # retrieve_paragraph_context 會在 evidence search 失敗（無 segment）後，
        # 呼叫 _fallback_context_chain_search。
        # 我們攔截 fallback，把 context_chain_service 換成指向 tmp_path 的版本。
        from app.core_pro.manuscript import context_inject as ci
        original_fallback = ci._fallback_context_chain_search

        def patched_fallback(**kwargs):
            # 注入指向 tmp_path 的 ContextChainService，讓 load_chain 讀到測試資料。
            kwargs["context_chain_service"] = chain_svc
            return original_fallback(**kwargs)

        monkeypatch.setattr(ci, "_fallback_context_chain_search", patched_fallback)

        drafter = Task8Drafter()
        monkeypatch.setattr(drafter, "_detect_intent", lambda prompt, **_k: "draft")

        with app.app_context():
            # 不種任何 EvidenceSegment，迫使走 fallback_context_chain 路徑。
            # 指令帶 "triage" 讓 fallback 的 lexical scoring 有共同詞元，
            # L1/L2 的哨兵也帶 triage，確保 non-stale 條件下能被 scoring 命中。
            drafter.process_request(
                USER_PROMPT,
                context_text="",
                pid=PID,
                title="COC Stale Test",
                section=SECTION,
            )

        return "\n".join(captured)

    def test_stale_layer_sentinel_absent(self, app, monkeypatch, tmp_path):
        """
        主斷言：stale L2/L3 的哨兵不進 provider request（NOTE-014）。

        同時斷言 L1（永遠不受 skip_stale 影響）的哨兵在場，
        確認不是「全部都沒進去」而是「只有 stale 的被擋」。
        """
        prompt = self._run_with_chain(app, monkeypatch, tmp_path, stale=True)

        assert prompt, "沒有攔截到任何 provider request"
        assert self.S_STALE_SUMMARY not in prompt, \
            "stale L2 的哨兵出現在 provider request（NOTE-014 失效）"
        assert self.S_STALE_BRIEF not in prompt, \
            "stale L3 的哨兵出現在 provider request（NOTE-014 失效）"
        assert self.S_FRESH_L1 in prompt, \
            "L1（人類剛改好的層）被一起擋掉了 —— 過度擋截"

    def test_fresh_layer_sentinel_present(self, app, monkeypatch, tmp_path):
        """
        *** 這是 test_stale_layer_sentinel_absent 的對照組，不可省略。 ***

        若 stale=False，同一批哨兵字串必須進得了 provider request，
        否則「stale 哨兵不在場」可能只是因為 fallback 路徑根本沒被走到，
        而不是 stale 擋截有效。X 根本沒被產生時，「X 不在場」恆為真。
        """
        prompt = self._run_with_chain(app, monkeypatch, tmp_path, stale=False)

        assert prompt, "沒有攔截到任何 provider request"
        assert self.S_STALE_SUMMARY in prompt, \
            "non-stale 的 L2 哨兵進不了 request —— 否定斷言的對照組失效"
        assert self.S_FRESH_L1 in prompt, \
            "L1 哨兵進不了 request —— fallback chain 路徑根本沒跑"
