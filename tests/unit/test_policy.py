"""T05 policy and approval boundary tests."""

from __future__ import annotations

import pytest

from coding_agent_neo.policy import (
    ApprovalMode,
    CallbackApprovalPort,
    DefaultExecutionPolicy,
    NonInteractiveApprovalPort,
    PolicyDecision,
)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("read_file", {"path": "src/main.py"}),
        ("list_files", {}),
        ("search", {"query": "needle"}),
        ("write_file", {"path": "src/main.py", "content": "pass"}),
        ("edit_file", {"path": "src/main.py", "old_text": "a", "new_text": "b"}),
    ],
)
def test_workspace_tools_are_allowed_by_default(name: str, arguments: dict[str, str]) -> None:
    assert DefaultExecutionPolicy().decide(name, arguments) is PolicyDecision.ALLOW


@pytest.mark.parametrize("mode", (ApprovalMode.ASK, "interactive"))
def test_bash_asks_by_default(mode: ApprovalMode | str) -> None:
    assert DefaultExecutionPolicy(mode=mode).decide("bash", {"command": "pytest"}) == "ask"


@pytest.mark.parametrize("mode", (ApprovalMode.AUTO, ApprovalMode.YOLO, "auto", "yolo"))
def test_bash_is_allowed_in_automatic_modes(mode: ApprovalMode | str) -> None:
    assert DefaultExecutionPolicy(mode=mode).decide("bash", {"command": "pytest"}) == "allow"


def test_deny_mode_and_unsafe_paths_are_denied() -> None:
    policy = DefaultExecutionPolicy(mode=ApprovalMode.DENY)
    assert policy.decide("bash", {"command": "pytest"}) is PolicyDecision.DENY
    for value in ("../outside", "/tmp/outside", "C:\\outside", "src\\..\\outside", "bad\x00path"):
        assert policy.decide("read_file", {"path": value}) is PolicyDecision.DENY
    assert policy.decide("bash", {"command": "pytest", "working_directory": "../outside"}) == "deny"


def test_approval_ports_are_injectable_and_noninteractive_never_calls_callback() -> None:
    seen: list[str] = []
    interactive = CallbackApprovalPort(lambda request: seen.append(request.tool_name) or True)
    assert interactive.request_approval.__name__ == "request_approval"
    noninteractive = NonInteractiveApprovalPort()
    assert noninteractive.interactive is False
    assert seen == []
