# 檔案路徑: test/unit/test_fusion_section_segments.py
# 產生時間: 2026-08-08 23:30 +08:00
# 版本: v1.0
# 模組定位:
#   由 fusion 的 Title 區塊切出章節級 evidence segment 的行為測試。
# 背景:
#   正式站的 evidence 索引原本 75 段全是「一頁一段」（segment_id='page-1'，
#   每段 4000~6500 字元）。而 manuscript_ruling 的檢索預算是 1200 tokens ——
#   一段就吃不下，等於檢索形同虛設，草稿因此永遠缺依據。
#   章節級索引原本只有 Flow B 的 semantic_sections.json 提供，但 Flow B 在
#   e2-medium（2 vCPU）跑本機 NLLB 翻譯必定 timeout，多數論文拿不到。
#   fusion 的 block 自帶 type，實測 SEBASR_IJMIR 有 18 個 Title 區塊
#   （例如 '1 Introduction'），足以切章節而不必等 Flow B。
# 主要責任:
#   1. Title 區塊為界切段，label 取該 Title 的文字。
#   2. 第一個 Title 之前的內容歸入 Front Matter，不得遺失
#      （Abstract 通常在那裡，正是寫摘要時最該被檢索到的東西）。
#   3. 沒有任何 Title → 回空 list，由呼叫端退回頁分段（寧可粗，不可沒有）。
#   4. header/footer/pagenum 與 is_page_noise 一律排除。
#   5. 依 reading_order 排序，不照陣列順序（雙欄排版會讀錯）。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   與 build_segments_from_reflow 回傳同形狀：
#   {segment_id, title, text, metadata}，metadata 帶 section_label 與 block_ids。
# 安全邊界:
#   - 純函式測試，不碰 DB、不碰檔案系統。
# 維護提醒:
#   - 若有人把「沒有 Title 就回空」改成回 Front Matter 單段，
#     test_no_titles_returns_empty_for_fallback 會紅 —— 那會讓整篇變成一段，
#     比頁分段更糟。
# 驗證方式:
#   python -m pytest test/unit/test_fusion_section_segments.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.paper_evidence_sync import (  # noqa: E402
    build_segments_from_fusion,
    build_segments_from_fusion_sections,
)

LONG = "x" * 60  # 超過 _MIN_SEGMENT_CHARS


def _blk(btype, content, order=0, bid=None, noise=False):
    b = {"type": btype, "content": content, "reading_order": order}
    if bid:
        b["id"] = bid
    if noise:
        b["is_page_noise"] = True
    return b


def _payload(pages):
    return {"content": [{"page": n, "blocks": bs} for n, bs in pages]}


def test_titles_start_new_sections():
    payload = _payload([
        (1, [
            _blk("Title", "1 Introduction", 0),
            _blk("Body", "intro body " + LONG, 1, "b1"),
            _blk("Title", "2 Method", 2),
            _blk("Body", "method body " + LONG, 3, "b2"),
        ]),
    ])
    segs = build_segments_from_fusion_sections(payload)
    assert [s["title"] for s in segs] == ["1 Introduction", "2 Method"]
    assert "intro body" in segs[0]["text"]
    assert "method body" in segs[1]["text"]
    assert segs[0]["metadata"]["block_ids"] == ["b1"]
    assert segs[0]["metadata"]["section_label"] == "1 Introduction"


def test_content_before_first_title_becomes_front_matter():
    """Abstract 通常在第一個 Title 之前，丟掉它等於丟掉寫摘要最需要的材料。"""
    payload = _payload([
        (1, [
            _blk("Body", "Abstract: this paper " + LONG, 0),
            _blk("Title", "1 Introduction", 1),
            _blk("Body", "intro " + LONG, 2),
        ]),
    ])
    segs = build_segments_from_fusion_sections(payload)
    assert segs[0]["title"] == "Front Matter"
    assert "Abstract" in segs[0]["text"]


def test_no_titles_returns_empty_for_fallback():
    """沒有 Title 就不該硬切；回空讓呼叫端退回頁分段。"""
    payload = _payload([(1, [_blk("Body", "just body " + LONG, 0)])])
    assert build_segments_from_fusion_sections(payload) == []
    # 對照：頁分段這時仍然給得出東西
    assert len(build_segments_from_fusion(payload)) == 1


def test_noise_blocks_excluded():
    payload = _payload([
        (1, [
            _blk("Title", "1 Introduction", 0),
            _blk("header", "RUNNING HEADER " + LONG, 1),
            _blk("Body", "real content " + LONG, 2),
            _blk("Body", "noisy repeat " + LONG, 3, noise=True),
        ]),
    ])
    segs = build_segments_from_fusion_sections(payload)
    assert "RUNNING HEADER" not in segs[0]["text"]
    assert "noisy repeat" not in segs[0]["text"]
    assert "real content" in segs[0]["text"]


def test_blocks_sorted_by_reading_order():
    """雙欄排版時陣列順序不等於閱讀順序。"""
    payload = _payload([
        (1, [
            _blk("Title", "1 Introduction", 0),
            _blk("Body", "SECOND " + LONG, 5),
            _blk("Body", "FIRST " + LONG, 1),
        ]),
    ])
    text = build_segments_from_fusion_sections(payload)[0]["text"]
    assert text.index("FIRST") < text.index("SECOND")


def test_sections_span_pages():
    """章節跨頁時要合成一段，並記錄頁碼區間。"""
    payload = _payload([
        (1, [_blk("Title", "3 Results", 0), _blk("Body", "part one " + LONG, 1)]),
        (2, [_blk("Body", "part two " + LONG, 0)]),
    ])
    segs = build_segments_from_fusion_sections(payload)
    assert len(segs) == 1
    assert "part one" in segs[0]["text"] and "part two" in segs[0]["text"]
    assert segs[0]["metadata"]["page_start"] == 1
    assert segs[0]["metadata"]["page_end"] == 2


def test_segments_are_smaller_than_page_segments():
    """本次改動的重點：章節段必須比頁段細，否則檢索預算照樣塞不下。"""
    payload = _payload([
        (1, [
            _blk("Title", "1 Introduction", 0),
            _blk("Body", "a" * 3000, 1),
            _blk("Title", "2 Method", 2),
            _blk("Body", "b" * 3000, 3),
        ]),
    ])
    page_segs = build_segments_from_fusion(payload)
    sec_segs = build_segments_from_fusion_sections(payload)
    assert len(page_segs) == 1
    assert len(sec_segs) == 2
    assert max(len(s["text"]) for s in sec_segs) < len(page_segs[0]["text"])
