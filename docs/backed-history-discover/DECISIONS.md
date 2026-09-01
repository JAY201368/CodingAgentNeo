# Decision Log

Add only durable, non-obvious decisions with downstream consequences.

## 2026-09-01 — Bootstrap fixes session storage beneath each workspace

- Choice: production session records exist only at `<resolved workspace>/.coding-agent-neo/sessions/`; remove `session_dir` configuration and CLI `--session-dir` rather than deprecating or aliasing them.
- Rationale and rejected alternative: a separately configurable directory cannot intrinsically prove workspace ownership and would keep cross-workspace discovery ambiguous.
- Consequences: existing custom session directories are not automatically discovered or migrated; tests may use internal dependency injection but ordinary frontends cannot select a directory or path.

## 2026-09-01 — Bootstrap uses one workspace-scoped backend provider

- Choice: adapters receive one `AgentBackendProvider` that owns history/list/read and new/resumed backend creation, while each returned `AgentBackend` continues to own one live linear session.
- Rationale and rejected alternative: putting history on an already-created session handle creates a bootstrap cycle; letting adapters call a repository beside the backend would create the prohibited persistence bypass.
- Consequences: backend and transport specifications must version the provider/DTO/error contract; assembly and adapters change, but Agent Loop, tools, model, policy, and environment remain untouched.

## 2026-09-01 — Bootstrap returns projections instead of raw JSONL

- Choice: history listing returns bounded summaries including the first canonical root user message, and history viewing returns bounded pages of canonical EventEnvelope values.
- Rationale and rejected alternative: raw files or caller-supplied paths would expose filesystem/configuration details and bypass canonical validation.
- Consequences: adapters map finite DTOs and stable safe errors; live events remain cursor-based iterators/SSE, and Web UI consumption is deferred.

## 2026-09-01 — T01 versions one provider and finite history binding

- Choice: both adapters receive one workspace-scoped `AgentBackendProvider`; history list/read returns immutable v1 DTO pages with newest-first list ordering, sequence-based event pagination, 4,096-byte first-message projection, and bounded diagnostics. Event history is finite JSON; only live events use iterators/SSE.
- Rationale and rejected alternative: a provider keeps persistence and resume authorization behind one backend dependency, while explicit bounds and opaque list cursors prevent unbounded responses or path-derived frontend inputs.
- Consequences: T02–T05 must implement the fixed `workspace/.coding-agent-neo/sessions` location, `session_...` ID validation, `1..100` list and `1..200` event limits, stable history/resume errors, and the HTTP `resume_session_id` request without restoring `session_dir`/`--session-dir`. T01 changes no product implementation or Web fixture.

## 2026-09-01 — T02 derives persistence and resume targets from resolved workspace

- Choice: `AppConfig` carries only the resolved workspace; assembly derives every production session file as `<workspace>/.coding-agent-neo/sessions/<session_id>.jsonl`, and resume accepts only a strict opaque `session_...` ID.
- Rationale and rejected alternative: retaining a path-or-ID resolver would preserve a caller-controlled persistence boundary and allow records from unrelated workspaces to be resumed.
- Consequences: legacy TOML, `CODING_AGENT_NEO_SESSION_DIR`, and CLI `--session-dir` inputs are rejected as unknown configuration; resume hints contain only the ID; direct path-based recovery remains available solely through the internal `recover_session_plan(path)` test seam for malformed-record coverage. Existing custom directories are neither discovered nor migrated.

## 2026-09-01 — T03 keeps history projection behind one provider

- Choice: the public provider port owns immutable bounded DTOs and stable safe errors, while a composition-owned fixed-directory repository parses canonical JSONL and captures short-lived opaque list cursors; event pages lower payload preview bounds when necessary to keep the complete response under 8 MiB.
- Rationale and rejected alternative: keeping repository access and resume validation inside the provider prevents adapters from opening paths or reconstructing session facts, and projection at the envelope payload preserves canonical IDs and sequence without returning raw JSONL.
- Consequences: listing isolates malformed candidates as bounded diagnostics, direct reads/resumes revalidate the current fixed file, incomplete tails remain resumable with diagnostics, and T04/T05 must bind their adapters to the provider rather than the repository or backend factory.
