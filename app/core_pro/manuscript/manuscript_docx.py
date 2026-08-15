# Roothinks source maintenance contract
# 檔案路徑: app/core_pro/manuscript/manuscript_docx.py
# 模組定位:
#   Manuscript 匯出層：把 2C 全篇的 HTML 轉成真正的 OOXML .docx 套件。
# 主要責任:
#   1. HtmlToDocxParser — 以 stdlib html.parser 把 HTML 掃成扁平的 Block 串列
#      （段落／標題／清單／表格／圖片），過程中剔除畫布 chrome。
#   2. RegistryImageResolver — 把 <img src> 對回本專案 image_registry.json 的
#      實體檔案。**唯一**的圖片來源，不存在任何網路抓取路徑。
#   3. build_docx — 用 stdlib zipfile 組出合法的 OOXML 套件並回傳 bytes。
# 上下游:
#   producer: 前端 2C 畫布 (manuscript_wsui.js exportFusionToWord) 或已存版本的
#             paper content；-> manuscript_routes.py 的
#             POST /manuscript/api/export/docx/<pid> -> 本模組。
#   consumer: 瀏覽器下載的 .docx 檔（離線可攜，不依賴伺服器）。
# 保存位置:
#   不落地。純函式，回傳記憶體中的 bytes；讀取來源是
#   data/<PID>-p/manuscript/image/ 底下已登記的圖片實體。
# ACL/安全邊界:
#   - **本模組不得 import 任何 HTTP client**（見 NOTE-032）。圖片解析是
#     「registry 查表 + safe_join_under 讀檔」，沒有 fetch 這個動作。
#   - pid 一律先過 ManuscriptImage._formal_pid（內含 validate_id），
#     registry 比對本身就是跨專案 ACL：別的 pid 的路徑查不到就是失敗。
#   - 呼叫端（route）另負責使用者對該 pid 的角色檢查；本模組不做身分判斷。
# 不變量:
#   1. 產出必須含 [Content_Types].xml、_rels/.rels、word/document.xml、
#      word/_rels/document.xml.rels，且可被 zipfile 開啟。
#   2. document.xml 不得殘留 chrome 痕跡（fusion-src-ver / S.Ver / bi- 圖示）。
#   3. 圖片缺失、越權或不支援格式一律 raise DocxExportError，
#      不得產生「少一張圖但看起來成功」的檔案。
#   4. 每一張被嵌入的圖都必須同時有 word/media/* 實體與 document.xml.rels 的
#      relationship；只有其中一半的 docx 在 Word 裡會直接報損毀。
# 相關 NOTE:
#   NOTE-031（結構化匯出、chrome 剔除）、NOTE-032（圖片只來自 registry）、
#   NOTE-034（Figure/Table 編號由資產身分推導）。
# 驗證:
#   python -m pytest test/unit/test_manuscript_docx_export.py -q
# ---------------------------------------------------------------------------
from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from app.security import safe_join_under
from app.core_pro.manuscript.manuscript_image import ManuscriptImage
from app.core_pro.manuscript.manuscript_io import _get_data_root

logger = logging.getLogger("manuscript_docx")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# OOXML 度量：EMU（English Metric Unit）每英吋 914400；twip 每英吋 1440。
EMU_PER_INCH = 914400
# A4 直式扣掉左右各 1 吋邊界後的可用寬度。圖片超過就等比縮到這個寬度，
# 否則 Word 會讓它溢出版心。
MAX_IMAGE_WIDTH_EMU = int(6.0 * EMU_PER_INCH)
MAX_IMAGE_HEIGHT_EMU = int(8.0 * EMU_PER_INCH)

# 這些副檔名是 Word 保證認得的點陣格式。SVG 不在內（Word 2016 以前不支援，
# 而且 SVG 可以夾帶 script —— 見 NOTE-033 的上傳側同款判斷）。
_IMAGE_CONTENT_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
}
_EXT_ALIASES = {"jpg": "jpeg", "jpe": "jpeg"}


class DocxExportError(Exception):
    """匯出失敗。訊息會直接回給使用者，所以必須是可行動的中文說明。"""


