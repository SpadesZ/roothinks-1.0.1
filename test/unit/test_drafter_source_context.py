# 檔案路徑: test/unit/test_drafter_source_context.py
# 產生時間: 2026-08-08 +08:00
# 版本: v1.0
# 模組定位:
#   app/core_pro/manuscript/source_context.py 的單元測試。
# 主要責任:
#   1. 研究筆記：有值 / 空值 / 檔案不存在 三種情境。
#   2. 論文全文：有 full_text.json / 缺 full_text.json / 混合 三種情境。
#   3. Block 排序：reading_order 小的先，Header / is_page_noise 略過。
#   4. Token 預算：多篇論文時每篇都必須貢獻語料（反擠佔性質）。
#   5. 不可信文字清洗：_sanitize 移除 NUL 字元與控制字元。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   全程使用 tmp_path 建立假資料目錄，不觸碰 data/ 目錄。
# 安全邊界:
#   - 使用 monkeypatch 讓 source_context._data_root() 指向 tmp_path，
#     確保測試不讀寫任何實際的 data/ 目錄。
# 維護提醒:
#   - 若有人修改 _NOISE_TYPES 或 is_page_noise 過濾邏輯，
#     test_header_and_noise_blocks_excluded 會紅。
#   - 若有人移除 per-paper 均分邏輯（改成截斷整串拼接），
#     test_budget_all_papers_contribute 會紅。
#   - 若有人把 _sanitize 的 NUL 清洗拿掉，
#     test_sanitize_removes_null_bytes 會紅。
# 驗證方式:
#   python -m pytest test/unit/test_drafter_source_context.py -q
# ------------------------------------------------------------------------------
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

# 被測模組（直接 import；不需要 Flask app context）
from app.core_pro.manuscript import source_context as sc


# ---------------------------------------------------------------------------
# 工具函數：在 tmp_path 下建立假資料目錄
# ---------------------------------------------------------------------------

def _make_note(tmp_path: Path, pid: str, notes: str) -> None:
    """建立 <pid>/study/<pid>_note.json。"""
    d = tmp_path / pid / "study"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}_note.json").write_text(
        json.dumps({"pid": pid, "notes": notes, "updated_at": "2026-08-08"}),
        encoding="utf-8",
    )


def _make_full_text(tmp_path: Path, pid: str, paper_name: str, pages: list) -> Path:
    """
    建立 <pid>/literature/papers/<paper_name>/05_interprets/fusion/full_text.json。
    pages 格式：[{"page": int, "blocks": [{...}]}]
    """
    d = tmp_path / pid / "literature" / "papers" / paper_name / "05_interprets" / "fusion"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "full_text.json"
    path.write_text(
        json.dumps({
            "paper_id": paper_name,
            "fusion_timestamp": "2026-08-08",
            "content": pages,
        }),
        encoding="utf-8",
    )
    return path


def _block(content: str, reading_order: int, btype: str = "Body", is_page_noise=None) -> dict:
    """建立一個測試用 block dict。"""
    b = {
        "id": f"blk_{reading_order}",
        "seq_id": f"seq_{reading_order}",
        "page": 1,
        "type": btype,
        "bbox": [0, 0, 100, 20],
        "score": 0.9,
        "content": content,
        "reading_order": reading_order,
        "method": "test",
        "source": "test",
    }
    if is_page_noise is not None:
        b["is_page_noise"] = is_page_noise
    return b


