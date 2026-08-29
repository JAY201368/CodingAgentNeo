# CodingAgentNeo

CodingAgentNeo is a Python 3.12 coding agent with an OpenAI-compatible model client,
workspace tools, append-only JSONL sessions, automatic context compaction, and interactive or
one-shot terminal operation.

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

Copy `config.example.toml` to the ignored `.coding-agent-neo.toml`, choose a model, then export the
environment variable named by `api_key_env`. Configuration precedence is CLI, then
`CODING_AGENT_NEO_*` environment variables, then local TOML, then defaults. The API key value is
read only from the named environment variable; there is deliberately no `--api-key` option.

```bash
export OPENAI_API_KEY="..."
coding-agent-neo --task "inspect this project and summarize its tests"
printf '%s\n' "find and fix the failing test" | coding-agent-neo --approval-mode auto
coding-agent-neo                       # interactive task and follow-up prompts
```

In one-shot mode, the final assistant text is written to stdout while event/status diagnostics are
written to stderr. Interactive prompts and events use stdout. Exit codes are: `0` completed, `1`
startup/runtime `FAILED`, `2` command usage or configuration failure, `3` `LIMIT_REACHED`, and
`130` `INTERRUPTED`. A session JSONL file is created below `session_dir` once execution begins.

With non-interactive `ask`, bash is immediately denied and stdin is never consumed for approval;
choose `--approval-mode auto` or `--yolo` for explicitly unattended bash execution. Never put a
key value in source, arguments, tracked configuration, or session files. `--resume` is reserved for
the next delivery and currently returns a usage error.
