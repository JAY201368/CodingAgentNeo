# PROGRESS.md

## Completed

- T01 — Backend and transport specifications now define one workspace-scoped provider, bounded history summaries/event pages, fixed-path opaque-ID resume, equivalent In-process/HTTP bindings, and stable safe errors; structural and existing architecture/transport tests passed.

## Current State

- T01 is accepted. The authoritative contracts are stable; product behavior remains at the baseline until T02 begins the fixed-path configuration change.

## Known Issues

- Existing session files in custom directories will not be migrated automatically.
- Web UI consumption is intentionally deferred and `web/` is excluded from this workflow.
- Bare system Python is PEP-668 managed and lacks `pytest`; a project-local `.venv` was created with the documented dev/HTTP extras, and the required matrix passed there with loopback proxy bypass.

## Next Recommended Task

- T02 — Remove production `session_dir`/`--session-dir` and derive all session persistence from the resolved workspace; T01 is checked and evidenced.
