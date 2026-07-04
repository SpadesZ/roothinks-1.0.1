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