# ---------------------------------------------------------------------------
# 文件模型
# ---------------------------------------------------------------------------

class Run:
    """一段共用格式的文字。"""

    __slots__ = ("text", "bold", "italic", "underline")

    def __init__(self, text: str, bold: bool = False, italic: bool = False,
                 underline: bool = False):
        self.text = text
        self.bold = bold
        self.italic = italic
        self.underline = underline


class Paragraph:
    __slots__ = ("runs", "style", "align", "list_kind", "list_level")

    def __init__(self, style: str = "Normal", align: str = "",
                 list_kind: str = "", list_level: int = 0):
        self.runs: List[Run] = []
        self.style = style
        self.align = align
        self.list_kind = list_kind      # '' | 'bullet' | 'number'
        self.list_level = list_level

    def is_empty(self) -> bool:
        return not any(r.text.strip() for r in self.runs)


class ImageBlock:
    __slots__ = ("data", "ext", "width_emu", "height_emu", "alt")

    def __init__(self, data: bytes, ext: str, width_emu: int, height_emu: int,
                 alt: str = ""):
        self.data = data
        self.ext = ext
        self.width_emu = width_emu
        self.height_emu = height_emu
        self.alt = alt


class TableBlock:
    __slots__ = ("rows",)

    def __init__(self, rows: List[List[List[Paragraph]]]):
        # rows[r][c] 是一個 cell，cell 內是段落串列（cell 可以有多段）。
        self.rows = rows


# ---------------------------------------------------------------------------
# 圖片解析：唯一來源是本專案的 image registry
# ---------------------------------------------------------------------------

