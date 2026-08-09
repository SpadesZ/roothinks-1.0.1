# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_organization_v2.py
# 主要責任: 重現並驗收 organization v2 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
"""
路徑(./test/unit/test_organization_v2.py)
版本 v1.0
更版時間 20260727

模組定位：所屬單位由舊三層搬遷到新五層的邏輯測試。
責任：驗證方向翻轉正確、冪等（重跑不會再翻一次）、空值與異常結構不會炸。
背景：舊三層是「由大到小」(l1=Institution、l3=Lab)，新五層是「由小到大」
      (L1 Lab/Unit → L5 Nationality/Region)。只改標籤不搬資料的話，
      既有那筆真資料會變成「Lab/Unit = Hanyang University」，完全顛倒。
"""
import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "migrate_organization_v2",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "scripts", "migrate_organization_v2.py"),
)
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)


REAL = {
    "l1": "Hanyang University",
    "l2": "Department of Electrical and Biomedical Engineering",
    "l3": "Multidisciplinary Computational Laboratory",
}


def test_direction_is_flipped_on_real_data():
    """核心：最小層與最大層必須對調，不能原地照抄。"""
    out = mig.convert(REAL)
    assert out["l1"] == "Multidisciplinary Computational Laboratory"   # 最小
    assert out["l2"] == "Department of Electrical and Biomedical Engineering"
    assert out["l4"] == "Hanyang University"                          # 最大
    assert out["l3"] == ""    # Institution/Branch，舊資料沒有這層
    assert out["l5"] == ""    # Nationality/Region，舊資料沒有這層


def test_converted_result_has_all_five_keys():
    """五個鍵一定要寫滿——l4/l5 的存在就是「已是新格式」的判準。"""
    assert set(mig.convert(REAL).keys()) == {"l1", "l2", "l3", "l4", "l5"}


def test_legacy_detection():
    assert mig.is_legacy(REAL) is True
    assert mig.is_legacy({"l1": "x"}) is True
    assert mig.is_legacy({"l1": "", "l2": "", "l3": ""}) is True


def test_already_migrated_is_not_touched_again():
    """冪等：重跑一次不能把資料再翻一輪（翻兩次就救不回來了）。"""
    once = mig.convert(REAL)
    assert mig.is_legacy(once) is False
    # 就算只有 l4 或只有 l5 也算新格式
    assert mig.is_legacy({"l1": "a", "l4": ""}) is False
    assert mig.is_legacy({"l1": "a", "l5": ""}) is False


def test_non_dict_and_empty_are_safe():
    for bad in [None, "", [], "l1", 123]:
        assert mig.is_legacy(bad) is False
    assert mig.is_legacy({}) is False


def test_values_are_stripped():
    out = mig.convert({"l1": "  A  ", "l2": "  B ", "l3": " C "})
    assert (out["l4"], out["l2"], out["l1"]) == ("A", "B", "C")


def test_missing_levels_become_empty_string():
    out = mig.convert({"l2": "只有系所"})
    assert out["l1"] == "" and out["l4"] == ""
    assert out["l2"] == "只有系所"


@pytest.mark.parametrize("org", [
    {"l1": None, "l2": None, "l3": None},
    {"l1": "x"},
    {"l3": "y"},
])
def test_convert_never_raises(org):
    out = mig.convert(org)
    assert all(isinstance(v, str) for v in out.values())
