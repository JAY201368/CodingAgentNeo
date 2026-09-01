"""Shared semantic scenarios for the real service and in-process binding."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_backend import wait_session

from coding_agent_neo.backend import (
    BackendClosedError,
    CloseSession,
    InvalidSessionHistoryCursorError,
    InvalidSessionHistoryIdError,
    InvalidSessionHistoryLimitError,
    SubmitTask,
)
from coding_agent_neo.models import EventType, RuntimeState


def test_workspace_history_binding_conformance(history_binding, tmp_path: Path) -> None:
    """Exercise the canonical In-process workspace binding over real JSONL."""

    scenario = history_binding
    binding = scenario.binding
    assert scenario.session_path.parent == tmp_path / ".coding-agent-neo" / "sessions"

    listing = binding.list_sessions(limit=1)
    assert len(listing.sessions) == 1
    item = listing.sessions[0]
    assert item.session_id == scenario.session_id
    assert item.first_user_message is not None
    assert item.first_user_message.text == "remember history"
    assert item.last_sequence == scenario.original_last_sequence
    assert listing.next_cursor is None

    first_page = binding.read_session_events(scenario.session_id, since=0, limit=2)
    assert len(first_page.events) == 2
    assert [event.sequence for event in first_page.events] == [1, 2]
    assert first_page.has_more is True
    assert first_page.next_cursor == first_page.events[-1].sequence

    second_page = binding.read_session_events(
        scenario.session_id,
        since=first_page.next_cursor,
        limit=2,
    )
    assert len(second_page.events) <= 2
    assert all(event.sequence > first_page.events[-1].sequence for event in second_page.events)

    with pytest.raises(InvalidSessionHistoryIdError):
        binding.read_session_events("../session_escape")
    with pytest.raises(InvalidSessionHistoryCursorError):
        binding.list_sessions(cursor="invalid-cursor")
    with pytest.raises(InvalidSessionHistoryCursorError):
        binding.read_session_events(scenario.session_id, since=-1)
    with pytest.raises(InvalidSessionHistoryLimitError):
        binding.list_sessions(limit=101)
    with pytest.raises(InvalidSessionHistoryLimitError):
        binding.read_session_events(scenario.session_id, limit=201)

    resumed = binding.create_session(resume_session_id=scenario.session_id)
    assert resumed.resume_last_sequence == scenario.original_last_sequence
    assert callable(resumed.send)
    assert callable(resumed.events)
    assert callable(resumed.close)
    try:
        resumed.send(SubmitTask("continue history"))
        follow_up_events = wait_session(
            tmp_path,
            lambda event: (
                event.type == EventType.TURN_END
                and event.sequence > scenario.original_last_sequence
            ),
        )
    finally:
        resumed.close()

    all_events = follow_up_events
    assert [event.sequence for event in all_events] == list(range(1, len(all_events) + 1))
    new_events = [event for event in all_events if event.sequence > scenario.original_last_sequence]
    assert new_events[0].sequence == scenario.original_last_sequence + 1
    assert any(event.type == EventType.TURN_END for event in new_events)
    assert not any(
        event.type == EventType.USER_MESSAGE and event.payload.get("text") == "remember history"
        for event in new_events
    )
    assert scenario.model.calls == 2


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
