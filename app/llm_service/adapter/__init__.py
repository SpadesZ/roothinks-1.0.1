# Roothinks source maintenance contract
# 檔案路徑: app/llm_service/adapter/__init__.py
# 模組定位: LLM provider 邊界層；把單一供應商協定轉成 LlmBus 的共同 tuple contract。
# 主要責任: 定義 provider adapter package 邊界；各 driver 必須遵守共同 send_message/cancel contract。
# 上下游: matching task -> dispatcher -> LlmBus -> provider adapter；binding/usage 由 llm_match DB 與 usage store 支援。
# 維護邊界: 不得記錄 API key 或完整 prompt；provider error、usage、cache 與 cancel_event 身分不可在層間遺失或靜默降級。
# 驗證: python -m pytest test/unit tests -q
# Placeholder for app/llm_service/adapter/__init__.py
