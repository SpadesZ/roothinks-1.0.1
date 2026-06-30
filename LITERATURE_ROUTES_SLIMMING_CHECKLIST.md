# Literature Routes 1500-Line Refactor Plan (roothinks-R)

Date: 2026-04-28
Target: shrink `app/core_pro/literature/literature_routes.py` to `<= 1500` lines  
Baseline: `3729` lines  
Current: `1347` lines

## 1) Objectives

1. Keep API contract stable (URL, request/response shape, status code behavior).
2. Split by responsibility, not by arbitrary line count.
3. Execute in small phases with immediate validation.
4. Preserve current monkeypatch-based tests (especially E2E patch points on `literature_routes`).

## 2) End-State Module Layout

1. `literature_routes.py`
   - Keep blueprint creation, ACL hook, shared glue/dependency providers.
   - Keep compatibility export points used by tests (e.g., `get_task_3search`, `_trigger_gold_bridge`, executors).
   - Size goal: `<= 1500`.
2. `literature_debug_routes.py`
   - `/api/literature/debug/*`
3. `literature_context_chain_routes.py`
   - `/api/literature/context_chain/*`
4. `literature_context_routes.py`
   - bootstrap/context/search/search_result persistence endpoints
5. `literature_batch_routes.py`
   - upload/batch/translation endpoints
6. `literature_processing_ops.py`
   - status/bridge/correction/full_json heavy logic (called by thin wrappers)
7. `literature_flowb_helpers.py`
   - Flow B text/reflow helper cluster
8. `literature_bflow.py`
   - Flow B route implementation + runtime helpers (already partially extracted)

## 3) Validation Gate (run after every phase)

1. `python -m py_compile app/core_pro/literature/literature_routes.py app/core_pro/literature/literature_bflow.py`
2. `python -m pytest test/integration_smoke -q`
3. `python -m pytest test/integration_e2e/test_user_journey_roothinks.py -q`

Pass criteria:
1. All tests above pass.
2. No endpoint regression in E2E journey.

## 4) Phase Plan (Cut + Test)

### Phase 0: Baseline lock

Tasks:
1. Confirm current line count and route list snapshot.
2. Confirm smoke/E2E baseline pass.

Rollback:
1. No code change in this phase.

### Phase 1: Extract debug routes (low risk)

Tasks:
1. Create `literature_debug_routes.py`.
2. Move:
   - `/api/literature/debug/runtime_log_tail`
   - `/api/literature/debug/flowb_probe`
   - `/api/literature/debug/recover_flowb`
3. Register extracted routes from `literature_routes.py`.

Risk:
1. Minimal; no frontend hard dependency.

Rollback:
1. Revert new module + registration import.

### Phase 2: Extract context-chain routes (low/medium risk)

Tasks:
1. Create `literature_context_chain_routes.py`.
2. Move:
   - `/api/literature/context_chain/brief`
   - `/api/literature/context_chain/topk`
   - `/api/literature/context_chain/hybrid_query`
   - `/api/literature/context_chain/override_claim`
3. Register from `literature_routes.py`.

Risk:
1. E2E uses these endpoints; must keep response schema unchanged.

Rollback:
1. Revert module + registration import.

### Phase 3: Extract context/search persistence routes (medium risk)

Tasks:
1. Create `literature_context_routes.py`.
2. Move:
   - `/api/literature/bootstrap`
   - `/api/literature/save_context`
   - `/api/literature/get_context_history`
   - `/api/literature/search`
   - `/api/literature/save_search_results`
   - `/api/literature/get_search_results`
   - `/api/literature/clear_search_results`
3. Register from `literature_routes.py`.

Risk:
1. Most user-facing literature entry APIs are here.

Rollback:
1. Revert module + registration import.

### Phase 4: Extract batch routes + preserve monkeypatch hooks (medium/high risk)

Tasks:
1. Create `literature_batch_routes.py`.
2. Move:
   - `/literature`, `/literature/`
   - `/api/literature/system_profile`
   - `/api/literature/preload_translation_models`
   - `/api/literature/upload`
   - `/api/literature/run_batch`
   - `/api/literature/run_translation`
   - `/api/literature/delete_paper`
   - `/api/literature/status/<pid>`
   - `/api/literature/get_region_image`
   - `/api/literature/get_block_json`
   - `/api/literature/get_block_manifest`
   - `/api/literature/save_correction`
   - `/api/literature/get_full_json`
3. Keep compatibility patch points on `literature_routes` (tests monkeypatch here).

Risk:
1. Highest; touches core pipeline journey.

Rollback:
1. Revert module + registration import.

### Phase 5: Utility compaction to hit <= 1500 (medium risk)

Tasks:
1. Move non-route helper clusters from `literature_routes.py` into utility modules (`literature_processing_ops.py`, `literature_flowb_helpers.py`).
2. Keep stable exported names in `literature_routes.py` via thin wrappers/import aliases.
3. Remove dead helper `_flowb_build_reflow_prompt` if still unused.

Risk:
1. Internal call graph breakage if helper signatures drift.

Rollback:
1. Revert utility extraction patches only.

## 5) Tracking (live)

1. Phase 0: `completed`
2. Phase 1: `completed`
3. Phase 2: `completed`
4. Phase 3: `completed`
5. Phase 4: `completed`
6. Phase 5: `completed`

## 6) Verification Snapshot

1. `python -m py_compile app/core_pro/literature/literature_routes.py app/core_pro/literature/literature_batch_routes.py app/core_pro/literature/literature_processing_ops.py app/core_pro/literature/literature_flowb_helpers.py`
   - `passed`
2. `python -m pytest test/integration_smoke -q`
   - `2 passed`
3. `python -m pytest test/integration_e2e/test_user_journey_roothinks.py -q`
   - `1 passed`
