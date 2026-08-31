"""Shared semantic scenarios for the real service and in-process binding."""

from __future__ import annotations

from pathlib import Path

from tests.unit.test_backend import wait_session

from coding_agent_neo.backend import BackendClosedError, CloseSession, SubmitTask
from coding_agent_neo.models import EventType, RuntimeState


def test_adapters_share_turn_cursor_and_close_semantics(backend_binding, tmp_path: Path) -> None:
    backend = backend_binding
    backend.send(SubmitTask("same scenario"))
    events = wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert backend.last_state is RuntimeState.COMPLETED_TURN

    resumed = []
    for event in backend.events(since=events[0].sequence - 1):
        resumed.append(event)
        if event.type == EventType.TURN_END:
            break
    assert [event.sequence for event in resumed] == [event.sequence for event in events]

    backend.send(CloseSession("test"))
    try:
        backend.send(SubmitTask("after close"))
    except BackendClosedError:
        pass
    else:  # pragma: no cover - assertion branch for a broken binding
        raise AssertionError("closed backend accepted a command")
