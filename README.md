# CodingAgentNeo

CodingAgentNeo is a Python 3.12 coding agent with an OpenAI-compatible model client, workspace tools, append-only JSONL sessions, automatic context compaction, and interactive or one-shot terminal operation.

The local `bash`/shell operation starts in the configured workspace, but it is not an OS sandbox: commands inherit the launching user's filesystem, network, and process permissions and may access paths outside the workspace. Structured file operations enforce the workspace boundary, including symlink checks.

## Development

Create or activate a Python 3.12 environment, then run:

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
```

Inspect or run the command-line interface with either:

```bash
python -m coding_agent_neo --help
coding-agent-neo --help
```

Copy `config.example.toml` to the ignored `.coding-agent-neo.toml`, choose a model, then export the environment variable named by `api_key_env`. Configuration precedence is CLI, then `CODING_AGENT_NEO_*` environment variables, then local TOML, then defaults. The API key value is read only from the named environment variable; there is deliberately no `--api-key` option.

```bash
export OPENAI_API_KEY="..."
coding-agent-neo --task "inspect this project and summarize its tests"
printf '%s\n' "find and fix the failing test" | coding-agent-neo --approval-mode auto
coding-agent-neo                       # interactive task and follow-up prompts
```

In one-shot mode, the final assistant text is written to stdout while event/status diagnostics are written to stderr. Interactive prompts and events use stdout. Exit codes are: `0` completed, `1` startup/runtime `FAILED`, `2` command usage or configuration failure, `3` `LIMIT_REACHED`, and `130` `INTERRUPTED`. A session JSONL file is created below `session_dir` once execution begins.

With non-interactive `ask`, bash is immediately denied and stdin is never consumed for approval; choose `--approval-mode auto` or `--yolo` for explicitly unattended bash execution. Never put a key value in source, arguments, tracked configuration, or session files. `--resume <session_id>` (or a JSONL path) continues a linear session without replaying historical tool side effects.

Adapter implementers use the versioned [Agent backend interface specification](docs/agent-backend-interface.md) as the internal command, event, cursor, approval, and lifecycle authority. Frontends use only their corresponding binding in the [Agent adapter interface specification](docs/agent-transport-interface.md). The CLI obtains the explicit `InProcessAdapter` through `build_in_process_adapter`; the shared runtime is `AgentBackendService` from `build_agent_backend`.

The optional, frontend-independent Agent HTTP/SSE binding is available with:

```bash
python -m pip install -e ".[dev,http]"
coding-agent-neo-http --config .coding-agent-neo.toml
```

For Web development, run the Agent HTTP service above and start Vite in a
second process. Vite forwards only `/api` to the loopback Agent service, so the
browser uses the same `/api/v1` wire client as the production composition:

```bash
npm --prefix web run dev
```

For a same-origin local demonstration, build the Web assets and start the
separate composition root:

```bash
npm --prefix web run build
coding-agent-neo-web --config .coding-agent-neo.toml
```

The Web launcher listens on `127.0.0.1:8765`, serves `/api/v1` through the
frontend-independent Agent HTTP adapter, and serves the built `web/dist` SPA.
The Python wheel contains no Node dependencies or Web build output; use
`--dist-dir PATH` when launching from an installed package with an external
Vite build. The launcher does not build or inject configuration into static
HTML, and a missing build is rejected before the service starts.

The Web package can also be run independently for frontend development; the
Vite server itself does not require an API key, although Agent interactions
need the separate local HTTP service (or the composition launcher above):

```bash
cd web
npm ci
npm run dev
```

Run its quality gates and production build with:

```bash
npm --prefix web run lint
npm --prefix web run type-check
npm --prefix web run test
npm --prefix web run build
```

The Web package targets Node.js 20+ and npm 10+. Stop the Vite development server with Ctrl+C. Agent HTTP/SSE connectivity and product interactions are planned for later Web tasks.

The [Agent adapter interface specification](docs/agent-transport-interface.md) is the sole
authority for its binding, wire, event, error, security, configuration, and lifecycle contract;
the README is not a supplementary specification.
