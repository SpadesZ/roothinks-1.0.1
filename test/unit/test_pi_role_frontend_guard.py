# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_pi_role_frontend_guard.py
# 主要責任: 重現並驗收 pi role frontend guard 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
"""
路徑(./test/unit/test_pi_role_frontend_guard.py)
版本 v1.0
更版時間 20260728

模組定位：前端 PI 判定的靜態防護。
背景：「共同主持人 (Co-PI)」這個字串同時包含「主持人」、「主持」與「PI」，
      所以任何以 includes / indexOf 做的 PI 判定都會把 Co-PI 誤判成 PI。
      後端 _validate_members 用 startswith 是對的，前端一度用 includes，
      造成「一位 PI + 一位 Co-PI」被擋下、卡片顯示錯的主持人。
為什麼要靜態檢查：這個 repo 沒有 JS 測試框架也沒有 CI，
      所以 pytest 直接盯住危險寫法；角色輸入輸出的行為另由
      test/js/test_pi_role_matching.js 使用 Node 內建 assert 驗證。
維護提醒：
      新增任何會判斷「誰是主持人」的前端檔案時，請把它加進 _FILES。
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FILES = [
    "app/static/js/dashboard.js",
    "app/static/js/paq_project.js",
]

# 會把 Co-PI 誤判成 PI 的寫法。'主持'、'主持人'、'PI'、'Principal' 都是
# 「共同主持人 (Co-PI)」的子字串。
_DANGEROUS = re.compile(
    r"""\.(includes|indexOf)\(\s*['"](主持人?|PI|Principal)['"]""",
)

CO_PI = "共同主持人 (Co-PI)"
PI = "主持人 (Principal Investigator)"


def _read(rel):
    p = _ROOT / rel
    if not p.exists():
        pytest.skip(f"{rel} 不存在")
    return p.read_text(encoding="utf-8")


def test_the_substring_trap_is_real():
    """先確認前提成立，否則這整組測試就沒有意義。"""
    for needle in ["主持人", "主持", "PI"]:
        assert needle in CO_PI
    assert not CO_PI.startswith("主持人")
    assert PI.startswith("主持人")


@pytest.mark.parametrize("rel", _FILES)
def test_no_substring_pi_detection(rel):
    """前端不得再用 includes/indexOf 判斷主持人。"""
    src = _read(rel)
    hits = []
    for lineno, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("//"):
            continue          # 註解裡提到 includes 是在解釋為何不能用，放行
        if _DANGEROUS.search(line):
            hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert not hits, (
        "偵測到會把 Co-PI 誤判成 PI 的寫法，請改用 startsWith('主持人')：\n"
        + "\n".join(hits)
    )


@pytest.mark.parametrize("rel", _FILES)
def test_uses_startswith(rel):
    """反向確認：檔案裡真的有 startsWith 版本的判定，而不是整段被刪掉。"""
    src = _read(rel)
    assert "startsWith('主持人')" in src or 'startsWith("主持人")' in src, (
        f"{rel} 找不到 startsWith('主持人') 判定"
    )


def test_dashboard_exposes_shared_helper():
    """dashboard.js 的判定要集中在一個具名函式，避免各處各寫一份。"""
    src = _read("app/static/js/dashboard.js")
    assert "function isPrincipalInvestigator(" in src
    # 四個使用點：卡片顯示 x2、下拉 selected、送出前驗證
    assert src.count("isPrincipalInvestigator(") >= 5


def test_paq_exposes_node_testable_helper():
    src = _read("app/static/js/paq_project.js")
    assert "function isPaqPrincipalInvestigator(" in src
    assert "module.exports = { isPaqPrincipalInvestigator }" in src
