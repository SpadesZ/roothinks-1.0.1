# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_context_chain_stale.py
# 子系統定位:
#   ContextChain stale 狀態對「寫作路徑」的阻斷護欄（NOTE-014）。
# 主要責任:
#   1. 被標為 stale 的衍生層（L2/L3）不得進入寫作用的檢索結果。
#   2. L1 不受影響：它裝的是人類剛改過的內容，跳過它等於把作者的修正也丟掉。
#   3. 降級必須可辨識（回傳 skipped 清單），不得靜默。
#   4. 未 stale 時行為不變，不得因為這道防護而少給脈絡。
# 明確不負責:
#   - 不驗證 ContextChain 自身的重算與 provenance（那是 context_chain_service 的責任）。
#   - 不驗證 Study 模組取用 compact_context 的行為（Study 是探索路徑，非寫作路徑）。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   純記憶體 dict，不落檔。
# 不變量:
#   - 「人工改過 L1 之後，舊摘要不得再蓋過作者的修正」是本檔的核心斷言。
# 相關 NOTE:
#   NOTE-014（stale ContextChain 對寫作路徑 fail-closed）。
# 驗證:
#   python -m pytest test/unit/test_context_chain_stale.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core_pro.manuscript.context_inject import _collect_context_chain_candidates

STALE_SENTINEL = "SENTINEL_STALE_L2_SUMMARY"
FRESH_L1_SENTINEL = "SENTINEL_FRESH_L1_HUMAN_EDIT"
STALE_L3_SENTINEL = "SENTINEL_STALE_L3_BRIEF"


def _chain(*, l2_stale: bool, l3_stale: bool) -> dict:
    """模擬 manual_override 之後的鏈：L1 新鮮，衍生層被標 stale。"""
    return {
        "project_id": "CHAINTST-p",
        "version": 7,
        "layers": {
            "L1": {
                "version": 7,
                "stale": False,
                "claims": [{"title": FRESH_L1_SENTINEL, "source": "human", "topic": "triage"}],
            },
            "L2": {
                "version": 6,
                "stale": l2_stale,
                "summary_packets": [{"packet_id": "p1", "text": STALE_SENTINEL}],
            },
            "L3": {
                "version": 6,
                "stale": l3_stale,
                "project_brief": STALE_L3_SENTINEL,
            },
        },
    }


def _texts(candidates):
    return " ".join(str(c.get("text") or c.get("title") or "") for c in candidates)


class TestStaleLayersAreNotServedToWriting:
    def test_stale_l2_summary_is_withheld(self):
        candidates, skipped = _collect_context_chain_candidates(_chain(l2_stale=True, l3_stale=False))
        assert STALE_SENTINEL not in _texts(candidates), "stale L2 仍被送進寫作路徑"
        assert "L2" in skipped

    def test_stale_l3_brief_is_withheld(self):
        candidates, skipped = _collect_context_chain_candidates(_chain(l2_stale=False, l3_stale=True))
        assert STALE_L3_SENTINEL not in _texts(candidates)
        assert "L3" in skipped

    def test_human_edited_l1_still_flows_through(self):
        """
        方向性很重要：stale 的意思是「衍生內容還沒跟上人類的修正」，
        所以要擋的是衍生層，不是人類剛改好的 L1。
        """
        candidates, skipped = _collect_context_chain_candidates(_chain(l2_stale=True, l3_stale=True))
        assert FRESH_L1_SENTINEL in _texts(candidates), "把作者的修正一起擋掉了，方向相反"
        assert set(skipped) == {"L2", "L3"}


class TestNoRegressionWhenFresh:
    def test_fresh_chain_serves_everything(self):
        candidates, skipped = _collect_context_chain_candidates(_chain(l2_stale=False, l3_stale=False))
        blob = _texts(candidates)
        assert STALE_SENTINEL in blob and STALE_L3_SENTINEL in blob and FRESH_L1_SENTINEL in blob
        assert skipped == [], "沒有 stale 卻回報跳過，會讓降級訊號失去意義"

    def test_skip_stale_can_be_disabled_for_diagnostics(self):
        """診斷／探索介面可以看 stale 內容，但必須是明確要求。"""
        candidates, skipped = _collect_context_chain_candidates(
            _chain(l2_stale=True, l3_stale=True), skip_stale=False
        )
        assert STALE_SENTINEL in _texts(candidates)
        assert skipped == []
