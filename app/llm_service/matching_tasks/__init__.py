# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/matching_tasks/__init__.py
# 模組定位: LLM task 業務層；組合特定任務 prompt，經 dispatcher 呼叫已綁定模型。
# 主要責任: 集中 LLM matching-task package 的可載入入口，避免 task 自行建立 provider client。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
# 驗證: python -m pytest test/unit tests -q
#路徑(./app/llm_service/matching_tasks/__init__.py) #版本 v0.1 #更版時間 20260208-1200
# 此檔案使 matching_tasks 成為一個 Python Package
# 允許其他模組使用 from app.llm_service.matching_tasks import ... 語法