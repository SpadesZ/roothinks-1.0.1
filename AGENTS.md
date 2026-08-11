# Roothinks agent instructions

## CodeGraph-first code exploration

- Before non-trivial code analysis, debugging, refactoring, or editing, check the CodeGraph index and use `codegraph_context` for the first structural lookup.
- Use `codegraph_trace` for end-to-end call paths and `codegraph_impact` before changing shared symbols. Use raw `rg`/file reads only for exact confirmation, unindexed templates/static assets, or runtime evidence.
- After source edits, allow the watcher to catch up or run `codegraph sync` before trusting graph results. CodeGraph supplements tests; it does not replace them.
- `.codegraph/` is a local, rebuildable cache and must never be committed.

## Rickie Second Brain

- Before non-trivial planning, architecture, debugging, review, research, or consequential decisions, call Rickie `brain_context` with the current task, repository path, and facets covering mechanism, trade-offs, pitfalls, and verification.
- Treat returned memories as prior evidence. Verify drift-prone repository and deployment facts against live source/runtime state.
- Respect the MCP privacy gate: do not bypass blocked content or read raw/sourcecard/review material directly. Second Brain access is read-only unless the user explicitly requests a separate curation workflow.

## Repository safety

- Preserve the dirty worktree and unrelated user changes.
- Do not index, copy, commit, or expose `.env`, `data/`, runtime databases, papers, logs, model files, or other ignored local artifacts.
