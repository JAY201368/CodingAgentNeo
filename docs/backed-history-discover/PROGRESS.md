# PROGRESS.md

## Completed

- T01 — Backend and transport specifications now define one workspace-scoped provider, bounded history summaries/event pages, fixed-path opaque-ID resume, equivalent In-process/HTTP bindings, and stable safe errors; structural and existing architecture/transport tests passed.
- T02 — Production configuration and launchers no longer expose a session directory; new/resumed JSONL is confined to the fixed workspace repository with opaque-ID and symlink/containment checks.
- T03 — Workspace-scoped provider, immutable history DTOs/errors, private fixed-directory discovery, bounded first-root-message/event projections, opaque snapshot cursors, and strict provider-controlled new/resume creation are accepted.
- T04 — Canonical provider-backed In-process workspace binding and provider-routed compatibility builders are accepted with reusable history/resume conformance.
- T05 — Provider-only HTTP finite history list/read and new/resume creation are accepted with strict bounded decoding, stable safe errors, recovered resume cursors, and preserved live transport behavior.
- T06 — Interface indexes, README/configuration guidance, workflow evidence, and aggregate provider-boundary checks are reconciled; the complete repository quality gate is accepted.

## Current State

- T01–T06 are accepted. The Backend History Discover workflow is complete: In-process and HTTP frontends can discover, read, and resume workspace sessions through the unified provider contract.

## T06 Integrated Milestone Report

- Accepted implementation commits: `16ed3f2` (T01 contracts), `c359510` (T02 fixed workspace storage), `f7ea236` (T03 provider/history), `84ddac2` (T04 In-process binding), and `fd9f4c2` (T05 HTTP binding). T04 and T05 were confirmed from the repository commit history before this audit.
- The accepted system has one production persistence location, `<workspace>/.coding-agent-neo/sessions/`, and one workspace-scoped `AgentBackendProvider` for bounded history list/read plus new/resumed per-session backend creation. History reads are finite JSON; live events remain SSE/iterators.
- Compatibility impact: legacy custom session directories are not migrated or discovered. Existing CLI `--resume` remains an opaque session-ID flow; removed path/config inputs fail through validation. The compatibility builder remains provider-routed and is not an adapter dependency.
- Web UI work is intentionally deferred from this workflow; no `web/` source was changed by T06.
- Requirement audit: product history list/first-root-message projection is covered by `tests/unit/test_session_history.py`; finite event reads and resume sequence/no-replay are covered by `tests/transports/test_adapter_conformance.py` and `tests/integration/test_http_history.py`; fixed-path/config/opaque CLI behavior is covered by `tests/unit/test_config.py` and `tests/integration/test_cli.py`/`test_resume_cli.py`; corruption, bounds, symlinks, traversal, diagnostics, and replacement revalidation are covered by the session-history/provider/security tests; provider-only adapter boundaries are enforced by `tests/architecture/test_forbidden_dependencies.py` and its acceptance-aggregate entry. No raw-file route or Web source change was introduced.
- Quality gates (loopback proxy bypass applied to HTTP acceptance tests): `.venv/bin/python -m pytest` — 342 passed, 1 third-party `StarletteDeprecationWarning`; `.venv/bin/python -m pytest tests/acceptance -m acceptance` — 56 passed, 1 same warning; `.venv/bin/python -m ruff check .` — passed; `.venv/bin/python -m ruff format --check .` — 122 files already formatted; `.venv/bin/python -m build` — built `coding_agent_neo-0.1.0.tar.gz` and `coding_agent_neo-0.1.0-py3-none-any.whl`; workflow validator — `OK workflow structure is valid (6 tasks)`; `git diff --check` — passed.
- No environment-only blocker remains for the listed gates. The only warning is the third-party Starlette/httpx deprecation warning emitted by the installed test dependency.

## Known Issues

- Existing session files in custom directories will not be migrated automatically.
- Web UI consumption is intentionally deferred and `web/` is excluded from this workflow.
- Bare system Python is PEP-668 managed and lacks `pytest`; all required gates were run in the project-local `.venv` with the documented dev/HTTP extras and loopback proxy bypass for HTTP acceptance tests.
- T05 evidence: main-agent HTTP/history/security matrix passed (34), Web launcher/acceptance regression passed (11), reusable adapter conformance including real HTTP history passed (5), Ruff check/format, workflow validation, and `git diff --check` passed; worker full suite passed (340).

## Next Recommended Task

- None. All workflow tasks are accepted; Web UI history consumption remains intentionally outside this workflow.
