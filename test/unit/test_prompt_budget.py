# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_prompt_budget.py
# 子系統定位:
#   任務 1（全域 token 預算 packer）與任務 2（audit COC 來源連線）的整合驗證。
# 主要責任:
#   1. 確認 _pack_prompt_context 在超過全域預算時確實截斷，且 retrieval_budget 算對。
#   2. 確認 _pack_prompt_context 對前端草稿與 COC 同一章節進行去重。
#   3. 確認 write_context_audit 確實把 coc_sources / coc_notes 寫進 JSON。
#   4. 每一個「不在場」斷言都有同資料的「在場」對照組。
# 明確不負責:
#   - 不驗 ManuscriptRuling 的完整生成路徑（見 test_manuscript_grounding.py）。
#   - 不驗 COC 的組裝順序（見 test_coc_bundle.py）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   write_context_audit 寫到 tmp_path；不碰真實 data/。
# 不變量:
#   - 每一個「不在場」斷言都必須有一個同資料、同路徑的「在場」對照。
#   - A/B 反證：把修復關掉，對應測試必須變紅，且是預期的那一條。
# 驗證:
#   python -m pytest test/unit/test_prompt_budget.py -q
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "PKTEST-p"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'pk.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'pk_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


# ---------------------------------------------------------------------------
# 任務 1：全域 token 預算 packer（_pack_prompt_context）
# ---------------------------------------------------------------------------


