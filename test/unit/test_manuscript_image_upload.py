# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_manuscript_image_upload.py
# 子系統定位:
#   圖片素材的寫入把關（NOTE-033）與資產身分／編號的穩定性（NOTE-034）。
# 主要責任:
#   1. 型別由 magic bytes 決定：SVG／HTML／腳本／偽裝副檔名全部拒絕，
#      而且**拒絕時不得留下任何檔案**。
#   2. 落地副檔名來自嗅探結果，不來自使用者給的 filename。
#   3. Figure 編號是伺服器依 registry 推導的連續值，不是亂數；
#      asset id 以 max+1 產生，刪除中間一筆後不得重號。
#   4. 前端原始碼裡不得再有自行產生編號的痕跡。
# 明確不負責:
#   - socket handler 的角色判定在 test_manuscript_image_acl.py。
#   - 圖片在瀏覽器裡長什麼樣（真瀏覽器的事）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   tmp_path 下的假 data root（monkeypatch _get_data_root）；不碰真實 data/。
# ACL/安全邊界:
#   test_svg_payload_is_rejected 與 test_rejected_upload_leaves_no_file 是
#   stored-XSS 的防線。SVG 是 XML，可以夾帶 <script>，而這個目錄的內容會由
#   /manuscript/image/<pid>/<filename> 服務出去。**放寬等於開一個上傳型 XSS。**
# 不變量:
#   - 嗅不出支援型別 → 例外 + 目錄內檔案數不變。
# 相關 NOTE:
#   NOTE-033、NOTE-034。
# 驗證:
#   python -m pytest test/unit/test_manuscript_image_upload.py -q
# ---------------------------------------------------------------------------
import base64
import io
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.core_pro.manuscript import manuscript_image as image_mod
from app.core_pro.manuscript.manuscript_image import ManuscriptImage

PID = "IMGTST-p"


