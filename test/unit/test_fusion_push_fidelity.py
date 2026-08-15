# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_fusion_push_fidelity.py
# 子系統定位:
#   2B → 2C 推送的搬運保真度（NOTE-030），以及素材插入樣板的形狀前提。
# 主要責任:
#   1. `cardActionPushToFusion()` 寫進 `.fusion-body` 的值必須是**未經轉換的**
#      `content`；任何 `\n` → `<br>` 之類的純文字換行補償都必須紅。
#   2. 證明這個缺陷不是理論的：實際去讀素材插入樣板，確認它們是多行字串，
#      因此舊轉換會**每張圖注入 4 個以上的 <br>**。
# 明確不負責:
#   - 不驗瀏覽器真的照這個字串渲染。這台機器沒有 Node（`node --check` 與
#     test/js/*.cjs 都跑不了），JS 的行為證據只能來自真瀏覽器，記在 docs/HANDOFF.md。
#     本檔負責的是「原始碼裡那個轉換確實不存在」與「樣板確實多行」這兩個可機檢的前提。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   唯讀 app/static/js/*.js，不寫任何檔案。
# ACL/安全邊界:
#   無（純靜態原始碼讀取）。
# 不變量:
#   - 2B 與 2C 對同一段內容的渲染結果必須一致；2C 是組裝，不是再排版。
# 相關 NOTE:
#   NOTE-030（本檔的主體）、NOTE-007（同一個函式的 upsert 不變量）。
# 驗證:
#   python -m pytest test/unit/test_fusion_push_fidelity.py -q
# ---------------------------------------------------------------------------
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOED = PROJECT_ROOT / "app" / "static" / "js" / "manuscript_soed.js"
IMAGE_JS = PROJECT_ROOT / "app" / "static" / "js" / "manuscript_image.js"
WSUI = PROJECT_ROOT / "app" / "static" / "js" / "manuscript_wsui.js"


def _read(path: Path) -> str:
    # 這幾支 JS 的行尾與縮排並不統一（同檔 LF/CRLF 並存，paq_* 甚至用 U+00A0），
    # 一律以 UTF-8 原樣讀進來，不做任何正規化，才不會把待測的空白改掉。
    return path.read_text(encoding="utf-8")


def _push_to_fusion_body_expression() -> str:
    """取出 `cardActionPushToFusion()` 裡指派給 `.fusion-body` 的那個運算式。

    刻意用 `body.innerHTML =` 當錨點而不是整段函式比對：函式本體會隨註解與
    版本標記改動，但這一行的語意（把 2B 的內容放進 2C）是穩定的契約。
    """
    src = _read(SOED)
    # 錨在**方法定義**上，不是方法名字：`cardActionPushToFusion` 的第一個出現
    # 位置其實是卡片工具列的 onclick 字串（約 L252），從那裡開窗口永遠找不到本體。
    m_def = re.search(r"^\s*cardActionPushToFusion\s*\([^)]*\)\s*\{", src, re.M)
    assert m_def, "manuscript_soed.js 找不到 cardActionPushToFusion 的方法定義"
    start = m_def.start()
    # 只在該函式往後的一小段窗口內找，避免抓到別的函式的同名指派。
    window = src[start:start + 2000]
    m = re.search(r"body\.innerHTML\s*=\s*([^;]+);", window)
    assert m, "cardActionPushToFusion() 內找不到 body.innerHTML 指派"
    return m.group(1).strip()


def test_push_to_fusion_copies_html_verbatim():
    """NOTE-030：搬運 HTML 就是搬運，不得再套用任何字串轉換。"""
    expr = _push_to_fusion_body_expression()
    assert expr == "content", (
        "2B 推 2C 必須原樣搬 innerHTML，實際運算式為: " + expr
    )


def test_push_to_fusion_applies_no_newline_substitution():
    """把「不得有 \\n → <br>」單獨立一條，讓 A/B 的紅點指得出來。"""
    expr = _push_to_fusion_body_expression()
    assert ".replace(" not in expr, (
        "推送路徑上出現字串轉換，來源是 HTML 不是純文字: " + expr
    )
    assert "<br>" not in expr, (
        "推送路徑上出現 <br> 補償，那是 innerText 時代的殘骸: " + expr
    )


def _multiline_insert_templates():
    """素材插入用的 HTML 樣板（真的從原始碼取，不是測試自己編的）。

    兩個來源都是跨行 template literal：
      - manuscript_image.js  setupSocketEvents() 的 imgTag（AI 產圖存檔後插入 2B）
      - manuscript_wsui.js   renderAssetGallery() 的 insertHtml（圖庫插入游標處）
    """
    out = []
    img_src = _read(IMAGE_JS)
    m = re.search(r"const imgTag = `(.*?)`;", img_src, re.S)
    assert m, "manuscript_image.js 找不到 imgTag 樣板"
    out.append(("manuscript_image.js:imgTag", m.group(1)))

    wsui_src = _read(WSUI)
    m = re.search(r"const insertHtml = `(.*?)`;", wsui_src, re.S)
    assert m, "manuscript_wsui.js 找不到 insertHtml 樣板"
    out.append(("manuscript_wsui.js:insertHtml", m.group(1)))
    return out


def test_asset_templates_are_multiline_so_the_old_transform_really_injected_breaks():
    """缺陷的量級：舊轉換對每一張插入的圖至少注入 4 個 <br>。

    這一條是 NOTE-030 的「為什麼看起來只是偶爾怪怪的」的可機檢版本 ——
    純文字段落的 innerHTML 通常沒有換行，所以沒圖的章節推過去毫無症狀；
    一旦該章有圖，樣板的排版換行就會全部變成真的分行。
    """
    for name, tpl in _multiline_insert_templates():
        injected = tpl.count("\n")
        assert injected >= 4, (
            f"{name} 預期是多行樣板（舊轉換的受害者），實際換行數 {injected}"
        )
        # 這些換行在瀏覽器裡是被折疊的排版空白，不是使用者的分行。
        assert "<br>" not in tpl.replace("<p><br></p>", ""), (
            f"{name} 的換行若本來就是 <br>，那 NOTE-030 的推論就不成立"
        )
