# Backend History Discover Requirements

## 1. Product requirements

1. Every frontend binding must be able to ask the Agent backend for historical sessions belonging to the currently configured workspace.
2. A historical-session summary must include the canonical session ID and the content of its first `user_message`, with an explicit bounded/truncated representation when necessary.
3. A frontend binding must be able to read a selected historical session's canonical events for display and select a historical session as the resume target.
4. History discovery, JSONL parsing, validation, projection, and resume selection are Agent backend capabilities. Transport adapters may map those capabilities but must not scan files, import `SessionStore`, or accept filesystem paths from ordinary frontends.
5. Production session records have exactly one location: `<workspace>/.coding-agent-neo/sessions/`. The production `session_dir` configuration field and CLI `--session-dir` option must be removed.
6. Resume must preserve the existing linear-session semantics: reuse the Agent session and root Agent identities, continue sequence allocation, rebuild context and budgets, and never replay historical tool side effects.

## 2. Security and lifecycle requirements

1. Frontends receive no workspace path, session-directory path, arbitrary filename, or raw filesystem access.
2. History lookup and resume accept only a validated opaque `session_id`; directory components, absolute paths, `.jsonl` filenames, traversal, and symlink escapes are rejected.
3. Only regular `.jsonl` files directly owned by the fixed session directory are candidates. One malformed or unreadable candidate must not fail the complete history listing.
4. History event reads and first-message projections are bounded. Invalid files expose only stable, safe diagnostics and never traceback, raw configuration, credentials, or unrelated file content.
5. Resume revalidates the selected file at creation time. A previous history-list response is never authorization or proof that the file is still resumable.
6. The existing single-active-transport-session rule remains in force. An active session cannot be replaced by a resume request.

## 3. Required deliverables

1. Update `docs/agent-backend-interface.md` before implementation so it defines the workspace-scoped backend provider, history DTOs, bounded reads, errors, and resume creation semantics.
2. Update `docs/agent-transport-interface.md` before implementation so In-process and HTTP/SSE bindings expose equivalent history and resume capabilities.
3. Implement the fixed production session location and remove public `session_dir` configuration and `--session-dir` CLI support.
4. Implement backend-owned history discovery, summary projection, historical event reads, and session creation/resume.
5. Map the capability through both In-process and HTTP adapters without modifying the Web UI in this workflow.
6. Add unit, transport-conformance, integration, security, architecture, CLI, and configuration evidence proportionate to the changed boundaries.

## 4. Explicit exclusions

- No arbitrary workspace file browser or raw JSONL download endpoint.
- No Web UI redesign or history picker implementation in `web/`.
- No remote/public deployment, authentication system, multi-workspace control plane, JSONL migration, deletion, rename, export, or search by message text.
- No Agent Loop, model, tool, policy, or execution-environment behavior changes except the composition needed to select the fixed persistence path.
