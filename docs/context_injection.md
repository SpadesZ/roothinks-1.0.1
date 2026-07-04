# Context Injection

產生時間: 2026-07-04 19:24 +08:00

Paragraph-level context injection lives in `app/core_pro/manuscript/context_inject.py`.

## Flow

1. Build a query from `section_title`, `paragraph_goal`, and `draft_text`.
2. Try `search_evidence()` from the local Evidence Index.
3. If evidence search is unavailable or empty, fallback to Context Chain L2/L1/L3 keyword retrieval.
4. Rank context items with deterministic keyword overlap.
5. Apply `top_k` and `max_tokens`.
6. Build a readable context block for Manuscript prompting.

## Context Item Fields

Each item includes `source_type`, `source_id`, `paper_id`, `segment_id`, `title`, `snippet`, `score`, `fingerprint`, `estimated_tokens`, `retrieval_mode`, and `degraded`.

## Token Budget

The current estimate is `max(1, len(text) // 4)`. This is intentionally simple and local-first. A future pass can replace it with `tiktoken` or provider tokenizers.

## Audit

`app/core_pro/manuscript/context_audit.py` writes sidecar JSON under `data/manuscript_context_audit/<project>/<section>/`. It stores source ids, fingerprints, prompt fingerprint, provider/model when known, and generation timestamp.

## Future Upgrade

The retrieval layer can be upgraded to sentence-transformers, sqlite-vec, ChromaDB, or FAISS without changing the Manuscript call shape.
