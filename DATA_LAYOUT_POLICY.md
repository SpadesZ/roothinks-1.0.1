# Data Layout Policy (Module-Rooted)

## Canonical Rule
All runtime I/O must stay under module roots:

```
data/<pid>/
  paq/
  literature/
    paq_records/
    papers/<paper_id>/
      00_origins/
      01_intermediate/
      03_recognizes/
      05_interprets/
      06_translates/
      _jobs/
    segmentation/
    stacka/
    stackb/
    arbiter/
  study/
  manuscript/
  submit/
```

Legacy location `data/<pid>/<paper_id>/...` is read-compatible only, and will be migrated to:

`data/<pid>/literature/papers/<paper_id>/...`

## Path Governance Entry

- Central path module: `app/core_pro/storage_layout.py`
- Key APIs:
  - `resolve_literature_paper_dir(...)`
  - `list_literature_papers(...)`
  - `ensure_project_module_roots(...)`

## Modules Updated To Canonical Layout

- Literature:
  - `literature_routes.py`
  - `literature_batch_routes.py`
  - `literature_cvpipeline.py`
  - `literature_processing_ops.py`
  - `literature_bflow.py`
  - `literature_arbiterlogic.py`
  - `literature_listen.py`
- LLM tasks:
  - `task_4cv.py`
  - `task_5interpret.py`
- Study:
  - `study_loader.py`
  - `study_routes.py`
- Project bootstrap:
  - `project_portfolio/project_routes.py`
  - `core_pro/paq/paq_routes.py`

## Migration Strategy

When write path is requested, `resolve_literature_paper_dir(..., for_write=True)` will:

1. Prefer canonical `literature/papers/<paper_id>`.
2. If only legacy folder exists, move legacy folder into canonical location.
3. Continue all subsequent writes in canonical location.

Read path (`for_write=False`) prefers canonical, but can fallback legacy.

