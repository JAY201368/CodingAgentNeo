"""Contract tests for the Web/API composition root."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from coding_agent_neo.backend import AgentCommand
from coding_agent_neo.models import RuntimeState
from coding_agent_neo.web_launcher import (
    WebAssetsError,
    build_app,
    build_parser,
    main,
    run_server,
)


class FakeBackend:
    """Port-only backend used to prove the wrapper preserves API ownership."""

    last_state = RuntimeState.RUNNING

    def __init__(self) -> None:
        self.commands: list[AgentCommand] = []
        self.close_calls = 0

    def send(self, command: AgentCommand) -> None:
        self.commands.append(command)

    def events(self, *, since: int = 0):
        del since
        yield from ()

    def close(self) -> None:
        self.close_calls += 1


class FakeProvider:
    """Provider-port fake used by the Web composition tests."""

    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50):
        del cursor, limit
        raise AssertionError("Web composition test must not read history")

    def read_session_events(self, session_id: str, *, since: int = 0, limit: int = 200):
        del session_id, since, limit
        raise AssertionError("Web composition test must not read history")

    def create_session(self, *, resume_session_id: str | None = None):
        del resume_session_id
        return self.backend


def _dist(tmp_path: Path) -> Path:
    directory = tmp_path / "web-dist"
    directory.mkdir()
    (directory / "index.html").write_text(
        "<!doctype html><html><body><main>SPA sentinel</main></body></html>",
        encoding="utf-8",
    )
    assets = directory / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('asset');", encoding="utf-8")
    return directory


def test_api_routes_win_and_extensionless_paths_use_spa_fallback(tmp_path: Path) -> None:
    backend = FakeBackend()
    app = build_app(_dist(tmp_path), provider=FakeProvider(backend))

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "protocol_version": 1}

        api_404 = client.get("/api/v1/not-a-route")
        assert api_404.status_code == 404
        assert "SPA sentinel" not in api_404.text

        static_bad_host = client.get("/workspace/task-1", headers={"host": "evil.example"})
        assert static_bad_host.status_code == 400
        assert static_bad_host.json()["error"]["code"] == "invalid_host"

        static_bad_origin = client.get(
            "/workspace/task-1",
            headers={"origin": "https://evil.example"},
        )
        assert static_bad_origin.status_code == 400
        assert static_bad_origin.json()["error"]["code"] == "invalid_origin"

        route = client.get("/workspace/task-1")
        assert route.status_code == 200
        assert "SPA sentinel" in route.text

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('asset');"

        missing_asset = client.get("/assets/missing.js")
        assert missing_asset.status_code == 404
        assert "SPA sentinel" not in missing_asset.text


def test_api_session_lifecycle_remains_idempotent_through_wrapper(tmp_path: Path) -> None:
    backend = FakeBackend()
    app = build_app(_dist(tmp_path), provider=FakeProvider(backend))

    with TestClient(app) as client:
        created = client.post("/api/v1/sessions", json={})
        assert created.status_code == 201
        transport_id = created.json()["transport_session_id"]
        assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204
        assert client.delete(f"/api/v1/sessions/{transport_id}").status_code == 204

    assert backend.close_calls == 1


def test_missing_dist_is_rejected_before_api_provider_or_config_side_effect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "private" / "missing-dist"
    provider = FakeProvider(FakeBackend())

    with pytest.raises(WebAssetsError, match="Web build"):
        build_app(missing, provider=provider)

    # The CLI path validates the build before attempting to load an API key or
    # any other configuration.  Its safe diagnostic does not echo the private
    # path supplied by the caller.
    code = main(["--dist-dir", str(missing)])
    assert code == 1
    assert str(missing) not in capsys.readouterr().err


def test_web_launcher_parser_shares_config_surface_without_host_override() -> None:
    parser = build_parser()
    args = parser.parse_args(["--dist-dir", "web/dist", "--port", "9000"])
    assert args.dist_dir == "web/dist"
    assert args.port == 9000
    with pytest.raises(SystemExit):
        parser.parse_args(["--host", "0.0.0.0"])


def test_run_server_passes_loopback_host_and_port(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}
    fake_uvicorn = SimpleNamespace(
        run=lambda app, **kwargs: calls.update({"app": app, **kwargs}),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    dist = _dist(tmp_path)
    backend = FakeBackend()

    run_server(
        object(),
        dist_dir=dist,
        port=9123,
        provider=FakeProvider(backend),
    )

    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9123
    assert calls["access_log"] is False
    assert calls["app"].api_app is not None


def test_python_module_and_package_boundaries_do_not_reference_web_from_http() -> None:
    for path in Path("src/coding_agent_neo/transports/http").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "web/dist" not in source
        assert "web_launcher" not in source
        assert "StaticFiles" not in source
