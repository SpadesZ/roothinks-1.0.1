# 檔案路徑: app/services/embedding_provider.py
# 產生時間: 2026-07-04 18:50 +08:00
# 版本: v0.1
# 模組定位:
#   Evidence retrieval 的 embedding provider 介面與 deterministic 測試實作。
# 主要責任:
#   1. 定義可替換介面。
#   2. 提供不需外部模型的 HashEmbeddingProvider。
# 維護提醒:
#   - 本輪不引入 ChromaDB / FAISS / sentence-transformers。
# -----------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import math


class EmbeddingProvider:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimensions: int = 32):
        self.dimensions = max(4, int(dimensions or 32))

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        tokens = str(text or "").lower().split()
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]