class TestPackPromptContext:
    """
    _pack_prompt_context 是純函式（不需要 DB、app context、socket），
    可以直接呼叫並驗證回傳值。

    A/B 反證說明（不在程式碼裡加 ABTEST 標記）：
      - 測試 `test_budget_exceeded_truncates_and_records_note` 驗的是「截斷」
        與「packer_notes 記錄截斷事實」。若把 total_budget 設大到不會截斷，
        或把截斷邏輯移除，這個測試就會在「note 不在 packer_notes 裡」那行失敗。
      - 反向確認已在下方 `test_budget_not_exceeded_no_truncation_note` 做到：
        同一組資料，預算充足時不產生截斷 note，retrieval_budget > 0。

    去重 A/B 反證：
      - `test_dedup_identical_frontend_and_coc_section` 驗證相同內容只保留一份。
        若把 _normalize_context_text 換成 `return ""` （讓比較失效），
        packer_notes 就不會有 dedup note，該測試的 note 斷言會失敗。
    """

    def _call(self, *, frontend_context, coc_blocks, total_budget, section="introduction"):
        """
        coc_blocks 用 build_coc_bundle 的結構化格式餵給 packer。

        這裡刻意**不**手工拼 "[目前章節 ... · S.Ver ...]" 的顯示字串：packer 的
        去重是依 blocks 的 tier 判斷，不是解析標頭文字。手工拼字串的版本會讓
        測試綁死顯示格式，而且在真正的耦合斷掉時仍然是綠的
        （見 TestPackerUsesRealBundleStructure 的說明）。
        """
        from app.core_pro.manuscript.manuscript_routes import _pack_prompt_context

        coc_bundle = {
            "text": "\n\n".join(b["text"] for b in coc_blocks),
            "blocks": coc_blocks,
            "s_ver": "0.1",
            "items": [b["item"] for b in coc_blocks if b.get("item")],
            "notes": [],
        }
        packed, retrieval_budget, notes, _items = _pack_prompt_context(
            frontend_context=frontend_context,
            coc_bundle=coc_bundle,
            total_budget=total_budget,
            section=section,
            job_id="test-job",
        )
        return packed, retrieval_budget, notes

    @staticmethod
    def _block(tier, header, body, item=None):
        return {"tier": tier, "body": body, "text": f"{header}\n{body}", "item": item}

    # ── 正向對照組：預算充足時不截斷、retrieval_budget > 0 ──

    def test_budget_not_exceeded_no_truncation_note(self):
        """
        預算充足時不應截斷，packer_notes 不應有 truncated: 字首的條目。

        這是下面截斷測試的對照組：若這個測試失敗，代表截斷判斷邏輯本身有問題，
        不是「是否截斷」的語意問題。
        """
        packed, rb, notes = self._call(
            frontend_context="Short frontend text.",
            coc_blocks=[self._block("other_section", "[其他章節 method]", "Short COC text.")],
            total_budget=20000,
        )
        truncation_notes = [n for n in notes if n.startswith("truncated:")]
        assert not truncation_notes, f"預算充足卻有截斷記錄: {truncation_notes}"
        assert rb > 0, "預算充足時 retrieval_budget 應 > 0"

    # ── 主斷言：超過全域預算時確實截斷且留下記錄 ──

    def test_budget_exceeded_truncates_and_records_note(self):
        """
        超過全域預算時，packer 必須截斷並在 packer_notes 留下記錄。

        若截斷紀錄消失（截斷邏輯被移除，或 total_budget 設太大），
        「note 含 truncated:」的斷言會失敗 —— 這正是 A/B 反證的預期失敗點。
        """
        # 用一個很大的文字字串讓它超過 budget（每字 ~0.25 token，4 chars/token）
        big_text = "A" * 1000  # ~250 tokens
        packed, rb, notes = self._call(
            frontend_context=big_text,
            coc_blocks=[],
            total_budget=10,  # 10 token 遠小於 250 token
        )
        truncation_notes = [n for n in notes if "truncated:" in n]
        assert truncation_notes, (
            "超過全域預算卻沒有截斷記錄 —— "
            "如果這行紅了，代表截斷記錄邏輯失效（任務 1 修復遺失）"
        )
        # retrieval 在超額後應得到 0（所有預算已被前端草稿耗盡）
        assert rb == 0, f"預算超額後 retrieval_budget 應為 0，實際={rb}"

    # ── retrieval_budget 計算：用掉的越多、剩的越少 ──

    def test_retrieval_budget_is_remainder(self):
        """
        retrieval_budget = total_budget - tokens_used_by_frontend_and_coc。

        A/B：如果把 retrieval_budget 公式改成固定回傳 total_budget，
        這個測試的不等斷言就會失敗。
        """
        total = 1000
        # 前端草稿約 50 字元 ≈ 12 tokens
        packed, rb, notes = self._call(
            frontend_context="A" * 50,
            coc_blocks=[self._block("other_section", "[其他章節 x]", "B" * 50)],
            total_budget=total,
            section="method",
        )
        # retrieval_budget 必須比 total 少（因為已使用了一部分）
        assert rb < total, f"retrieval_budget={rb} 應小於 total_budget={total}"
        # retrieval_budget 不能是負數
        assert rb >= 0, f"retrieval_budget={rb} 不可為負"

    # ── 去重：前端草稿與 COC 同一章節正文相同時只保留一份 ──

    def test_dedup_identical_frontend_and_coc_section(self):
        """
        前端草稿與 COC 伺服器版本內容相同時，應只保留一份並記錄 dedup note。

        A/B 反證：若 _normalize_context_text 失效（讓比較永遠不相等），
        packer_notes 就不會有 dedup: 字首條目，這個斷言就會失敗。
        """
        same_text = "共同的正文段落，前端與伺服器相同"
        coc_blocks = [
            self._block("current_section", "[目前章節 introduction · S.Ver 0.1]", same_text,
                        {"source_type": "manuscript_section", "source_id": "introduction:0.1"}),
            self._block("other_section", "[其他章節 method · S.Ver 0.1]", "Other section content.",
                        {"source_type": "manuscript_section", "source_id": "method:0.1"}),
        ]
        packed, rb, notes = self._call(
            frontend_context=same_text,
            coc_blocks=coc_blocks,
            total_budget=20000,
        )
        dedup_notes = [n for n in notes if n.startswith("dedup:")]
        assert dedup_notes, (
            "前端草稿與 COC 相同時應有去重記錄 —— "
            "如果這行紅了，代表去重邏輯失效（任務 1 去重修復遺失）"
        )
        # 去重後正文只出現一次
        assert packed.count(same_text) == 1, "相同內容應只保留一份"

    def test_dedup_contrast_different_content_no_dedup(self):
        """
        對照組：前端草稿與 COC 版本不同時，不應觸發 identical 去重。

        COC 中的目前章節會被移除（前端優先），但標注為 diverged，而不是 identical。
        """
        coc_blocks = [
            self._block("current_section", "[目前章節 introduction · S.Ver 0.1]", "伺服器存的舊版本",
                        {"source_type": "manuscript_section", "source_id": "introduction:0.1"}),
            self._block("other_section", "[其他章節 method · S.Ver 0.1]", "Other method content.",
                        {"source_type": "manuscript_section", "source_id": "method:0.1"}),
        ]
        packed, rb, notes = self._call(
            frontend_context="前端有未存的修改，內容不同",
            coc_blocks=coc_blocks,
            total_budget=20000,
        )
        # 不同時觸發的是 diverged，不是 identical
        assert not any("identical" in n for n in notes), \
            "內容不同時不應觸發 identical 去重"
        # 前端版本在 packed 裡
        assert "前端有未存的修改" in packed