class RegistryImageResolver:
    """把 <img src> 對回 data/<PID>-p/manuscript/image/ 底下的實體檔案。

    NOTE(NOTE-032) 這裡**故意沒有**任何 HTTP client。匯出的 HTML 由使用者控制，
    若伺服器照著 src 去抓，內網位址（雲端 metadata、redis）就會由伺服器代為
    請求，結果還會被打包進使用者拿得到的檔案。防線不是「檢查 URL 安不安全」，
    是「根本沒有抓取這個動作」——registry 查不到就直接失敗。
    """

    def __init__(self, pid: str):
        self.formal_pid = ManuscriptImage._formal_pid(pid)
        self._registry = ManuscriptImage.get_image_registry(self.formal_pid)
        self._by_filename: Dict[str, Dict[str, Any]] = {}
        for entry in self._registry or []:
            if not isinstance(entry, dict):
                continue
            fname = str(entry.get("filename") or "").strip()
            if fname:
                self._by_filename[fname] = entry
        # 同一次匯出裡同一張圖只讀一次磁碟；2C 常見同圖多處引用。
        self._cache: Dict[str, ImageBlock] = {}

    # 這個前綴由 ManuscriptImage.save_image_asset 產生（meta['path']），
    # 是 registry 與畫布之間唯一約定的形狀。
    def _expected_prefix(self) -> str:
        return f"/manuscript/image/{self.formal_pid}/"

    def resolve(self, src: str, alt: str = "") -> ImageBlock:
        raw = str(src or "").strip()
        if not raw:
            raise DocxExportError("匯出中止：文件內有一張沒有來源的圖片。")

        if raw in self._cache:
            cached = self._cache[raw]
            return ImageBlock(cached.data, cached.ext, cached.width_emu,
                              cached.height_emu, alt or cached.alt)

        lowered = raw.lower()
        if lowered.startswith("data:"):
            # 沒有 SSRF 風險，但它代表一張沒有登記在素材庫的圖：
            # 不可追溯、無法套用 NOTE-034 的編號、也讓檔案大小失去上界。
            raise DocxExportError(
                "匯出中止：文件內有未登記到圖片庫的內嵌圖片。"
                "請先把圖片存進圖片庫（素材庫 → 上傳至圖片庫）後再匯出。"
            )

        parts = urlsplit(raw)
        if parts.scheme or parts.netloc:
            raise DocxExportError(
                f"匯出中止：不允許外部圖片來源（{parts.scheme or 'network'}）。"
                "論文圖片必須先存進本專案的圖片庫。"
            )

        # 只看 path，丟掉 query。畫布上的 src 會帶 ?access_token=…（見
        # manuscript_wsui.js 的 _withAuthToken），那是瀏覽器讀圖用的，
        # 與磁碟上的檔案身分無關。
        path = unquote(parts.path or "")
        prefix = self._expected_prefix()
        if not path.startswith(prefix):
            raise DocxExportError(
                "匯出中止：文件內有不屬於本專案的圖片來源。"
                "跨專案的素材不會被嵌入。"
            )

        filename = path[len(prefix):]
        # 這裡已經是 registry 的鍵，不該再含任何路徑分隔。含了就是穿越嘗試。
        if not filename or "/" in filename or "\\" in filename:
            raise DocxExportError("匯出中止：圖片來源路徑不合法。")

        entry = self._by_filename.get(filename)
        if entry is None:
            raise DocxExportError(
                f"匯出中止：圖片「{filename}」不在本專案的圖片庫裡，無法嵌入。"
            )

        block = self._load_asset(filename, alt)
        self._cache[raw] = block
        return block

    def _load_asset(self, filename: str, alt: str) -> ImageBlock:
        data_root = _get_data_root()
        # safe_join_under 是第二道：即使 registry 被人動過手腳寫進奇怪的檔名，
        # 也出不了 image 目錄。
        img_path = safe_join_under(
            data_root, self.formal_pid, "manuscript", "image", filename
        )
        if not os.path.isfile(img_path):
            raise DocxExportError(
                f"匯出中止：圖片「{filename}」已登記但實體檔案不存在。"
            )
        try:
            with open(img_path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise DocxExportError(f"匯出中止：讀取圖片「{filename}」失敗。") from exc

        if not data:
            raise DocxExportError(f"匯出中止：圖片「{filename}」是空檔案。")

        ext, px_w, px_h = _probe_image(data)
        if ext not in _IMAGE_CONTENT_TYPES:
            raise DocxExportError(
                f"匯出中止：圖片「{filename}」的格式 Word 不支援，"
                "請改用 PNG 或 JPEG。"
            )
        w_emu, h_emu = _fit_image_emu(px_w, px_h)
        return ImageBlock(data, ext, w_emu, h_emu, alt)


def _probe_image(data: bytes) -> Tuple[str, int, int]:
    """回傳 (副檔名, 寬 px, 高 px)。以實際位元組判斷，不信任檔名。"""
    try:
        from PIL import Image  # Pillow 已在 requirements.txt，是既有相依
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").lower()
            width, height = im.size
    except Exception as exc:  # noqa: BLE001 - 任何解碼失敗都當成不支援
        raise DocxExportError("匯出中止：圖片無法解碼，可能已損毀。") from exc
    fmt = _EXT_ALIASES.get(fmt, fmt)
    if not width or not height:
        raise DocxExportError("匯出中止：圖片尺寸不合法。")
    return fmt, int(width), int(height)


def _fit_image_emu(px_w: int, px_h: int) -> Tuple[int, int]:
    """像素換 EMU 並縮進版心。以 96 DPI 當基準（螢幕稿的慣例）。"""
    w = int(px_w * EMU_PER_INCH / 96)
    h = int(px_h * EMU_PER_INCH / 96)
    if w > MAX_IMAGE_WIDTH_EMU:
        h = int(h * MAX_IMAGE_WIDTH_EMU / w)
        w = MAX_IMAGE_WIDTH_EMU
    if h > MAX_IMAGE_HEIGHT_EMU:
        w = int(w * MAX_IMAGE_HEIGHT_EMU / h)
        h = MAX_IMAGE_HEIGHT_EMU
    return max(w, 1), max(h, 1)


# ---------------------------------------------------------------------------
# HTML -> 文件模型
# ---------------------------------------------------------------------------

# 整棵子樹都不要的標籤。button/svg/script/style 都不是論文內容。
_DROP_SUBTREE_TAGS = {"script", "style", "svg", "button", "noscript", "select",
                      "textarea", "iframe", "object", "video", "audio", "canvas"}
# 整棵子樹都不要的 class。fusion-src-ver 是「這段來自 2B 第幾版」的編輯徽章，
# 給編輯者看的，印進論文就是錯的（NOTE-031）。
_DROP_SUBTREE_CLASSES = {"fusion-src-ver", "editor-only", "no-export"}

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TAGS = _HEADING_TAGS | {
    "p", "div", "li", "blockquote", "figcaption", "pre", "section", "article",
}

_HEADING_STYLE = {
    "h1": "Heading1", "h2": "Heading1", "h3": "Heading2",
    "h4": "Heading3", "h5": "Heading3", "h6": "Heading4",
}


def _classes(attrs: List[Tuple[str, Optional[str]]]) -> set:
    for name, value in attrs:
        if name == "class":
            return set(str(value or "").split())
    return set()


def _attr(attrs: List[Tuple[str, Optional[str]]], key: str) -> str:
    for name, value in attrs:
        if name == key:
            return str(value or "")
    return ""


class HtmlToDocxParser(HTMLParser):
    """把 2C 畫布的 HTML 掃成扁平的 Block 串列。

    刻意不建完整的 DOM 樹：2C 的內容是「章節區塊 + 段落 + 圖 + 表」這種淺結構，
    用 open-tag 堆疊追蹤格式與容器就夠了，換一個 HTML 剖析相依只是增加攻擊面。
    """

    def __init__(self, resolver: Optional[RegistryImageResolver] = None):
        super().__init__(convert_charrefs=True)
        self.resolver = resolver
        self.blocks: List[Any] = []
        self._para: Optional[Paragraph] = None
        # (tag, classes) 堆疊，用來判斷「現在在誰裡面」。
        self._stack: List[Tuple[str, set]] = []
        self._drop_depth = 0          # >0 代表正在被丟棄的子樹裡
        self._bold = 0
        self._italic = 0
        self._underline = 0
        self._list_stack: List[str] = []
        # 表格是唯一需要巢狀收集的結構，所以單獨開一組狀態。
        self._table: Optional[List[List[List[Paragraph]]]] = None
        self._row: Optional[List[List[Paragraph]]] = None
        self._cell: Optional[List[Paragraph]] = None

    # -- 內部工具 ---------------------------------------------------------

    def _in_class(self, cls: str) -> bool:
        return any(cls in classes for _tag, classes in self._stack)

    def _parent_has_class(self, cls: str) -> bool:
        # 呼叫時 self._stack 的最後一項已經是「目前這個元素」，所以看倒數第二項。
        return len(self._stack) >= 2 and cls in self._stack[-2][1]

    def _flush(self) -> None:
        para, self._para = self._para, None
        if para is None or para.is_empty():
            return
        if self._cell is not None:
            self._cell.append(para)
        else:
            self.blocks.append(para)

    def _start_para(self, style: str = "Normal", align: str = "",
                    list_kind: str = "", list_level: int = 0) -> None:
        self._flush()
        self._para = Paragraph(style, align, list_kind, list_level)

    def _emit_block(self, block: Any) -> None:
        self._flush()
        if self._cell is not None:
            # 表格儲存格內不放圖／巢狀表：Word 支援，但 2C 不產生這種結構，
            # 支援它等於維護一條沒有使用者的路徑。
            return
        self.blocks.append(block)

    # -- HTMLParser 介面 --------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        classes = _classes(attrs)

        if self._drop_depth > 0:
            if tag not in _VOID_TAGS:
                self._stack.append((tag, classes))
                self._drop_depth += 1
            return

        drop = tag in _DROP_SUBTREE_TAGS or bool(classes & _DROP_SUBTREE_CLASSES)
        # Bootstrap 的圖示是空的 <i class="bi bi-xxx">。它同時是 <i>（斜體），
        # 所以不能只靠標籤名判斷 —— 要靠 class 認出「這是圖示不是斜體」。
        if tag == "i" and any(c == "bi" or c.startswith("bi-") for c in classes):
            drop = True
        if drop:
            # **刻意不 _flush()**：被丟掉的 chrome 幾乎都是行內的，而且常常出現在
            # 段落的最前面 —— 2C 的章節標題就長成
            # `<h5><i class="bi …"></i>Introduction<span class="fusion-src-ver">…`。
            # 若在這裡斷段，剛剛才用 Heading1 開好的空段落會被丟掉，等 "Introduction"
            # 進來時只能重開一個 Normal 段 —— 標題樣式就這樣默默不見了，
            # 而且完全不會報錯。實測就是這樣紅的。
            if tag in _VOID_TAGS:
                # 空元素沒有子樹可丟。若在這裡進入 drop 狀態就永遠等不到對應的
                # 結束標籤，之後整份文件都會被靜默吃掉。
                return
            self._stack.append((tag, classes))
            self._drop_depth = 1
            return

        if tag not in _VOID_TAGS:
            self._stack.append((tag, classes))

        if tag == "table":
            self._flush()
            self._table = []
            return
        if tag == "tr" and self._table is not None:
            self._row = []
            return
        if tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._start_para("TableHeader" if tag == "th" else "Normal")
            return

        if tag in ("b", "strong"):
            self._bold += 1
            return
        if tag in ("i", "em", "cite"):
            self._italic += 1
            return
        if tag == "u":
            self._underline += 1
            return
        if tag == "br":
            if self._para is None:
                self._start_para()
            self._para.runs.append(Run("\n"))
            return
        if tag == "img":
            self._handle_img(attrs)
            return
        if tag in ("ul", "ol"):
            self._flush()
            self._list_stack.append("number" if tag == "ol" else "bullet")
            return
        if tag == "li":
            kind = self._list_stack[-1] if self._list_stack else "bullet"
            self._start_para("ListParagraph", list_kind=kind,
                             list_level=max(len(self._list_stack) - 1, 0))
            return

        if tag in _HEADING_TAGS:
            # NOTE(NOTE-031) 章節標題在 2C 是 <h5>，那是版面選擇不是文件層級。
            # `.fusion-block` 的直屬標題一律當 Heading1，其餘才照標籤映射。
            style = "Heading1" if self._parent_has_class("fusion-block") \
                else _HEADING_STYLE.get(tag, "Heading2")
            self._start_para(style)
            return

        if tag == "figcaption" or (tag == "p" and (self._in_class("image-container")
                                                   or self._in_class("table-container"))):
            self._start_para("Caption", align="center")
            return

        if tag == "blockquote":
            self._start_para("Quote")
            return

        if tag in _BLOCK_TAGS:
            # div/section 這種容器只在「已經有累積內容」時才斷段，
            # 否則每一層 Bootstrap 包裝都會多出一個空段落。
            self._flush()
            return

    def handle_startendtag(self, tag, attrs):
        """`<img/>` 這種自閉合寫法只算開始標籤。

        HTMLParser 的預設實作會接著呼叫 handle_endtag，而空元素從來沒有被推進
        _stack，那一次 pop 會把它的**父元素**彈掉 —— 之後 _parent_has_class /
        _in_class 的判斷全部錯位。症狀不會是例外，是標題樣式默默跑掉。
        """
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()

        # 空元素不在 _stack 上（見 handle_starttag），所以這裡也不能 pop。
        # 格式良好的 HTML 不會送 </br>，但 contenteditable 產出的東西不保證格式良好。
        if tag in _VOID_TAGS:
            return

        if self._drop_depth > 0:
            if self._stack:
                self._stack.pop()
            self._drop_depth -= 1
            return

        if tag in ("b", "strong"):
            self._bold = max(self._bold - 1, 0)
        elif tag in ("i", "em", "cite"):
            self._italic = max(self._italic - 1, 0)
        elif tag == "u":
            self._underline = max(self._underline - 1, 0)
        elif tag in ("ul", "ol"):
            self._flush()
            if self._list_stack:
                self._list_stack.pop()
        elif tag in ("td", "th"):
            self._flush()
            if self._row is not None and self._cell is not None:
                self._row.append(self._cell or [Paragraph()])
            self._cell = None
        elif tag == "tr":
            if self._table is not None and self._row is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            rows, self._table = self._table, None
            self._row = None
            self._cell = None
            if rows:
                self.blocks.append(TableBlock(rows))
        elif tag in _BLOCK_TAGS:
            self._flush()

        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._drop_depth > 0 or not data:
            return
        # HTML 的原始換行是排版空白，不是使用者的分行（同 NOTE-030 的判斷）。
        text = re.sub(r"[ \t\r\n]+", " ", data)
        if not text.strip():
            # 純空白只有在段落中間才有意義（單字之間的分隔）。
            if self._para is not None and self._para.runs and text == " ":
                self._para.runs.append(Run(" "))
            return
        if self._para is None:
            self._start_para()
        self._para.runs.append(Run(text, bool(self._bold), bool(self._italic),
                                   bool(self._underline)))

    # -- img ---------------------------------------------------------------

    def _handle_img(self, attrs):
        src = _attr(attrs, "src")
        alt = _attr(attrs, "alt")
        if self.resolver is None:
            raise DocxExportError("匯出中止：沒有可用的圖片解析器。")
        self._emit_block(self.resolver.resolve(src, alt))

    def close(self):
        super().close()
        self._flush()
        return self.blocks


_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "col",
              "area", "base", "embed", "param", "track", "wbr"}


