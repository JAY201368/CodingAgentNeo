"""Component tests for the local execution environment."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from coding_agent_neo.environment import (
    EditFileRequest,
    ListFilesRequest,
    LocalExecutionEnvironment,
    ReadFileRequest,
    RunCommandRequest,
    SearchRequest,
    WriteFileRequest,
)
from coding_agent_neo.models import EnvironmentStatus
from coding_agent_neo.runtime import CancellationSignal


def test_lifecycle_and_six_operations_use_backend_neutral_results(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    file_path = source / "main.py"
    file_path.write_text("first\nsecond\n", encoding="utf-8")
    environment = LocalExecutionEnvironment(tmp_path, use_rg=False)
    cancellation = CancellationSignal()

    before_start = environment.read_file(ReadFileRequest("src/main.py"), cancellation)
    assert before_start.status is EnvironmentStatus.ERROR
    assert before_start.metadata["reason"] == "not_started"

    environment.start()
    environment.start()
    read = environment.read_file(
        ReadFileRequest("src/main.py", start_line=2, end_line=2), cancellation
    )
    listed = environment.list_files(ListFilesRequest(recursive=True), cancellation)
    searched = environment.search(SearchRequest("second", use_regex=False), cancellation)
    written = environment.write_file(WriteFileRequest("src/new.py", "pass\n"), cancellation)
    edited = environment.edit_file(
        EditFileRequest("src/main.py", "second", "updated"), cancellation
    )
    command = environment.run_command(RunCommandRequest("pwd"), cancellation)

    assert read.status is EnvironmentStatus.SUCCESS
    assert read.content == "second\n"
    assert listed.status is EnvironmentStatus.SUCCESS
    assert listed.entries == ("src", "src/main.py")
    assert searched.status is EnvironmentStatus.SUCCESS
    assert [(match.path, match.line_number) for match in searched.matches] == [("src/main.py", 2)]
    assert written.status is EnvironmentStatus.SUCCESS
    assert written.path == "src/new.py"
    assert edited.status is EnvironmentStatus.SUCCESS
    assert file_path.read_text(encoding="utf-8") == "first\nupdated\n"
    assert command.status is EnvironmentStatus.SUCCESS
    assert command.stdout.strip() == str(tmp_path.resolve())
    assert command.exit_code == 0
    assert command.timed_out is False
    assert command.duration_seconds is not None
    assert command.metadata["working_directory"] == "."
    environment.close()
    environment.close()
    after_close = environment.write_file(WriteFileRequest("src/after.py", "x"), cancellation)
    assert after_close.status is EnvironmentStatus.ERROR
    assert after_close.metadata["reason"] == "closed"


def test_edit_requires_exactly_one_occurrence_and_preserves_bytes(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_bytes(b"old\nold\n")
    environment = LocalExecutionEnvironment(tmp_path, use_rg=False)
    environment.start()
    cancellation = CancellationSignal()

    before = file_path.read_bytes()
    missing = environment.edit_file(EditFileRequest("file.txt", "missing", "new"), cancellation)
    duplicate = environment.edit_file(EditFileRequest("file.txt", "old", "new"), cancellation)
    after = file_path.read_bytes()

    assert missing.status is EnvironmentStatus.ERROR
    assert duplicate.status is EnvironmentStatus.ERROR
    assert missing.metadata["reason"] == "edit_occurrence_mismatch"
    assert duplicate.metadata["actual_replacements"] == 2
    assert after == before

    successful = environment.edit_file(
        EditFileRequest("file.txt", "old", "new", expected_replacements=2), cancellation
    )
    assert successful.status is EnvironmentStatus.SUCCESS
    assert file_path.read_text(encoding="utf-8") == "new\nnew\n"


def test_read_list_search_and_command_outputs_are_explicitly_bounded(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("0123456789" * 20, encoding="utf-8")
    for index in range(5):
        (tmp_path / f"match-{index}.txt").write_text("needle\n", encoding="utf-8")
    environment = LocalExecutionEnvironment(
        tmp_path,
        max_file_bytes=16,
        max_list_entries=2,
        max_search_results=2,
        max_command_output_bytes=16,
        command_timeout_seconds=2,
        use_rg=False,
    )
    environment.start()
    cancellation = CancellationSignal()

    read = environment.read_file(ReadFileRequest("large.txt"), cancellation)
    listed = environment.list_files(ListFilesRequest(max_entries=99), cancellation)
    searched = environment.search(SearchRequest("needle", max_results=99), cancellation)
    command = environment.run_command(
        RunCommandRequest("printf 01234567890123456789", max_output_bytes=8), cancellation
    )

    assert read.truncated is True
    assert read.original_length == 200
    assert "truncated" in read.content
    assert listed.truncated is True
    assert listed.original_length == 6
    assert len(listed.entries) == 2
    assert searched.truncated is True
    assert searched.original_length == 5
    assert len(searched.matches) == 2
    assert command.truncated is True
    assert command.original_output_length == 20
    assert len(command.stdout.encode("utf-8")) <= 8


def test_missing_rg_has_a_documented_standard_library_fallback(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "file.txt").write_text("needle\n", encoding="utf-8")

    def no_rg(_name: str) -> None:
        return None

    monkeypatch.setattr("coding_agent_neo.environment.local.shutil.which", no_rg)
    environment = LocalExecutionEnvironment(tmp_path)
    environment.start()
    result = environment.search(SearchRequest("needle"), CancellationSignal())

    assert environment.rg_available is False
    assert result.status is EnvironmentStatus.SUCCESS
    assert result.metadata["engine"] == "stdlib"
    assert result.metadata["fallback"] == "python"
    assert result.metadata["rg_available"] is False


def test_command_reports_nonzero_and_timeout_without_losing_observability(tmp_path: Path) -> None:
    environment = LocalExecutionEnvironment(tmp_path, command_timeout_seconds=2)
    environment.start()
    cancellation = CancellationSignal()

    failed = environment.run_command(
        RunCommandRequest("printf out; printf err >&2; exit 7"), cancellation
    )
    timed_out = environment.run_command(
        RunCommandRequest("sleep 2", timeout_seconds=0.1), cancellation
    )

    assert failed.status is EnvironmentStatus.ERROR
    assert failed.exit_code == 7
    assert failed.stdout == "out"
    assert failed.stderr == "err"
    assert timed_out.status is EnvironmentStatus.TIMEOUT
    assert timed_out.timed_out is True
    assert timed_out.exit_code is not None
    assert timed_out.duration_seconds is not None


def test_timeout_reaps_background_descendants_after_shell_leader_exits(tmp_path: Path) -> None:
    environment = LocalExecutionEnvironment(tmp_path, command_timeout_seconds=5)
    environment.start()
    marker = tmp_path / "timeout-marker"

    started_at = time.monotonic()
    result = environment.run_command(
        RunCommandRequest("(sleep 1; printf leaked > timeout-marker) &", timeout_seconds=0.1),
        CancellationSignal(),
    )
    elapsed = time.monotonic() - started_at

    assert result.status is EnvironmentStatus.TIMEOUT
    assert result.timed_out is True
    assert elapsed < 0.75
    assert not marker.exists()
    time.sleep(1.1)
    assert not marker.exists()


def test_cancel_before_side_effect_and_cancel_during_command(tmp_path: Path) -> None:
    environment = LocalExecutionEnvironment(tmp_path, command_timeout_seconds=5)
    environment.start()
    cancellation = CancellationSignal()
    cancellation.cancel("user interrupt")

    blocked = environment.write_file(
        WriteFileRequest("blocked.txt", "must not write"), cancellation
    )
    assert blocked.status is EnvironmentStatus.CANCELLED
    assert not (tmp_path / "blocked.txt").exists()

    running_cancellation = CancellationSignal()
    results = []

    def run() -> None:
        results.append(environment.run_command(RunCommandRequest("sleep 5"), running_cancellation))

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.1)
    running_cancellation.cancel("test cancellation")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0].status is EnvironmentStatus.CANCELLED
    assert results[0].timed_out is False

    close_results = []

    def run_until_close() -> None:
        close_results.append(
            environment.run_command(RunCommandRequest("sleep 5"), CancellationSignal())
        )

    close_thread = threading.Thread(target=run_until_close)
    close_thread.start()
    time.sleep(0.1)
    environment.close()
    close_thread.join(timeout=2)

    assert not close_thread.is_alive()
    assert len(close_results) == 1
    assert close_results[0].status is EnvironmentStatus.CANCELLED


def test_cancel_reaps_background_descendants_after_shell_leader_exits(tmp_path: Path) -> None:
    environment = LocalExecutionEnvironment(tmp_path, command_timeout_seconds=5)
    environment.start()
    cancellation = CancellationSignal()
    results = []
    marker = tmp_path / "cancel-marker"

    def run() -> None:
        results.append(
            environment.run_command(
                RunCommandRequest("(sleep 1; printf leaked > cancel-marker) &"), cancellation
            )
        )

    started_at = time.monotonic()
    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.1)
    cancellation.cancel("test cancellation")
    thread.join(timeout=2)
    elapsed = time.monotonic() - started_at

    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0].status is EnvironmentStatus.CANCELLED
    assert elapsed < 0.75
    assert not marker.exists()
    time.sleep(1.1)
    assert not marker.exists()
