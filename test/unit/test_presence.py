# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_presence.py
# 主要責任: 重現並驗收 presence 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/unit/test_presence.py -q
"""
路徑(./test/unit/test_presence.py)
版本 v1.0
更版時間 20260727

模組定位：在線協作者註冊表的單元測試。
責任：確認去重、離線移除、專案隔離，以及「不記章節」這個刻意的設計邊界。
背景：同一章節可以有多人同時寫入（總編輯與 owner 依定義可寫所有章節），
      在線提示的用途是讓彼此知道對方也在，避免各自存出不同版本再人工合併。
"""
import pytest

from app.core_pro.manuscript import presence


@pytest.fixture(autouse=True)
def clean():
    presence.reset()
    yield
    presence.reset()


def test_online_lists_entered_users():
    presence.enter("sid1", "P-p", 1, "alice")
    presence.enter("sid2", "P-p", 2, "bob")
    assert [u["name"] for u in presence.online("P-p")] == ["alice", "bob"]


def test_same_user_two_tabs_counted_once():
    """同一人開兩個分頁是兩條連線，但在線名單只該出現一次。"""
    presence.enter("sid1", "P-p", 1, "alice")
    presence.enter("sid2", "P-p", 1, "alice")
    assert len(presence.online("P-p")) == 1


def test_drop_removes_and_reports_pid():
    presence.enter("sid1", "P-p", 1, "alice")
    assert presence.drop("sid1") == "P-p"
    assert presence.online("P-p") == []
    # 重複 drop 不該爆，回 None 讓呼叫端知道不用廣播
    assert presence.drop("sid1") is None


def test_closing_one_tab_keeps_user_online():
    """關掉其中一個分頁時，人還在線上，不能整個消失。"""
    presence.enter("sid1", "P-p", 1, "alice")
    presence.enter("sid2", "P-p", 1, "alice")
    presence.drop("sid1")
    assert [u["name"] for u in presence.online("P-p")] == ["alice"]


def test_projects_are_isolated():
    """A 專案的在線名單不能看到 B 專案的人。"""
    presence.enter("sid1", "A-p", 1, "alice")
    presence.enter("sid2", "B-p", 2, "bob")
    assert [u["name"] for u in presence.online("A-p")] == ["alice"]
    assert [u["name"] for u in presence.online("B-p")] == ["bob"]


def test_enter_is_idempotent_per_sid():
    """同一條連線重複註冊（例如重連）只該有一筆。"""
    presence.enter("sid1", "P-p", 1, "alice")
    presence.enter("sid1", "P-p", 1, "alice")
    assert len(presence.online("P-p")) == 1


def test_missing_sid_or_pid_is_ignored():
    presence.enter("", "P-p", 1, "alice")
    presence.enter("sid1", "", 1, "alice")
    assert presence.online("P-p") == []


def test_no_section_dimension_is_recorded():
    """刻意的設計邊界：只記專案層級。

    章節層級的在線狀態會把「誰被指派了哪一章」洩漏給看不到該章的限定編輯，
    產品上也不需要那麼細。這個測試是防止日後有人「順手」把 section 加回來。
    """
    presence.enter("sid1", "P-p", 1, "alice")
    row = presence.online("P-p")[0]
    assert set(row.keys()) == {"user_id", "name"}


def test_blank_name_falls_back():
    presence.enter("sid1", "P-p", 1, "")
    assert presence.online("P-p")[0]["name"] == "協作者"
