# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_fusion_legacy_adoption.py
# 子系統定位:
#   2C 舊區塊認領（NOTE-037）的**守衛條件**，以及缺陷根因的可機檢紀錄。
# 主要責任:
#   1. 釘住根因：NOTE-007 之前的 fusion-block 樣板**沒有 data-section**
#      （直接從 git 的那個 commit 讀出來比對，不是憑記憶寫在測試裡）。
#   2. 釘住四道認領門檻。這是整個 2C 最容易把使用者稿件搬錯位置的地方：
#      - 只認領完全沒有 data-section 的區塊（不得從別章手上搶）
#      - 標題必須**全等**（不得用 includes / startsWith）
#      - 只認領第一個
#      - 不得搬動位置（不得出現 insertBefore / appendChild(block) 之類的搬移）
#   3. 同名多個時**不得自動刪除**使用者的稿件。
# 明確不負責:
#   - 不驗瀏覽器真的把內容貼進去了。這台機器沒有 Node，行為證據只能來自真瀏覽器，
#     記在 docs/HANDOFF.md（載入舊 HTML → 推同章 → 仍只有一個該章區塊）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   唯讀 app/static/js/manuscript_soed.js 與 git object；不寫任何檔案。
# ACL/安全邊界:
#   無（純靜態原始碼讀取）。
# 不變量:
#   - 放寬任何一道門檻都會讓「推一次 Abstract」有機會改到別的章節。
# 相關 NOTE:
#   NOTE-037（本檔主體）、NOTE-007（原始的 upsert 決策）、NOTE-030（同一個函式）。
# 驗證:
#   python -m pytest test/unit/test_fusion_legacy_adoption.py -q
# ---------------------------------------------------------------------------
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

SOED = PROJECT_ROOT / "app" / "static" / "js" / "manuscript_soed.js"

# NOTE-007 之前的實作所在的 commit。根因就是這個版本的樣板。
LEGACY_COMMIT = "32e1949"


def _source():
    return SOED.read_text(encoding="utf-8")


def _method_body(name):
    """取出某個方法的原始碼（以大括號配對，不用行數，才不會被註解量影響）。"""
    src = _source()
    m = re.search(r"^\s*" + re.escape(name) + r"\s*\([^)]*\)\s*\{", src, re.M)
    assert m, f"找不到方法 {name}"
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
        i += 1
    raise AssertionError(f"{name} 的大括號沒有配對")


# ---------------------------------------------------------------------------
# 根因：舊樣板沒有 data-section
# ---------------------------------------------------------------------------

def _legacy_source():
    try:
        out = subprocess.run(
            ["git", "show", f"{LEGACY_COMMIT}:app/static/js/manuscript_soed.js"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git 不可用，跳過歷史根因比對")
    if out.returncode != 0:
        pytest.skip(f"取不到 {LEGACY_COMMIT} 的檔案（淺複製？）")
    return out.stdout.decode("utf-8", errors="replace")


def test_legacy_template_really_had_no_section_identity():
    """根因釘死：舊樣板產生的區塊沒有身分，所以今天查不到、只能 append。

    這一條是從 git 讀真的歷史原始碼，不是把結論抄進測試裡。
    """
    legacy = _legacy_source()
    m = re.search(r"const fusionHtml = `(.*?)`;", legacy, re.S)
    assert m, "舊版找不到 fusionHtml 樣板"
    template = m.group(1)
    assert "fusion-block" in template
    assert "data-section" not in template, (
        "舊樣板若有 data-section，NOTE-037 的根因就不成立")
    # 而且它是 append —— 這才是「多一個 Abstract」的機制。
    assert "fusionCanvas.innerHTML +=" in legacy


def test_current_template_has_section_identity():
    """對照組：現在新建的區塊一定帶 data-section。"""
    body = _method_body("_ensureFusionBlock")
    assert "setAttribute('data-section', sectionId)" in body


# ---------------------------------------------------------------------------
# 四道認領門檻（NOTE-037）
# ---------------------------------------------------------------------------

class TestAdoptionGuards:
    def test_only_adopts_blocks_without_section_identity(self):
        """絕不從別的章節手上搶區塊。"""
        body = _method_body("_adoptLegacyFusionBlock")
        assert "!b.hasAttribute('data-section')" in body, (
            "少了『必須完全沒有 data-section』這道，認領會搶走別章的區塊")

    def test_heading_match_is_exact_not_substring(self):
        """`Abstract` 不得認領 `Abstract and Keywords`。"""
        body = _method_body("_adoptLegacyFusionBlock")
        assert "===" in body, "標題比對必須是全等"
        for loose in (".includes(", ".startsWith(", ".indexOf(", ".match("):
            assert loose not in body, (
                f"認領用了寬鬆比對 {loose}，會把相近章節名認錯")

    def test_adopts_only_the_first_match(self):
        body = _method_body("_adoptLegacyFusionBlock")
        assert "candidates[0]" in body, "必須只認領第一個相符者"

    def test_adoption_does_not_move_the_block(self):
        """認領只補屬性，不搬位置 —— 使用者的排版是他自己排的。"""
        body = _method_body("_adoptLegacyFusionBlock")
        for mover in ("insertBefore", "prepend(", "canvas.appendChild",
                      "before(", "after("):
            assert mover not in body, f"認領路徑出現搬移動作：{mover}"

    def test_adoption_never_deletes_user_content(self):
        """同名多個時提示使用者，**不得**自動刪除稿件。"""
        body = _method_body("_adoptLegacyFusionBlock")
        for destructive in (".remove()", "removeChild", "innerHTML = ''"):
            assert destructive not in body, (
                f"認領路徑出現破壞性操作：{destructive}；靜默刪除比留下重複更糟")
        assert "showNotice" in body, "同名多個時必須告知使用者"

    def test_heading_text_helper_strips_chrome_on_a_clone(self):
        """剝 icon/徽章必須在複本上做，否則會把畫面上的標題改掉。"""
        body = _method_body("_blockHeadingText")
        assert "cloneNode(true)" in body, (
            "直接在畫面節點上 remove() 會把使用者看到的標題內容刪掉")
        assert "fusion-src-ver" in body


class TestLookupWiring:
    def test_find_falls_back_to_adoption(self):
        body = _method_body("_findFusionBlock")
        assert "_adoptLegacyFusionBlock" in body, (
            "_findFusionBlock 沒有接上認領，舊區塊仍然查不到")

    def test_identity_match_still_wins(self):
        """帶身分的比對必須先做；認領只是找不到時的退路。"""
        body = _method_body("_findFusionBlock")
        id_pos = body.index("data-section]")
        adopt_pos = body.index("_adoptLegacyFusionBlock")
        assert id_pos < adopt_pos, "認領不得排在身分比對之前"


class TestNormalisation:
    def test_normalise_is_case_and_whitespace_insensitive(self):
        body = _method_body("_normaliseHeading")
        assert "toLowerCase()" in body
        assert "trim()" in body
        assert re.search(r"replace\(/\\s\+/g", body), "必須收斂連續空白"