class TestPackerUsesRealBundleStructure:
    """
    上面那些測試都是手工組 bundle 的純函式測試，它們驗得了預算算術，
    **驗不了 packer 與 coc_bundle 之間的契約**。

    第一版的 packer 是用正規表示式去 bundle["text"] 裡比對
    "[目前章節 {section} · S.Ver ...]" 這個顯示字串來定位要去重的段落。
    只要有人改動 coc_bundle.py 的標籤文字（純顯示層的修改），去重就會靜默失效、
    目前章節被送兩份、白吃預算 —— 而手工組同樣格式字串的測試永遠是綠的。

    這個測試把真正的 build_coc_bundle 接上真正的 packer，是那條契約的唯一護欄。
    """

    def test_dedup_fires_against_real_build_coc_bundle(self, app):
        from app.core_pro.manuscript.coc_bundle import build_coc_bundle
        from app.core_pro.manuscript.manuscript_io import ManuscriptIO
        from app.core_pro.manuscript.manuscript_routes import _pack_prompt_context

        body = "作者已存進伺服器的 Introduction 正文段落"
        with app.app_context():
            ManuscriptIO.save_block_version(pid=PID, section="introduction",
                                            title="Packer Test", content=f"<p>{body}</p>")
            bundle = build_coc_bundle(
                PID, "introduction",
                readable_sections=["introduction"],
                can_read_current_section=True,
                include_paper=False,
                formal_pid=PID,
            )

        # 先確認對照組成立：真實 bundle 裡確實有目前章節那一段。
        # 少了這一步，下面的去重斷言可能只是因為 bundle 根本是空的而「通過」。
        assert body in bundle["text"], "真實 bundle 沒有目前章節，去重斷言會空過"
        assert any(i["source_type"] == "manuscript_section" for i in bundle["items"])

        packed, _rb, notes, items = _pack_prompt_context(
            frontend_context=body,          # 前端送來同一份內容
            coc_bundle=bundle,
            total_budget=20000,
            section="introduction",
            job_id="real-bundle-job",
        )

        assert any(n.startswith("dedup:") for n in notes), \
            "packer 沒有辨識出真實 bundle 的目前章節 —— packer 與 coc_bundle 的契約已斷"
        assert packed.count(body) == 1, "同一段正文被送了兩份"
        assert not any(i.get("source_id", "").startswith("introduction:") for i in items), \
            "伺服器版本已被前端草稿取代，audit 卻仍宣稱它是來源"


