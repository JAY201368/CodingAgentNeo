"""AC-10: tracked tree, examples, and logs must not contain live API keys."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_KEY_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
)
UNTRACKED_SECRET_NAMES = {
    ".coding-agent-neo.toml",
    ".env",
    "credentials.json",
}


def _tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [REPO_ROOT / Path(item) for item in completed.stdout.decode("utf-8").split("\0") if item]


def test_ac10_tracked_tree_has_no_real_api_keys() -> None:
    tracked = _tracked_files()
    relative = {path.relative_to(REPO_ROOT).as_posix() for path in tracked}
    for name in UNTRACKED_SECRET_NAMES:
        assert name not in relative
    assert not any(path.suffix == ".jsonl" for path in tracked)
    assert not any(path.name.endswith(".session.jsonl") for path in tracked)

    example = (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert "api_key_env" in example
    assert re.search(r"^api_key\s*=", example, flags=re.MULTILINE) is None
    assert "sk-" not in example

    violations: list[str] = []
    for path in tracked:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in LIVE_KEY_PATTERNS:
            if pattern.search(text):
                violations.append(path.relative_to(REPO_ROOT).as_posix())
                break
    assert violations == []
