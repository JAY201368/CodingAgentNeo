"""Static boundary checks for tools host I/O and frontend/backend decoupling."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "coding_agent_neo"
TOOLS_ROOT = PACKAGE_ROOT / "tools"
FORBIDDEN_MODULES = {"os", "pathlib", "subprocess", "shutil"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "system", "popen"}
FORBIDDEN_CLI_NAMES = {
    "AgentLoop",
    "AgentRuntime",
    "SessionStore",
    "LocalExecutionEnvironment",
    "OpenAICompatibleModelClient",
    "ChatCompletionsModelClient",
    "OpenAICompatibleClient",
    "OpenAICompatibleChatCompletionsClient",
    "OpenAIChatCompletionsClient",
    "OpenAIModelClient",
    "ToolRegistry",
    "ModelClient",
}
FRONTEND_MODULES = {"cli", "renderer"}
STD_STREAMS = {"stdin", "stdout", "stderr"}


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


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".", 1)[0])
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def test_cli_does_not_import_backend_implementation_objects() -> None:
    source = PACKAGE_ROOT / "cli.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imported = _imported_names(tree)
    leaked = FORBIDDEN_CLI_NAMES & imported
    assert not leaked, f"cli.py imports forbidden backend objects: {sorted(leaked)}"


def _resolve_package_module(module: str) -> Path | None:
    if not module.startswith("coding_agent_neo"):
        return None
    parts = module.split(".")[1:]
    if not parts:
        return None
    file_path = PACKAGE_ROOT.joinpath(*parts).with_suffix(".py")
    if file_path.exists():
        return file_path
    init_path = PACKAGE_ROOT.joinpath(*parts) / "__init__.py"
    if init_path.exists() and init_path != PACKAGE_ROOT / "__init__.py":
        return init_path
    return None


def _package_imports(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("coding_agent_neo"):
                    modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "coding_agent_neo"
        ):
            modules.append(node.module or "")
    return modules


def _uses_standard_streams(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in STD_STREAMS:
            if isinstance(node.value, ast.Name) and node.value.id == "sys":
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "sys":
            if any(alias.name in STD_STREAMS for alias in node.names):
                return True
    return False


def _frontend_import_target(module: str) -> str | None:
    if module in {"coding_agent_neo.cli", "coding_agent_neo.renderer"}:
        return module.rsplit(".", 1)[-1]
    parts = module.split(".")
    if len(parts) >= 2 and parts[-1] in FRONTEND_MODULES:
        return parts[-1]
    return None


def test_backend_and_assembly_do_not_import_frontend_or_standard_streams() -> None:
    entries = [PACKAGE_ROOT / "backend.py", PACKAGE_ROOT / "assembly.py"]
    seen: set[Path] = set()
    stack = list(entries)
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not _uses_standard_streams(tree), f"{path.name} uses sys.stdin/stdout/stderr"
        for module in _package_imports(tree):
            frontend = _frontend_import_target(module)
            assert frontend is None, f"{path.name} imports frontend module {frontend}"
            resolved = _resolve_package_module(module)
            if resolved is not None and resolved not in seen:
                stack.append(resolved)
    assert (PACKAGE_ROOT / "backend.py") in seen
    assert (PACKAGE_ROOT / "assembly.py") in seen