# ---------------------------------------------------------------------------
# 文件模型 -> OOXML
# ---------------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _clean_text(text: str) -> str:
    """移除 XML 1.0 不允許的控制字元。留著會讓 Word 直接判定檔案損毀。"""
    return "".join(ch for ch in text
                   if ch in "\t\n\r" or 0x20 <= ord(ch) <= 0xD7FF
                   or 0xE000 <= ord(ch) <= 0xFFFD or ord(ch) >= 0x10000)


def _run_xml(run: Run) -> str:
    props = []
    if run.bold:
        props.append("<w:b/>")
    if run.italic:
        props.append("<w:i/>")
    if run.underline:
        props.append('<w:u w:val="single"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""

    out = []
    # Run 內的 \n 來自 <br>，在 OOXML 要變成真的 <w:br/>，不能留在 w:t 裡。
    segments = _clean_text(run.text).split("\n")
    for idx, seg in enumerate(segments):
        if idx:
            out.append("<w:br/>")
        if seg:
            out.append(f'<w:t xml:space="preserve">{_xml_escape(seg)}</w:t>')
    if not out:
        return ""
    return f"<w:r>{rpr}{''.join(out)}</w:r>"


_NUM_ID = {"bullet": 1, "number": 2}


def _paragraph_xml(para: Paragraph) -> str:
    props = []
    if para.style and para.style != "Normal":
        props.append(f'<w:pStyle w:val="{para.style}"/>')
    if para.list_kind:
        num_id = _NUM_ID.get(para.list_kind, 1)
        props.append(
            f'<w:numPr><w:ilvl w:val="{para.list_level}"/>'
            f'<w:numId w:val="{num_id}"/></w:numPr>'
        )
    if para.align:
        props.append(f'<w:jc w:val="{para.align}"/>')
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    runs = "".join(_run_xml(r) for r in para.runs)
    return f"<w:p>{ppr}{runs}</w:p>"


def _image_xml(img: ImageBlock, rel_id: str, seq: int) -> str:
    name = _xml_escape(f"Picture {seq}")
    descr = _xml_escape(_clean_text(img.alt)[:200])
    return (
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{img.width_emu}" cy="{img.height_emu}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{seq}" name="{name}" descr="{descr}"/>'
        '<wp:cNvGraphicFramePr>'
        '<a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{seq}" name="{name}" descr="{descr}"/>'
        '<pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/>'
        '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{img.width_emu}" cy="{img.height_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline>'
        '</w:drawing></w:r></w:p>'
    )


def _table_xml(table: TableBlock) -> str:
    cols = max((len(r) for r in table.rows), default=0)
    if not cols:
        return ""
    # 版心 6 吋 = 8640 twip，平均分給每一欄。2C 的表格沒有欄寬資訊，
    # 平分是唯一不會憑空捏造版面意圖的作法。
    col_w = max(8640 // cols, 100)
    grid = "".join(f'<w:gridCol w:w="{col_w}"/>' for _ in range(cols))

    rows_xml = []
    for row in table.rows:
        cells_xml = []
        for idx in range(cols):
            paras = row[idx] if idx < len(row) else []
            body = "".join(_paragraph_xml(p) for p in paras) or "<w:p/>"
            cells_xml.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{col_w}" w:type="dxa"/></w:tcPr>'
                f'{body}</w:tc>'
            )
        rows_xml.append(f"<w:tr>{''.join(cells_xml)}</w:tr>")

    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    # 表格後面必須接一個段落，否則 Word 會抱怨文件結構不完整
    # （兩個相鄰的 w:tbl 之間、或 body 以 w:tbl 結尾都不合法）。
    return (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
        f'<w:tblW w:w="0" w:type="auto"/><w:tblBorders>{borders}</w:tblBorders>'
        f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>'
        f"{''.join(rows_xml)}</w:tbl><w:p/>"
    )


_SECT_PR = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"'
    ' w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
)