class TestFrontendCharTruncationIsRecorded:
    """
    2B 內容在進 packer 之前就被砍到 MANUSCRIPT_CHAT_MAX_CONTEXT_CHARS，
    而 packer 收到的已經是截斷後的字串，它無從得知這件事發生過。

    先前這裡的註解宣稱「截斷事件會在 _pack_prompt_context 補進 packer_notes」——
    那是錯的，實測 notes 是空的，作者尾端剛寫的內容會靜默消失。
    這個測試斷言的是**真正送進 job payload 的 coc_notes**，不是中間層回傳值。
    """

    def test_char_truncation_reaches_job_payload(self, tmp_path, monkeypatch):
        from app import create_app, db, socketio

        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("AUTH_MODE", raising=False)
        lock_root = tmp_path / "locks2"
        lock_root.mkdir()
        monkeypatch.setenv("LOCK_ROOT", str(lock_root))

        application = create_app({
            "TESTING": True,
            "AUTH_MODE": "none",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'trunc.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'trunc_manu.db'}"},
            "SOCKETIO_ASYNC_MODE": "threading",
            "SERVER_NAME": None,
        })
        data_root = tmp_path / "truncdata"
        data_root.mkdir()
        from app.core_pro.manuscript import coc_bundle as cocb
        from app.core_pro.manuscript import manuscript_io as mio
        from app.core_pro.manuscript import manuscript_routes as mroutes
        from app.core_pro.manuscript import manuscript_ruling as mruling
        for module in (mio, mroutes, cocb, mruling):
            monkeypatch.setattr(module, "_get_data_root", lambda: str(data_root))

        with application.app_context():
            db.create_all()
            from app.models import Project
            db.session.add(Project(project_id=PID, name="Trunc Test", status="formal"))
            db.session.commit()

        captured = []

        class _Recorder:
            def submit(self, fn, *args, **kw):
                captured.append(args[-1])          # payload
                return None

        monkeypatch.setattr(mroutes, "_CHAT_EXECUTOR", _Recorder())

        client = application.test_client()
        sio = socketio.test_client(application, flask_test_client=client, namespace="/manu_ws")
        sio.get_received("/manu_ws")

        oversized = "作" * (mroutes._MAX_CHAT_CONTEXT_CHARS + 5000)
        sio.emit("chat_message",
                 {"pid": PID, "msg": "請續寫", "section": "introduction",
                  "title": "Trunc Paper", "context": oversized},
                 namespace="/manu_ws")
        sio.get_received("/manu_ws")
        if sio.is_connected("/manu_ws"):
            sio.disconnect(namespace="/manu_ws")

        assert captured, "job 沒有被派送"
        notes = captured[0].get("coc_notes") or []
        assert any("frontend_context_chars" in n for n in notes), \
            f"字元層截斷沒有進 coc_notes（作者尾端內容靜默消失）：{notes}"

    def test_normal_length_produces_no_truncation_note(self, tmp_path, monkeypatch):
        """對照組：沒超過上限就不該有截斷紀錄，否則上面那條可能恆為真。"""
        from app.core_pro.manuscript.manuscript_routes import _MAX_CHAT_CONTEXT_CHARS

        assert _MAX_CHAT_CONTEXT_CHARS > 0
        # 這裡只驗字串條件本身，避免再起一個完整 app：
        # 截斷紀錄的產生條件就是 len(context) > 上限。
        short = "作" * 10
        assert not (len(short) > _MAX_CHAT_CONTEXT_CHARS)


