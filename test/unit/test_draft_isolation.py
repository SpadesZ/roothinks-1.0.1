# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_draft_isolation.py
# 主要責任: 重現並驗收 draft isolation 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/unit/test_draft_isolation.py -q
"""
路徑(./test/unit/test_draft_isolation.py)
版本 v1.0
更版時間 20260727

模組定位：草稿層的多人隔離測試。
責任：確保兩個人同時編輯同一章節時，自動存檔不會互相覆蓋、也不會讀到對方的草稿。
背景：v1.0 的 save_draft 對整個章節只寫一份 _draft.json，沒有使用者維度。
      多使用者實測（12 人併發）確認 B 的自動存檔會整份蓋掉 A 未存檔的內容，
      而且 A 重新進頁面時會被提示復原「你的草稿」——內容其實是 B 打的。
      正式版本快照本來就各自獨立，破口只在草稿層。
"""
import os

import pytest

from app.core_pro.manuscript.manuscript_io import (
    DRAFT_FILENAME,
    DRAFT_PREFIX,
    ManuscriptIO,
)

PID = "DRAFTISO-p"
SEC = "introduction"


@pytest.fixture
def draft_dir(tmp_path, monkeypatch):
    """把 DATA_ROOT 指到 tmp_path，完全不碰 repo 的 data/ 目錄。"""
    import app.core_pro.manuscript.manuscript_io as mio
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(mio, "_get_data_root", lambda: str(root))
    yield root


def _save(owner_key, name, text):
    return ManuscriptIO.save_draft(PID, SEC, "T", text,
                                   updated_by=name, owner_key=owner_key)


def test_two_users_drafts_do_not_overwrite(draft_dir):
    """核心迴歸：A 與 B 交錯自動存檔，兩份內容都必須留著。"""
    for i in range(3):
        _save(101, "alice", f"<p>A-{i}</p>")
        _save(202, "bob", f"<p>B-{i}</p>")

    a = ManuscriptIO.load_draft(PID, SEC, owner_key=101, owner_name="alice")
    b = ManuscriptIO.load_draft(PID, SEC, owner_key=202, owner_name="bob")
    assert a["content"] == "<p>A-2</p>"
    assert b["content"] == "<p>B-2</p>"


def test_user_never_reads_another_users_draft(draft_dir):
    """B 沒寫過草稿時要拿到 None，不能拿到 A 的內容。"""
    _save(101, "alice", "<p>alice 的機密段落</p>")
    assert ManuscriptIO.load_draft(PID, SEC, owner_key=202, owner_name="bob") is None


def test_clear_draft_leaves_other_users_untouched(draft_dir):
    """A 按正式存檔清掉自己的草稿，不能順手刪掉 B 正在打的字。"""
    _save(101, "alice", "<p>A</p>")
    _save(202, "bob", "<p>B</p>")

    assert ManuscriptIO.clear_draft(PID, SEC, owner_key=101, owner_name="alice")
    assert ManuscriptIO.load_draft(PID, SEC, owner_key=101, owner_name="alice") is None
    assert ManuscriptIO.load_draft(PID, SEC, owner_key=202,
                                   owner_name="bob")["content"] == "<p>B</p>"


def test_draft_filename_is_per_user_and_sanitized(draft_dir):
    """檔名要帶使用者維度，且清洗掉可能跳出目錄或造成碰撞的字元。"""
    assert ManuscriptIO._draft_filename(None) == DRAFT_FILENAME
    assert ManuscriptIO._draft_filename(7) == f"{DRAFT_PREFIX}7.json"

    evil = ManuscriptIO._draft_filename("../../etc/passwd")
    assert ".." not in evil and "/" not in evil and evil.endswith(".json")

    # 全非 ASCII 的值清洗後為空，需退回雜湊而不是變成同一個檔名
    cjk_a = ManuscriptIO._draft_filename("王小明")
    cjk_b = ManuscriptIO._draft_filename("陳大文")
    assert cjk_a != cjk_b
    assert cjk_a.startswith(DRAFT_PREFIX)


def test_legacy_shared_draft_only_visible_to_its_author(draft_dir):
    """升級相容：舊的共用 _draft.json 只回傳給原作者，其他人視為看不到。"""
    ManuscriptIO.save_draft(PID, SEC, "T", "<p>升級前留下的</p>", updated_by="alice")
    legacy = os.path.join(ManuscriptIO._block_dir(PID, SEC), DRAFT_FILENAME)
    assert os.path.exists(legacy)

    assert ManuscriptIO.load_draft(PID, SEC, owner_key=101,
                                   owner_name="alice")["content"] == "<p>升級前留下的</p>"
    assert ManuscriptIO.load_draft(PID, SEC, owner_key=202, owner_name="bob") is None


def test_new_draft_supersedes_legacy_for_same_user(draft_dir):
    """同一人寫了新草稿之後，應讀到新的而不是舊共用檔的殘留。"""
    ManuscriptIO.save_draft(PID, SEC, "T", "<p>舊</p>", updated_by="alice")
    _save(101, "alice", "<p>新</p>")
    got = ManuscriptIO.load_draft(PID, SEC, owner_key=101, owner_name="alice")
    assert got["content"] == "<p>新</p>"


def test_legacy_clear_removes_own_shared_draft(draft_dir):
    """原作者正式存檔時，舊共用草稿也該一併清掉，否則會一直被提示復原。"""
    ManuscriptIO.save_draft(PID, SEC, "T", "<p>舊</p>", updated_by="alice")
    ManuscriptIO.clear_draft(PID, SEC, owner_key=101, owner_name="alice")
    assert ManuscriptIO.load_draft(PID, SEC, owner_key=101, owner_name="alice") is None


def test_anonymous_mode_still_works(draft_dir):
    """AUTH_MODE=none 時 owner_key 為 None，仍要能存能讀（走舊格式）。"""
    ManuscriptIO.save_draft(PID, SEC, "T", "<p>dev</p>", updated_by=None)
    assert ManuscriptIO.load_draft(PID, SEC)["content"] == "<p>dev</p>"
    assert ManuscriptIO.clear_draft(PID, SEC)
    assert ManuscriptIO.load_draft(PID, SEC) is None
