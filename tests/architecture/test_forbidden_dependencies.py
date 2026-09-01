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
ADAPTER_ROOT = PACKAGE_ROOT / "transports"
FORBIDDEN_ADAPTER_MODULES = {
    "coding_agent_neo.assembly",
    "coding_agent_neo.backend_factory",
    "coding_agent_neo.backend_provider",
    "coding_agent_neo.session",
}
FORBIDDEN_ADAPTER_NAMES = {
    "AgentBackendFactory",
    "FileSessionHistoryRepository",
    "LocalAgentBackendProvider",
    "SessionHistoryRepository",
    "SessionStore",
    "read_session",
    "resolve_session_path",
}


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


def test_adapters_use_only_public_ports_not_session_storage_or_assembly() -> None:
    """Keep history discovery and backend construction behind the provider port."""

    for source_path in sorted(ADAPTER_ROOT.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules = {alias.name for alias in node.names}
                assert not imported_modules & FORBIDDEN_ADAPTER_MODULES, (
                    f"{source_path.relative_to(PACKAGE_ROOT)} imports a private composition module"
                )
                imported_names = {
                    alias.asname or alias.name.split(".", 1)[0] for alias in node.names
                }
                assert not imported_names & FORBIDDEN_ADAPTER_NAMES, (
                    f"{source_path.relative_to(PACKAGE_ROOT)} imports a private history seam"
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in FORBIDDEN_ADAPTER_MODULES, (
                    f"{source_path.relative_to(PACKAGE_ROOT)} imports {module}"
                )
                imported_names = {alias.asname or alias.name for alias in node.names}
                assert not imported_names & FORBIDDEN_ADAPTER_NAMES, (
                    f"{source_path.relative_to(PACKAGE_ROOT)} imports a private history seam"
                )


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


LOCAL_ENVIRONMENT = PACKAGE_ROOT / "environment" / "local.py"
HOST_PROCESS_MODULES = {"subprocess"}
HOST_PROCESS_ATTRS = {
    "system",
    "popen",
    "spawn",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "killpg",
}
SOURCE_BRANCH_NAMES = {
    "skill",
    "skills",
    "mcp",
    "Skill",
    "MCP",
    "MCPClient",
    "SkillLoader",
}
PROCESS_STATE_NAMES = {
    "current_agent",
    "current_runtime",
    "current_session",
    "current_workspace",
    "ACTIVE_TOOLS",
    "GLOBAL_BUDGET",
    "_CURRENT_AGENT",
    "_current_runtime",
}
MUTABLE_CTORS = {"dict", "list", "set"}
ALLOWED_MUTABLE_ASSIGN_TARGETS = {"__all__"}
PROTOCOL_ONLY_MODULES = (
    PACKAGE_ROOT / "agent_loop.py",
    PACKAGE_ROOT / "executor.py",
    PACKAGE_ROOT / "context.py",
    PACKAGE_ROOT / "compactor.py",
    PACKAGE_ROOT / "policy.py",
    PACKAGE_ROOT / "runtime.py",
)
RESOURCE_SCAN_ATTRS = {"rglob", "glob", "iterdir", "listdir", "walk", "scandir"}
RESOURCE_SCAN_CALLS = {"open", "listdir", "scandir", "walk", "glob"}
RESOURCE_SCAN_IMPORTS = {"os", "pathlib", "glob", "subprocess", "importlib"}


def _iter_package_python() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _is_mutable_literal_or_ctor(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in MUTABLE_CTORS
    return False


def _assign_target_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            names.update(_assign_target_names(item))
    return names


def test_only_local_environment_contains_generic_host_file_and_command_side_effects() -> None:
    local_tree = ast.parse(
        LOCAL_ENVIRONMENT.read_text(encoding="utf-8"), filename=str(LOCAL_ENVIRONMENT)
    )
    assert "subprocess" in _imported_names(local_tree)

    for path in _iter_package_python():
        if path == LOCAL_ENVIRONMENT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(PACKAGE_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    assert root not in HOST_PROCESS_MODULES, f"{relative} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                assert root not in HOST_PROCESS_MODULES, f"{relative} imports {node.module}"
            elif isinstance(node, ast.Attribute) and node.attr in HOST_PROCESS_ATTRS:
                if isinstance(node.value, ast.Name) and node.value.id in {"os", "subprocess"}:
                    raise AssertionError(f"{relative} calls {node.value.id}.{node.attr}")


def test_loop_and_executor_have_no_tool_source_branches() -> None:
    for filename in ("agent_loop.py", "executor.py"):
        path = PACKAGE_ROOT / filename
        source = path.read_text(encoding="utf-8")
        assert "BUILTIN_TOOL_NAMES" not in source
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in SOURCE_BRANCH_NAMES, f"{filename} names {node.id}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in SOURCE_BRANCH_NAMES, f"{filename} names {node.attr}"


def test_loop_and_runtime_have_no_process_level_mutable_run_state() -> None:
    for filename in ("agent_loop.py", "runtime.py"):
        path = PACKAGE_ROOT / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = set()
                for target in node.targets:
                    names.update(_assign_target_names(target))
                leaked = names & PROCESS_STATE_NAMES
                assert not leaked, f"{filename} assigns process state {sorted(leaked)}"
                if names - ALLOWED_MUTABLE_ASSIGN_TARGETS and _is_mutable_literal_or_ctor(
                    node.value
                ):
                    raise AssertionError(
                        f"{filename} has module-level mutable state {sorted(names)}"
                    )
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                names = _assign_target_names(node.target)
                if names - ALLOWED_MUTABLE_ASSIGN_TARGETS and _is_mutable_literal_or_ctor(
                    node.value
                ):
                    raise AssertionError(f"{filename} has annotated module-level mutable state")
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                leaked = set(node.names) & PROCESS_STATE_NAMES
                assert not leaked, f"{filename} uses global {sorted(leaked)}"
            if isinstance(node, ast.Name) and node.id in PROCESS_STATE_NAMES:
                raise AssertionError(f"{filename} names process-level state {node.id}")


def test_context_builder_does_not_scan_skills_or_external_resources() -> None:
    for filename in ("context.py", "compactor.py"):
        path = PACKAGE_ROOT / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_names(tree)
        leaked_imports = imported & RESOURCE_SCAN_IMPORTS
        assert not leaked_imports, f"{filename} imports {sorted(leaked_imports)}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in SOURCE_BRANCH_NAMES, f"{filename} names {node.id}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in RESOURCE_SCAN_CALLS, f"{filename} calls {node.func.id}"
            if isinstance(node, ast.Attribute):
                assert node.attr not in RESOURCE_SCAN_ATTRS, f"{filename} uses {node.attr}"
                assert node.attr not in SOURCE_BRANCH_NAMES, f"{filename} names {node.attr}"


def test_loop_and_tools_depend_on_environment_protocol_not_local() -> None:
    forbidden = ("LocalExecutionEnvironment", "environment.local")
    paths = list(PROTOCOL_ONLY_MODULES) + sorted(TOOLS_ROOT.glob("*.py"))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.relative_to(PACKAGE_ROOT)} mentions {token}"


def test_frontend_type_surface_does_not_name_agent_objects() -> None:
    source = PACKAGE_ROOT / "cli.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_CLI_NAMES:
            raise AssertionError(f"cli.py names backend object {node.id}")