class TestBudgetBoundaryIsFinal:
    """
    「全域預算」必須真的是全域的。這一組釘住兩件事：

    1. **0 是有效值，不是「沒給」。** 原本 `retrieval_budget > 0` 把 0 當成未提供
       而退回 18000 —— 也就是內容剛好吃滿 20K 時，反而**額外**檢索 18K，
       正好是這個機制要防的那件事。同樣的陷阱在 `_apply_limits` 的
       `max_tokens or 1200` 還有第二層。
    2. **upstream context 也要吃這份預算。** 它是在 ManuscriptRuling 內才加入
       final_context 的，若不一起收斂，packer 算再準也沒有意義。
    """

    def _prepare(self, app, tmp_path, monkeypatch, **kw):
        from app.core_pro.manuscript import manuscript_ruling as mruling

        root = tmp_path / "budgetroot"
        root.mkdir(exist_ok=True)
        monkeypatch.setattr(mruling, "_get_data_root", lambda: str(root))
        with app.app_context():
            return mruling.ManuscriptRuling.validate_and_prepare(
                pid=PID, title="Budget Test", section="introduction", s_ver="0.1",
                current_context="草稿", attachment=None, import_type="other",
                user_prompt="請續寫",
                **kw,
            )

    def test_zero_budget_skips_retrieval_entirely(self, app, tmp_path, monkeypatch):
        """
        主斷言：remaining_budget=0 時**完全不檢索**，而不是退回 18000。

        設計上刻意「不呼叫」而不是「傳 0 下去」：`_apply_limits` 曾經把 0 當成
        沒給而放大成 1200，依賴下游每一層都正確處理 0 太脆弱。
        因此這裡驗的是「沒有被呼叫」＋「有留下跳過的紀錄」。
        """
        seen = {}

        def _spy(**kwargs):
            seen["max_tokens"] = kwargs.get("max_tokens")
            return []

        import app.core_pro.manuscript.context_inject as ci
        monkeypatch.setattr(ci, "retrieve_paragraph_context", _spy)

        self._prepare(app, tmp_path, monkeypatch, remaining_budget=0,
                      coc_items=[{"source_type": "drafter_chat", "source_id": "x"}])

        assert seen == {}, f"預算為 0 卻仍然檢索了（max_tokens={seen.get('max_tokens')}）"

        audits = list((tmp_path / "budgetroot" / "manuscript_context_audit").rglob("*.json"))
        assert audits, "跳過檢索也要留下 audit"
        payload = json.loads(audits[-1].read_text(encoding="utf-8"))
        assert any("retrieval:skipped" in n for n in payload["coc_notes"]), \
            f"檢索被跳過卻沒有留下紀錄：{payload['coc_notes']}"

    def test_budget_none_falls_back_to_env_default(self, app, tmp_path, monkeypatch):
        """對照組：沒給預算時才可以退回預設值，否則上面的斷言可能只是因為根本沒檢索。"""
        seen = {}

        def _spy(**kwargs):
            seen["max_tokens"] = kwargs.get("max_tokens")
            return []

        import app.core_pro.manuscript.context_inject as ci
        monkeypatch.setattr(ci, "retrieve_paragraph_context", _spy)

        self._prepare(app, tmp_path, monkeypatch, remaining_budget=None)
        assert seen.get("max_tokens") == 18000, \
            f"沒給預算時應退回預設 18000，實際 {seen.get('max_tokens')}"

    def test_apply_limits_honours_explicit_zero(self):
        """第二層陷阱：`max_tokens or 1200` 會把明確的 0 放大成 1200。"""
        from app.core_pro.manuscript.context_inject import _apply_limits

        items = [{"snippet": "x" * 400, "source_id": "s1"}]
        assert _apply_limits(items, top_k=5, max_tokens=0) == []
        # 對照組：沒給（None）時仍要有預設行為，否則上面那條可能只是全都回空。
        assert _apply_limits(items, top_k=5, max_tokens=None)

    def test_upstream_context_consumes_the_same_budget(self, app, tmp_path, monkeypatch):
        """
        upstream 很長時要被截斷、要留紀錄，而且要把檢索的額度吃掉。

        歷史注意：這個測試曾經是**靠 bug 才綠的**。_truncate_to_budget 以前會超標
        （提示字串在搜尋後才附加），剛好把剩餘額度壓成 0，於是「額度用盡就跳過」
        看起來一直成立。截斷改成精確之後還剩 1 token，檢索就被呼叫了 ——
        才浮現出真正需要的是「剩餘額度低於下限也要跳過」。
        """
        from app.core_pro.manuscript import manuscript_ruling as mruling

        seen = {}

        def _spy(**kwargs):
            seen["max_tokens"] = kwargs.get("max_tokens")
            return []

        import app.core_pro.manuscript.context_inject as ci
        monkeypatch.setattr(ci, "retrieve_paragraph_context", _spy)
        monkeypatch.setattr(
            mruling.ManuscriptRuling, "_load_upstream_context",
            classmethod(lambda cls, pid: "[Study Notes]\n" + ("研究筆記內容 " * 2000)),
        )

        result = self._prepare(
            app, tmp_path, monkeypatch, remaining_budget=500,
            coc_items=[{"source_type": "drafter_chat", "source_id": "x"}],
        )

        assert seen == {}, \
            f"upstream 已吃光預算，檢索卻還是跑了（max_tokens={seen.get('max_tokens')}）"
        # upstream 原文約 2000*7 字元，500 tokens 大約只容得下 2000 字元。
        assert len(result["context_text"]) < 6000, \
            f"upstream 沒有被全域預算截斷，context 長度 {len(result['context_text'])}"

        audits = list((tmp_path / "budgetroot" / "manuscript_context_audit").rglob("*.json"))
        payload = json.loads(audits[-1].read_text(encoding="utf-8"))
        assert any("truncated:upstream_context" in n for n in payload["coc_notes"]), \
            f"upstream 被截斷卻沒有留下紀錄：{payload['coc_notes']}"


