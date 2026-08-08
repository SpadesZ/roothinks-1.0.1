# 檔案路徑: test/unit/test_ruling_injected_block_binding.py
# 產生時間: 2026-08-08 22:40 +08:00
# 版本: v1.0
# 模組定位:
#   ManuscriptRuling.validate_and_prepare 不得因 injected_block 未綁定而崩潰。
# 背景:
#   405efe8 引入的錯：build_injected_context_block 的 import 寫在
#   `if pid and section:` 內的 try 區塊，但呼叫寫在該 if 之外且無條件執行。
#   pid 未傳（或 import 失敗）時 → UnboundLocalError，草稿生成整條掛掉。
#   當時 567 個單元測試全綠 —— 因為沒有任何一個測試走真實呼叫路徑。
#   實測是在正式站容器裡直接呼叫 process_request 才炸出來的。
# 主要責任:
#   1. pid=None 時 validate_and_prepare 不得拋例外。
#   2. section=None 時同樣不得拋例外。
#   3. context injection 失敗時要「降級」而不是崩潰 —— 原本 except 區塊在
#      呼叫點之前，那句「degrade, not crash」的註解是假的防護。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   validate_and_prepare 回傳 dict，含 ok / context_text。
#   本測試只斷言「不炸」與回傳型別，不斷言注入內容 —— 內容依賴專案資料，
#   綁在測試裡會變成脆弱斷言。
# 安全邊界:
#   - 不建立 Flask app context 以外的任何狀態，不寫 data/。
# 維護提醒:
#   - 若有人把 injected_block 的初始化拿掉，或把呼叫移回 if 之外，這裡會紅。
# 驗證方式:
#   python -m pytest test/unit/test_ruling_injected_block_binding.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

TITLE = "A Perfectly Valid Manuscript Title"


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
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'ruling.db'}",
        "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'ruling_manu.db'}"},
        "SERVER_NAME": None,
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()


def _call(pid, section):
    from app.core_pro.manuscript.manuscript_ruling import ManuscriptRuling

    return ManuscriptRuling.validate_and_prepare(
        pid=pid,
        title=TITLE,
        section=section,
        s_ver="0.1",
        current_context="some working context",
        attachment=None,
        import_type="other",
        user_prompt="write the abstract",
    )


def test_no_pid_does_not_raise(app):
    """核心：pid 缺席時 import 不會執行，呼叫點必須仍然安全。"""
    with app.app_context():
        result = _call(pid=None, section="abstract")
    assert isinstance(result, dict)


def test_no_section_does_not_raise(app):
    with app.app_context():
        result = _call(pid="NOPE-p", section=None)
    assert isinstance(result, dict)


def test_injection_failure_degrades_instead_of_crashing(app, monkeypatch):
    """注入失敗要降級。原本 except 攔得到例外，但呼叫點在 try 之外照樣崩。"""
    import app.core_pro.manuscript.context_inject as ci

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated retrieval failure")

    monkeypatch.setattr(ci, "retrieve_paragraph_context", _boom)

    with app.app_context():
        result = _call(pid="NOPE-p", section="abstract")

    assert isinstance(result, dict)
    # 降級後仍須帶回可用的 context，不能整包不見
    assert "context_text" in result
