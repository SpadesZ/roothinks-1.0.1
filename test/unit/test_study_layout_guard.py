# 檔案路徑: test/unit/test_study_layout_guard.py
# 產生時間: 2026-08-06 19:00 +08:00
# 版本: v1.0
# 模組定位:
#   Study 頁版面修正的回歸護欄（原始碼層級）。
# 背景:
#   2026-08-06 使用者回報四件事：
#     1. Idle 狀態顯示「等待任務啟動...」純屬雜訊。
#     2. 破圖 —— 工具列擠爆後壓到 aux-pane 上，「重跑解說」被壓成直書。
#     3. AI 家教／研究筆記寫死 h-50，拉不動。
#     4. 分割拉不小，minSize [400,300] 加 gutter 就吃掉 708px。
#   實測（真實 study.css + 原樣 markup，pane 拉到 300px）：
#     修正前「重跑解說」按鈕是 32×136（直書），修正後 92×31 且溢出 0px。
#     根因是 .matrix-toolbar 為了讓維度篩選的 dropdown 能溢出而設
#     overflow:visible，卻沒有 flex-wrap —— 擠不下就直接壓到隔壁 pane。
#     解法不是改 hidden（dropdown 會被剪掉），是讓工具列會換行。
# 主要責任:
#   守住四項修正不被無聲改回去，外加 study.css 的 cache-bust 版號。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   純文字斷言，證明不了「畫出來對不對」—— 那部分靠實際量測，
#   見上面背景記錄的數字。這裡只擋「有人把設定改回去」。
# 安全邊界:
#   - 只讀檔，不啟動應用程式。
# 維護提醒:
#   - 改 study.css 一定要 bump study.html 裡的 ?v=，否則瀏覽器吃舊樣式表，
#     看起來就像「CSS 改了沒生效」。test_study_css_is_cache_busted 擋這個。
# 驗證方式:
#   python -m pytest test/unit/test_study_layout_guard.py -q
# ------------------------------------------------------------------------------
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

HTML = (PROJECT_ROOT / "app" / "templates" / "study.html").read_text(encoding="utf-8")
CSS = (PROJECT_ROOT / "app" / "static" / "css" / "study.css").read_text(encoding="utf-8")

# 註解不會被畫出來。修正的註解本身就會提到舊字串「等待任務啟動」，
# 不剝掉的話這道測試會咬到自己的說明文字。
HTML_RENDERED = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)


# --- 1. Idle 佔位字串 ------------------------------------------------------

def test_matrix_title_has_no_idle_placeholder():
    assert "等待任務啟動" not in HTML_RENDERED
    assert re.search(r'<span id="matrix-title"[^>]*>\s*</span>', HTML_RENDERED), (
        "matrix-title 必須預設為空，靠 #matrix-title:empty 隱藏"
    )


def test_empty_matrix_title_is_hidden_by_css():
    assert re.search(r"#matrix-title:empty\s*\{[^}]*display:\s*none", CSS)


def test_no_orphan_vr_before_matrix_title():
    """分隔線要做成 title 的 border-left，否則 title 隱藏後會留一根孤兒直線。"""
    assert not re.search(r'<div class="vr[^"]*"></div>\s*<span id="matrix-title"', HTML)
    assert re.search(r"#matrix-title:not\(:empty\)\s*\{[^}]*border-left", CSS)


# --- 2. 破圖 ---------------------------------------------------------------

def test_toolbar_wraps_instead_of_overflowing():
    """核心：工具列必須會換行。

    overflow 要維持 visible（維度篩選的 dropdown 得溢出），
    所以唯一能避免壓到 aux-pane 的辦法就是不產生水平溢出。
    """
    block = re.search(r"\.matrix-toolbar\s*\{(.*?)\}", CSS, re.S)
    assert block, "找不到 .matrix-toolbar 規則"
    body = block.group(1)
    assert re.search(r"flex-wrap:\s*wrap", body), (
        "工具列沒有 flex-wrap:wrap —— 擠不下時會直接壓到 aux-pane 上"
    )
    assert re.search(r"overflow:\s*visible", body), (
        "改成 hidden 會把維度篩選的 dropdown 剪掉"
    )


def test_toolbar_buttons_do_not_shrink_to_vertical_text():
    """按鈕不得被壓縮成逐字斷行（32×136 的直書破圖就是這樣來的）。"""
    assert re.search(
        r"\.matrix-toolbar \.btn,.*?\{[^}]*white-space:\s*nowrap[^}]*flex-shrink:\s*0",
        CSS,
        re.S,
    )


# --- 3 & 4. 分割 -----------------------------------------------------------

def test_aux_panes_have_ids_and_no_fixed_half_height():
    """h-50 是寫死的一半高度，Split.js 拉不動它。"""
    assert 'id="aux-chat-pane"' in HTML
    assert 'id="aux-notes-pane"' in HTML
    for pane_id in ("aux-chat-pane", "aux-notes-pane"):
        tag = re.search(rf'<div id="{pane_id}"[^>]*>', HTML)
        assert tag, pane_id
        assert "h-50" not in tag.group(0), f"{pane_id} 仍寫死 h-50，拉不動"


def test_vertical_split_is_initialised():
    assert re.search(
        r"Split\(\s*\['#aux-chat-pane',\s*'#aux-notes-pane'\].*?direction:\s*'vertical'",
        HTML,
        re.S,
    ), "AI 家教／研究筆記沒有建立上下分割"


def test_split_min_sizes_allow_shrinking():
    """minSize 原本 [400,300]，兩邊加 gutter 就要 708px，視窗一窄就拉不動。"""
    sizes = [
        [int(n) for n in m.split(",")]
        for m in re.findall(r"minSize:\s*\[([\d,\s]+)\]", HTML)
    ]
    assert sizes, "找不到 minSize 設定"
    for pair in sizes:
        for value in pair:
            assert value <= 50, f"minSize {value} 太大，分割會拉不小"


# --- 5. cache-bust ---------------------------------------------------------

def test_study_css_is_cache_busted():
    """改了 study.css 卻沒 bump 版號，瀏覽器會吃舊樣式表。"""
    m = re.search(r"filename='css/study\.css'\s*\)\s*\}\}\?v=([\d.]+)", HTML)
    assert m, "study.css 的 <link> 沒有 ?v= 版號"
