# PROGRESS.md

## Completed

- T01 — Backend and transport specifications now define one workspace-scoped provider, bounded history summaries/event pages, fixed-path opaque-ID resume, equivalent In-process/HTTP bindings, and stable safe errors; structural and existing architecture/transport tests passed.
- T02 — Production configuration and launchers no longer expose a session directory; new/resumed JSONL is confined to the fixed workspace repository with opaque-ID and symlink/containment checks.
- T03 — Workspace-scoped provider, immutable history DTOs/errors, private fixed-directory discovery, bounded first-root-message/event projections, opaque snapshot cursors, and strict provider-controlled new/resume creation are accepted.
- T04 — Canonical provider-backed In-process workspace binding and provider-routed compatibility builders are accepted with reusable history/resume conformance.
- T05 — Provider-only HTTP finite history list/read and new/resume creation are accepted with strict bounded decoding, stable safe errors, recovered resume cursors, and preserved live transport behavior.

## Current State

- T01–T05 are accepted. In-process and HTTP frontends can discover, read, and resume workspace sessions through the unified provider contract.
- T06 is unstarted by explicit user instruction; repository-wide reconciliation and aggregate milestone acceptance remain pending.

## Known Issues

- Existing session files in custom directories will not be migrated automatically.
- Web UI consumption is intentionally deferred and `web/` is excluded from this workflow.
- Bare system Python is PEP-668 managed and lacks `pytest`; a project-local `.venv` was created with the documented dev/HTTP extras, and the required matrix passed there with loopback proxy bypass.
- T05 evidence: main-agent HTTP/history/security matrix passed (34), Web launcher/acceptance regression passed (11), reusable adapter conformance including real HTTP history passed (5), Ruff check/format, workflow validation, and `git diff --check` passed; worker full suite passed (340).

## Next Recommended Task

- T06 — Reconcile contracts and run the repository aggregate quality gate. It is ready but intentionally not started.