# ---------------------------------------------------------------------------
# fixture：把 _data_root() 指向 tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_data_root(tmp_path, monkeypatch):
    """所有測試一律把 _data_root() 的回傳值指向 tmp_path。"""
    monkeypatch.setattr(sc, "_data_root", lambda: str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# 1. 研究筆記
# ---------------------------------------------------------------------------

def test_notes_present(tmp_path):
    """研究筆記有內容時，corpus 應包含 [研究筆記...] 段落。"""
    _make_note(tmp_path, "TESTPRJ-p", "This is my research idea.")
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "[研究筆記" in result["corpus"]
    assert "This is my research idea." in result["corpus"]
    assert "study_notes" in result["sources"]


def test_notes_empty(tmp_path):
    """研究筆記為空字串時，corpus 不應輸出空標頭。"""
    _make_note(tmp_path, "TESTPRJ-p", "")
    result = sc.build_drafter_corpus("TESTPRJ-p")
    # 沒有論文，corpus 應該是空的（而不是一個空的 [研究筆記] 標頭）
    assert "[研究筆記" not in result["corpus"]
    assert "study_notes" not in result["sources"]


def test_notes_file_missing(tmp_path):
    """研究筆記檔案不存在時，不 crash，corpus 不包含筆記段落。"""
    # 不建立 note.json
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "[研究筆記" not in result["corpus"]
    assert result["corpus"] == ""


# ---------------------------------------------------------------------------
# 2. 論文全文
# ---------------------------------------------------------------------------

def test_paper_with_full_text(tmp_path):
    """有 full_text.json 的論文：corpus 應包含其正文。"""
    pages = [{"page": 1, "blocks": [_block("Introduction text here.", 1)]}]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "Introduction text here." in result["corpus"]
    assert any("paper:paper_A" in s for s in result["sources"])
    assert "paper_A" not in result["skipped_papers"]


def test_paper_missing_full_text(tmp_path):
    """缺 full_text.json 的論文：記錄到 skipped_papers，不 crash。"""
    # 建立論文目錄但不建立 full_text.json
    paper_dir = tmp_path / "TESTPRJ-p" / "literature" / "papers" / "paper_B"
    paper_dir.mkdir(parents=True)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "paper_B" in result["skipped_papers"]
    assert not any("paper:paper_B" in s for s in result["sources"])


def test_mixed_papers(tmp_path):
    """一篇有 full_text.json，一篇沒有：兩種情況並存應正確處理。"""
    pages = [{"page": 1, "blocks": [_block("Paper A content.", 1)]}]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)
    # paper_B 只建目錄，沒有 full_text.json
    (tmp_path / "TESTPRJ-p" / "literature" / "papers" / "paper_B").mkdir(parents=True)

    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "Paper A content." in result["corpus"]
    assert "paper_B" in result["skipped_papers"]
    assert "paper_A" not in result["skipped_papers"]


# ---------------------------------------------------------------------------
# 3. Block 排序與雜訊過濾
# ---------------------------------------------------------------------------

def test_block_ordering_by_reading_order(tmp_path):
    """reading_order 較小的 block 應出現在較大的之前。"""
    # 故意把 reading_order 倒著放，確認排序有作用
    pages = [{
        "page": 1,
        "blocks": [
            _block("SECOND TEXT", reading_order=2),
            _block("FIRST TEXT", reading_order=1),
        ],
    }]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    idx_first = result["corpus"].find("FIRST TEXT")
    idx_second = result["corpus"].find("SECOND TEXT")
    assert idx_first != -1 and idx_second != -1
    assert idx_first < idx_second, "reading_order=1 的 block 應出現在 reading_order=2 之前"


def test_header_and_noise_blocks_excluded(tmp_path):
    """type=Header 或 is_page_noise=True 的 block 必須被略過。"""
    pages = [{
        "page": 1,
        "blocks": [
            _block("BODY TEXT KEEP", reading_order=1, btype="Body"),
            _block("HEADER NOISE REMOVE", reading_order=2, btype="Header"),
            _block("PAGE NOISE REMOVE", reading_order=3, btype="Body", is_page_noise=True),
        ],
    }]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "BODY TEXT KEEP" in result["corpus"]
    assert "HEADER NOISE REMOVE" not in result["corpus"]
    assert "PAGE NOISE REMOVE" not in result["corpus"]


# ---------------------------------------------------------------------------
# 4. Token 預算：每篇論文都必須貢獻（反擠佔性質）
# ---------------------------------------------------------------------------

def test_budget_all_papers_contribute(tmp_path):
    """
    當 budget_tokens 極小時，每篇論文仍應貢獻至少一點文字。
    這是「反擠佔」性質：大論文不能讓其他論文全軍覆沒。
    """
    # 建立 3 篇論文，每篇都有大量文字
    for i in range(1, 4):
        long_text = f"PAPER{i} " * 500  # 每篇約 500 個詞
        pages = [{"page": 1, "blocks": [_block(long_text, 1)]}]
        _make_full_text(tmp_path, "TESTPRJ-p", f"paper_{i:02d}", pages)

    # 設一個很小的 budget，確保每篇都被截斷但都有貢獻
    result = sc.build_drafter_corpus("TESTPRJ-p", budget_tokens=3000)
    corpus = result["corpus"]

    # 每篇論文的標記都必須出現在 corpus 中
    assert "PAPER1" in corpus, "paper_01 應有貢獻"
    assert "PAPER2" in corpus, "paper_02 應有貢獻"
    assert "PAPER3" in corpus, "paper_03 應有貢獻"


