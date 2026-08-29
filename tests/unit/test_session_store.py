"""T06 append-only JSONL storage, bounds, and tail diagnostics."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from coding_agent_neo.events import PendingEvent
from coding_agent_neo.session import (
    DuplicateEventIdError,
    IncompleteSessionTailError,
    SessionStore,
    SessionWriteError,
    read_session,
)


def _event(event_type: str = "user_message", **payload):
    return PendingEvent(
        session_id="session-1",
        agent_id="agent-1",
        type=event_type,
        payload=payload,
    )


def test_jsonl_is_one_schema_v1_object_per_flushed_append(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(path, session_id="session-1", fsync=False)
    with path.open("a+", encoding="utf-8") as independent_reader:
        first = store.append(_event(text="first"))
        independent_reader.seek(0)
        visible_after_first = independent_reader.read()
        assert visible_after_first.endswith("\n")
        assert json.loads(visible_after_first)["event_id"] == first.event_id

        second = store.append(_event("assistant_message", text="second"))
        independent_reader.seek(0)
        lines = independent_reader.readlines()

    records = [json.loads(line) for line in lines]
    assert [record["sequence"] for record in records] == [1, 2]
    assert [record["schema_version"] for record in records] == [1, 1]
    assert [record["agent_id"] for record in records] == ["agent-1", "agent-1"]
    assert second.sequence == 2


def test_default_append_crosses_the_fsync_boundary_before_success(tmp_path, monkeypatch) -> None:
    fsync_calls = []
    monkeypatch.setattr(os, "fsync", lambda descriptor: fsync_calls.append(descriptor))
    store = SessionStore(tmp_path / "session.jsonl", session_id="session-1")

    persisted = store.append(_event(text="durable"))

    assert persisted.sequence == 1
    assert len(fsync_calls) == 1


def test_reopening_a_complete_store_continues_the_sequence(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    SessionStore(path, session_id="session-1", fsync=False).append(_event(text="one"))

    reopened = SessionStore(path, session_id="session-1", fsync=False)
    appended = reopened.append(_event(text="two"))

    assert appended.sequence == 2
    assert [event.sequence for event in reopened.read_events()] == [1, 2]


def test_one_store_serializes_concurrent_append_sequence_allocation(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(path, session_id="session-1", fsync=False)
    events = [_event(text=f"message-{index}") for index in range(24)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        persisted = list(pool.map(store.append, events))

    assert sorted(event.sequence for event in persisted) == list(range(1, 25))
    loaded = store.read_events()
    assert [event.sequence for event in loaded] == list(range(1, 25))
    assert len({event.event_id for event in loaded}) == 24


def test_valid_final_record_without_newline_gets_an_append_only_separator(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    first_store = SessionStore(path, session_id="session-1", fsync=False)
    first_store.append(_event(text="first"))
    path.write_bytes(path.read_bytes().removesuffix(b"\n"))

    reopened = SessionStore(path, session_id="session-1", fsync=False)
    reopened.append(_event(text="second"))

    assert [event.payload["text"] for event in reopened.read_events()] == [
        "first",
        "second",
    ]


def test_duplicate_explicit_event_id_is_rejected_without_touching_file(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(path, session_id="session-1", fsync=False)
    event = PendingEvent(
        "session-1",
        "agent-1",
        "user_message",
        event_id="event-fixed",
    )
    store.append(event)
    before = path.read_bytes()

    with pytest.raises(DuplicateEventIdError):
        store.append(event)

    assert path.read_bytes() == before
    assert store.next_sequence == 2


def test_large_payload_is_replaced_by_bounded_head_tail_metadata(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(
        path,
        session_id="session-1",
        max_payload_bytes=256,
        fsync=False,
    )
    original = "HEAD-" + ("middle" * 500) + "-TAIL"

    stored = store.append(_event(output=original))
    payload = stored.payload

    assert payload["truncated"] is True
    assert payload["original_length"] > 256
    assert "HEAD-" in payload["head"]
    assert "-TAIL" in payload["tail"]
    serialized_payload = json.dumps(
        dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert len(serialized_payload) <= 256


def test_partial_write_never_rewrites_prior_lines_and_poisoned_store_refuses_retry(
    tmp_path,
) -> None:
    path = tmp_path / "session.jsonl"
    healthy = SessionStore(path, session_id="session-1", fsync=False)
    healthy.append(_event(text="complete"))
    complete_prefix = path.read_bytes()

    class InterruptedStore(SessionStore):
        def _write_line(self, line: str) -> None:
            descriptor = os.open(self.path, os.O_APPEND | os.O_WRONLY)
            try:
                os.write(descriptor, b'{"schema_version":1')
            finally:
                os.close(descriptor)
            raise OSError("simulated interruption")

    interrupted = InterruptedStore(path, session_id="session-1", fsync=False)
    with pytest.raises(SessionWriteError):
        interrupted.append(_event(text="never complete"))
    assert path.read_bytes().startswith(complete_prefix)
    with pytest.raises(SessionWriteError):
        interrupted.append(_event(text="retry"))

    loaded = read_session(path, expected_session_id="session-1")
    assert [event.payload["text"] for event in loaded.events] == ["complete"]
    assert loaded.tail_diagnostic is not None
    assert loaded.tail_diagnostic.line_number == 2
    assert loaded.tail_diagnostic.byte_offset == len(complete_prefix)


def test_incomplete_tail_is_reported_and_blocks_further_append(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(path, session_id="session-1", fsync=False)
    store.append(_event(text="complete"))
    complete_length = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"session_id":"session-1"')

    result = read_session(path, expected_session_id="session-1")
    assert len(result.events) == 1
    assert result.tail_diagnostic is not None
    assert result.tail_diagnostic.code == "incomplete_tail"
    assert result.tail_diagnostic.line_number == 2
    assert result.tail_diagnostic.byte_offset == complete_length

    reopened = SessionStore(path, session_id="session-1", fsync=False)
    with pytest.raises(IncompleteSessionTailError):
        reopened.append(_event(text="must not hide the bad tail"))
