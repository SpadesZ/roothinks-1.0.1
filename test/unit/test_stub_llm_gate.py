# Roothinks source maintenance contract
# 檔案路徑: test/unit/test_stub_llm_gate.py
# 子系統定位:
#   stub LLM adapter 的兩道鎖（NOTE-028）。
# 主要責任:
#   1. 未設 ROOTHINKS_ALLOW_STUB_LLM=1 時**不得**建立實例（擋「跑得起來」）。
#   2. 每一則回覆都必須帶 [STUB] 前綴（擋「看起來像真的」）。
#   3. adapter 的類名/模組名符合 LlmBus._load_driver 的動態載入慣例 ——
#      名字寫錯的話 vendor='stub' 會靜默載不到，而症狀是 LLM_NOT_BOUND
#      那類完全不指向命名的錯誤。
# 明確不負責:
#   - 不驗 PAQ chat 的持久化（見 test_paq_chat_persistence.py 與實機驗收）。
# 上游呼叫者:
#   pytest。
# 讀寫或持久化位置:
#   啟用時會寫 <data_root>/_stub_llm/。本檔一律先把 cwd 指到 tmp_path，
#   避免在真實 data/ 留下 prompt 落檔。
# ACL/安全邊界:
#   這個檔守的是「假 provider 不得在正式站可用」，放寬任一斷言都會讓
#   偽造內容有機會混進研究資料。
# 不變量:
#   - 第一道鎖是 env，第二道鎖是前綴。兩者必須各自有測試，
#     只驗其中一道時，另一道被拿掉不會有人發現。
# 相關 NOTE:
#   NOTE-028。
# 驗證:
#   python -m pytest test/unit/test_stub_llm_gate.py -q
# ---------------------------------------------------------------------------
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.llm_service.adapter import llm_stub


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """落檔走 cwd/data，所以每個測試都先把 cwd 釘在 tmp。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(llm_stub.ENV_FLAG, raising=False)


class TestFirstLockIsTheEnvFlag:
    def test_refuses_to_instantiate_without_the_flag(self):
        with pytest.raises(RuntimeError) as excinfo:
            llm_stub.StubClient(api_key="x", model="stub-1")
        assert llm_stub.ENV_FLAG in str(excinfo.value), (
            "錯誤訊息必須說出要設哪個 env，否則下一個人只會看到無來由的載入失敗"
        )

    def test_instantiates_once_the_flag_is_set(self, monkeypatch):
        """對照組。少了它，上面那條在 adapter 整個壞掉時也會綠。"""
        monkeypatch.setenv(llm_stub.ENV_FLAG, "1")
        client = llm_stub.StubClient(api_key="x", model="stub-1")
        assert client.model == "stub-1"

    @pytest.mark.parametrize("value", ["0", "", "true", "yes", "2"])
    def test_only_exactly_one_enables_it(self, monkeypatch, value):
        """`if os.environ.get(FLAG)` 這種寫法會讓 '0' 也啟用 —— 守住嚴格比對。"""
        monkeypatch.setenv(llm_stub.ENV_FLAG, value)
        with pytest.raises(RuntimeError):
            llm_stub.StubClient()


class TestSecondLockIsTheMarker:
    def test_every_reply_is_marked(self, monkeypatch):
        monkeypatch.setenv(llm_stub.ENV_FLAG, "1")
        client = llm_stub.StubClient()

        ok, res, msg = client.send_text_and_optional_images("hello")

        assert ok is True
        assert res["text"].startswith(llm_stub.STUB_MARKER), (
            "回覆沒有 [STUB] 前綴 —— 假內容會看起來像真的"
        )

    def test_prompt_is_recorded_for_evidence(self, monkeypatch, tmp_path):
        """
        落檔是「第 N 輪帶了前 N-1 輪」唯一的直接證據，因此它本身要有測試。
        """
        monkeypatch.setenv(llm_stub.ENV_FLAG, "1")
        client = llm_stub.StubClient()

        client.send_text_and_optional_images("User: 第一輪\nUser: 第二輪\n")

        recorded = list((tmp_path / "data" / "_stub_llm").glob("prompt_*.json"))
        assert len(recorded) == 1
        assert "第一輪" in recorded[0].read_text(encoding="utf-8")

    def test_reply_reports_turn_count(self, monkeypatch):
        """
        回覆帶出輪次數，讓實機驗收光看畫面就能分辨「有帶前文」與「沒帶前文」。
        """
        monkeypatch.setenv(llm_stub.ENV_FLAG, "1")
        client = llm_stub.StubClient()

        _, one, _ = client.send_text_and_optional_images("User: a\n")
        _, three, _ = client.send_text_and_optional_images("User: a\nUser: b\nUser: c\n")

        assert "1 次" in one["text"]
        assert "3 次" in three["text"]


class TestDriverNamingConvention:
    """
    LlmBus._load_driver 靠命名慣例動態載入：模組 llm_<vendor>、類名以 Client
    結尾且（小寫後）含 vendor 前三字。名字寫錯會靜默載不到。
    """

    def test_module_and_class_names_match_the_loader_rules(self):
        vendor = "stub"
        assert llm_stub.__name__.endswith(f"llm_{vendor}")

        names = [
            n for n in dir(llm_stub)
            if n.endswith("Client") and vendor[:3] in n.lower()
        ]
        assert names == ["StubClient"], f"動態載入找不到唯一的 Client 類別: {names}"