def test_budget_respected(tmp_path):
    """corpus 的估算 token 數不應大幅超出 budget_tokens。"""
    # 建立一篇非常長的論文
    long_text = "word " * 10000
    pages = [{"page": 1, "blocks": [_block(long_text, 1)]}]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)

    budget = 500
    result = sc.build_drafter_corpus("TESTPRJ-p", budget_tokens=budget)
    # 允許少量超出（標頭文字本身的 overhead），但不能超出 2 倍
    assert result["estimated_tokens"] <= budget * 2


# ---------------------------------------------------------------------------
# 5. 不可信文字清洗
# ---------------------------------------------------------------------------

def test_sanitize_removes_null_bytes(tmp_path):
    """_sanitize 必須移除 NUL 字元（防止 prompt injection 及 provider 拒絕）。"""
    dirty = "clean text\x00injected null\x00more text"
    cleaned = sc._sanitize(dirty)
    assert "\x00" not in cleaned
    assert "clean text" in cleaned


def test_sanitize_removes_control_chars(tmp_path):
    """_sanitize 必須移除控制字元（保留 \\t \\n \\r）。"""
    # 包含多種控制字元
    dirty = "text\x01\x02\x03valid\x1b[31mcolor\x1b[0m"
    cleaned = sc._sanitize(dirty)
    assert "\x01" not in cleaned
    assert "\x1b" not in cleaned
    assert "textvalid" in cleaned


def test_notes_text_is_sanitized(tmp_path):
    """從檔案讀進來的研究筆記文字需經過清洗。"""
    # 筆記中夾帶 NUL 字元
    _make_note(tmp_path, "TESTPRJ-p", "研究重點\x00注入點\x00正常文字")
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "\x00" not in result["corpus"]
    assert "研究重點" in result["corpus"]
    assert "正常文字" in result["corpus"]


def test_paper_text_is_sanitized(tmp_path):
    """從 full_text.json 讀進來的 block content 需經過清洗。"""
    dirty_content = "paper text\x00injection attempt\x01"
    pages = [{"page": 1, "blocks": [_block(dirty_content, 1)]}]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "\x00" not in result["corpus"]
    assert "\x01" not in result["corpus"]
    assert "paper text" in result["corpus"]


# ---------------------------------------------------------------------------
# 6. 邊界情境
# ---------------------------------------------------------------------------

def test_no_papers_dir(tmp_path):
    """literature/papers 目錄不存在時，skipped_papers 為空，corpus 為空。"""
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert result["corpus"] == ""
    assert result["skipped_papers"] == []


def test_corpus_includes_notes_and_papers(tmp_path):
    """研究筆記與論文同時存在時，兩者都應出現在 corpus 中。"""
    _make_note(tmp_path, "TESTPRJ-p", "My important research note.")
    pages = [{"page": 1, "blocks": [_block("Paper introduction.", 1)]}]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)

    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "My important research note." in result["corpus"]
    assert "Paper introduction." in result["corpus"]
    # 筆記應在論文之前（放在最前面作為 primary source）
    idx_note = result["corpus"].find("My important research note.")
    idx_paper = result["corpus"].find("Paper introduction.")
    assert idx_note < idx_paper, "研究筆記應在論文全文之前"


def test_skipped_papers_returned(tmp_path):
    """build_drafter_corpus 的 skipped_papers 欄位必須列出缺 full_text.json 的論文。"""
    # 一個只有空目錄的「論文」
    (tmp_path / "TESTPRJ-p" / "literature" / "papers" / "incomplete_paper").mkdir(parents=True)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    assert "incomplete_paper" in result["skipped_papers"]


def test_multipage_order(tmp_path):
    """多頁論文：第 1 頁的文字應出現在第 2 頁之前。"""
    pages = [
        {"page": 1, "blocks": [_block("PAGE ONE TEXT", 1)]},
        {"page": 2, "blocks": [_block("PAGE TWO TEXT", 1)]},
    ]
    _make_full_text(tmp_path, "TESTPRJ-p", "paper_A", pages)
    result = sc.build_drafter_corpus("TESTPRJ-p")
    idx_p1 = result["corpus"].find("PAGE ONE TEXT")
    idx_p2 = result["corpus"].find("PAGE TWO TEXT")
    assert idx_p1 < idx_p2, "第 1 頁的文字應出現在第 2 頁之前"