_DOC_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
    '<w:body>'
)

_RELS_ROOT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)

_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _styles_xml() -> str:
    def style(sid, name, *, outline=None, size=None, bold=False, italic=False,
              align=None, before=240, after=120, color=None):
        ppr = []
        if outline is not None:
            ppr.append(f'<w:outlineLvl w:val="{outline}"/>')
        ppr.append(f'<w:spacing w:before="{before}" w:after="{after}"/>')
        if align:
            ppr.append(f'<w:jc w:val="{align}"/>')
        rpr = []
        if bold:
            rpr.append("<w:b/>")
        if italic:
            rpr.append("<w:i/>")
        if color:
            rpr.append(f'<w:color w:val="{color}"/>')
        if size:
            rpr.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
        return (
            f'<w:style w:type="paragraph" w:styleId="{sid}">'
            f'<w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
            f'<w:qFormat/><w:pPr>{"".join(ppr)}</w:pPr>'
            f'<w:rPr>{"".join(rpr)}</w:rPr></w:style>'
        )

    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft JhengHei"/>'
        '<w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
        '</w:pPr></w:pPrDefault></w:docDefaults>',
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style>',
        style("Title", "Title", size=56, bold=True, align="center", before=0, after=240),
        style("Heading1", "heading 1", outline=0, size=32, bold=True),
        style("Heading2", "heading 2", outline=1, size=28, bold=True),
        style("Heading3", "heading 3", outline=2, size=26, bold=True),
        style("Heading4", "heading 4", outline=3, size=24, bold=True, italic=True),
        style("Caption", "caption", size=20, italic=True, align="center",
              before=60, after=240, color="595959"),
        style("Quote", "Quote", italic=True, before=120, after=120),
        style("TableHeader", "Table Header", bold=True, before=40, after=40),
        '<w:style w:type="paragraph" w:styleId="ListParagraph">'
        '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>',
        '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
        '<w:tblPr><w:tblCellMar>'
        '<w:top w:w="60" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
        '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
        '</w:tblCellMar></w:tblPr></w:style>',
        "</w:styles>",
    ]
    return "".join(parts)


