# CodingAgentNeo

CodingAgentNeo is a Python 3.12 coding-agent project. The current milestone
only establishes an installable package, development quality gates, an example
configuration, and the public CLI help surface. Agent execution, model access,
tools, environments, sessions, and interactive runs are intentionally not
implemented yet.

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

The example TOML contains only an environment-variable name for the future
API key. Never put a key value in source, arguments, tracked configuration, or
session files.
