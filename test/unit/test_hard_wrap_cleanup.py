# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_hard_wrap_cleanup.py
# 子系統定位:
#   Word 貼上殘留硬換行清理（NOTE-038）的**判定規則**與**安全性質**。
# 主要責任:
#   1. 規則本身：以正式站真實稿件（DGVRYV-p V12）的實際形狀為樣本，
#      驗證「句中換行」與「句末換行」被正確分類。
#   2. 安全性質（原始碼層）：只動 <br>、不自動執行、不自動存檔、要回報數量。
# 明確不負責:
#   - 不驗瀏覽器真的把節點換掉了。這台機器沒有 Node，行為證據只能來自真瀏覽器，
#     記在 docs/HANDOFF.md。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   唯讀 app/static/js/manuscript_soed.js；不寫任何檔案、不連線。
# ACL/安全邊界:
#   無（純靜態原始碼讀取 + 純函式規則驗證）。
# 不變量:
#   - 清理只移除 <br>，任何文字節點都不得被刪除或改寫。
#   - 不得在載入／存檔路徑自動觸發（那等於在使用者沒看到時改他的論文）。
# 相關 NOTE:
#   NOTE-038（本檔主體）、NOTE-030（產生這些 <br> 的原始缺陷）。
# 驗證:
#   python -m pytest test/unit/test_hard_wrap_cleanup.py -q
# ---------------------------------------------------------------------------
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOED = PROJECT_ROOT / "app" / "static" / "js" / "manuscript_soed.js"


def _method_body(name):
    src = SOED.read_text(encoding="utf-8")
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
    raise AssertionError(f"{name} 大括號未配對")


# ---------------------------------------------------------------------------
# 規則：用 JS 裡那兩條 regex 的 Python 等價物，餵真實稿件的片段
# ---------------------------------------------------------------------------

PREV_OK = re.compile(r"[A-Za-z0-9,;:)\]\-一-鿿]$")
NEXT_OK = re.compile(r"^[A-Za-z0-9(一-鿿]")


def _is_soft_wrap(prev, nxt):
    prev = prev.rstrip()
    if not prev or not nxt:
        return False
    if not PREV_OK.search(prev):
        return False
    return bool(NEXT_OK.match(nxt.lstrip()))


class TestRuleMatchesTheRealManuscript:
    """樣本全部取自正式站 DGVRYV-p 的 paper/V12.json（實際量到 146 個 <br>）。"""

    # (前文, 後文) —— 這些在真實稿件裡就是句子中間的硬斷行
    REAL_SOFT_WRAPS = [
        ("revalence of bilingual communication and code-switching", "requires automatic speech"),
        ("omatic speech recognition systems capable of processing", "mixed-language inputs."),
        ("inputs. Traditional speech recognition solutions, which", "primarily rely on"),
        ("onolingual architectures or rule-based combined models,", "encounter structural"),
        ("al limitations when processing dynamic intra-sentential", "Mandarin-English code"),
        ("arin-English code-switched dataset comprising", "8,420 synthesized natural"),
        ("entional VOSK framework with a self-evaluated", "Mandarin-English mixed"),
        ("ric misalignment between monolingual Mandarin", "Character-Error Rate (CER)"),
        ("pared to a standard monolingual WER of around", "10% "),
        ("han\"><span lang=\"EN-US\">Recently, integrating", "Transformer-based speech"),
        # 縮排續行：<br> 後面先有一串空白才是文字
        ("n & Arbitration (BA 44 & BA 45):", "     Finally, signals reach the inner"),
        ("lex syntactic parsing, predictive coding", "     (anticipating logical sentence"),
        ("his executive control, a third impartial", "     LLM operates as an \"AI Judge\""),
    ]

    def test_all_real_mid_sentence_wraps_are_detected(self):
        missed = [(p, n) for p, n in self.REAL_SOFT_WRAPS if not _is_soft_wrap(p, n)]
        assert not missed, f"真實稿件裡的句中硬換行沒被認出來: {missed}"

    # 句末之後的換行：作者可能是故意的，必須保留
    SENTENCE_ENDS = [
        ("This constrains their application.", "The next paragraph begins"),
        ("完成了實驗。", "接下來說明結果"),
        ("Is that so?", "Yes it is"),
        ("Stop!", "Now continue"),
    ]

    def test_sentence_end_breaks_are_preserved(self):
        wrongly_removed = [(p, n) for p, n in self.SENTENCE_ENDS if _is_soft_wrap(p, n)]
        assert not wrongly_removed, f"句末換行被誤判成殘骸: {wrongly_removed}"

    def test_break_with_no_following_text_is_preserved(self):
        assert not _is_soft_wrap("some text", "")
        assert not _is_soft_wrap("", "some text")

    def test_break_before_a_tag_is_preserved(self):
        """`[7]<br></b>` 這種後面直接接標籤的不該被當成句中換行。"""
        assert not _is_soft_wrap("style=\"text-align: justify;\">[7]", "")


class TestSafetyProperties:
    def test_only_br_nodes_are_touched(self):
        body = _method_body("cleanupHardWraps")
        # 允許移除/取代 <br>；不得對文字節點動刀。
        assert "querySelectorAll('br')" in body
        for forbidden in ("innerHTML =", "textContent =", "innerText ="):
            assert forbidden not in body, (
                f"清理路徑出現 {forbidden}，那會改寫文字內容而不只是 <br>")

    def test_mid_sentence_break_becomes_a_space_not_nothing(self):
        """直接刪掉會讓前後字黏在一起（`code-switchingrequires`）。"""
        body = _method_body("cleanupHardWraps")
        assert "createTextNode(' ')" in body

    def test_cleanup_does_not_autosave(self):
        body = _method_body("cleanupHardWraps")
        for forbidden in ("cmd_save_paper", "saveAllBlocks", "scheduleAutosave",
                          "cmd_autosave_block"):
            assert forbidden not in body, (
                f"清理不得自動存檔（出現 {forbidden}）；使用者要能靠重整放棄")

    def test_cleanup_reports_how_many_were_removed(self):
        body = _method_body("cleanupHardWraps")
        assert "showNotice" in body
        assert "removed" in body

    def test_cleanup_is_only_triggered_by_an_explicit_button(self):
        """不得掛在載入／存檔／切章路徑上自動執行。"""
        src = SOED.read_text(encoding="utf-8")
        callers = [ln.strip() for ln in src.split("\n")
                   if "cleanupHardWraps(" in ln and "cleanupHardWraps()" not in ln.split("(")[0]]
        # 原始碼內只該有定義本身，沒有其他呼叫端。
        internal = [c for c in callers if not c.startswith("cleanupHardWraps")]
        assert not internal, f"cleanupHardWraps 有內部呼叫端: {internal}"

        html = (PROJECT_ROOT / "app" / "templates"
                / "manuscript_workspace.html").read_text(encoding="utf-8")
        assert "wsApp.soed.cleanupHardWraps()" in html, "模板缺少那顆按鈕"

    def test_rule_uses_dom_not_regex_over_html(self):
        """用 regex 改寫 HTML 是已知的坑：那份稿件的 style 屬性裡就有跳脫過的 br 字樣。"""
        body = _method_body("_isSoftWrapBreak")
        assert "previousSibling" in _method_body("_visibleTextBefore")
        assert "nextSibling" in _method_body("_visibleTextAfter")
        assert "innerHTML" not in body
