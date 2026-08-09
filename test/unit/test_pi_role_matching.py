# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_pi_role_matching.py
# 主要責任: 重現並驗收 pi role matching 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 驗證: python -m pytest test/unit/test_pi_role_matching.py -q
"""
路徑(./test/unit/test_pi_role_matching.py)
版本 v1.0
更版時間 20260728

模組定位：主持人 (PI) 角色判定的迴歸測試。
背景：「共同主持人 (Co-PI)」這個字串本身包含「主持人」三個字。
      前端原本用 role.includes('主持人') 計算 PI 人數，於是 Co-PI 被算成
      第二位 PI，一個正常的「一位 PI + 一位 Co-PI」專案會被
      「必須且只能有一位主持人」擋下來——人員加不了、專案也存不了。
      後端用的是 startswith，判定正確；兩邊不一致導致前端擋掉後端接受的資料。
責任：鎖住後端的正確判定，並把「Co-PI 不是 PI」這件事寫成明確斷言，
      避免日後有人「順手」把 startswith 改回 includes。
"""
import pytest

from app.project_portfolio.project_service import ProjectService

PI = "主持人 (Principal Investigator)"
CO_PI = "共同主持人 (Co-PI)"
CONTRIB = "主貢獻人員 (Principal Contributor)"
CORRESP = "通信窗口 (Correspondent)"
COLLAB = "合作人員 (Collaborator)"
SUPPORT = "支援人員 (Supporter)"


def member(role, name="Kuo"):
    return {"role": role, "name": {"en": {"surname": name, "given": "X"}}}


def test_the_substring_trap_is_real():
    """這就是 bug 的來源：用『包含』比對，Co-PI 會被當成 PI。"""
    assert "主持人" in CO_PI          # ← 原本前端的判斷方式，會誤判
    assert not CO_PI.startswith("主持人")   # ← 正確的判斷方式


def test_one_pi_plus_one_co_pi_is_valid():
    """使用者實際撞到的情境：一位 PI + 一位 Co-PI 必須能存。"""
    ok, msg = ProjectService._validate_members([member(PI), member(CO_PI, "Lin")])
    assert ok, msg


def test_one_pi_plus_every_other_role_is_valid():
    """PI 之外的所有角色都不該被算成 PI。"""
    members = [member(PI)] + [member(r, f"N{i}") for i, r in
                              enumerate([CO_PI, CONTRIB, CORRESP, COLLAB, SUPPORT])]
    ok, msg = ProjectService._validate_members(members)
    assert ok, msg


def test_two_real_pis_are_rejected():
    """真的有兩位 PI 時仍要擋——修正不能把保護一起拿掉。"""
    ok, msg = ProjectService._validate_members([member(PI), member(PI, "Lin")])
    assert not ok
    assert "Found 2" in msg


def test_zero_pi_is_rejected():
    ok, msg = ProjectService._validate_members([member(CO_PI), member(COLLAB, "A")])
    assert not ok
    assert "Found 0" in msg


def test_co_pi_alone_does_not_count_as_pi():
    """只有 Co-PI 時 PI 數應為 0，不是 1。"""
    ok, msg = ProjectService._validate_members([member(CO_PI)])
    assert not ok
    assert "Found 0" in msg


@pytest.mark.parametrize("role", [CO_PI, CONTRIB, CORRESP, COLLAB, SUPPORT])
def test_each_non_pi_role_paired_with_pi_passes(role):
    ok, msg = ProjectService._validate_members([member(PI), member(role, "Other")])
    assert ok, f"{role} 不該被算成第二位 PI：{msg}"
