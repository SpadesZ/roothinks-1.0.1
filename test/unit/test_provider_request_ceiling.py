# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_provider_request_ceiling.py
# 子系統定位:
#   「真正送出去的量」的上限護欄。斷言對象是 dispatch_task 實際收到的字串，
#   不是任何中間層算出來的預算數字。
# 主要責任:
#   1. router + draft 兩次呼叫的總量不得超過 LLM_REQUEST_MAX_TOKENS。
#   2. 上限要涵蓋 system 規則、使用者指令、附件，不只 context。
#   3. _truncate_to_budget 的回傳值（含截斷提示字串）不得超過傳入預算。
# 明確不負責:
#   - 不驗 COC 組裝與章節 ACL（見 test_coc_acl.py / test_coc_bundle.py）。
#   - 不驗 handler 層的 packer 分配（見 test_prompt_budget.py）。
#   - 不驗真實 tokenizer 的精確度：估算函式與 provider 的 tokenizer 本來就有誤差，
#     這裡驗的是「系統有沒有依自己的估算收斂」，不是絕對 token 數。
# 上游呼叫者:
#   pytest。不被應用程式碼 import。
# 讀寫或持久化位置:
#   無。dispatch_task 被攔截，不打外部 API。
#   **但必須主動隔離 data root**：本檔直接呼叫 Task8Drafter（沒有 Flask app context），
#   而 ManuscriptRuling 會往下走到 ContextChain 的 fallback，`_get_data_root()`
#   在沒有 context 時退回 `os.getcwd()/data` —— 也就是 repo 的真實資料目錄。
#   實測踩過：第一版讓 data/CEIL-p 出現在真實 data/ 底下。
# 不變量:
#   - 「不超標」的斷言必須配一個「關掉收斂就會超標」的對照組，
#     否則測試資料太小時它恆為真（見 test_without_ceiling_the_same_input_overflows）。
# 相關 NOTE:
#   NOTE-017（provider request 層統一結算）。
# 驗證:
#   python -m pytest test/unit/test_provider_request_ceiling.py -q
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

CEILING = 24000
ROUTER_RESERVE = 2200


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path, monkeypatch):
    """
    見檔頭：沒有 app context 時 `_get_data_root()` 退回 cwd/data（真實資料目錄）。
    這些模組各自 `from ... import _get_data_root`，持有獨立的名稱綁定，
    只 patch 一個是無效的。
    """
    root = tmp_path / "ceildata"
    root.mkdir()
    from app.core_pro.manuscript import manuscript_io as mio
    from app.core_pro.manuscript import manuscript_ruling as mruling
    for module in (mio, mruling):
        monkeypatch.setattr(module, "_get_data_root", lambda: str(root))
    return root


def _drafter_and_capture(monkeypatch):
    from app.llm_service.matching_tasks import task_8drafter
    from app.llm_service.matching_tasks.task_8drafter import Task8Drafter

    captured = []

    def fake_dispatch(task_id, prompt, **kwargs):
        captured.append(str(prompt))
        return {"ok": True, "text": "drafted."}

    monkeypatch.setattr(task_8drafter, "dispatch_task", fake_dispatch)
    return Task8Drafter(), captured


def _oversized_inputs():
    """
    reviewer 的復現案例：合法的 4,000 字中文指令 + 接近 20K token 的 context，
    連附件都沒有。以中文計價（1.5 token/字），13,500 字約 20,250 tokens。
    """
    return {
        "prompt": "請" * 4000,
        "context": "研究內容" * 3400,
    }


