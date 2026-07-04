# Roothinks System Audit And Roadmap

產生時間: 2026-07-04 19:24 +08:00

## System Position

Roothinks is a local-first research workspace that links literature processing, study notes, manuscript drafting, LAVA/LLM task routing, and project context. This pass keeps existing behavior intact while adding safer errors, traceable context injection, quality evaluation, and local evidence retrieval.

## Implemented In This Pass

- Added structured `AppError`, `ErrorCode`, and `ErrorSeverity`.
- Added centralized `ProcessStatus` / `InterpretationStatus` and legacy status mapping.
- Added `safe_json_loads()` and changed `Paper.get_results()` to return explicit `invalid_json` metadata instead of silently returning `{}`.
- Added `context_chain.schema_version=1`, backward-compatible upgrade, and env-configurable `CONTEXT_CHAIN_L2_MAX_PACKETS`.
- Implemented paragraph-level context retrieval and readable injected context blocks.
- Added manuscript context audit sidecar with source ids and fingerprints.
- Added conservative opt-in LLM response cache utility and dispatcher single-path integration.
- Added Alembic scaffold with no-op baseline and additive `evidence_segments` migration.
- Added OCR evaluation metrics and runner.
- Added local Evidence Index service, citation suggestions, and reference export utilities.

## Confirmed Risks Not Fully Fixed

- `Paper` still uses a legacy composite primary key: `(paper_id, pid)`.
- Status strings still exist in older routes and frontend code.
- Some OCR / LLM paths still catch broad exceptions by design for resilience.
- Evidence search is lexical baseline only; no production vector store is introduced.
- LLM cache is opt-in and intentionally narrow.

## Phase 2 / Phase 3 Roadmap

- Calibrate OCR Arbiter thresholds using `evaluation/ocr_ground_truth`.
- Backfill evidence segments from existing literature segmentation outputs.
- Add a small admin action to rebuild the evidence index per project.
- Replace lexical search scoring with a hybrid lexical + local vector backend.
- Add UI visibility for citation suggestions and context audit history.

## DB Migration Notes

Alembic is scaffolded. `0001_baseline_schema` is a no-op marker and should be stamped only after verifying an existing DB. `0002_create_evidence_segments` only creates the new evidence index table and indexes. It does not alter or rebuild existing tables.

## Context Chain Notes

Old context files without `schema_version` are treated as version 0 and upgraded in memory to version 1. Corrupted context files return an explicit failed load state instead of pretending to succeed.

## LLM Service Notes

Dispatcher now returns structured `error_code` values while preserving legacy tuple/dict responses. LLM cache is disabled unless `LLM_RESPONSE_CACHE_ENABLED=true` is set.

## OCR Evaluation Notes

OCR quality is measured with CER/WER helpers and `scripts/evaluate_ocr.py`. The runner compares `*.gt.txt` annotations against local prediction text files; it does not call OCR engines directly.