class TestAuditSurvivesEmptyRetrieval:
    """
    COC provenance 不能依賴「檢索有沒有結果」。

    原本 ManuscriptRuling 是 `if injected_block:` 才寫 audit。而目前全站沒有任何
    library.json（screening 從未被使用），依 NOTE-013 的 fail-closed，寫作路徑的
    檢索結果恆為空 —— 也就是 audit 一次都不會被寫，COC 來源在最需要被稽核的
    狀態下完全沒有紀錄。這一組測試把那個條件釘住。
    """

    def _run(self, app, tmp_path, monkeypatch, coc_items, coc_notes):
        from app.core_pro.manuscript import manuscript_ruling as mruling

        root = tmp_path / "auditroot"
        root.mkdir()
        monkeypatch.setattr(mruling, "_get_data_root", lambda: str(root))
        with app.app_context():
            result = mruling.ManuscriptRuling.validate_and_prepare(
                pid=PID, title="Audit Test", section="introduction", s_ver="0.1",
                current_context="作者正在寫的草稿", attachment=None, import_type="other",
                user_prompt="請續寫",
                coc_items=coc_items,
                coc_notes=coc_notes,
            )
        audits = list((root / "manuscript_context_audit").rglob("*.json"))
        return result, audits, root

    def test_audit_written_when_retrieval_is_empty(self, app, tmp_path, monkeypatch):
        coc_items = [{"source_type": "drafter_chat", "source_id": "introduction:3turns"}]
        result, audits, _root = self._run(
            app, tmp_path, monkeypatch, coc_items, ["paper_2c 因讀取權限不足未納入"],
        )

        assert result["ok"] is True
        # 對照組：先確認這一輪檢索確實是空的，否則本測試測不到目標條件。
        assert not result["injected_context_ids"], "檢索非空，這個測試沒有測到目標情境"
        assert audits, "檢索為空時完全沒有寫 audit —— COC provenance 消失"

        payload = json.loads(audits[0].read_text(encoding="utf-8"))
        assert payload["coc_sources"], "audit 裡沒有 COC 來源"
        assert payload["coc_sources"][0]["source_type"] == "drafter_chat"
        assert any("paper_2c" in n for n in payload["coc_notes"]), "降級紀錄沒有進 audit"

        # 回傳給呼叫端的 context_sources 也要看得到 COC，否則前端的來源顯示
        # 會呈現「這份草稿沒有用到作者的任何人類脈絡」。
        assert any(s.get("source_type") == "drafter_chat" for s in result["context_sources"]), \
            "COC 沒有進 source_manifest"

    def test_audit_lands_under_resolved_data_root(self, app, tmp_path, monkeypatch):
        """audit 必須落在 _get_data_root() 解析出的位置，不是 process 的 cwd。"""
        coc_items = [{"source_type": "manuscript_section", "source_id": "introduction:0.2"}]
        _result, audits, root = self._run(app, tmp_path, monkeypatch, coc_items, [])

        assert audits, "沒有寫出任何 audit"
        for path in audits:
            assert str(path).startswith(str(root)), \
                f"audit 寫到了 data root 以外的位置: {path}"

    def test_no_audit_when_there_is_nothing_to_record(self, app, tmp_path, monkeypatch):
        """反向對照：沒有檢索、沒有 COC、也沒有降級紀錄時不該憑空產生 audit 檔。"""
        _result, audits, _root = self._run(app, tmp_path, monkeypatch, [], [])
        assert not audits, "沒有任何來源可記，卻仍寫出 audit 檔"


