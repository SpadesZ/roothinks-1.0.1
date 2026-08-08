# 檔案路徑: test/unit/test_manuscript_editor_signals.py
# 產生時間: 2026-08-09 11:05 +08:00
# 版本: v1.0
# 模組定位:
#   手稿工作區「訊號不得說謊」的回歸護欄。
# 主要責任:
#   1. 守住 insertEditorCard 的插入點前置動作 —— 少了它，載入舊版與復原草稿
#      會回報成功但畫布全白。
#   2. 守住連線徽章不得是寫死的 Online。
#   3. 證明段落檢索失敗時會留下日誌，而不是靜默降級。
# 呼叫來源:
#   pytest test/unit。
# 輸入輸出契約:
#   - 前端部分是靜態檢查：這個 repo 沒有 JS 測試框架也沒有 CI，
#     沿用 test_pi_role_frontend_guard.py 既有作法，由 pytest 盯住危險寫法。
#   - 後端部分走 ManuscriptRuling.validate_and_prepare 真實路徑。
# 安全邊界:
#   - 只讀檔案，不寫入 data/。
# 維護提醒:
#   - 靜態檢查擋不住「有呼叫但順序錯」以外的情形；真正的載入行為仍須
#     以瀏覽器實機點過（見 docs/HANDOFF.md「怎麼驗證」）。
#   - 改動 manuscript_soed.js 時記得同步 bump 模板的 ?v=，否則瀏覽器吃舊檔。
# -----------------------------------------------------------------------------

import logging
import re
from pathlib import Path

import pytest

from app.core_pro.manuscript.manuscript_ruling import ManuscriptRuling

ROOT = Path(__file__).resolve().parents[2]
SOED_JS = ROOT / "app/static/js/manuscript_soed.js"
MANU_HTML = ROOT / "app/templates/manuscript_workspace.html"


def _js():
    return SOED_JS.read_text(encoding="utf-8")


def _html():
    return MANU_HTML.read_text(encoding="utf-8")


