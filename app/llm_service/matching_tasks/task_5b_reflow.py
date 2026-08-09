# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/task_5b_reflow.py
# 模組定位: LLM task 業務層；組合特定任務 prompt，經 dispatcher 呼叫已綁定模型。
# 主要責任: 把解析後段落依語意與 reading order 重排，保留 segment identity 與 evidence lineage。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
# 驗證: python -m pytest test/unit tests -q
#路徑(app/llm_service/matching_tasks/task_5b_reflow.py) #版本 v1.0 #更版時間 20260420
import logging

from app.llm_service.llm_dispatcher import dispatch_task

logger = logging.getLogger(__name__)


class SemanticReflowTask:
    """
    Task 5B: Flow B 語意重組
    職責：
    1. 接收上游組好的 reflow prompt
    2. 透過 LAVA 綁定的 task_5b_reflow 線路呼叫 LLM
    """

    TASK_ID = "task_5b_reflow"

    def generate(self, prompt: str):
        text = str(prompt or "").strip()
        if not text:
            return False, "", "empty prompt"
        try:
            result = dispatch_task(self.TASK_ID, text, priority=5, max_retries=0)
            if result.get("ok"):
                return True, str(result.get("text") or ""), ""
            return False, "", str(result.get("msg") or "dispatch failed")
        except Exception as e:
            logger.error("[Task5B] dispatch exception: %s", e)
            return False, "", str(e)
