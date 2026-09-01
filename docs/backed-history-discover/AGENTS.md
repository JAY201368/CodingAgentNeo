# Backend History Discover Agent Working Agreement

`requirement.md` is the product authority, `ARCHITECTURE.md` controls technical boundaries and contracts, and `TASKS.md` controls task scope, dependencies, and acceptance. Repository-level Agent requirements are inherited from `docs/baseline/AGENTS.md` unless this workflow is stricter.

## 1. Before work

1. Read this file, `requirement.md`, `ARCHITECTURE.md`, `TASKS.md`, `PROGRESS.md`, `DECISIONS.md`, `../baseline/AGENTS.md`, and the selected task with all completed dependencies.
2. Inspect the worktree and preserve unrelated changes.
3. Do not assume an incomplete dependency exists or implement a substitute outside scope.
4. Work on one task only. Update architecture and task cards before changing public contracts.
5. The orchestrator assigns each task ID to a fresh dedicated subagent. A worker must never claim, accept, or continue into another task.

## 2. Standard commands

| Command | Purpose |
| --- | --- |
| `python -m pip install -e ".[dev,http]"` | Install development and HTTP dependencies without overwriting local configuration |
| `python -m pytest` | Run all tests |
| `python -m ruff check .` | Run lint without rewriting files |
| `python -m ruff format --check .` | Check formatting without rewriting files |
| `python -m build` | Build sdist and wheel |
| `python -m pytest tests/acceptance -m acceptance` | Run the local aggregate acceptance suite |
| `python /Users/jay/.codex/skills/orchestrate-spec-driven-development/scripts/validate_workflow.py --repo docs/backed-history-discover` | Validate workflow structure |

Use the active Python 3.12 environment. Formatting fixes must name only task-owned paths.

## 3. Directory and module boundaries

- `docs/agent-backend-interface.md` is authoritative for provider and per-session backend semantics; `docs/agent-transport-interface.md` is authoritative for frontend bindings.
- `session.py` may own canonical fixed-directory discovery/parsing primitives. Transport modules must not import `SessionStore`, call `read_session`, enumerate paths, or derive session summaries.
- The workspace-scoped provider is the adapter's single backend application dependency. The per-session `AgentBackend` retains live command/event lifecycle only.
- `assembly.py` owns concrete provider construction and new/resumed session assembly. It must not import CLI, renderer, HTTP, or Web modules.
- `transports/in_process.py` and `transports/http/` map public contracts only. They must not own history, path, recovery, or Agent execution semantics.
- `cli.py` may preserve opaque-ID `--resume`; it must not scan history or accept session directories/files. `web/` is excluded from every task in this workflow.

## 4. Code and contract conventions

- Target Python is 3.12. Public boundaries use typed frozen dataclasses, Protocols, stable string error codes, and JSON-compatible values.
- Production persistence is always `resolved_workspace / ".coding-agent-neo" / "sessions"`; do not add an alias, environment variable, hidden fallback, or deprecated public override.
- Public resume input is an opaque `session_id`, never a path. Validate before path construction, do not recurse or follow symlinks, and never return/log a path.
- History responses and text are bounded with explicit truncation. One corrupt candidate must not fail a listing; direct invalid reads/resumes must fail safely.
- Preserve canonical EventEnvelope schema and sequence. Historical read pagination is finite; live events remain SSE/iterators.
- API keys, workspace paths, user text, raw JSONL, traceback, and provider payloads must not enter safe errors or new logs.

## 5. Minimum verification

| Change type | Minimum verification |
| --- | --- |
| Documentation | Links, paths, commands, code blocks, and architecture consistency |
| Domain logic | Unit tests for success, failure, and boundaries |
| Public contract | Integration tests for payloads, permissions, status, and errors |
| JSONL/history | Unit tests for bounds, ordering, corruption/tail, symlink/traversal, sequence, and no replay |
| Configuration/CLI | Parser/config tests plus subprocess help/resume/error behavior |
| Adapter | Shared conformance plus binding-specific mapping and lifecycle tests |
| UI | Out of scope; confirm no `web/` changes |
| Deployment | Configuration validation, health, routing, and persistence checks |

## 6. Prohibited actions

- Do not commit secrets, private user data, credentials, backups, or large generated artifacts.
- Do not use destructive repository, database, storage, or volume operations without explicit authorization.
- Do not rewrite unrelated files, lower quality gates, or report unverified behavior as complete.
- Do not modify `web/`, migrate or delete existing session files, add raw file endpoints, or restore `session_dir` under another public name.
- Workers must not commit. The main agent alone accepts and commits each completed task.

## 7. Delivery report

Report the task ID and observable behavior, changed modules, exact commands and results, contract/migration/configuration impacts, and limitations. Update `PROGRESS.md` and durable `DECISIONS.md` facts, but do not check the task or commit. Only the main agent may independently accept, check off, and commit a task.
