# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_manuscript_docx_export.py
# 子系統定位:
#   2C 全篇匯出成真正 .docx 的轉換契約（NOTE-031）與圖片來源封閉性（NOTE-032）。
# 主要責任:
#   1. 產出是**可解壓的合法 OOXML 套件**：必要的 part 一個不少，
#      document.xml 是 well-formed XML（用 ElementTree 真的 parse，不是找字串）。
#   2. 畫布 chrome 不得進入文件：`S.Ver` 徽章、Bootstrap 圖示、button。
#   3. 圖片必須同時有 word/media/* 實體與 rels relationship，
#      且**只**能來自本專案 registry：外部 URL、跨專案、data: URI、缺檔全部明確失敗。
#   4. heading/段落/粗體/斜體/清單/表格/caption 真的變成 DOCX 結構。
# 明確不負責:
#   - 不驗 Word 打開後的視覺呈現。那只有真人開檔算數，證據記在 docs/HANDOFF.md。
#   - 不驗前端有沒有呼叫這條路由（真瀏覽器的事）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的假 data root，透過 monkeypatch 綁 _get_data_root；不碰真實 data/。
# ACL/安全邊界:
#   test_module_imports_no_http_client 是 NOTE-032 的結構性防線：
#   只要有人在這個模組裡引進 requests/urllib/httpx，這條就會紅。
#   **放寬它等於把 SSRF 的門重新打開。**
# 不變量:
#   - 圖片解析失敗一律 raise，不得產生「少一張圖但看起來成功」的 docx。
#   - media 實體數必須等於 image relationship 數。
# 相關 NOTE:
#   NOTE-031、NOTE-032、NOTE-030（換行語意，同一條匯出鏈的上游）。
# 驗證:
#   python -m pytest test/unit/test_manuscript_docx_export.py -q
# ---------------------------------------------------------------------------
import io
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core_pro.manuscript import manuscript_docx as docx_mod
from app.core_pro.manuscript.manuscript_docx import (
    DocxExportError,
    RegistryImageResolver,
    build_docx,
    safe_download_name,
)

