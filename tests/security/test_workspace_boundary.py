"""Security tests for LocalExecutionEnvironment path enforcement."""

from __future__ import annotations

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


def test_absolute_and_parent_paths_are_rejected_before_file_side_effects(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("do not touch", encoding="utf-8")
    environment = LocalExecutionEnvironment(tmp_path, use_rg=False)
    environment.start()
    cancellation = CancellationSignal()

    operations = (
        lambda: environment.read_file(ReadFileRequest(str(outside_file)), cancellation),
        lambda: environment.list_files(ListFilesRequest(str(outside)), cancellation),
        lambda: environment.search(SearchRequest("secret", str(outside)), cancellation),
        lambda: environment.write_file(
            WriteFileRequest(str(outside_file), "changed"), cancellation
        ),
        lambda: environment.edit_file(
            EditFileRequest(str(outside_file), "do not touch", "changed"), cancellation
        ),
        lambda: environment.run_command(
            RunCommandRequest("printf bad", working_directory=str(outside)), cancellation
        ),
        lambda: environment.read_file(ReadFileRequest("../outside/secret.txt"), cancellation),
        lambda: environment.write_file(
            WriteFileRequest("nested/../../escape.txt", "bad"), cancellation
        ),
    )

    results = [operation() for operation in operations]

    assert all(result.status is EnvironmentStatus.ERROR for result in results)
    assert all(result.metadata["reason"] == "path_boundary" for result in results)
    assert outside_file.read_text(encoding="utf-8") == "do not touch"
    assert not (tmp_path / "escape.txt").exists()


def test_existing_and_pending_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-symlink-outside"
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("private", encoding="utf-8")
    outside_dir = outside / "directory"
    outside_dir.mkdir()
    (tmp_path / "file-link").symlink_to(outside_file)
    (tmp_path / "directory-link").symlink_to(outside_dir, target_is_directory=True)
    (tmp_path / "safe-parent").symlink_to(outside_dir, target_is_directory=True)
    environment = LocalExecutionEnvironment(tmp_path, use_rg=False)
    environment.start()
    cancellation = CancellationSignal()

    existing_file = environment.read_file(ReadFileRequest("file-link"), cancellation)
    existing_directory = environment.list_files(ListFilesRequest("directory-link"), cancellation)
    pending_parent = environment.write_file(
        WriteFileRequest("safe-parent/new.txt", "must not escape"), cancellation
    )
    pending_file = environment.write_file(
        WriteFileRequest("file-link", "must not overwrite"), cancellation
    )

    assert existing_file.status is EnvironmentStatus.ERROR
    assert existing_directory.status is EnvironmentStatus.ERROR
    assert pending_parent.status is EnvironmentStatus.ERROR
    assert pending_file.status is EnvironmentStatus.ERROR
    assert outside_file.read_text(encoding="utf-8") == "private"
    assert not (outside_dir / "new.txt").exists()


def test_recursive_list_and_search_never_follow_outside_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-recursive-outside"
    outside.mkdir()
    (outside / "secret.py").write_text("needle outside", encoding="utf-8")
    (tmp_path / "inside.py").write_text("needle inside", encoding="utf-8")
    (tmp_path / "external-dir").symlink_to(outside, target_is_directory=True)
    (tmp_path / "external-file").symlink_to(outside / "secret.py")
    environment = LocalExecutionEnvironment(tmp_path, use_rg=False)
    environment.start()
    cancellation = CancellationSignal()

    listed = environment.list_files(ListFilesRequest(recursive=True), cancellation)
    searched = environment.search(SearchRequest("needle"), cancellation)

    assert listed.status is EnvironmentStatus.SUCCESS
    assert all("secret.py" not in entry for entry in listed.entries)
    assert all("external" not in entry for entry in listed.entries)
    assert searched.status is EnvironmentStatus.SUCCESS
    assert [match.path for match in searched.matches] == ["inside.py"]
