# PROGRESS.md

## Completed

- T01 — Backend and transport specifications now define one workspace-scoped provider, bounded history summaries/event pages, fixed-path opaque-ID resume, equivalent In-process/HTTP bindings, and stable safe errors; structural and existing architecture/transport tests passed.
- T02 — Production configuration and launchers no longer expose a session directory; new/resumed JSONL is confined to the fixed workspace repository with opaque-ID and symlink/containment checks.
- T03 — Workspace-scoped provider, immutable history DTOs/errors, private fixed-directory discovery, bounded first-root-message/event projections, opaque snapshot cursors, and strict provider-controlled new/resume creation are accepted.
- T04 — Canonical provider-backed In-process workspace binding and provider-routed compatibility builders are accepted with reusable history/resume conformance.

## Current State

- T01–T04 are accepted. In-process frontends can discover/read/resume through the unified provider; HTTP remains at the baseline until T05.

## Known Issues

- Existing session files in custom directories will not be migrated automatically.
- Web UI consumption is intentionally deferred and `web/` is excluded from this workflow.
- Bare system Python is PEP-668 managed and lacks `pytest`; a project-local `.venv` was created with the documented dev/HTTP extras, and the required matrix passed there with loopback proxy bypass.

## Next Recommended Task

- T05 — Add finite provider-backed HTTP history routes and `resume_session_id` creation; T01–T04 are checked and evidenced.
