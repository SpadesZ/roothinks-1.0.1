# Migration Notes

產生時間: 2026-07-04 19:24 +08:00

## Alembic

Alembic scaffold has been added:

- `alembic.ini`
- `migrations/env.py`
- `migrations/versions/0001_baseline_schema.py`
- `migrations/versions/0002_create_evidence_segments.py`

`0001_baseline_schema` is no-op. Stamp it only after manually verifying the existing local DB schema.

## Evidence Segments

`0002_create_evidence_segments` creates a new additive table only. It is safe to rollback by dropping that new table, but doing so removes the local evidence index and requires rebuilding it.

## Paper Primary Key

`Paper` currently uses composite primary key `(paper_id, pid)`. This is high-risk for a first migration because row identity, foreign references, existing DB files, and file naming semantics are not fully proven. This pass does not change it.

Recommended future plan:

1. Add a nullable surrogate `id` in a compatibility migration.
2. Backfill ids.
3. Add unique constraint on `(pid, paper_id)`.
4. Update ORM references.
5. Run rollback rehearsal on a copied DB.

## Legacy Status

New defaults use `pending`. Legacy values map `idle -> pending`, `done -> completed`, and `error -> failed`. Older frontend paths still emit some legacy strings and need a separate compatibility pass.

## Invalid JSON

Invalid `Paper.result_json` no longer silently returns `{}`. It returns explicit `invalid_json` metadata unless strict mode is requested.