_BULLET_RPR = (
    '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr>'
)
# Symbol 字型裡的實心圓點，OOXML 慣例用 private-use 的 U+F0B7 表示。
_BULLET_CHAR = "\uf0b7"


def _numbering_xml() -> str:
    """兩組編號定義：numId 1 = 項目符號，numId 2 = 十進位。

    只做三層。2C 的內容來自 contenteditable，實務上不會出現更深的巢狀清單，
    多定義的層級只是沒有使用者的程式碼。
    """
    def abstract(aid: int, fmt: str) -> str:
        levels = []
        for i in range(3):
            if fmt == "bullet":
                lvl_text = _BULLET_CHAR
                rpr = _BULLET_RPR
            else:
                lvl_text = "%%%d." % (i + 1)
                rpr = ""
            levels.append(
                '<w:lvl w:ilvl="%d"><w:start w:val="1"/>'
                '<w:numFmt w:val="%s"/>'
                '<w:lvlText w:val="%s"/>'
                '<w:lvlJc w:val="left"/>'
                '<w:pPr><w:ind w:left="%d" w:hanging="360"/></w:pPr>'
                '%s</w:lvl>'
                % (i, fmt, _xml_escape(lvl_text), 720 * (i + 1), rpr)
            )
        return '<w:abstractNum w:abstractNumId="%d">%s</w:abstractNum>' % (
            aid, "".join(levels)
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        + abstract(0, "bullet")
        + abstract(1, "decimal")
        + '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        + '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
        + "</w:numbering>"
    )