class TestProviderRequestStaysUnderCeiling:
    def test_total_of_all_dispatched_prompts_is_under_ceiling(self, monkeypatch):
        monkeypatch.setenv("LLM_REQUEST_MAX_TOKENS", str(CEILING))
        monkeypatch.setenv("DRAFTER_ROUTER_RESERVE_TOKENS", str(ROUTER_RESERVE))

        drafter, captured = _drafter_and_capture(monkeypatch)
        data = _oversized_inputs()
        drafter.process_request(
            data["prompt"], context_text=data["context"],
            pid="CEIL-p", title="Ceiling Paper", section="introduction",
        )

        assert captured, "沒有攔截到任何 provider request"
        total = sum(drafter._estimate_tokens_fast(p) for p in captured)
        assert total <= CEILING, (
            f"實際送出總量 {total} 超過上限 {CEILING}；"
            f"各次呼叫={[drafter._estimate_tokens_fast(p) for p in captured]}"
        )

    def test_without_ceiling_the_same_input_overflows(self, monkeypatch):
        """
        對照組。沒有它，上面的斷言可能只是因為測試資料根本不夠大而恆為真 ——
        那就完全測不到收斂邏輯。這裡把上限開到很大，證明同一組輸入確實會超過
        25K，也就是 reviewer 復現的那個情境真的存在於這組資料上。
        """
        monkeypatch.setenv("LLM_REQUEST_MAX_TOKENS", "999999")
        monkeypatch.setenv("DRAFTER_ROUTER_RESERVE_TOKENS", "0")

        drafter, captured = _drafter_and_capture(monkeypatch)
        data = _oversized_inputs()
        drafter.process_request(
            data["prompt"], context_text=data["context"],
            pid="CEIL-p", title="Ceiling Paper", section="introduction",
        )

        total = sum(drafter._estimate_tokens_fast(p) for p in captured)
        assert total > 25000, (
            f"對照組只有 {total} tokens，沒有超過 25K —— "
            "測試資料不足以觸發收斂，上面那條斷言是空的"
        )

    def test_ceiling_covers_attachment_too(self, monkeypatch):
        """附件也在同一個上限之內：它同樣會進 [Context / Reference Material]。"""
        monkeypatch.setenv("LLM_REQUEST_MAX_TOKENS", str(CEILING))
        monkeypatch.setenv("DRAFTER_ROUTER_RESERVE_TOKENS", str(ROUTER_RESERVE))

        drafter, captured = _drafter_and_capture(monkeypatch)
        monkeypatch.setattr(drafter, "_extract_attachment_text",
                            lambda _a: "附件內容" * 4000)
        data = _oversized_inputs()
        drafter.process_request(
            data["prompt"], context_text=data["context"],
            attachment={"name": "big.txt", "mime": "text/plain", "data": ""},
            import_type="notes",
            pid="CEIL-p", title="Ceiling Paper", section="introduction",
        )

        total = sum(drafter._estimate_tokens_fast(p) for p in captured)
        assert total <= CEILING, f"帶附件時總量 {total} 超過上限 {CEILING}"


class TestTruncationMarkerFitsInsideBudget:
    """
    `_truncate_to_budget` 原本是二分搜尋完才把提示字串接上去，回傳值必然超標。
    上游拿它當預算邊界用，於是每一段都固定溢出十幾個 token。
    """

    # 樣本在測試內部才展開。用大字串當 parametrize 參數會讓 pytest 把整串放進
    # 測試 ID，再寫進 PYTEST_CURRENT_TEST 環境變數，Windows 上直接
    # ValueError: the environment variable is longer than 32767 characters。
    SAMPLES = {"cjk": ("中文內容", 2000), "ascii": ("english words ", 2000)}

    @pytest.mark.parametrize("kind", ["cjk", "ascii"])
    @pytest.mark.parametrize("budget", [500, 40, 8])
    def test_source_context_version(self, kind, budget):
        from app.core_pro.manuscript.source_context import _estimate_tokens, _truncate_to_budget

        unit, times = self.SAMPLES[kind]
        out = _truncate_to_budget(unit * times, budget)
        assert _estimate_tokens(out) <= budget, \
            f"截斷後仍有 {_estimate_tokens(out)} tokens，超過傳入的 {budget}"

    @pytest.mark.parametrize("kind", ["cjk", "ascii"])
    @pytest.mark.parametrize("budget", [500, 40, 8])
    def test_drafter_version(self, kind, budget):
        from app.llm_service.matching_tasks.task_8drafter import Task8Drafter

        unit, times = self.SAMPLES[kind]
        d = Task8Drafter()
        out = d._truncate_to_budget(unit * times, budget)
        assert d._estimate_tokens_fast(out) <= budget, \
            f"截斷後仍有 {d._estimate_tokens_fast(out)} tokens，超過傳入的 {budget}"

    def test_marker_is_still_present_when_it_fits(self):
        """對照組：預算放得下時仍要保留提示，否則作者不知道內容被裁過。"""
        from app.core_pro.manuscript.source_context import _truncate_to_budget

        out = _truncate_to_budget("中文內容" * 2000, 500)
        assert "truncated" in out, "截斷了卻沒有留下任何提示"
