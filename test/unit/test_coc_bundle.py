# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_coc_bundle.py
# 子系統定位:
#   Manuscript 人類決策鏈（COC）組裝層的回歸護欄。
# 主要責任:
#   1. 版本真相由伺服器解析：真實 S.Ver 取代前端寫死的 '0.1'。
#   2. 2A 對話歷史真的被讀回並進入送給 LLM 的 context（本次修復的核心缺口）。
#   3. 跨專案資料不得出現在 bundle 裡。
#   4. 正文取自版本檔而非瀏覽器 DOM，因此不含章節 badge 等 UI 文字。
#   5. 預算不足時只能截斷／捨棄並記錄於 notes，不得靜默遺失。
# 明確不負責:
#   - 不驗證論文證據檢索的納入／排除（見 test_evidence_inclusion.py，NOTE-013）。
#   - 不驗證 ContextChain stale 行為（見 test_context_chain_stale.py，NOTE-014）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   只在 tmp_path 下建立 <pid>/manuscript/{block,chat,paper} 檔案；不碰真實 data/。
#   _get_data_root() 由 SQLALCHEMY_DATABASE_URI 的目錄推導，故 tmp_path 即隔離邊界。
# 不變量:
#   - 這些斷言是行為級的：改用別的組裝方式也應該通過，不綁定實作字串。
# 相關 NOTE:
#   NOTE-012（COC 由伺服器依真實版本組裝，前端不得指定版本真相）。
# 驗證:
#   python -m pytest test/unit/test_coc_bundle.py -q
# ---------------------------------------------------------------------------
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "COCTST-p"
OTHER_PID = "OTHERP-p"


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
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'coc.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'coc_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()


def _write_chat(pid, section, turns):
    """直接落地 chat_<section>.json，模擬先前幾輪 2A 對話。"""
    from app.core_pro.manuscript.manuscript_io import _get_data_root
    d = os.path.join(_get_data_root(), pid, "manuscript", "chat")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"chat_{section}.json"), "w", encoding="utf-8") as f:
        json.dump(turns, f, ensure_ascii=False)


def _save_block(pid, section, content, from_ver=None):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    return ManuscriptIO.save_block_version(
        pid=pid, section=section, title="COC Test", content=content, from_ver=from_ver,
    )


def _bundle(pid, section, **kw):
    """
    本檔驗的是組裝行為，不是權限，所以統一餵「可讀全部」的 ACL 參數。

    build_coc_bundle 的三個 ACL 參數刻意沒有預設值（忘了帶要 TypeError），
    因此這裡集中補上，避免每個測試重複三行與權限無關的樣板。
    權限行為本身由 test_coc_acl.py 負責。
    """
    from app.core_pro.manuscript.coc_bundle import build_coc_bundle

    kw.setdefault("readable_sections", [section])
    kw.setdefault("can_read_current_section", True)
    kw.setdefault("include_paper", True)
    kw.setdefault("formal_pid", pid)
    return build_coc_bundle(pid, section, **kw)


class TestServerResolvesRealVersion:
    def test_latest_version_wins_over_frontend_hardcoded_01(self, app):
        """
        前端長期送 s_ver='0.1'。伺服器必須回報真正的最新版。
        """
        from app.core_pro.manuscript.coc_bundle import resolve_section_version

        with app.app_context():
            _save_block(PID, "introduction", "<p>first</p>")
            second = _save_block(PID, "introduction", "<p>second</p>", from_ver="0.1")
            ver, filename = resolve_section_version(PID, "introduction")

        assert second["ver"] == "0.2", second
        assert ver == "0.2", "伺服器解析出的版本仍不是最新版"
        assert ver != "0.1", "退回前端寫死的 0.1 就是這次要修的缺陷"
        assert filename

    def test_bundle_reports_resolved_version(self, app):
        with app.app_context():
            _save_block(PID, "method", "<p>m1</p>")
            _save_block(PID, "method", "<p>m2</p>", from_ver="0.1")
            bundle = _bundle(PID, "method")

        assert bundle["s_ver"] == "0.2"
        assert "m2" in bundle["text"]
        assert "m1" not in bundle["text"], "應載入最新版，不是第一版"


class TestChatHistoryReachesPrompt:
    def test_previous_human_turn_is_included(self, app):
        """
        本次修復的核心：save_chat_history 一直有寫，但生成時沒有人讀回來。
        """
        sentinel = "SENTINEL_HUMAN_TURN_KEEP_PASSIVE_VOICE"
        with app.app_context():
            _save_block(PID, "abstract", "<p>abstract body</p>")
            _write_chat(PID, "abstract", [
                {"role": "user", "content": sentinel, "type": "text"},
                {"role": "ai", "content": "understood", "type": "text"},
            ])
            bundle = _bundle(PID, "abstract")

        assert sentinel in bundle["text"], "前一輪的人類指令沒有進入 context"
        assert any(i["source_type"] == "drafter_chat" for i in bundle["items"])

    def test_history_absent_is_not_an_error(self, app):
        with app.app_context():
            _save_block(PID, "results", "<p>r</p>")
            bundle = _bundle(PID, "results")
        assert bundle["text"]


class TestProjectIsolation:
    def test_other_project_content_never_appears(self, app):
        """跨專案取材是缺陷，不是降級。"""
        leak = "SENTINEL_OTHER_PROJECT_MUST_NOT_LEAK"
        with app.app_context():
            _save_block(PID, "discussion", "<p>mine</p>")
            _save_block(OTHER_PID, "discussion", f"<p>{leak}</p>")
            _write_chat(OTHER_PID, "discussion", [{"role": "user", "content": leak, "type": "text"}])
            bundle = _bundle(PID, "discussion")

        assert "mine" in bundle["text"]
        assert leak not in bundle["text"]


class TestNoUiChromeInContext:
    def test_section_badge_text_is_not_in_context(self, app):
        """
        正文取自版本檔而非 editorCanvas.innerText，所以卡片 badge 不會混進來。
        對照：舊路徑送的是 innerText，實測空白畫布都會算到 badge（見 NOTE-006）。
        """
        with app.app_context():
            _save_block(PID, "conclusion", "<p>real body text</p>")
            bundle = _bundle(PID, "conclusion")
        assert "real body text" in bundle["text"]
        for chrome in ("Save Block", "Push to Fusor", "唯讀 · 可留言"):
            assert chrome not in bundle["text"]

    def test_html_is_flattened_but_paragraphs_survive(self):
        from app.core_pro.manuscript.coc_bundle import html_to_text
        out = html_to_text("<p>one</p><p>two</p><ul><li>item</li></ul>")
        assert "<p>" not in out
        assert "one" in out and "two" in out and "item" in out
        # 段落必須斷行，否則 LLM 讀到的結構與作者寫的不同：
        # `</p><p>` 相鄰的兩段若黏成一行，模型會當成同一段話。
        assert re.search(r"one\s*\n\s*two", out), f"段落沒有斷行: {out!r}"


class TestBudgetLossIsRecorded:
    def test_truncation_is_reported_not_silent(self, app):
        with app.app_context():
            _save_block(PID, "introduction", "<p>" + ("長" * 4000) + "</p>")
            bundle = _bundle(PID, "introduction", budget_tokens=200)
        assert bundle["notes"], "內容被裁掉卻沒有任何紀錄"