def _content_types_xml(image_exts: List[str]) -> str:
    defaults = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    ]
    for ext in sorted(set(image_exts)):
        ctype = _IMAGE_CONTENT_TYPES.get(ext)
        if ctype:
            defaults.append(f'<Default Extension="{ext}" ContentType="{ctype}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + "".join(defaults)
        + '<Override PartName="/word/document.xml" ContentType="'
        + DOCX_MIME + '.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        "</Types>"
    )


def build_docx(pid: str, title: str, html: str,
               resolver: Optional[RegistryImageResolver] = None) -> bytes:
    """把 2C 的 HTML 組成一份 .docx 的位元組。

    resolver 預設綁在 pid 上；測試可以注入替身，但**不得**注入會連網的東西
    （NOTE-032）。
    """
    if resolver is None:
        resolver = RegistryImageResolver(pid)

    parser = HtmlToDocxParser(resolver)
    try:
        parser.feed(html or "")
        blocks = parser.close()
    except DocxExportError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[docx] HTML 解析失敗 pid=%s: %s", pid, exc, exc_info=True)
        raise DocxExportError("匯出中止：2C 內容無法解析。") from exc

    clean_title = _clean_text(str(title or "").strip())

    body_parts: List[str] = []
    if clean_title:
        body_parts.append(_paragraph_xml(
            _titled_paragraph(clean_title)
        ))

    media: List[Tuple[str, bytes]] = []
    rels: List[str] = [
        f'<Relationship Id="rId1" Type="{_REL_NS}/styles" Target="styles.xml"/>',
        f'<Relationship Id="rId2" Type="{_REL_NS}/numbering" Target="numbering.xml"/>',
    ]
    next_rel = 3
    img_seq = 0
    has_content = bool(clean_title)

    for block in blocks:
        if isinstance(block, Paragraph):
            if block.is_empty():
                continue
            body_parts.append(_paragraph_xml(block))
            has_content = True
        elif isinstance(block, TableBlock):
            xml = _table_xml(block)
            if xml:
                body_parts.append(xml)
                has_content = True
        elif isinstance(block, ImageBlock):
            img_seq += 1
            rel_id = f"rId{next_rel}"
            next_rel += 1
            media_name = f"image{img_seq}.{block.ext}"
            media.append((media_name, block.data))
            rels.append(
                f'<Relationship Id="{rel_id}" Type="{_REL_NS}/image" '
                f'Target="media/{media_name}"/>'
            )
            body_parts.append(_image_xml(block, rel_id, img_seq))
            has_content = True

    if not has_content:
        raise DocxExportError("匯出中止：2C 目前沒有可匯出的內容。")

    document_xml = _DOC_OPEN + "".join(body_parts) + _SECT_PR + "</w:body></w:document>"
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels) + "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml 必須是第一個 entry —— 這是 OPC 規範，
        # 有些讀取器（含舊版 Word）真的會依賴這個順序。
        zf.writestr("[Content_Types].xml",
                    _content_types_xml([ext for ext in
                                        (name.rsplit(".", 1)[-1] for name, _ in media)]))
        zf.writestr("_rels/.rels", _RELS_ROOT)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/styles.xml", _styles_xml())
        zf.writestr("word/numbering.xml", _numbering_xml())
        for name, data in media:
            zf.writestr(f"word/media/{name}", data)
    return buf.getvalue()


def _titled_paragraph(title: str) -> Paragraph:
    para = Paragraph("Title", align="center")
    para.runs.append(Run(title))
    return para


def safe_download_name(title: str, fallback: str = "manuscript") -> str:
    """檔名用的清洗。只留檔名安全字元，長度設上限避免 header 過長。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(title or "").strip())
    name = re.sub(r"\s+", "_", name).strip("._")
    return (name[:100] or fallback)
