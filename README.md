# Roothinks R-10005

Current Roothinks mainline local implementation package selected on 2026-06-30.

Roothinks is a local AI research/workflow system for literature processing,
study workflows, PAQ interaction, manuscript workspace support, and LLM/LAVA
task routing.

## Main Areas

- `app/core_pro/literature` - literature routes, FlowB processing, OCR/layout
  helpers, segmentation, translation, and context chain logic.
- `app/core_pro/study` - study index, loader, matrix, tutor, and routes.
- `app/core_pro/paq` - PAQ taxonomy, matrix, interaction, and route logic.
- `app/core_pro/manuscript` - manuscript workspace, section models, image
  handling, context injection, and drafting/ruling helpers.
- `app/llm_service` - LLM provider adapters, dispatcher, routes, and matching
  tasks.
- `app/project_portfolio` - project portfolio routes and service layer.

## Local Data Boundary

The repository intentionally excludes local runtime data, model weights,
uploaded papers, generated segmentation outputs, caches, logs, and `.env`.

Use `env.prod.template` or local deployment docs as the starting point for
environment configuration. Keep secrets in local `.env` or a secret manager.

## Useful Documents

- `PROD_TRANSITION_PLAN.md`
- `DATA_LAYOUT_POLICY.md`
- `CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md`
- `LITERATURE_ROUTES_SLIMMING_CHECKLIST.md`
- `debugging/FLOWB_MONITORING_RECOVERY_CHECKLIST.md`
