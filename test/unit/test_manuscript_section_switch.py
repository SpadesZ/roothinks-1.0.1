# 檔案路徑: test/unit/test_manuscript_section_switch.py
# 產生時間: 2026-08-10 +08:00
# 版本: v1.0
# 模組定位:
#   2B 切換章節時「內容必須隔離、且必須載入目標章版本」的回歸護欄。
# 主要責任:
#   1. 守住 block_list / block_loaded 的 req_token 回送契約 —— 前端靠它辨識
#      遲到的舊章回應，少了它快速 A→B→C 會被 A 的回應覆蓋。
#   2. 守住「每一條 return 路徑都帶 echo」：漏一條，前端就永久停在載入中。
#   3. 守住版本清單順序（新版在前），前端據此自動載入最新版。
#   4. 前端靜態檢查：切章走單一入口、先隔離再載入、stale 檢查、跨章存檔阻擋。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   - 後端部分走真實 Socket.IO test_client，DATA_ROOT 導向 tmp_path。
#   - 前端部分是靜態檢查：這個 repo 沒有 JS 測試框架也沒有 CI，
#     沿用 test_manuscript_editor_signals.py 既有作法，由 pytest 盯住危險寫法。
# 安全邊界:
#   - 只寫 tmp_path，不碰 repo 的 data/ 目錄。
# 維護提醒:
#   - 靜態檢查證明不了「畫面真的換了」。切章行為仍須以瀏覽器實機點過：
#     Introduction 打字存檔 → 切 Reference → 畫布不得殘留 Introduction 內容。
#   - 改動 manuscript_soed.js / manuscript_wsui.js 時記得同步 bump 模板的 ?v=，
#     否則瀏覽器吃舊檔，測試綠燈但使用者看到的還是舊行為。
# 驗證方式:
#   python -m pytest test/unit/test_manuscript_section_switch.py -q
# ------------------------------------------------------------------------------
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

PID = "SWITCHPJ"
FORMAL_PID = "SWITCHPJ-p"

SOED_JS = PROJECT_ROOT / "app/static/js/manuscript_soed.js"
WSUI_JS = PROJECT_ROOT / "app/static/js/manuscript_wsui.js"
MANU_HTML = PROJECT_ROOT / "app/templates/manuscript_workspace.html"
MANU_CSS = PROJECT_ROOT / "app/static/css/manuscript.css"