def _method_body(src: str, name: str) -> str:
    """粗略取出某個 class method 的內容：從 `    name(` 到下一個同縮排的方法。

    只用於靜態順序檢查，不需要真的 parse JS。
    """
    start = re.search(rf"^    {re.escape(name)}\(", src, re.MULTILINE)
    assert start, f"找不到方法 {name}"
    rest = src[start.end():]
    nxt = re.search(r"^    [A-Za-z_]\w*\(", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


# --- 1. 插入點前置動作 -------------------------------------------------------

def test_insert_editor_card_places_caret_before_exec_command():
    """execCommand('insertHTML') 插在「目前的插入點」，插入點不在畫布內時它會
    安靜地什麼都不做（不丟例外、不回 false）。

    block_loaded 與草稿復原都是「先清空畫布再呼叫 insertEditorCard」，
    因此若少了這個前置動作，使用者看到的就是「回報載入成功、畫布卻是空的」。
    """
    body = _method_body(_js(), "insertEditorCard")
    guard = body.find("this._placeCaretInEditor()")
    exec_cmd = body.find("execCommand('insertHTML'")

    assert guard != -1, (
        "insertEditorCard 未呼叫 _placeCaretInEditor()："
        "載入舊版與復原草稿會回報成功但畫布全白。"
    )
    assert exec_cmd != -1, "insertEditorCard 找不到 execCommand('insertHTML')"
    assert guard < exec_cmd, "_placeCaretInEditor() 必須在 execCommand 之前呼叫"


def test_place_caret_helper_focuses_and_collapses_to_end():
    """反向確認 helper 沒有被掏空成空函式。"""
    body = _method_body(_js(), "_placeCaretInEditor")
    for needle in ("canvas.focus()", "selectNodeContents", "collapse(false)", "addRange"):
        assert needle in body, f"_placeCaretInEditor 少了 {needle}"


def test_broken_call_sites_still_clear_canvas_before_insert():
    """這兩處就是使用者回報的路徑；保留斷言以免日後被改成別的寫法而失去覆蓋。"""
    src = _js()
    assert "this.insertEditorCard(data.content.content, data.section)" in src, \
        "block_loaded 載入舊版的呼叫點不見了"
    assert "this.insertEditorCard(draft.content, section)" in src, \
        "自動儲存草稿復原的呼叫點不見了"


# --- 2. 連線徽章 -------------------------------------------------------------

def test_connection_badge_is_not_hardcoded_online():
    """徽章原本是寫死的 bg-success/Online：socket 一次都沒連上時仍顯示綠燈，
    害 CORS 那次的診斷往錯方向走了一輪。"""
    html = _html()
    assert 'id="socketStatusBadge"' in html, "連線徽章沒有 id，無法被 JS 更新"
    assert not re.search(
        r'<span class="badge bg-success[^"]*"[^>]*>Online</span>', html
    ), "偵測到寫死的綠色 Online 徽章"


def test_connection_badge_bound_to_real_socket_events():
    src = _js()
    assert "_setConnectionBadge(" in src
    assert "this.app.socket.on('connect_error'" in src, \
        "沒有 connect_error handler，連不上時畫面不會有任何跡象"
    for state in ("'online'", "'offline'"):
        assert f"_setConnectionBadge({state}" in src, f"徽章沒有 {state} 狀態的更新點"


def test_connection_badge_initialises_from_current_socket_state():
    """socket 在 manuscript_ws.js 就建立，往往在 setupSocketEvents() 註冊 handler
    之前就已連上 —— 'connect' 事件不會補送，只靠事件更新的徽章會永遠停在
    Connecting…。

    這個缺陷是**瀏覽器實測**才抓到的：socket.connected 為 true、console 沒有任何
    [Socket] Connection established、徽章卻是灰的。純靜態檢查（上面那個測試）
    當時是綠的 —— 這就是為什麼 UI 一定要真的點過。
    """
    body = _method_body(_js(), "setupSocketEvents")
    init = body.find("_setConnectionBadge(")
    on_connect = body.find("this.app.socket.on('connect'")
    assert init != -1, "setupSocketEvents 沒有先以當下狀態初始化徽章"
    assert on_connect != -1, "找不到 connect handler"
    assert init < on_connect, (
        "徽章必須在註冊 connect handler 之前先讀 socket.connected 初始化；"
        "只靠事件的話，早於註冊時機的連線永遠不會反映出來"
    )
    assert "socket.connected" in body[init:on_connect], \
        "初始化沒有讀 socket.connected，等於又是一個猜的狀態"


# --- 3. 標題不得被檔名清洗毀掉 ------------------------------------------------

@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """把 DATA_ROOT 指到 tmp_path，完全不碰 repo 的 data/ 目錄。"""
    import app.core_pro.manuscript.manuscript_io as mio
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(mio, "_get_data_root", lambda: str(root))
    yield root


# 正式站實測被毀掉的那個標題（存進去時變成
# 'An_LLM-Augmented_..._Bilingua'，在 Bilingual 中間被截斷）。
LONG_TITLE = (
    "An LLM-Augmented Validation and Analysis Framework for "
    "A Self Evaluated Bilingual ASR System"
)


@pytest.mark.parametrize("title", [
    LONG_TITLE,                       # 含空白且超過 80 字元
    "中文研究題目：雙語語音辨識",        # 非 ASCII 會被整串換成底線
    "Title with (parens) & symbols",
])
def test_paper_version_preserves_display_title(data_root, title):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    import json

    ManuscriptIO.save_paper_version("TITLE01-p", title, "<p>body</p>")
    saved = json.loads(
        (data_root / "TITLE01-p" / "manuscript" / "paper" / "V1.json").read_text(encoding="utf-8")
    )
    assert saved["title"] == title, (
        "版本檔存的標題被檔名清洗毀掉了；標題早已不進檔名（V1.json），不該再清洗"
    )


@pytest.mark.parametrize("title", [LONG_TITLE, "中文研究題目：雙語語音辨識"])
def test_block_version_preserves_display_title(data_root, title):
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO
    import json

    res = ManuscriptIO.save_block_version("TITLE02-p", "abstract", title, "body")
    path = data_root / "TITLE02-p" / "manuscript" / "block" / "abstract" / res["filename"]
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == title


def test_safe_component_still_sanitises_paths():
    """反向確認：檔名／目錄名的清洗沒有被一起拿掉，否則中文章節會撞目錄。"""
    from app.core_pro.manuscript.manuscript_io import ManuscriptIO

    assert ManuscriptIO._safe_component("a b/c") == "a_b_c"
    assert ManuscriptIO._section_dir_name("緒論") != ManuscriptIO._section_dir_name("討論")


# --- 4. Title 要寫回專案才會在重整後留存 --------------------------------------

def test_title_is_persisted_back_to_project():
    """擁有者決策：手稿 Title 即專案題目。

    在這之前 Title 只進版本檔 payload，頁面載入一律以 research_title 重填，
    所以「改了、重整就變回舊的」。
    """
    src = _js()
    assert "async savePaperTitle(" in src, "沒有把 Title 寫回專案的路徑"
    assert "/api/project/update/" in src, "沒有呼叫既有的專案更新端點"
    assert "this.savePaperTitle()" in src, "Title 輸入框沒有掛上儲存事件"


def test_title_save_reuses_http_endpoint_not_a_new_socket_route():
    """刻意不另開 socket 端點：/api/project/update 已有 enforce_project_ownership
    （POST 需 editor），自建一條等於繞過權限檢查。"""
    routes = (ROOT / "app/core_pro/manuscript/manuscript_routes.py").read_text(encoding="utf-8")
    assert "cmd_save_paper_title" not in routes, (
        "偵測到新增的標題 socket 端點；那會繞過 /api/project/update 的 ownership 檢查"
    )


def test_title_input_has_maxlength_matching_db_declaration():
    """Project.name / research_title 宣告 300；輸入框沒有上限的話，
    超長字串會靜靜寫進 SQLite（不強制長度），換 PostgreSQL 才炸。"""
    html = _html()
    match = re.search(r'<input[^>]*id="paperTitle"[^>]*>', html, re.DOTALL)
    assert match, "找不到 paperTitle 輸入框"
    assert 'maxlength="300"' in match.group(0)


# --- 5. 檢索失敗必須留下痕跡 --------------------------------------------------

def test_retrieval_failure_is_logged_not_silent(monkeypatch, caplog):
    """檢索失敗時草稿仍應產出（degrade, not crash），但必須留下日誌。

    原本這個 except 完全靜默，檢索一壞使用者只會拿到沒有依據的草稿而毫無訊號
    —— 先前那個 UnboundLocalError 就是躲在同一個區塊裡。
    """
    from app.core_pro.manuscript import context_inject

    monkeypatch.setattr(ManuscriptRuling, "_read_local_file", lambda *args: None)
    monkeypatch.setattr(ManuscriptRuling, "_load_upstream_context", lambda pid: "")

    def boom(**kwargs):
        raise RuntimeError("evidence index unavailable")

    monkeypatch.setattr(context_inject, "retrieve_paragraph_context", boom)

    with caplog.at_level(logging.ERROR):
        result = ManuscriptRuling.validate_and_prepare(
            pid="DGVRYV-p",
            title="Grounded ASR",
            section="abstract",
            s_ver="0.1",
            current_context="草稿內容。" * 20,
            attachment=None,
            import_type="other",
            user_prompt="請寫摘要",
        )

    assert result["ok"] is True, "檢索失敗不應讓整個生成中斷"
    assert result["injected_context_ids"] == []
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "段落檢索失敗" in joined, (
        "檢索失敗沒有留下任何日誌 —— 這正是先前查不到 UnboundLocalError 的原因"
    )
    assert "evidence index unavailable" in caplog.text, "沒有保留原始例外堆疊"
