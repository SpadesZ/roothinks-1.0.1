# Evidence Index

產生時間: 2026-07-04 19:24 +08:00

## Model

`EvidenceSegment` stores project-scoped searchable evidence:

- `project_id`
- `source_type`
- `source_id`
- `paper_id`
- `segment_id`
- `title`
- `text`
- `content_hash`
- `metadata_json`

## Source Types

Supported constants are `paper_segment`, `study_note`, `paq_note`, `manuscript_note`, and `context_chain_item`.

## Indexing Flow

`upsert_evidence_segment()` uses `content_hash` to avoid duplicates. `index_paper_segments()` accepts extracted segment dictionaries and writes paper evidence.

## Search

`search_evidence()` currently uses deterministic lexical scoring: keyword overlap, BM25-like frequency, recency, and source priority.

## Fallback

Manuscript context injection first tries Evidence Index. If the DB table or app context is unavailable, it falls back to Context Chain keyword retrieval with `retrieval_mode="fallback_context_chain"` and `degraded=true`.

## Future Vector Upgrade

The current service can be replaced or augmented with sqlite-vec, ChromaDB, FAISS, or sentence-transformers. The stable caller contract is a list of evidence dicts with provenance.