def _soed():
    return SOED_JS.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """
    去掉註解，只留可執行的程式碼。

    為什麼需要：這一批都是字串比對測試，而「不可以用 X」這種禁令往往同時寫在
    註解裡（例如 `// 這裡不能用原生 confirm()`）。直接對原始碼比對 `confirm(`
    會比中自己寫的說明文字 —— 禁令測試因此永遠是紅的，卻完全沒檢查到真正的程式碼。
    只剝除整行註解與 /* */ 區塊，不動行尾片段，避免誤傷字串裡的 `https://`。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    keep = [ln for ln in src.splitlines() if not re.match(r"\s*(//|\*)", ln)]
    return "\n".join(keep)


def _method_body(src: str, name: str) -> str:
    """粗略取出某個 class method 的內容（同 test_manuscript_editor_signals.py）。"""
    start = re.search(rf"^    {re.escape(name)}\(", src, re.MULTILINE)
    assert start, f"找不到方法 {name}"
    rest = src[start.end():]
    nxt = re.search(r"^    [A-Za-z_]\w*\(", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


# ==============================================================================
# 後端：socket 事件契約
# ==============================================================================


@pytest.fixture(scope="module")
def _app_bundle(tmp_path_factory):
    """App 與 socket client 只建立一次（理由見 test_manuscript_socket_versioning.py）。"""
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("secswitch")

    mp.setenv("FLASK_ENV", "development")
    mp.delenv("APP_ENV", raising=False)
    mp.delenv("AUTH_MODE", raising=False)

    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    mp.setenv("LOCK_ROOT", str(lock_root))

    from app import create_app, db, socketio

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'sw.db'}",
            "SQLALCHEMY_BINDS": {"manuscript": f"sqlite:///{tmp_path / 'sw_manu.db'}"},
            "SOCKETIO_ASYNC_MODE": "threading",
        }
    )

    data_root = tmp_path / "data"
    data_root.mkdir()
    from app.core_pro.manuscript import manuscript_io as mio
    from app.core_pro.manuscript import manuscript_routes as mroutes
    mp.setattr(mio, "_get_data_root", lambda: str(data_root))
    mp.setattr(mroutes, "_get_data_root", lambda: str(data_root))

    with app.app_context():
        db.create_all()
        from app.models import Project
        db.session.add(Project(project_id=FORMAL_PID, name="Switch Test", status="formal"))
        db.session.commit()

    client = app.test_client()
    sio = socketio.test_client(app, flask_test_client=client, namespace="/manu_ws")
    assert sio.is_connected("/manu_ws"), "socket connect refused"
    sio.get_received("/manu_ws")

    yield {"app": app, "sio": sio, "data_root": data_root}

    if sio.is_connected("/manu_ws"):
        sio.disconnect(namespace="/manu_ws")
    with app.app_context():
        db.session.remove()
        for eng in db.engines.values():
            eng.dispose()
    mp.undo()


@pytest.fixture()
def bundle(_app_bundle):
    import shutil

    shutil.rmtree(_app_bundle["data_root"] / FORMAL_PID / "manuscript", ignore_errors=True)
    _app_bundle["sio"].get_received("/manu_ws")
    return _app_bundle


def _events(sio, name):
    return [e for e in sio.get_received("/manu_ws") if e["name"] == name]


def _list_blocks(sio, section, token=None, intent=None, pid=FORMAL_PID):
    payload = {"section": section}
    if pid is not None:
        payload["pid"] = pid
    if token is not None:
        payload["req_token"] = token
    if intent is not None:
        payload["intent"] = intent
    sio.emit("cmd_list_blocks", payload, namespace="/manu_ws")
    events = _events(sio, "block_list")
    assert events, "expected block_list"
    return events[-1]["args"][0]


def _save(sio, section, content):
    sio.emit(
        "cmd_save_block",
        {"pid": FORMAL_PID, "title": "Switch Paper", "section": section, "content": content},
        namespace="/manu_ws",
    )
    acks = _events(sio, "save_ack")
    assert acks, "expected save_ack"
    return acks[-1]["args"][0]


class TestBlockListEchoContract:
    def test_echoes_req_token_and_intent(self, bundle):
        """前端靠 req_token 判斷「這份回應屬於哪一次切章」。"""
        data = _list_blocks(bundle["sio"], "introduction", token="s7", intent="section_switch")
        assert data["req_token"] == "s7"
        assert data["intent"] == "section_switch"
        assert data["section"] == "introduction"

    def test_echo_present_on_missing_pid_path(self, bundle):
        """漏掉任何一條失敗路徑的 echo，前端就永遠停在「載入中…」。

        切章時畫布會先被切成 loading，只有收到「符合目前 token」的回應才離開
        該狀態。沒帶 token 的失敗回應會被 stale 檢查丟掉 —— 使用者看到的是
        一個永遠轉圈、無法編輯的畫布。
        """
        data = _list_blocks(bundle["sio"], "introduction", token="s9",
                            intent="section_switch", pid=None)
        assert data["req_token"] == "s9", "missing-pid 路徑漏掉 echo，前端會卡在載入中"
        assert data["files"] == []

    def test_legacy_payload_unchanged_without_token(self, bundle):
        """舊前端不送 req_token；回應不得因此多出 None 欄位而改變契約。"""
        data = _list_blocks(bundle["sio"], "introduction")
        assert "req_token" not in data
        assert "intent" not in data
        for key in ("files", "section", "versions", "draft", "next_ver"):
            assert key in data, f"舊契約欄位 {key} 不見了"


class TestAutoLoadInputs:
    def test_versions_newest_first_so_index_zero_is_latest(self, bundle):
        """前端自動載入 versions[0]；順序錯就會載到最舊的版本。"""
        sio = bundle["sio"]
        _save(sio, "introduction", "FIRST")
        _save(sio, "introduction", "SECOND")
        _save(sio, "introduction", "THIRD")

        data = _list_blocks(sio, "introduction", token="s1", intent="section_switch")
        vers = [v["ver"] for v in data["versions"]]
        assert vers == ["0.3", "0.2", "0.1"], f"版本順序不是新版在前: {vers}"
        assert data["versions"][0]["filename"] == "V0.3.json"

    def test_never_saved_section_reports_no_versions(self, bundle):
        """對應前端「顯示真正空白、可編輯的新章畫布」分支。"""
        data = _list_blocks(bundle["sio"], "reference", token="s2", intent="section_switch")
        assert data["versions"] == []
        assert data["files"] == []
        assert data["draft"] is None

    def test_autosave_only_section_has_draft_but_no_versions(self, bundle):
        """只有個人 autosave 的章節：不得被當成「有正式版本」而去載入。"""
        sio = bundle["sio"]
        sio.emit(
            "cmd_autosave_block",
            {"pid": FORMAL_PID, "title": "T", "section": "discussion", "content": "DRAFT ONLY"},
            namespace="/manu_ws",
        )
        sio.get_received("/manu_ws")

        data = _list_blocks(sio, "discussion", token="s3", intent="section_switch")
        assert data["versions"] == [], "自動存檔草稿不該出現在版本清單"
        assert data["draft"] and "DRAFT ONLY" in data["draft"]["content"]

    def test_sections_are_isolated_from_each_other(self, bundle):
        """跨章不得互相看到內容：這是「切章後畫布殘留上一章」的資料面反證。"""
        sio = bundle["sio"]
        _save(sio, "introduction", "INTRO BODY")
        _save(sio, "reference", "REF BODY")

        intro = _list_blocks(sio, "introduction", token="a", intent="section_switch")
        ref = _list_blocks(sio, "reference", token="b", intent="section_switch")
        assert intro["versions"][0]["filename"] == "V0.1.json"
        assert ref["versions"][0]["filename"] == "V0.1.json"

        sio.emit("cmd_load_block", {
            "pid": FORMAL_PID, "section": "reference",
            "filename": "V0.1.json", "req_token": "b", "intent": "section_switch",
        }, namespace="/manu_ws")
        loaded = _events(sio, "block_loaded")[-1]["args"][0]
        assert loaded["ok"] is True
        assert loaded["content"]["content"] == "REF BODY", "載到別章的內容了"


class TestBlockLoadedEchoContract:
    def test_echoes_req_token_on_success(self, bundle):
        sio = bundle["sio"]
        _save(sio, "introduction", "BODY")
        sio.emit("cmd_load_block", {
            "pid": FORMAL_PID, "section": "introduction",
            "filename": "V0.1.json", "req_token": "s5", "intent": "section_switch",
        }, namespace="/manu_ws")
        data = _events(sio, "block_loaded")[-1]["args"][0]
        assert data["req_token"] == "s5"
        assert data["section"] == "introduction"

    def test_missing_version_still_reports_block_loaded(self, bundle):
        """版本檔不在時舊版只送 sys_msg —— 切章的自動載入會永遠等不到終止事件。

        必須回一個帶 token 的 ok=False，前端才能退回空白畫布讓使用者繼續作業。
        """
        sio = bundle["sio"]
        sio.emit("cmd_load_block", {
            "pid": FORMAL_PID, "section": "introduction",
            "filename": "V9.9.json", "req_token": "s6", "intent": "section_switch",
        }, namespace="/manu_ws")
        events = _events(sio, "block_loaded")
        assert events, "載入失敗沒有回 block_loaded，前端會卡在載入中"
        data = events[-1]["args"][0]
        assert data["ok"] is False
        assert data["req_token"] == "s6"


# ==============================================================================
# 前端：靜態護欄
# ==============================================================================


class TestSwitchGoesThroughSingleEntryPoint:
    def test_dropdown_delegates_to_request_section_switch(self):
        """章節下拉不得自己 emit cmd_list_blocks —— 那會繞過 stale 防護。"""
        src = WSUI_JS.read_text(encoding="utf-8")
        assert "this.app.soed.requestSectionSwitch(sectionId)" in src, \
            "章節下拉沒有走 requestSectionSwitch"
        # 比對「真的送出」的程式碼形式，而不是字串本身 ——
        # 註解裡會提到這個事件名，用裸字串比對會抓到說明文字而誤判。
        assert "emit('cmd_list_blocks'" not in src, \
            "wsui 仍自行 emit cmd_list_blocks，切章會繞過 token 機制"

    def test_dropdown_no_longer_relies_on_load_multi_section_content(self):
        """loadMultiSectionContent 只在畫布近乎空白時才畫佔位；

        切到有內容的章節時它什麼都不做，上一章的正文與 badge 就原封不動留著
        —— 這正是使用者回報的畫面。
        """
        src = WSUI_JS.read_text(encoding="utf-8")
        assert "soed.loadMultiSectionContent()" not in src, \
            "切章仍依賴 loadMultiSectionContent，無法隔離上一章內容"

    def test_chat_section_switch_also_loads_block(self):
        """這條路徑同時是「重整／離頁返回」的進入點。"""
        body = _method_body(_soed(), "switchChatSection")
        assert "this.requestSectionSwitch(sectionId)" in body, \
            "重整後不會載入該章最新版，正文永遠是空的"


class TestIsolationBeforeLoad:
    def test_request_section_switch_clears_canvas_before_emitting(self):
        """必須先隔離再送請求。

        若等回應到了才清畫布，中間這段時間畫面是「新章標題 + 舊章正文」，
        使用者此時按存檔就會把上一章的內容存進來。
        """
        body = _method_body(_soed(), "requestSectionSwitch")
        clear = body.find("_renderSectionLoading(")
        emit = body.find("cmd_list_blocks")
        assert clear != -1, "切章沒有先把畫布切成 loading"
        assert emit != -1, "切章沒有送出 cmd_list_blocks"
        assert clear < emit, "必須先清畫布再送請求，否則中間狀態可被存檔"

    def test_token_is_monotonic(self):
        body = _method_body(_soed(), "requestSectionSwitch")
        assert "++this._sectionReqSeq" in body, "token 沒有遞增，無法分辨新舊請求"
        assert "this._activeSectionReq = { token, section: sectionId }" in body

    def test_blank_section_uses_editor_card_not_placeholder(self):
        """空白新章必須是 .editor-card：cardActionSave / saveAllBlocks 都只認它，

        舊做法畫的是唯讀佔位區塊，全新章節打完字根本存不了。
        """
        body = _method_body(_soed(), "_renderBlankSection")
        assert "this.insertEditorCard(" in body, "空白新章不是可存檔的 editor-card"


class TestStaleResponseRejection:
    def test_helper_checks_both_token_and_section(self):
        body = _method_body(_soed(), "_isStaleSectionReq")
        assert "data.req_token !== req.token" in body
        assert "data.section !== req.section" in body

    def test_block_list_rejects_stale_switch(self):
        src = _soed()
        assert "const isSwitch = data.intent === 'section_switch'" in src, \
            "block_list 沒有分辨切章來源；開啟舊版 modal 會洗掉編輯中的內容"
        assert "const stale = isSwitch && this._isStaleSectionReq(data)" in src, \
            "block_list 沒有做 stale 檢查，A→B→C 會被舊回應覆蓋"

    def test_block_loaded_rejects_stale_switch(self):
        src = _soed()
        assert "const fromSwitch = data.req_token != null" in src, \
            "block_loaded 沒有辨識自動載入來源"
        assert "if (fromSwitch && this._isStaleSectionReq(data)) return;" in src, \
            "block_loaded 沒有拒絕遲到的舊章回應"

    def test_failed_autoload_falls_back_to_blank_not_spinner(self):
        src = _soed()
        assert "if (!data.ok)" in src, "block_loaded 沒有處理失敗情形"
        assert "已開啟空白畫布" in src, "自動載入失敗後沒有離開 loading 狀態"

    def test_canvas_rewriting_paths_cancel_pending_switch(self):
        """使用者用別的方式改寫畫布時，在途的切章回應必須作廢。"""
        src = _soed()
        assert "_cancelPendingSectionSwitch()" in src
        body = _method_body(src, "importToEditor")
        assert "_cancelPendingSectionSwitch()" in body, \
            "從 2A 複製草稿後，遲到的 block_loaded 會把它換成舊版本"


class TestDraftRestoreOrdering:
    def test_draft_is_deferred_until_content_loaded(self):
        """草稿詢問必須等 block_loaded 之後。

        切章當下畫布還停在 loading，_offerDraftRestore 會判定「畫布是空的」而
        直接復原草稿，接著稍晚到達的 block_loaded 又把它蓋掉 —— 使用者的未存檔
        內容無聲消失。
        """
        body = _method_body(_soed(), "_applySectionSwitch")
        assert "pendingDraft" in body, "有版本時草稿沒有延後處理"
        # 一律比對程式碼形式（帶 this. 與引號），否則會抓到註解裡的說明文字。
        offer = body.find("this._offerDraftRestore(")
        emit = body.find("'cmd_load_block'")
        assert emit != -1, "有版本的分支沒有送出 cmd_load_block"
        # 有版本的分支不得直接呼叫 _offerDraftRestore（只有 else 分支可以）
        assert offer == -1 or offer > emit, "草稿在載入完成前就被復原，會被正文蓋掉"
        assert "_consumePendingDraft" in _soed(), "延後的草稿沒有任何消費點"


class TestCrossSectionSaveGuard:
    def test_card_action_save_refuses_foreign_section(self):
        """存檔目標取自卡片的 data-section；畫布殘留別章卡片時，

        按存檔會替「那一章」建立新版本，而使用者看到的選單是另一章。
        """
        body = _method_body(_soed(), "cardActionSave")
        assert "_currentSectionId()" in body, "cardActionSave 沒有比對目前章節"
        assert "已阻止跨章存檔" in body, "跨章存檔沒有被擋下"
        guard = body.find("已阻止跨章存檔")
        emit = body.find("cmd_save_block")
        assert emit != -1
        assert guard < emit, "阻擋必須發生在送出存檔之前"

    def test_save_all_skips_empty_cards(self):
        """空白新章也是一張正常卡片，不濾掉會替空章生出空版本。"""
        body = _method_body(_soed(), "saveAllBlocks")
        assert "innerText" in body and "length === 0" in body, \
            "saveAllBlocks 沒有濾掉空卡"

    def test_emptiness_check_is_content_based(self):
        body = _method_body(_soed(), "_editorHasContent")
        assert ".editor-card .card-content" in body
        assert "Awaiting content draft" not in body, \
            "改回比對佔位字串會在新增佔位畫面時漏判"


class TestEditorFormattingFeatures:
    def test_toolbar_has_ordered_list_and_level_controls(self):
        html = MANU_HTML.read_text(encoding="utf-8")
        editor_cmds = re.findall(r"wsApp\.soed\.formatText\('([^']+)',\s*null,\s*'editor'\)", html)
        for needed in ("bold", "italic", "underline", "h1", "h2",
                       "insertUnorderedList", "orderedList", "indent", "outdent"):
            assert needed in editor_cmds, f"2B 工具列缺少 {needed}"

    def test_indent_is_restricted_to_list_items(self):
        """execCommand('indent') 在一般段落上產生 <blockquote>，不是「降一層」。"""
        body = _method_body(_soed(), "formatText")
        assert "_selectionInListItem(canvas)" in body, \
            "indent/outdent 沒有限制在清單內，會把段落包成 blockquote"

    def test_ordered_list_command_is_mapped(self):
        body = _method_body(_soed(), "formatText")
        assert "cmd = 'insertOrderedList'" in body

    def test_three_list_levels_are_visually_distinct(self):
        """巢狀後每層都長一樣的話，使用者從畫面上分不出自己在第幾層。"""
        css = MANU_CSS.read_text(encoding="utf-8")
        assert ".manus-editor-canvas ol ol ol" in css, "編號清單沒有第 3 層樣式"
        assert ".manus-editor-canvas ol ol" in css, "編號清單沒有第 2 層樣式"
        levels = re.findall(r"\.manus-editor-canvas ol(?: ol)* \{ list-style-type: (\w[\w-]*); \}", css)
        assert len(set(levels)) == len(levels) >= 3, f"三層編號樣式不夠分明: {levels}"


class TestCacheBusting:
    def test_changed_assets_are_cache_busted(self):
        """?v= 沒 bump 的話瀏覽器吃舊檔：測試全綠，使用者看到的還是舊行為。"""
        html = MANU_HTML.read_text(encoding="utf-8")
        for asset, minimum in (("manuscript_soed.js", 2.3), ("manuscript_wsui.js", 1.5),
                               ("manuscript.css", 1.4)):
            # 必須錨定在 url_for(...) 標籤上。寬鬆的 `{asset}[^?]*\?v=` 會從註解裡
            # 提到的檔名一路吃到後面某個不相干的 ?v=，比出來的版本號是別的資產的。
            m = re.search(
                rf"filename='(?:js|css)/{re.escape(asset)}'\s*\)\s*\}}\}}\?v=([\d.]+)", html
            )
            assert m, f"找不到 {asset} 的 ?v= 標記"
            assert float(m.group(1)) >= minimum, (
                f"{asset} 的 ?v={m.group(1)} 低於本次變更的 {minimum}，瀏覽器會吃到舊檔"
            )


class TestDraftRestoreIsNonBlocking:
    """
    草稿復原提示不得使用原生 confirm()。

    原生對話框會凍結整個分頁：socket 回應全部排隊，使用者無法先捲動看一眼
    目前內容就得決定要不要覆蓋它；自動化驗證也會整個停擺（實測 preview_eval
    連 `1+1` 都逾時，只能重啟瀏覽器）。決策見 docs/NOTES.md NOTE-011。
    """

    def test_offer_draft_restore_does_not_call_confirm(self):
        # 比對前必須去註解：這個方法的註解本身就寫著「不能用原生 confirm()」。
        body = _code_only(_method_body(_soed(), "_offerDraftRestore"))
        assert "confirm(" not in body, "草稿復原又用回原生 confirm()，會凍結分頁"
        assert "_showDraftRestoreBar(" in body, "沒有改走非阻塞提示條"

    def test_restore_bar_offers_both_choices_and_is_not_auto_dismissed(self):
        body = _method_body(_soed(), "_showDraftRestoreBar")
        assert "復原草稿" in body, "提示條沒有『復原』選項"
        assert "保留目前內容" in body, "提示條沒有『保留目前內容』選項"
        # 這是需要決定的提示，不是通知；自動消失會讓使用者永遠錯過它。
        assert "setTimeout" not in _code_only(body), "復原提示條不應自動消失"

    def test_restore_bar_is_scoped_to_one_section(self):
        """提示條必須綁章節，否則切章後會對著別章的畫布提議復原上一章的草稿。"""
        body = _method_body(_soed(), "_showDraftRestoreBar")
        assert "data-section" in body
        assert "CSS.escape(section)" in body

    def test_bar_text_is_escaped(self):
        """草稿時間與章節標籤都進 innerHTML，必須走 _esc。"""
        body = _method_body(_soed(), "_showDraftRestoreBar")
        assert "this._esc(when)" in body
        assert "this._esc(this._sectionLabel(section))" in body
