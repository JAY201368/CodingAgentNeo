# CodingAgentNeo

CodingAgentNeo is a Python 3.12 coding-agent project. The current milestone includes an installable package, development quality gates, backend-neutral environment contracts, and a workspace-bound LocalExecutionEnvironment. Agent execution, model access, tools, sessions, and interactive runs are still being implemented.

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

Inspect the reserved command-line interface with either:

```bash
python -m coding_agent_neo --help
coding-agent-neo --help
```

The example TOML contains only an environment-variable name for the future API key. Never put a key value in source, arguments, tracked configuration, or session files.