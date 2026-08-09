# 檔案路徑: test/unit/test_evidence_snippet_window.py
# 產生時間: 2026-08-09 15:40 +08:00
# 版本: v1.0
# 模組定位:
#   檢索 snippet 窗口選取與非證據段落排除的行為測試。
# 背景:
#   正式站實測（DGVRYV-p）：
#   1. `_snippet` 原本用 `min(第一次出現位置)` 當窗口左界，等於永遠貼著段落開頭。
#      SEBASR 的 `5.2 Performance Evaluation` 共 6865 字元，17 個查詢命中有 13 個
#      落在最後 3000 字元，而「平均MER從52.08%下降到7.56%」在第 6824 字元 ——
#      舊窗口錨在 494，把整句留在窗外。症狀：草稿看得出引用了該章節，
#      卻寫不出任何具體數字，且**無法從外部察覺**。
#   2. `References` 段落 143482 字元，是全篇最長的段落，幾乎命中任何查詢詞，
#      在指名 SEBASR 的查詢裡被排到第 1 名 —— 一個 top_k 名額與 3000 字元預算
#      全花在別人的論文標題上。
# 主要責任:
#   1. 窗口要落在命中最密集處，而不是最早命中處。
#   2. 段落本身不超過上限時原封不動回傳。
#   3. 完全沒有命中時退回開頭（維持舊行為，不得改成回空字串）。
#   4. 書目／致謝／OCR 殘渣一律不得作為寫作依據。
# 呼叫來源:
#   pytest。不被應用程式碼 import。
# 輸入輸出契約:
#   `_snippet(text, query_tokens, size)` 回傳長度 <= size 的字串。
#   `is_non_evidence_segment(title)` 回傳 bool。
# 安全邊界:
#   - 純函式測試，不碰 DB、不碰檔案系統、不呼叫 LLM。
# 維護提醒:
#   - 若有人把窗口改回「錨在最早命中」，test_window_follows_hit_density 會紅。
#   - 排除清單是**檢索時**過濾，不是不建索引 —— 引用建議仍需要書目段落。
# 驗證方式:
#   python -m pytest test/unit/test_evidence_snippet_window.py -q
# ------------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.services.evidence_index_service import _snippet, is_non_evidence_segment


QUERY_TOKENS = ["error", "rate", "mer", "資料集"]


def _build_sec22_shaped_text():
    """重現正式站 sec-22 的形狀：命中在開頭稀疏、在尾巴密集，而要的數字在尾巴。

    比例照實測抓：真實 sec-22 共 17 個命中，開頭 3348 字元內只有 4 個，
    最後 3000 字元內有 13 個。第一版測試在開頭放了 10 次 "error rate"（20 個命中），
    密度最高的反而是開頭 —— 那是測試資料寫錯，不是實作錯，所以下面要斷言前提。
    """
    head = "This section describes the error rate metric. " * 2           # 4 個命中
    filler = "背景敘述與方法說明。" * 300                                   # 中段無命中
    tail = (
        "就平均 MER 而言，SEB-ASR 系統在所有資料集上始終優於單語 VOSK 系統。"
        "資料集 A 的平均 MER 從 54.93% 下降到 9.36%。"
        "資料集 B 的測試顯示辨識率提高了 6.89 倍，平均 MER 從 52.08% 下降到 7.56%。"
        "對於 Dataset All，error rate 亦大幅下降。"
    )
    return head + filler + tail, len(head)


def _count_hits(body, lo, hi):
    lower = body.lower()
    total = 0
    for token in QUERY_TOKENS:
        start = lo
        while True:
            idx = lower.find(token, start)
            if idx < 0 or idx >= hi:
                break
            total += 1
            start = idx + len(token)
    return total


def test_window_follows_hit_density_not_first_hit():
    """核心迴歸：數字在章節尾巴時，窗口必須跟著命中密度走。"""
    body, head_len = _build_sec22_shaped_text()
    size = 600

    # --- 先確認測試前提成立，否則這個測試證明不了任何事 ---
    assert len(body) > size, "本文必須長於窗口"
    assert body.find("7.56") > size, "目標數字必須落在『從頭取 size』之外"
    head_hits = _count_hits(body, 0, head_len)
    tail_hits = _count_hits(body, len(body) - size, len(body))
    assert head_hits < tail_hits, (
        "測試資料不成立：開頭命中(%d) 必須少於尾巴命中(%d)，"
        "否則錨在開頭也會通過，測不出密度選取" % (head_hits, tail_hits)
    )

    out = _snippet(body, QUERY_TOKENS, size=size)

    assert len(out) <= size
    assert "7.56" in out, (
        "窗口沒有跟著命中密度走，具體數據被切掉 —— "
        "這正是『草稿引用了章節卻寫不出數字』的原因"
    )


def test_short_segment_returned_intact():
    body = "短段落，整段就是一個語意單位。"
    assert _snippet(body, ["段落"], size=3000) == body


def test_no_hits_falls_back_to_head():
    """完全沒命中時退回開頭；不得回空字串（那會讓該段變成沒有內容的來源）。"""
    body = "abcdefghij" * 100
    out = _snippet(body, ["zzz", "qqq"], size=50)
    assert out == body[:50]


def test_window_never_exceeds_size():
    body = "error rate " * 500
    out = _snippet(body, ["error", "rate"], size=120)
    assert len(out) <= 120


@pytest.mark.parametrize("title", [
    "References",
    "references",
    "  REFERENCES  ",
    "7 References",
    "Bibliography",
    "Acknowledgements",
    "Acknowledgments",
    "Appendix (Uncovered OCR Blocks)",
])
def test_non_evidence_titles_are_excluded(title):
    assert is_non_evidence_segment(title) is True


@pytest.mark.parametrize("title", [
    "Introduction",
    "5.2 Performance Evaluation",
    "Evaluation Metrics",
    "Abstract",
    "4.1 Datasets",
    "Related Work and References Review",   # 含 references 但不是書目段
    "",
])
def test_real_evidence_titles_are_kept(title):
    assert is_non_evidence_segment(title) is False
