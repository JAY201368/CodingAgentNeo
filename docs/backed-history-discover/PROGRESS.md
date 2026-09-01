# PROGRESS.md

## Completed

- T01 — Backend and transport specifications now define one workspace-scoped provider, bounded history summaries/event pages, fixed-path opaque-ID resume, equivalent In-process/HTTP bindings, and stable safe errors; structural and existing architecture/transport tests passed.
- T02 — Production configuration and launchers no longer expose a session directory; new/resumed JSONL is confined to the fixed workspace repository with opaque-ID and symlink/containment checks.

## Current State

- T01 and T02 are accepted. Fixed-path persistence is implemented; the workspace-scoped history provider and DTO behavior remain unimplemented until T03.

## Known Issues

- Existing session files in custom directories will not be migrated automatically.
- Web UI consumption is intentionally deferred and `web/` is excluded from this workflow.
- Bare system Python is PEP-668 managed and lacks `pytest`; a project-local `.venv` was created with the documented dev/HTTP extras, and the required matrix passed there with loopback proxy bypass.

## Next Recommended Task

- T03 — Implement backend-owned history discovery, bounded event pages, first-message summaries, and provider-controlled new/resumed backend creation; T01 and T02 are checked and evidenced.