PID = "DOCXTS-p"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _png_bytes(width=40, height=30, color=(200, 30, 30)):
    """真的產生一張 PNG。用假位元組測不到 _probe_image 的解碼路徑。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    img_dir = root / PID / "manuscript" / "image"
    img_dir.mkdir(parents=True)
    (img_dir / "fig_a.png").write_bytes(_png_bytes())
    (img_dir / "fig_b.png").write_bytes(_png_bytes(60, 20, (10, 90, 200)))
    # 登記在 registry 但實體被刪掉 —— 用來驗「明確失敗」而不是靜默略過。
    registry = [
        {"id": "img_001", "filename": "fig_a.png", "fig_id": "Figure 1",
         "caption": "First", "path": f"/manuscript/image/{PID}/fig_a.png"},
        {"id": "img_002", "filename": "fig_b.png", "fig_id": "Figure 2",
         "caption": "Second", "path": f"/manuscript/image/{PID}/fig_b.png"},
        {"id": "img_003", "filename": "ghost.png", "fig_id": "Figure 3",
         "caption": "Missing", "path": f"/manuscript/image/{PID}/ghost.png"},
    ]
    import json
    (img_dir / "image_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(docx_mod, "_get_data_root", lambda: str(root))
    monkeypatch.setattr(
        "app.core_pro.manuscript.manuscript_image._get_data_root", lambda: str(root))
    return root


def _unzip(blob):
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return zf, set(zf.namelist())


def _document_root(blob):
    zf, _ = _unzip(blob)
    return ET.fromstring(zf.read("word/document.xml"))


def _all_text(blob):
    root = _document_root(blob)
    return "".join(el.text or "" for el in root.iter(f"{{{W_NS}}}t"))


def _paragraph_styles(blob):
    root = _document_root(blob)
    out = []
    for para in root.iter(f"{{{W_NS}}}p"):
        style = para.find(f"{{{W_NS}}}pPr/{{{W_NS}}}pStyle")
        out.append(style.get(f"{{{W_NS}}}val") if style is not None else "Normal")
    return out


# 一段貼近真實 2C 畫布的 HTML：fusion-block 外框、bootstrap 圖示、S.Ver 徽章。
FUSION_HTML = (
    '<div class="fusion-block mb-4 border-start border-4 border-success ps-3"'
    ' data-section="introduction" data-src-ver="0.3">'
    '<h5 class="text-success fw-bold">'
    '<i class="bi bi-check2-circle me-1"></i>Introduction'
    '<span class="badge bg-light text-secondary fw-normal ms-2 fusion-src-ver">'
    'S.Ver 0.3</span></h5>'
    '<div class="fusion-body">'
    '<p>Plain paragraph with <b>bold</b> and <i>italic</i> text.</p>'
    '<ul><li>first bullet</li><li>second bullet</li></ul>'
    '</div></div>'
)


# ---------------------------------------------------------------------------
# 套件結構
# ---------------------------------------------------------------------------

def test_package_contains_required_parts(data_root):
    blob = build_docx(PID, "My Paper", FUSION_HTML)
    zf, names = _unzip(blob)
    for required in ("[Content_Types].xml", "_rels/.rels", "word/document.xml",
                     "word/_rels/document.xml.rels", "word/styles.xml"):
        assert required in names, f"缺少 OOXML part: {required}"
    assert zf.testzip() is None
    # OPC 規範：[Content_Types].xml 必須是第一個 entry。
    assert names and zf.namelist()[0] == "[Content_Types].xml"


def test_document_xml_is_well_formed(data_root):
    blob = build_docx(PID, "My Paper", FUSION_HTML)
    root = _document_root(blob)          # 解析失敗會直接丟 ParseError
    assert root.tag == f"{{{W_NS}}}document"
    assert root.find(f"{{{W_NS}}}body") is not None


def test_output_is_not_the_old_fake_html_doc(data_root):
    """舊匯出是 HTML 字串加 .doc 副檔名；新產出必須是 zip 且不含 HTML 骨架。"""
    blob = build_docx(PID, "My Paper", FUSION_HTML)
    assert blob[:2] == b"PK", "產出不是 zip，仍是舊的假 Word 匯出"
    assert b"<!DOCTYPE html" not in blob
    assert b"<body>" not in blob


# ---------------------------------------------------------------------------
# chrome 剔除（NOTE-031）
# ---------------------------------------------------------------------------

def test_canvas_chrome_is_stripped(data_root):
    blob = build_docx(PID, "My Paper", FUSION_HTML)
    text = _all_text(blob)
    assert "S.Ver" not in text, "編輯用的版本徽章被印進論文了"
    assert "fusion-src-ver" not in text
    zf, _ = _unzip(blob)
    doc = zf.read("word/document.xml").decode("utf-8")
    assert "bi-check2-circle" not in doc
    assert "border-start" not in doc, "Bootstrap class 不該出現在 OOXML 裡"
    # 章節標題本身必須留著。
    assert "Introduction" in text


def test_button_and_script_subtrees_are_dropped(data_root):
    html = ('<div class="fusion-body"><p>keep this</p>'
            '<button class="btn">推回 2B</button>'
            '<script>alert("x")</script>'
            '<style>.x{color:red}</style></div>')
    text = _all_text(build_docx(PID, "T", html))
    assert "keep this" in text
    assert "推回 2B" not in text
    assert "alert" not in text
    assert "color:red" not in text


def test_fusion_block_heading_becomes_heading1(data_root):
    """h5 在 2C 是版面選擇，不是文件層級（NOTE-031）。"""
    styles = _paragraph_styles(build_docx(PID, "My Paper", FUSION_HTML))
    assert "Heading1" in styles
    # 標題頁的 Title 也必須在。
    assert "Title" in styles


# ---------------------------------------------------------------------------
# 內容結構
# ---------------------------------------------------------------------------

def test_bold_and_italic_become_run_properties(data_root):
    root = _document_root(build_docx(PID, "T", FUSION_HTML))
    bold_texts, italic_texts = [], []
    for run in root.iter(f"{{{W_NS}}}r"):
        rpr = run.find(f"{{{W_NS}}}rPr")
        t = "".join(el.text or "" for el in run.iter(f"{{{W_NS}}}t"))
        if rpr is None:
            continue
        if rpr.find(f"{{{W_NS}}}b") is not None:
            bold_texts.append(t)
        if rpr.find(f"{{{W_NS}}}i") is not None:
            italic_texts.append(t)
    assert "bold" in bold_texts
    assert "italic" in italic_texts


def test_lists_get_numbering_properties(data_root):
    root = _document_root(build_docx(PID, "T", FUSION_HTML))
    num_ids = [el.get(f"{{{W_NS}}}val")
               for el in root.iter(f"{{{W_NS}}}numId")]
    assert len(num_ids) == 2, f"兩個 li 應各有一個 numPr，實際 {num_ids}"


def test_ordered_and_unordered_lists_use_different_numbering(data_root):
    html = '<ul><li>a</li></ul><ol><li>b</li></ol>'
    root = _document_root(build_docx(PID, "T", html))
    num_ids = [el.get(f"{{{W_NS}}}val") for el in root.iter(f"{{{W_NS}}}numId")]
    assert num_ids == ["1", "2"], f"項目符號與編號清單必須不同 numId，實際 {num_ids}"


def test_table_becomes_real_docx_table(data_root):
    html = ('<figure class="table-container">'
            '<table><tr><th>Metric</th><th>Value</th></tr>'
            '<tr><td>Accuracy</td><td>0.91</td></tr></table>'
            '<figcaption>Table 1. Results</figcaption></figure>')
    blob = build_docx(PID, "T", html)
    root = _document_root(blob)
    tables = list(root.iter(f"{{{W_NS}}}tbl"))
    assert len(tables) == 1
    rows = list(tables[0].iter(f"{{{W_NS}}}tr"))
    assert len(rows) == 2
    cells = list(rows[0].iter(f"{{{W_NS}}}tc"))
    assert len(cells) == 2
    text = _all_text(blob)
    for expected in ("Metric", "Value", "Accuracy", "0.91", "Table 1. Results"):
        assert expected in text
    assert "Caption" in _paragraph_styles(blob)


def test_br_becomes_docx_break_not_literal_text(data_root):
    root = _document_root(build_docx(PID, "T", "<p>line one<br>line two</p>"))
    assert list(root.iter(f"{{{W_NS}}}br")), "<br> 必須變成 w:br"
    assert "line one" in _all_text(build_docx(PID, "T", "<p>line one<br>line two</p>"))


def test_source_newlines_do_not_become_breaks(data_root):
    """NOTE-030 的伺服器側對照：HTML 原始換行是排版空白，不是分行。"""
    html = "<div>\n  <p>alpha</p>\n  <p>beta</p>\n</div>"
    root = _document_root(build_docx(PID, "T", html))
    assert not list(root.iter(f"{{{W_NS}}}br")), "排版換行被誤當成使用者的分行"


# ---------------------------------------------------------------------------
# 圖片：實體 + relationship（NOTE-031 不變量 4）
# ---------------------------------------------------------------------------

def test_image_is_embedded_with_media_and_relationship(data_root):
    html = (f'<div class="image-container">'
            f'<img src="/manuscript/image/{PID}/fig_a.png" alt="First figure">'
            f'<p>Figure 1. First figure</p></div>')
    blob = build_docx(PID, "T", html)
    zf, names = _unzip(blob)

    media = [n for n in names if n.startswith("word/media/")]
    assert len(media) == 1, f"圖片實體沒有寫進 word/media/*：{sorted(names)}"
    assert zf.read(media[0]) == (data_root / PID / "manuscript" / "image"
                                 / "fig_a.png").read_bytes()

    rels = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    image_rels = [r for r in rels
                  if r.get("Type", "").endswith("/image")]
    assert len(image_rels) == 1, "缺 relationship 的 docx 在 Word 裡會直接報損毀"
    assert image_rels[0].get("Target") == media[0].replace("word/", "")

    # document.xml 必須用那個 rId 參照，而且不得殘留伺服器 URL。
    doc = zf.read("word/document.xml").decode("utf-8")
    assert f'r:embed="{image_rels[0].get("Id")}"' in doc
    assert "/manuscript/image/" not in doc, "文件裡還留著伺服器相對 URL"
    assert "access_token" not in doc


def test_same_image_twice_still_pairs_media_and_rels(data_root):
    src = f'<img src="/manuscript/image/{PID}/fig_a.png">'
    blob = build_docx(PID, "T", f"<div>{src}{src}</div>")
    zf, names = _unzip(blob)
    media = [n for n in names if n.startswith("word/media/")]
    rels = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    image_rels = [r for r in rels if r.get("Type", "").endswith("/image")]
    assert len(media) == len(image_rels), "media 數與 relationship 數必須相等"


def test_access_token_query_is_ignored_when_resolving(data_root):
    """畫布上的 src 帶 ?access_token=…，那是瀏覽器讀圖用的，不是檔案身分。"""
    html = f'<img src="/manuscript/image/{PID}/fig_a.png?access_token=abc123">'
    blob = build_docx(PID, "T", html)
    _zf, names = _unzip(blob)
    assert [n for n in names if n.startswith("word/media/")]


def test_content_types_declares_png(data_root):
    html = f'<img src="/manuscript/image/{PID}/fig_a.png">'
    zf, _ = _unzip(build_docx(PID, "T", html))
    ctypes = zf.read("[Content_Types].xml").decode("utf-8")
    assert 'Extension="png"' in ctypes, "未宣告 png，Word 會判定套件不合法"


# ---------------------------------------------------------------------------
# 圖片來源封閉性（NOTE-032）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "http://169.254.169.254/latest/meta-data/",
    "https://evil.example.com/x.png",
    "//evil.example.com/x.png",
    "file:///etc/passwd",
])
def test_external_url_image_is_rejected(data_root, src):
    with pytest.raises(DocxExportError):
        build_docx(PID, "T", f'<img src="{src}">')


def test_absolute_url_that_mimics_the_local_path_is_rejected(data_root):
    """這一條才真的把 scheme 守衛單獨隔離出來。

    A/B 抓到的事：`https://evil.example.com/x.png` 之類的網址其實是被**前綴檢查**
    擋掉的（它的 path 不是 /manuscript/image/<pid>/），所以把 scheme 守衛關掉，
    那組 parametrize 依然全綠 —— 等於什麼都沒證明。
    真正只有 scheme 守衛擋得住的，是一個 path 恰好長得跟本地路徑一樣的絕對網址：
    沒有這道守衛，它會安靜地解析成本機那張圖，讓「外部來源」變成成功案例。
    """
    src = f"https://evil.example.com/manuscript/image/{PID}/fig_a.png"
    with pytest.raises(DocxExportError) as exc:
        build_docx(PID, "T", f'<img src="{src}">')
    assert "外部圖片來源" in str(exc.value)


def test_protocol_relative_url_that_mimics_the_local_path_is_rejected(data_root):
    src = f"//evil.example.com/manuscript/image/{PID}/fig_a.png"
    with pytest.raises(DocxExportError):
        build_docx(PID, "T", f'<img src="{src}">')


def test_cross_pid_image_is_rejected(data_root):
    with pytest.raises(DocxExportError) as exc:
        build_docx(PID, "T", '<img src="/manuscript/image/OTHER1-p/fig_a.png">')
    assert "不屬於本專案" in str(exc.value)


def test_path_traversal_image_is_rejected(data_root):
    with pytest.raises(DocxExportError):
        build_docx(
            PID, "T",
            f'<img src="/manuscript/image/{PID}/../../../../etc/passwd">')


def test_data_uri_image_is_rejected(data_root):
    with pytest.raises(DocxExportError) as exc:
        build_docx(PID, "T", '<img src="data:image/png;base64,iVBORw0KGgo=">')
    assert "圖片庫" in str(exc.value)


def test_missing_image_fails_loudly(data_root):
    """registry 有登記但實體不見 —— 必須失敗，不得交出缺圖的 docx。"""
    with pytest.raises(DocxExportError) as exc:
        build_docx(PID, "T", f'<img src="/manuscript/image/{PID}/ghost.png">')
    assert "實體檔案不存在" in str(exc.value)


def test_unregistered_filename_is_rejected(data_root):
    (data_root / PID / "manuscript" / "image" / "sneaky.png").write_bytes(_png_bytes())
    with pytest.raises(DocxExportError) as exc:
        build_docx(PID, "T", f'<img src="/manuscript/image/{PID}/sneaky.png">')
    assert "圖片庫" in str(exc.value)


def test_corrupt_image_bytes_fail_loudly(data_root):
    import json
    img_dir = data_root / PID / "manuscript" / "image"
    (img_dir / "broken.png").write_bytes(b"not really a png")
    reg = json.loads((img_dir / "image_registry.json").read_text(encoding="utf-8"))
    reg.append({"id": "img_004", "filename": "broken.png", "fig_id": "Figure 4",
                "caption": "", "path": f"/manuscript/image/{PID}/broken.png"})
    (img_dir / "image_registry.json").write_text(
        json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DocxExportError):
        build_docx(PID, "T", f'<img src="/manuscript/image/{PID}/broken.png">')


def test_module_imports_no_http_client():
    """NOTE-032 的結構性防線：這個模組不得有任何抓取能力。

    放寬這一條等於把 SSRF 的門重新打開 —— 匯出的 HTML 是使用者控制的輸入。
    """
    source = (PROJECT_ROOT / "app" / "core_pro" / "manuscript"
              / "manuscript_docx.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"requests", "httpx", "aiohttp", "urllib3", "socket", "http"}
    assert not (imported & forbidden), (
        f"manuscript_docx 引進了網路能力: {sorted(imported & forbidden)}")
    # urllib 只准用解析用的 urllib.parse，不准 urllib.request。
    assert "urllib.request" not in source
    assert "urlopen" not in source


# ---------------------------------------------------------------------------
# 邊界
# ---------------------------------------------------------------------------

def test_empty_canvas_fails_with_actionable_message(data_root):
    with pytest.raises(DocxExportError) as exc:
        build_docx(PID, "", '<div class="fusion-body">   </div>')
    assert "沒有可匯出的內容" in str(exc.value)


def test_placeholder_only_canvas_is_still_exportable_when_titled(data_root):
    blob = build_docx(PID, "Only Title", "<div></div>")
    assert "Only Title" in _all_text(blob)


def test_xml_special_characters_are_escaped(data_root):
    blob = build_docx(PID, "T", "<p>a &lt; b &amp; c &gt; d \"quoted\"</p>")
    text = _all_text(blob)
    assert "a < b & c > d" in text


def test_control_characters_are_stripped(data_root):
    """XML 1.0 不允許的控制字元留著會讓 Word 直接判定檔案損毀。"""
    blob = build_docx(PID, "T", "<p>before\x0bafter\x00end</p>")
    ET.fromstring(_unzip(blob)[0].read("word/document.xml"))  # 不得 ParseError


def test_cjk_title_survives_and_download_name_is_sanitised(data_root):
    blob = build_docx(PID, "中文論文：第一版/草稿", "<p>x</p>")
    assert "中文論文：第一版/草稿" in _all_text(blob)
    name = safe_download_name("中文論文：第一版/草稿")
    assert "/" not in name and "\\" not in name
    assert name


def test_resolver_reports_pid_scope(data_root):
    resolver = RegistryImageResolver("DOCXTS")     # 不帶 -p 也要正規化
    assert resolver.formal_pid == PID
