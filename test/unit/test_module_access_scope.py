# Roothinks source maintenance contract
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 維護邊界: 不得讀寫正式 data/.env、送出真實外部請求或以弱化 assertion 配合實作；環境缺件要明確 skip/fail。
# 檔案路徑: roothinks/test/unit/test_module_access_scope.py
# 產生時間: 2026-08-04 +08:00
# 版本: v1.0
# 模組定位:
#   模組層存取控制測試——限定編輯(coauthor)只能進 Manuscript。
# 背景:
#   模組/專案層守衛 enforce_project_ownership 對 GET 用 min_role="viewer"，
#   再交給 require_workspace_role 做 ROLE_ORDER 線性比較。
#   coauthor(2) >= viewer(1) 會通過 —— 於是限定編輯讀得到 Literature /
#   PAQ / Study 的全部資料。正式站實測 rickiekuo1203 在 DGVRYV-p 是
#   coauthor，章節層正確擋下未指派章節，但模組層放行。
#
#   models.ROLE_ORDER 的註解本來就寫明「coauthor 的讀取範圍比 viewer 還窄，
#   線性比較會得到相反結果」，章節層照辦了，模組層漏了。
# 主要責任:
#   1. coauthor 只進得去 manuscript，其餘模組一律擋。
#   2. viewer / editor / owner 不受影響。
#   3. coauthor_open_access 開關**不得**影響模組權限（只管章節）。
#   4. 新增模組預設是擋的（白名單制）。
# 驗證方式:
#   - pytest test/unit/test_module_access_scope.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import security  # noqa: E402
from app.security import (  # noqa: E402
    COAUTHOR_ALLOWED_MODULES,
    can_access_module,
    module_of_path,
)

ALL_MODULES = ["literature", "paq", "study", "submit", "setup", "manuscript"]


@pytest.fixture
def as_role(monkeypatch):
    """把 get_workspace_role 換成固定角色，避免碰 DB。"""
    def _apply(role, open_access=False):
        monkeypatch.setattr(security, "get_workspace_role", lambda uid, pid: role)
        monkeypatch.setattr(security, "coauthor_open_access", lambda pid: open_access)
    return _apply


# --- 路徑 → 模組 -----------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/literature", "literature"),
    ("/literature/", "literature"),
    ("/api/literature/status/ABC", "literature"),
    ("/study/project/ABC", "study"),
    ("/paq/ABC", "paq"),
    ("/paq.html", "paq"),
    ("/submit", "submit"),
    ("/manuscript", "manuscript"),
    ("/setup", "setup"),
    ("/lava_setup.html", "setup"),
    ("/", None),                      # Dashboard 不屬任何模組
    ("/auth/login", None),
])
def test_module_of_path(path, expected):
    assert module_of_path(path) == expected


# --- 限定編輯的範圍（本組核心）----------------------------------------------

@pytest.mark.parametrize("module", [m for m in ALL_MODULES if m != "manuscript"])
def test_coauthor_blocked_from_other_modules(as_role, module):
    as_role("coauthor")
    assert can_access_module(1, "P-p", module) is False


def test_coauthor_can_access_manuscript(as_role):
    as_role("coauthor")
    assert can_access_module(1, "P-p", "manuscript") is True


@pytest.mark.parametrize("role", ["viewer", "editor", "owner"])
@pytest.mark.parametrize("module", ALL_MODULES)
def test_other_roles_unaffected(as_role, role, module):
    """這次修補不能連帶擋掉本來就有權限的人。"""
    as_role(role)
    assert can_access_module(1, "P-p", module) is True


def test_non_member_blocked(as_role):
    as_role(None)
    assert can_access_module(1, "P-p", "literature") is False


# --- 開放開關不得連動 -------------------------------------------------------

@pytest.mark.parametrize("module", ["literature", "paq", "study", "submit"])
def test_open_access_does_not_open_modules(as_role, module):
    """coauthor_open_access 的語意是「放寬能讀到哪些章節」。

    若它同時打開模組權限，owner 會誤判自己開放了什麼——
    以為只是讓共同作者看得到全文，實際上連文獻庫與金額都攤開了。
    """
    as_role("coauthor", open_access=True)
    assert can_access_module(1, "P-p", module) is False


def test_open_access_still_allows_manuscript(as_role):
    as_role("coauthor", open_access=True)
    assert can_access_module(1, "P-p", "manuscript") is True


# --- 白名單制 ---------------------------------------------------------------

def test_unknown_module_defaults_to_blocked_for_coauthor(as_role):
    """新增模組時預設要是「限定編輯看不到」。

    忘記更新白名單的後果應該是少看到東西，而不是外洩。
    """
    as_role("coauthor")
    assert can_access_module(1, "P-p", "some_future_module") is False


def test_whitelist_contains_only_manuscript():
    assert COAUTHOR_ALLOWED_MODULES == frozenset({"manuscript"})


def test_no_module_means_no_gating(as_role):
    """判斷不出模組的路徑（例如 Dashboard）不擋。"""
    as_role("coauthor")
    assert can_access_module(1, "P-p", None) is True


# --- 沒帶 pid 的裸頁面 ------------------------------------------------------
#
# 迴歸案例：第一版把「沒有任何成員資格」也當成限定編輯處理，
# 導致剛註冊、還沒加入任何專案的帳號連 /literature 都開不了（403）。
# 是既有的 test_literature_bootstrap_isolation 抓到的。

class _FakeMember:
    def __init__(self, role):
        self.role = role


def _patch_memberships(monkeypatch, roles):
    monkeypatch.setattr(
        security, "_has_any_non_section_scoped_membership",
        lambda uid: (not roles) or any(r != "coauthor" for r in roles),
    )


def test_brand_new_account_without_projects_is_allowed(monkeypatch, as_role):
    """沒有專案 != 限定編輯。新帳號要進得去，頁面自然是空的。"""
    as_role(None)
    _patch_memberships(monkeypatch, [])
    assert can_access_module(1, None, "literature") is True


def test_coauthor_only_account_blocked_on_bare_page(monkeypatch, as_role):
    """所有成員資格都是限定編輯 → 裸頁面也該擋。"""
    as_role(None)
    _patch_memberships(monkeypatch, ["coauthor", "coauthor"])
    assert can_access_module(1, None, "literature") is False


def test_mixed_role_account_allowed_on_bare_page(monkeypatch, as_role):
    """在別的專案是 editor，就不該被當成限定編輯擋掉。"""
    as_role(None)
    _patch_memberships(monkeypatch, ["coauthor", "editor"])
    assert can_access_module(1, None, "literature") is True


def test_bare_manuscript_always_allowed(monkeypatch, as_role):
    as_role(None)
    _patch_memberships(monkeypatch, ["coauthor"])
    assert can_access_module(1, None, "manuscript") is True