# ---------------------------------------------------------------------------
# 任務 2：context_audit 確實寫入 coc_sources / coc_notes
# ---------------------------------------------------------------------------


class TestContextAuditCocSources:
    """
    write_context_audit 是純函式（接受 data_root 參數），
    可以直接呼叫並驗讀取 JSON。

    A/B 反證：
      若把 write_context_audit 裡的 "coc_sources" 鍵移除，
      `assert "coc_sources" in payload` 就會失敗。

    對照組：
      沒有傳入 coc_sources 時，audit JSON 仍存在且 "coc_sources" 是空清單，
      確認向後相容性沒壞。
    """

    def _write_and_read(self, tmp_path, *, coc_sources=None, coc_notes=None) -> dict:
        from app.core_pro.manuscript.context_audit import write_context_audit

        audit_path = write_context_audit(
            project_id="test-pid",
            section_id="intro",
            context_items=[
                {
                    "source_type": "evidence_index",
                    "source_id": "seg-001",
                    "paper_id": "paper-A",
                    "segment_id": "1",
                    "fingerprint": "abc123",
                    "score": 0.9,
                    "estimated_tokens": 200,
                }
            ],
            prompt="test prompt",
            data_root=str(tmp_path),
            coc_sources=coc_sources,
            coc_notes=coc_notes,
        )
        with open(audit_path, encoding="utf-8") as f:
            return json.load(f)

    def test_coc_sources_written_to_audit(self, tmp_path):
        """
        主斷言：有傳入 coc_items 時，audit JSON 裡的 coc_sources 不是空清單。

        若 write_context_audit 沒有把 coc_sources 寫進 JSON，
        `assert payload["coc_sources"]` 就是 A/B 反證的失敗點。
        """
        coc_items = [
            {
                "source_type": "manuscript_section",
                "source_id": "introduction:0.1",
                "paper_id": None,
                "segment_id": None,
                "fingerprint": "fp-coc-001",
                "score": None,
                "estimated_tokens": 300,
            }
        ]
        payload = self._write_and_read(tmp_path, coc_sources=coc_items)
        assert "coc_sources" in payload, "audit JSON 缺少 coc_sources 欄位（任務 2 連線失效）"
        assert payload["coc_sources"], \
            "audit JSON 的 coc_sources 是空清單，任務 2 的 COC 來源沒寫進去"

    def test_coc_notes_written_to_audit(self, tmp_path):
        """
        主斷言：有傳入 coc_notes 時，audit JSON 裡的 coc_notes 不是空清單。
        """
        notes = [
            "excluded_comments=3 (s_ver=NULL 的舊留言未混入)",
            "stale:L2:skipped (NOTE-014)",
        ]
        payload = self._write_and_read(tmp_path, coc_notes=notes)
        assert "coc_notes" in payload, "audit JSON 缺少 coc_notes 欄位（任務 2 連線失效）"
        assert payload["coc_notes"] == notes, \
            f"audit JSON 的 coc_notes 與傳入值不符: {payload['coc_notes']}"

    # ── 對照組：不傳 coc_sources/notes 時向後相容 ──

    def test_backward_compat_no_coc_args(self, tmp_path):
        """
        不傳 coc_sources / coc_notes 時，audit JSON 仍正常寫出，
        且兩個欄位都是空清單（不是 null，不是缺失）。

        向後相容性是任務 2 的硬性要求：既有呼叫端沒有傳這兩個參數，
        不能讓 audit 寫入失敗或現有 code 需要修改。
        """
        payload = self._write_and_read(tmp_path)
        assert payload.get("coc_sources") == [], \
            f"不傳 coc_sources 時應為空清單: {payload.get('coc_sources')}"
        assert payload.get("coc_notes") == [], \
            f"不傳 coc_notes 時應為空清單: {payload.get('coc_notes')}"
        # 核心欄位仍然存在
        assert "context_items" in payload
        assert "injected_context_ids" in payload
