"""Real-provider integration evidence for finite HTTP history and resume."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from tests.unit.fake_environment import FakeExecutionEnvironment
from tests.unit.test_backend import ScriptedModel, wait_session

from coding_agent_neo.assembly import build_agent_backend_provider
from coding_agent_neo.backend import BackendClosedError, CloseSession, SubmitTask
from coding_agent_neo.config import AppConfig
from coding_agent_neo.models import EventType, NormalizedAssistantResponse
from coding_agent_neo.transports.http import create_app

_WIRE_FIXTURE = json.loads(
    (
        Path(__file__).parents[2] / "web" / "src" / "domain" / "fixtures" / "transport-v1.json"
    ).read_text(encoding="utf-8")
)


def test_browser_wire_fixture_history_samples_match_http_shape() -> None:
    """Keep HTTP history integration tests aligned with the shared browser fixture."""

    history = _WIRE_FIXTURE["history"]
    listing = history["list"]
    item = listing["sessions"][0]
    assert set(listing) == {"sessions", "next_cursor"}
    assert set(item) == {
        "session_id",
        "first_user_message",
        "created_at",
        "updated_at",
        "last_sequence",
        "last_state",
        "resumable",
        "diagnostics",
    }
    assert set(item["first_user_message"]) == {
        "text",
        "truncated",
        "original_length",
        "limit",
        "encoding",
    }
    events_page = history["events"]
    assert set(events_page) == {"session_id", "events", "next_cursor", "has_more", "diagnostics"}
    assert events_page["session_id"].startswith("session_")
    resume = history["resume"]
    assert set(resume["request"]) == {"resume_session_id"}
    assert set(resume["response"]) == {
        "transport_session_id", "state", "cursor", "approval_mode"
    }
    assert resume["response"]["transport_session_id"].startswith("transport_")
    assert resume["request"]["resume_session_id"] == events_page["session_id"]


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        api_key="placeholder",
        context_window=8_000,
        reserved_output_tokens=1_000,
    )


@pytest.fixture
def history_client(tmp_path: Path):
    config = _config(tmp_path)
    model = ScriptedModel(
        [
            NormalizedAssistantResponse(text="seed response"),
            NormalizedAssistantResponse(text="resumed response"),
        ]
    )
    provider = build_agent_backend_provider(
        config,
        interactive=False,
        model_client=model,
        environment=FakeExecutionEnvironment(),
        worker_shutdown_timeout_seconds=2.0,
        event_poll_timeout_seconds=0.05,
        fsync=False,
    )
    seeded = provider.create_session()
    try:
        seeded.send(SubmitTask("remember HTTP history"))
        wait_session(tmp_path, lambda event: event.type == EventType.TURN_END)
    finally:
        try:
            seeded.send(CloseSession("seed_done"))
        except BackendClosedError:
            pass
        seeded.close()
    session_path = next((tmp_path / ".coding-agent-neo" / "sessions").glob("*.jsonl"))
    session_id = session_path.stem

    app = create_app(provider, close_timeout_seconds=2.0)
    with TestClient(app) as client:
        yield client, session_id, tmp_path


def test_real_provider_history_list_read_and_resume(history_client) -> None:
    client, session_id, tmp_path = history_client

    listing = client.get("/api/v1/session-history", params={"limit": "1"})
    assert listing.status_code == 200
    assert set(listing.json()) == {"sessions", "next_cursor"}
    item = listing.json()["sessions"][0]
    assert set(item) == {
        "session_id",
        "first_user_message",
        "created_at",
        "updated_at",
        "last_sequence",
        "last_state",
        "resumable",
        "diagnostics",
    }
    assert item["session_id"] == session_id
    assert item["first_user_message"]["text"] == "remember HTTP history"
    original_cursor = item["last_sequence"]

    first = client.get(
        f"/api/v1/session-history/{session_id}/events",
        params={"since": "0", "limit": "2"},
    )
    assert first.status_code == 200
    first_page = first.json()
    assert set(first_page) == {"session_id", "events", "next_cursor", "has_more", "diagnostics"}
    assert first_page["session_id"] == session_id
    assert [event["sequence"] for event in first_page["events"]] == [1, 2]
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == 2

    resumed = client.post(
        "/api/v1/sessions",
        json={"resume_session_id": session_id},
    )
    assert resumed.status_code == 201
    transport_id = resumed.json()["transport_session_id"]
    assert resumed.json()["cursor"] == original_cursor
    try:
        closed = client.delete(f"/api/v1/sessions/{transport_id}")
        assert closed.status_code == 204
    finally:
        # The route is idempotent, and the context manager closes any leftover
        # provider-owned backend during app shutdown.
        client.delete(f"/api/v1/sessions/{transport_id}")
    assert (tmp_path / ".coding-agent-neo" / "sessions" / f"{session_id}.jsonl").is_file()


def test_real_provider_history_query_and_resume_errors_are_stable(history_client) -> None:
    client, session_id, _tmp_path = history_client

    cases = (
        ("/api/v1/session-history?limit=0", "invalid_history_limit"),
        ("/api/v1/session-history?limit=1&limit=2", "invalid_history_limit"),
        ("/api/v1/session-history?cursor=", "invalid_history_cursor"),
        (
            f"/api/v1/session-history/{session_id}/events?since=-1",
            "invalid_history_cursor",
        ),
        (
            f"/api/v1/session-history/{session_id}/events?limit=201",
            "invalid_history_limit",
        ),
        (
            "/api/v1/session-history/session_missing123/events",
            "history_not_found",
        ),
    )
    for url, code in cases:
        response = client.get(url)
        assert response.status_code in {400, 404}
        assert response.json()["error"]["code"] == code
        assert "/" not in response.text.split("message", 1)[-1]
        assert "traceback" not in response.text.casefold()

    invalid_resume = client.post(
        "/api/v1/sessions",
        json={"resume_session_id": "../outside"},
    )
    assert invalid_resume.status_code == 400
    assert invalid_resume.json()["error"]["code"] == "invalid_history_id"
