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

_READ_CASES = (
    ("read_file", {"path": "src/main.py"}),
    ("list_files", {}),
    ("search", {"query": "needle"}),
)
_WRITE_CASES = (
    ("write_file", {"path": "src/main.py", "content": "pass"}),
    ("edit_file", {"path": "src/main.py", "old_text": "a", "new_text": "b"}),
    ("bash", {"command": "pytest"}),
)


@pytest.mark.parametrize(("name", "arguments"), _READ_CASES)
@pytest.mark.parametrize("mode", (ApprovalMode.ASK, ApprovalMode.AUTO, ApprovalMode.DENY))
def test_read_tools_are_allowed_without_approval(
    name: str, arguments: dict[str, str], mode: ApprovalMode
) -> None:
    assert DefaultExecutionPolicy(mode=mode).decide(name, arguments) is PolicyDecision.ALLOW


@pytest.mark.parametrize(("name", "arguments"), _WRITE_CASES)
@pytest.mark.parametrize("mode", (ApprovalMode.ASK, "interactive"))
def test_write_tools_ask_by_default(
    name: str, arguments: dict[str, str], mode: ApprovalMode | str
) -> None:
    assert DefaultExecutionPolicy(mode=mode).decide(name, arguments) == "ask"


@pytest.mark.parametrize(("name", "arguments"), _WRITE_CASES)
@pytest.mark.parametrize("mode", (ApprovalMode.AUTO, ApprovalMode.YOLO, "auto", "yolo"))
def test_write_tools_are_allowed_in_automatic_modes(
    name: str, arguments: dict[str, str], mode: ApprovalMode | str
) -> None:
    assert DefaultExecutionPolicy(mode=mode).decide(name, arguments) == "allow"


@pytest.mark.parametrize(("name", "arguments"), _WRITE_CASES)
def test_write_tools_are_denied_in_deny_mode(name: str, arguments: dict[str, str]) -> None:
    policy = DefaultExecutionPolicy(mode=ApprovalMode.DENY)
    assert policy.decide(name, arguments) is PolicyDecision.DENY


@pytest.mark.parametrize(("name", "arguments"), _WRITE_CASES)
def test_write_tools_noninteractive_ask_is_denied(name: str, arguments: dict[str, str]) -> None:
    policy = DefaultExecutionPolicy(mode=ApprovalMode.ASK, interactive=False)
    assert policy.decide(name, arguments) is PolicyDecision.DENY


def test_unsafe_paths_are_denied() -> None:
    policy = DefaultExecutionPolicy()
    for value in ("../outside", "/tmp/outside", "C:\\outside", "src\\..\\outside", "bad\x00path"):
        assert policy.decide("read_file", {"path": value}) is PolicyDecision.DENY
        assert policy.decide("write_file", {"path": value, "content": "x"}) is PolicyDecision.DENY
    assert policy.decide("bash", {"command": "pytest", "working_directory": "../outside"}) == "deny"


def test_approval_ports_are_injectable_and_noninteractive_never_calls_callback() -> None:
    seen: list[str] = []
    interactive = CallbackApprovalPort(lambda request: seen.append(request.tool_name) or True)
    assert interactive.request_approval.__name__ == "request_approval"
    noninteractive = NonInteractiveApprovalPort()
    assert noninteractive.interactive is False
    assert seen == []
