# Roothinks source maintenance contract
# 模組定位: Roothinks 自動化驗收層；把對應 production contract 固定成可重跑案例。
# 主要責任: 重現並驗收 embedding provider 的成功、失敗與回歸邊界。
# 上下游: pytest/node runner -> fixture/monkeypatch -> 對應 app 模組；測試資料只放 tmp/in-memory。
# 驗證: python -m pytest tests/test_embedding_provider.py -q
# 檔案路徑: tests/test_embedding_provider.py
# 產生時間: 2026-07-04 21:40 +08:00
# 版本: v0.1
# 維護提醒: deterministic embedding provider tests; do not require external models.

from app.services.embedding_provider import HashEmbeddingProvider


def test_hash_embedding_provider_is_stable():
    provider = HashEmbeddingProvider(dimensions=8)
    assert provider.embed_texts(["alpha beta"]) == provider.embed_texts(["alpha beta"])


def test_hash_embedding_provider_changes_with_text():
    provider = HashEmbeddingProvider(dimensions=8)
    assert provider.embed_texts(["alpha"]) != provider.embed_texts(["beta"])
