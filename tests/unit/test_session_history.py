"""Workspace-scoped history discovery and finite event-page coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent_neo.backend import (
    InvalidSessionHistoryCursorError,
    InvalidSessionHistoryIdError,
    InvalidSessionHistoryLimitError,
    SessionHistoryNotFoundError,
    SessionHistoryUnavailableError,
)
from coding_agent_neo.backend_provider import LocalAgentBackendProvider


def envelope(
    sequence: int,
    event_type: str,
    payload: dict,
    *,
    session_id: str = "session_history1",
    agent_id: str = "agent_history1",
    parent_agent_id: str | None = None,
    timestamp: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "event_id": f"event_history{sequence}",
        "agent_id": agent_id,
        "parent_agent_id": parent_agent_id,
        "sequence": sequence,
        "type": event_type,
        "correlation_id": None,
        "provider_tool_call_id": None,
        "timestamp": timestamp or f"2026-09-01T00:00:{sequence:02d}Z",
        "payload": payload,
    }


def write_records(path: Path, records: list[dict], *, tail: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            for record in records
        )
        + tail
    )


def healthy_records(session_id: str = "session_history1") -> list[dict]:
    return [
        envelope(1, "session_start", {"state": "RUNNING"}, session_id=session_id),
        envelope(
            2,
            "agent_start",
            {"state": "RUNNING", "active_tools": ["read_file"]},
            session_id=session_id,
        ),
        envelope(
            3,
            "user_message",
            {"text": "first task"},
            session_id=session_id,
        ),
        envelope(
            4,
            "assistant_message",
            {"text": "answer", "new_field": {"kept": True}},
            session_id=session_id,
        ),
        envelope(
            5,
            "turn_end",
            {"state": "COMPLETED_TURN"},
            session_id=session_id,
        ),
    ]


def provider(tmp_path: Path) -> LocalAgentBackendProvider:
    return LocalAgentBackendProvider(tmp_path, backend_factory=lambda _resume: object())


def test_listing_projects_root_message_and_safe_bounded_text(tmp_path: Path) -> None:
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_history1.jsonl"
    records = healthy_records()
    records[2]["payload"]["text"] = "😀" * 2_000
    write_records(path, records)

    item = provider(tmp_path).list_sessions().sessions[0]

    assert item.session_id == "session_history1"
    assert item.first_user_message is not None
    assert item.first_user_message.truncated is True
    assert len(item.first_user_message.text.encode("utf-8")) <= 4_096
    assert item.first_user_message.original_length == len("😀".encode()) * 2_000
    assert item.created_at == records[0]["timestamp"].replace("Z", ".000000Z")
    assert item.last_sequence == 5
    assert item.last_state == "COMPLETED_TURN"
    assert item.resumable is True
    assert not hasattr(item, "path")


def test_listing_isolates_malformed_empty_and_incomplete_candidates(tmp_path: Path) -> None:
    sessions = tmp_path / ".coding-agent-neo" / "sessions"
    write_records(sessions / "session_healthy1.jsonl", healthy_records("session_healthy1"))
    (sessions / "session_empty1.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (sessions / "session_empty1.jsonl").write_text("", encoding="utf-8")
    (sessions / "session_broken1.jsonl").write_text("{not-json}\n", encoding="utf-8")
    write_records(
        sessions / "session_tail1.jsonl",
        healthy_records("session_tail1"),
        tail=b'{"schema_version":1,"session_id":"session_tail1"',
    )

    items = {item.session_id: item for item in provider(tmp_path).list_sessions().sessions}

    assert items["session_healthy1"].diagnostics == ()
    assert items["session_healthy1"].resumable is True
    assert items["session_broken1"].resumable is False
    assert any(item.code == "invalid_record" for item in items["session_broken1"].diagnostics)
    assert items["session_empty1"].resumable is False
    assert any(item.code == "missing_root_agent" for item in items["session_empty1"].diagnostics)
    assert items["session_tail1"].resumable is True
    assert any(item.code == "incomplete_tail" for item in items["session_tail1"].diagnostics)


def test_listing_is_newest_first_and_cursor_is_bounded_opaque(tmp_path: Path) -> None:
    sessions = tmp_path / ".coding-agent-neo" / "sessions"
    for session_id, timestamp in (
        ("session_old1", "2026-09-01T00:00:01Z"),
        ("session_new1", "2026-09-01T00:00:03Z"),
        ("session_new0", "2026-09-01T00:00:03Z"),
    ):
        records = healthy_records(session_id)
        for record in records:
            record["timestamp"] = timestamp
        write_records(sessions / f"{session_id}.jsonl", records)

    history = provider(tmp_path)
    first = history.list_sessions(limit=2)
    assert [item.session_id for item in first.sessions] == ["session_new1", "session_new0"]
    assert first.next_cursor is not None
    assert len(first.next_cursor) <= 256
    assert str(tmp_path) not in first.next_cursor
    second = history.list_sessions(cursor=first.next_cursor, limit=2)
    assert [item.session_id for item in second.sessions] == ["session_old1"]
    assert second.next_cursor is None


def test_listing_cursor_keeps_snapshot_when_a_new_session_is_appended(tmp_path: Path) -> None:
    sessions = tmp_path / ".coding-agent-neo" / "sessions"
    for session_id, timestamp in (
        ("session_old2", "2026-09-01T00:00:02Z"),
        ("session_old1", "2026-09-01T00:00:01Z"),
        ("session_old0", "2026-09-01T00:00:00Z"),
    ):
        records = healthy_records(session_id)
        for record in records:
            record["timestamp"] = timestamp
        write_records(sessions / f"{session_id}.jsonl", records)

    history = provider(tmp_path)
    first = history.list_sessions(limit=2)
    assert [item.session_id for item in first.sessions] == ["session_old2", "session_old1"]
    assert first.next_cursor is not None

    appended = healthy_records("session_appended")
    for record in appended:
        record["timestamp"] = "2026-09-01T00:01:00Z"
    write_records(sessions / "session_appended.jsonl", appended)

    second = history.list_sessions(cursor=first.next_cursor, limit=2)
    assert [item.session_id for item in second.sessions] == ["session_old0"]
    assert second.next_cursor is None

    fresh = history.list_sessions(limit=10)
    assert [item.session_id for item in fresh.sessions] == [
        "session_appended",
        "session_old2",
        "session_old1",
        "session_old0",
    ]


def test_listing_ignores_nested_non_candidates_directories_and_symlinks(tmp_path: Path) -> None:
    sessions = tmp_path / ".coding-agent-neo" / "sessions"
    write_records(sessions / "session_good1.jsonl", healthy_records("session_good1"))
    (sessions / "session_directory1.jsonl").mkdir(parents=True)
    nested = sessions / "nested"
    nested.mkdir()
    write_records(nested / "session_nested1.jsonl", healthy_records("session_nested1"))
    outside = tmp_path / "outside.jsonl"
    write_records(outside, healthy_records("session_link1"))
    try:
        (sessions / "session_link1.jsonl").symlink_to(outside)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    items = provider(tmp_path).list_sessions().sessions

    assert [item.session_id for item in items] == ["session_good1"]


def test_listing_rejects_a_symlinked_fixed_history_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-history-outside"
    outside.mkdir()
    root = tmp_path / ".coding-agent-neo"
    try:
        root.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    with pytest.raises(SessionHistoryUnavailableError) as failure:
        provider(tmp_path).list_sessions()
    assert str(failure.value) == "session history is unavailable"
    assert str(tmp_path) not in str(failure.value)


def test_first_user_message_only_uses_the_canonical_root_agent(tmp_path: Path) -> None:
    session_id = "session_root_message"
    records = [
        envelope(1, "session_start", {"state": "RUNNING"}, session_id=session_id),
        envelope(
            2,
            "agent_start",
            {"state": "RUNNING", "active_tools": ["read_file"]},
            session_id=session_id,
            agent_id="agent_root_message",
        ),
        envelope(
            3,
            "user_message",
            {"text": "child message must not win"},
            session_id=session_id,
            agent_id="agent_child_message",
            parent_agent_id="agent_root_message",
        ),
        envelope(
            4,
            "user_message",
            {"text": "root message"},
            session_id=session_id,
            agent_id="agent_root_message",
        ),
        envelope(
            5,
            "assistant_message",
            {"text": "answer", "tool_calls": []},
            session_id=session_id,
            agent_id="agent_root_message",
        ),
        envelope(
            6,
            "turn_end",
            {"state": "COMPLETED_TURN"},
            session_id=session_id,
            agent_id="agent_root_message",
        ),
    ]
    write_records(
        tmp_path / ".coding-agent-neo" / "sessions" / f"{session_id}.jsonl",
        records,
    )

    item = provider(tmp_path).list_sessions().sessions[0]

    assert item.first_user_message is not None
    assert item.first_user_message.text == "root message"
    assert not any(
        diagnostic.code == "missing_first_user_message" for diagnostic in item.diagnostics
    )


def test_event_page_filters_sequence_and_reports_more(tmp_path: Path) -> None:
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_history1.jsonl"
    write_records(path, healthy_records())
    history = provider(tmp_path)

    first = history.read_session_events("session_history1", limit=2)
    assert [event.sequence for event in first.events] == [1, 2]
    assert first.next_cursor == 2
    assert first.has_more is True
    assert first.events[1].payload["active_tools"] == ("read_file",)
    second = history.read_session_events("session_history1", since=first.next_cursor, limit=10)
    assert [event.sequence for event in second.events] == [3, 4, 5]
    assert second.next_cursor is None
    assert second.has_more is False
    empty = history.read_session_events("session_history1", since=5)
    assert empty.events == ()
    assert empty.next_cursor is None
    assert empty.has_more is False


def test_event_page_bounds_payload_and_incomplete_tail_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / ".coding-agent-neo" / "sessions" / "session_history1.jsonl"
    records = healthy_records()
    records[3]["payload"]["huge"] = "x" * 100_000
    write_records(path, records, tail=b"partial")

    page = provider(tmp_path).read_session_events("session_history1", limit=10)
    payload = page.events[3].payload
    assert payload["truncated"] is True
    assert payload["limit"] == 65_536
    assert payload["original_length"] > payload["limit"]
    assert any(item.code == "incomplete_tail" for item in page.diagnostics)
    encoded = json.dumps(page.to_dict(), ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) < 8 * 1024 * 1024


def test_event_page_bounds_aggregate_size_for_200_large_events(tmp_path: Path) -> None:
    session_id = "session_large_page"
    records = [
        envelope(
            sequence,
            "assistant_message",
            {"text": "x" * 65_000, "sequence_marker": sequence},
            session_id=session_id,
            timestamp="2026-09-01T00:00:00Z",
        )
        for sequence in range(1, 201)
    ]
    path = tmp_path / ".coding-agent-neo" / "sessions" / f"{session_id}.jsonl"
    write_records(path, records)

    page = provider(tmp_path).read_session_events(session_id, limit=200)
    encoded = json.dumps(page.to_dict(), ensure_ascii=False, separators=(",", ":")).encode()

    assert len(encoded) <= 8 * 1024 * 1024
    assert [event.sequence for event in page.events] == list(range(1, 201))
    assert [event.event_id for event in page.events] == [
        f"event_history{sequence}" for sequence in range(1, 201)
    ]
    assert all(event.session_id == session_id for event in page.events)
    assert all(event.payload["truncated"] is True for event in page.events)
    assert page.next_cursor is None
    assert page.has_more is False


@pytest.mark.parametrize(
    "method, args, error",
    [
        ("list_sessions", {"limit": 0}, InvalidSessionHistoryLimitError),
        ("list_sessions", {"limit": True}, InvalidSessionHistoryLimitError),
        ("list_sessions", {"cursor": "not-issued"}, InvalidSessionHistoryCursorError),
        ("read_session_events", {"session_id": "../bad"}, InvalidSessionHistoryIdError),
        (
            "read_session_events",
            {"session_id": "session_history1", "since": -1},
            InvalidSessionHistoryCursorError,
        ),
        (
            "read_session_events",
            {"session_id": "session_history1", "limit": 201},
            InvalidSessionHistoryLimitError,
        ),
    ],
)
def test_history_validates_opaque_ids_cursors_and_bounds(
    tmp_path: Path, method, args, error
) -> None:
    with pytest.raises(error):
        getattr(provider(tmp_path), method)(**args)


def test_direct_reads_distinguish_unknown_and_unavailable_records(tmp_path: Path) -> None:
    sessions = tmp_path / ".coding-agent-neo" / "sessions"
    write_records(sessions / "session_broken1.jsonl", [{"not": "an event"}])
    history = provider(tmp_path)

    with pytest.raises(SessionHistoryNotFoundError):
        history.read_session_events("session_missing1")
    with pytest.raises(SessionHistoryUnavailableError) as failure:
        history.read_session_events("session_broken1")
    assert str(failure.value) == "session history is unavailable"
    assert str(tmp_path) not in str(failure.value)