def _png(width=8, height=8):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(width=8, height=8):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def _b64(raw, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


@pytest.fixture()
def img_dir(tmp_path, monkeypatch):
    root = tmp_path / "data"
    d = root / PID / "manuscript" / "image"
    d.mkdir(parents=True)
    monkeypatch.setattr(image_mod, "_get_data_root", lambda: str(root))
    return d


def _files(img_dir):
    return sorted(p.name for p in img_dir.iterdir() if p.name != "image_registry.json")


def _registry(img_dir):
    p = img_dir / "image_registry.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


# ---------------------------------------------------------------------------
# 型別把關（NOTE-033）
# ---------------------------------------------------------------------------

class TestMagicBytes:
    def test_png_and_jpeg_are_accepted(self, img_dir):
        ManuscriptImage.save_image_asset(PID, _b64(_png()), "a.png", "Figure 1", "c")
        ManuscriptImage.save_image_asset(PID, _b64(_jpeg(), "image/jpeg"), "b.jpg",
                                         "Figure 2", "c")
        assert len(_files(img_dir)) == 2

    @pytest.mark.parametrize("payload,name", [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
         "evil.svg"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>', "ok.svg"),
        (b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>", "x.html"),
        (b"#!/bin/sh\necho pwned\n", "x.sh"),
        (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "x.pdf"),
        (b"PK\x03\x04" + b"\x00" * 30, "x.zip"),
    ])
    def test_non_raster_payloads_are_rejected(self, img_dir, payload, name):
        with pytest.raises(ValueError):
            ManuscriptImage.save_image_asset(PID, _b64(payload), name, "Figure 1", "c")

    def test_svg_is_rejected_even_when_named_png(self, img_dir):
        """副檔名偽裝：內容是 SVG，名字叫 .png，MIME 也謊報 image/png。

        這一條是 stored-XSS 的正面案例 —— 三個「線索」全部指向 PNG，
        只有位元組說了實話。
        """
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with pytest.raises(ValueError):
            ManuscriptImage.save_image_asset(PID, _b64(svg, "image/png"),
                                             "totally_a.png", "Figure 1", "c")

    def test_rejected_upload_leaves_no_file(self, img_dir):
        """先驗後寫。舊流程是先組檔名寫檔再說，被拒的位元組已經躺在磁碟上了。"""
        before = _files(img_dir)
        with pytest.raises(ValueError):
            ManuscriptImage.save_image_asset(
                PID, _b64(b"<svg/>" + b"x" * 64), "x.png", "Figure 1", "c")
        assert _files(img_dir) == before, "被拒絕的上傳留下了檔案"

    def test_extension_comes_from_sniffing_not_from_filename(self, img_dir):
        meta = ManuscriptImage.save_image_asset(
            PID, _b64(_jpeg(), "image/png"), "mislabelled.png", "Figure 1", "c")
        assert meta["filename"].endswith(".jpg"), (
            f"副檔名應該來自嗅探結果，實際 {meta['filename']}")

    def test_path_traversal_in_filename_cannot_escape(self, img_dir):
        meta = ManuscriptImage.save_image_asset(
            PID, _b64(_png()), "../../../../etc/passwd.png", "Figure 1", "c")
        assert "/" not in meta["filename"] and "\\" not in meta["filename"]
        assert (img_dir / meta["filename"]).is_file()

    def test_oversized_payload_is_rejected(self, img_dir, monkeypatch):
        monkeypatch.setattr(image_mod, "MAX_IMAGE_BYTES", 128)
        with pytest.raises(ValueError):
            ManuscriptImage.save_image_asset(PID, _b64(_png(200, 200)), "big.png",
                                             "Figure 1", "c")
        assert _files(img_dir) == []


class TestSniffer:
    @pytest.mark.parametrize("data,expected", [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "jpg"),
        (b"GIF89a" + b"\x00" * 8, "gif"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4, "webp"),
        (b"<svg xmlns=\"x\">padding</svg>", None),
        (b"", None),
        (b"short", None),
    ])
    def test_sniff(self, data, expected):
        assert ManuscriptImage.sniff_image_type(data) == expected


# ---------------------------------------------------------------------------
# 資產身分與編號（NOTE-034）
# ---------------------------------------------------------------------------

class TestAssetIdentity:
    def test_figure_label_is_deterministic(self, img_dir):
        labels = []
        for i in range(3):
            label = ManuscriptImage.next_figure_label(PID)
            labels.append(label)
            ManuscriptImage.save_image_asset(PID, _b64(_png()), f"f{i}.png", label, "c")
        assert labels == ["Figure 1", "Figure 2", "Figure 3"], labels

    def test_figure_labels_are_unique_across_a_project(self, img_dir):
        for i in range(5):
            ManuscriptImage.save_image_asset(
                PID, _b64(_png()), f"f{i}.png",
                ManuscriptImage.next_figure_label(PID), "c")
        figs = [e["fig_id"] for e in _registry(img_dir)]
        assert len(set(figs)) == len(figs) == 5, f"編號重複: {figs}"

    def test_asset_id_does_not_repeat_after_a_delete(self, img_dir):
        """`len()+1` 在刪掉中間一筆後會產生重複 id，而 id 是刪除的鍵。

        重號之後 delete_image_asset 會刪到錯的那一張 —— 使用者按刪除 A，
        消失的是 B。這是資料損毀，不是介面瑕疵。
        """
        for i in range(3):
            ManuscriptImage.save_image_asset(PID, _b64(_png()), f"f{i}.png",
                                             f"Figure {i + 1}", "c")
        assert [e["id"] for e in _registry(img_dir)] == ["img_001", "img_002", "img_003"]

        assert ManuscriptImage.delete_image_asset(PID, "img_002") is True
        ManuscriptImage.save_image_asset(PID, _b64(_png()), "f3.png", "Figure 4", "c")

        ids = [e["id"] for e in _registry(img_dir)]
        assert len(set(ids)) == len(ids), f"asset id 重號: {ids}"
        assert "img_004" in ids

    def test_registry_path_matches_the_stored_file(self, img_dir):
        meta = ManuscriptImage.save_image_asset(PID, _b64(_png()), "a.png",
                                                "Figure 1", "cap")
        assert meta["path"] == f"/manuscript/image/{PID}/{meta['filename']}"
        assert (img_dir / meta["filename"]).is_file()


class TestFrontEndDoesNotInventNumbers:
    """NOTE-034 的原始碼防線。

    這台機器沒有 Node，JS 的**行為**證據只能來自真瀏覽器；但「那段程式碼還在
    不在」是可機檢的，而它正是缺陷本身。
    """

    def _js(self, name):
        return (PROJECT_ROOT / "app" / "static" / "js" / name).read_text(encoding="utf-8")

    @staticmethod
    def _emitted_fig_id_lines(src):
        """只找**送出的欄位**：物件字面值裡的 `fig_id:`。

        不能只用 `"fig_id" in ln`：`data.meta.fig_id` 是**讀取伺服器回傳的編號**，
        那正是修好之後該有的用法，把它算成違規會讓這條測試永遠紅。
        """
        return [ln for ln in src.split("\n") if re.search(r"^\s*fig_id\s*:", ln)]

    def test_no_random_figure_number_is_emitted(self):
        src = self._js("manuscript_image.js")
        assert not self._emitted_fig_id_lines(src), "前端仍在送出 fig_id"
        # 不變量是「亂數不得參與**編號**」，不是「整支檔案不准用 Math.random」
        # —— L88 的 `mermaid_ + Math.random()` 是產生 DOM 元素 id，那完全正當。
        # 先剝掉註解（註解刻意引用了舊寫法當說明），再找同時出現兩者的行。
        code_only = re.sub(r"//.*", "", src)
        offenders = [ln for ln in code_only.split("\n")
                     if "Math.random" in ln and re.search(r"[Ff]ig", ln)]
        assert not offenders, f"亂數仍參與 Figure 編號: {offenders}"

    def test_gallery_upload_does_not_send_fig_id(self):
        src = self._js("manuscript_wsui.js")
        assert not self._emitted_fig_id_lines(src), "圖庫上傳仍在送出 fig_id"

    def test_gallery_upload_sends_section_for_the_acl_check(self):
        """沒有 section，伺服器就只能退回專案層判定，coauthor 會被誤擋。"""
        src = self._js("manuscript_wsui.js")
        block_start = src.index("async uploadGalleryImage()")
        block = src[block_start:block_start + 3000]
        assert "section:" in block, "圖庫上傳沒有送出 section"
