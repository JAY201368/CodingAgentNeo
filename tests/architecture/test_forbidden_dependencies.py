"""Static boundary checks for the side-effect-free tools package."""

from __future__ import annotations

import ast
from pathlib import Path

TOOLS_ROOT = Path(__file__).parents[2] / "src" / "coding_agent_neo" / "tools"
FORBIDDEN_MODULES = {"os", "pathlib", "subprocess", "shutil"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "system", "popen"}


def test_tools_do_not_import_or_call_host_side_effect_apis() -> None:
    for source_path in TOOLS_ROOT.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".", 1)[0] not in FORBIDDEN_MODULES for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".", 1)[0] not in FORBIDDEN_MODULES
            elif isinstance(node, ast.Call):
                function_name = node.func.id if isinstance(node.func, ast.Name) else ""
                assert function_name not in FORBIDDEN_CALLS
