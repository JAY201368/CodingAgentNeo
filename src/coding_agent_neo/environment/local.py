"""Local implementation of the backend-neutral execution environment.

The class in this module is deliberately the only place in the project that
performs filesystem I/O, invokes ``rg`` or starts a subprocess.  Paths exposed
by the public environment contract are logical, workspace-relative paths;
absolute host paths are kept private to this implementation.

``run_command`` is a convenience execution boundary, not an operating-system
sandbox.  A shell process inherits the user, network access and filesystem
permissions of the process running CodingAgentNeo.  The workspace check only
establishes the initial current directory.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterable
from pathlib import Path, PureWindowsPath
from typing import Any

from coding_agent_neo.environment.base import (
    CommandResult,
    EditFileRequest,
    ExecutionEnvironment,
    FileResult,
    ListFilesRequest,
    ListResult,
    ReadFileRequest,
    RunCommandRequest,
    SearchRequest,
    SearchResult,
    WriteFileRequest,
)
from coding_agent_neo.models import EnvironmentStatus, SearchMatch
from coding_agent_neo.runtime import CancellationSignal

_DEFAULT_FILE_LIMIT = 64 * 1024
_DEFAULT_LIST_LIMIT = 100
_DEFAULT_SEARCH_LIMIT = 100
_DEFAULT_COMMAND_OUTPUT_LIMIT = 64 * 1024
_DEFAULT_COMMAND_TIMEOUT = 30.0
_READ_CHUNK_SIZE = 64 * 1024
_POLL_INTERVAL = 0.05
_UNSET = object()


class _PathBoundaryError(ValueError):
    """A logical path cannot be safely resolved within the workspace."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LocalExecutionEnvironment:
    """A workspace-bound implementation of :class:`ExecutionEnvironment`.

    ``workspace`` itself may be an absolute host path because it is supplied
    by the process configuration.  Every request path, by contrast, must be a
    relative logical path.  The root is resolved once by :meth:`start`; all
    later existing-path checks use real paths and all creation checks resolve
    the nearest existing parent before any write is attempted.

    The limits are intentionally conservative defaults and can be overridden
    per request by the fields in the backend-neutral request models.
    """

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        max_file_bytes: int = _DEFAULT_FILE_LIMIT,
        max_read_bytes: int | None = None,
        max_list_entries: int = _DEFAULT_LIST_LIMIT,
        max_search_results: int = _DEFAULT_SEARCH_LIMIT,
        max_search_bytes: int | None = None,
        max_command_output_bytes: int = _DEFAULT_COMMAND_OUTPUT_LIMIT,
        max_output_bytes: int | None = None,
        command_timeout_seconds: float = _DEFAULT_COMMAND_TIMEOUT,
        command_timeout: float | None = None,
        rg_path: str | os.PathLike[str] | None = None,
        use_rg: bool = True,
    ) -> None:
        if not isinstance(workspace, (str, os.PathLike)):
            raise TypeError("workspace must be a path-like value")
        workspace_text = os.fspath(workspace)
        if isinstance(workspace_text, bytes):
            raise TypeError("workspace must be a text path")
        if "\x00" in workspace_text:
            raise ValueError("workspace must not contain NUL")
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int):
            raise TypeError("max_file_bytes must be a non-negative integer")
        if max_file_bytes < 0:
            raise ValueError("max_file_bytes must be non-negative")
        if max_read_bytes is not None:
            if isinstance(max_read_bytes, bool) or not isinstance(max_read_bytes, int):
                raise TypeError("max_read_bytes must be a non-negative integer or None")
            if max_read_bytes < 0:
                raise ValueError("max_read_bytes must be non-negative")
            max_file_bytes = max_read_bytes
        for value, name in (
            (max_list_entries, "max_list_entries"),
            (max_search_results, "max_search_results"),
            (max_command_output_bytes, "max_command_output_bytes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be a non-negative integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if max_output_bytes is not None:
            if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int):
                raise TypeError("max_output_bytes must be a non-negative integer or None")
            if max_output_bytes < 0:
                raise ValueError("max_output_bytes must be non-negative")
            max_command_output_bytes = max_output_bytes
        for value, name in ((max_search_bytes, "max_search_bytes"),):
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise TypeError(f"{name} must be a non-negative integer or None")
                if value < 0:
                    raise ValueError(f"{name} must be non-negative")
        if isinstance(command_timeout_seconds, bool) or not isinstance(
            command_timeout_seconds, (int, float)
        ):
            raise TypeError("command_timeout_seconds must be a non-negative number")
        if command_timeout_seconds < 0:
            raise ValueError("command_timeout_seconds must be non-negative")
        if command_timeout is not None:
            if isinstance(command_timeout, bool) or not isinstance(command_timeout, (int, float)):
                raise TypeError("command_timeout must be a non-negative number or None")
            if command_timeout < 0:
                raise ValueError("command_timeout must be non-negative")
            command_timeout_seconds = float(command_timeout)
        if not math.isfinite(float(command_timeout_seconds)):
            raise ValueError("command_timeout_seconds must be finite")
        if not isinstance(use_rg, bool):
            raise TypeError("use_rg must be a boolean")

        self._workspace_input = Path(workspace_text)
        self._root: Path | None = None
        self._started = False
        self._closed = False
        self._max_file_bytes = max_file_bytes
        self._max_list_entries = max_list_entries
        self._max_search_results = max_search_results
        self._max_search_bytes = max_search_bytes
        self._max_command_output_bytes = max_command_output_bytes
        self._command_timeout_seconds = float(command_timeout_seconds)
        self._use_rg = use_rg
        self._rg_input = os.fspath(rg_path) if rg_path is not None else "rg"
        if isinstance(self._rg_input, bytes):
            raise TypeError("rg_path must be a text path")
        self._rg_path: str | None | object = _UNSET
        self._active_processes: set[subprocess.Popen[bytes]] = set()
        self._process_lock = threading.RLock()

    @property
    def workspace(self) -> Path | None:
        """Return the resolved workspace after start, for diagnostics/tests."""

        return self._root

    @property
    def root(self) -> Path | None:
        """Compatibility spelling for callers that call the workspace root ``root``."""

        return self._root

    @property
    def started(self) -> bool:
        return self._started

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def rg_available(self) -> bool | None:
        """Whether ``rg`` was found (``None`` until :meth:`start`)."""

        if self._rg_path is _UNSET:
            return None
        return self._rg_path is not None

    def start(self) -> None:
        """Resolve and validate the workspace and detect the optional ``rg``."""

        if self._closed:
            raise RuntimeError("environment is closed")
        if self._started:
            return
        try:
            root = self._workspace_input.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("workspace does not exist or cannot be resolved") from exc
        if not root.is_dir():
            raise ValueError("workspace must be a directory")
        self._root = root
        self._detect_rg()
        self._started = True

    def close(self) -> None:
        """Close the environment and terminate commands still owned by it.

        Closing is idempotent.  A command that is already running observes the
        closed state as cooperative cancellation and receives a structured
        ``CANCELLED`` result after its process group is reaped.
        """

        if self._closed:
            return
        self._closed = True
        with self._process_lock:
            processes = tuple(self._active_processes)
        for process in processes:
            self._terminate_process(process)

    def read_file(self, request: ReadFileRequest, cancellation: CancellationSignal) -> FileResult:
        started_at = time.monotonic()
        state_error = self._state_error(started_at, FileResult)
        if state_error is not None:
            return state_error
        if self._cancelled(cancellation):
            return self._file_result(
                status=EnvironmentStatus.CANCELLED,
                message="operation cancelled before reading",
                duration=self._duration(started_at),
                path=self._request_path(request),
            )
        try:
            _, resolved = self._resolve_existing(request.path, kind="file")
            if not resolved.is_file():
                raise _PathBoundaryError("path is not a regular file")
            raw = self._read_bytes(resolved, cancellation)
            if raw is None:
                return self._file_result(
                    status=EnvironmentStatus.CANCELLED,
                    message="operation cancelled while reading",
                    duration=self._duration(started_at),
                    path=request.path,
                )
            text = raw.decode("utf-8", errors="replace")
            text = self._select_lines(text, request.start_line, request.end_line)
            limit = request.max_bytes if request.max_bytes is not None else self._max_file_bytes
            content, truncated, original_length, original_bytes = _truncate_text(text, limit)
            return self._file_result(
                status=EnvironmentStatus.SUCCESS,
                message="file read",
                duration=self._duration(started_at),
                path=request.path,
                content=content,
                truncated=truncated,
                original_length=original_length,
                metadata={"original_length_bytes": original_bytes},
            )
        except _PathBoundaryError as exc:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message=exc.reason,
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "path_boundary"},
            )
        except (OSError, UnicodeError) as exc:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message=_public_os_error(exc, "unable to read file"),
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "file_error"},
            )

    def list_files(self, request: ListFilesRequest, cancellation: CancellationSignal) -> ListResult:
        started_at = time.monotonic()
        state_error = self._state_error(started_at, ListResult)
        if state_error is not None:
            return state_error
        if self._cancelled(cancellation):
            return self._list_result(
                status=EnvironmentStatus.CANCELLED,
                message="operation cancelled before listing",
                duration=self._duration(started_at),
                metadata={"reason": "cancelled"},
            )
        try:
            _, resolved = self._resolve_existing(request.path, kind="directory", allow_empty=True)
            if not resolved.is_dir():
                raise _PathBoundaryError("path is not a directory")
            entries, cancelled, skipped = self._collect_entries(
                resolved,
                request.path,
                recursive=request.recursive,
                cancellation=cancellation,
            )
            bounded, truncated = _bound_sequence(
                entries,
                min(request.max_entries, self._max_list_entries),
            )
            metadata: dict[str, Any] = {
                "original_length": len(entries),
                "recursive": request.recursive,
            }
            if skipped:
                metadata["skipped_symlinks"] = skipped
            if cancelled:
                return self._list_result(
                    status=EnvironmentStatus.CANCELLED,
                    message="operation cancelled while listing",
                    duration=self._duration(started_at),
                    entries=bounded,
                    truncated=truncated,
                    original_length=len(entries),
                    metadata=metadata,
                )
            return self._list_result(
                status=EnvironmentStatus.SUCCESS,
                message="files listed",
                duration=self._duration(started_at),
                entries=bounded,
                truncated=truncated,
                original_length=len(entries),
                metadata=metadata,
            )
        except _PathBoundaryError as exc:
            return self._list_result(
                status=EnvironmentStatus.ERROR,
                message=exc.reason,
                duration=self._duration(started_at),
                metadata={"reason": "path_boundary"},
            )
        except OSError as exc:
            return self._list_result(
                status=EnvironmentStatus.ERROR,
                message=_public_os_error(exc, "unable to list files"),
                duration=self._duration(started_at),
                metadata={"reason": "file_error"},
            )

    def search(self, request: SearchRequest, cancellation: CancellationSignal) -> SearchResult:
        started_at = time.monotonic()
        state_error = self._state_error(started_at, SearchResult)
        if state_error is not None:
            return state_error
        if self._cancelled(cancellation):
            return self._search_result(
                status=EnvironmentStatus.CANCELLED,
                message="operation cancelled before searching",
                duration=self._duration(started_at),
                metadata={"reason": "cancelled"},
            )
        try:
            _, resolved = self._resolve_existing(
                request.path, kind="search target", allow_empty=True
            )
            if self._rg_path is _UNSET:
                self._detect_rg()
            if self._use_rg and self._rg_path is not None:
                result = self._search_with_rg(request, cancellation, resolved)
                # An executable can disappear between detection and use.  In
                # that case, make the documented standard-library fallback
                # explicit instead of returning an unexplained empty result.
                if result is None:
                    matches, metadata, status, message = self._search_with_python(
                        request, cancellation, resolved
                    )
                else:
                    matches, metadata, status, message = result
            else:
                matches, metadata, status, message = self._search_with_python(
                    request, cancellation, resolved
                )
            bounded, truncated = _bound_search_matches(
                matches,
                min(request.max_results, self._max_search_results),
                request.max_bytes if request.max_bytes is not None else self._max_search_bytes,
            )
            metadata = dict(metadata)
            metadata["original_length"] = len(matches)
            if status is EnvironmentStatus.CANCELLED:
                return self._search_result(
                    status=status,
                    message=message,
                    duration=self._duration(started_at),
                    matches=bounded,
                    truncated=truncated,
                    original_length=len(matches),
                    metadata=metadata,
                )
            if status is EnvironmentStatus.ERROR:
                return self._search_result(
                    status=status,
                    message=message,
                    duration=self._duration(started_at),
                    matches=bounded,
                    truncated=truncated,
                    original_length=len(matches),
                    metadata=metadata,
                )
            if not matches:
                return self._search_result(
                    status=EnvironmentStatus.ERROR,
                    message="no matches found",
                    duration=self._duration(started_at),
                    matches=(),
                    truncated=False,
                    original_length=0,
                    metadata={**metadata, "reason": "no_matches"},
                )
            return self._search_result(
                status=EnvironmentStatus.SUCCESS,
                message="matches found",
                duration=self._duration(started_at),
                matches=bounded,
                truncated=truncated,
                original_length=len(matches),
                metadata=metadata,
            )
        except _PathBoundaryError as exc:
            return self._search_result(
                status=EnvironmentStatus.ERROR,
                message=exc.reason,
                duration=self._duration(started_at),
                metadata={"reason": "path_boundary"},
            )
        except OSError as exc:
            return self._search_result(
                status=EnvironmentStatus.ERROR,
                message=_public_os_error(exc, "unable to search files"),
                duration=self._duration(started_at),
                metadata={"reason": "file_error"},
            )

    def write_file(self, request: WriteFileRequest, cancellation: CancellationSignal) -> FileResult:
        started_at = time.monotonic()
        state_error = self._state_error(started_at, FileResult)
        if state_error is not None:
            return state_error
        if self._cancelled(cancellation):
            return self._file_result(
                status=EnvironmentStatus.CANCELLED,
                message="operation cancelled before writing",
                duration=self._duration(started_at),
                path=request.path,
            )
        try:
            _, target = self._resolve_for_write(request.path)
            if target.exists() and target.is_dir():
                raise _PathBoundaryError("path is a directory")
            if self._cancelled(cancellation):
                return self._file_result(
                    status=EnvironmentStatus.CANCELLED,
                    message="operation cancelled before writing",
                    duration=self._duration(started_at),
                    path=request.path,
                )
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(request.content)
            limit = request.max_bytes if request.max_bytes is not None else self._max_file_bytes
            content, truncated, original_length, original_bytes = _truncate_text(
                request.content, limit
            )
            return self._file_result(
                status=EnvironmentStatus.SUCCESS,
                message="file written",
                duration=self._duration(started_at),
                path=request.path,
                content=content,
                truncated=truncated,
                original_length=original_length,
                metadata={
                    "original_length_bytes": original_bytes,
                    "bytes_written": original_bytes,
                },
            )
        except _PathBoundaryError as exc:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message=exc.reason,
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "path_boundary"},
            )
        except (OSError, UnicodeError) as exc:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message=_public_os_error(exc, "unable to write file"),
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "file_error"},
            )

    def edit_file(self, request: EditFileRequest, cancellation: CancellationSignal) -> FileResult:
        started_at = time.monotonic()
        state_error = self._state_error(started_at, FileResult)
        if state_error is not None:
            return state_error
        if self._cancelled(cancellation):
            return self._file_result(
                status=EnvironmentStatus.CANCELLED,
                message="operation cancelled before editing",
                duration=self._duration(started_at),
                path=request.path,
            )
        try:
            _, target = self._resolve_existing(request.path, kind="file")
            if not target.is_file():
                raise _PathBoundaryError("path is not a regular file")
            raw = self._read_bytes(target, cancellation)
            if raw is None:
                return self._file_result(
                    status=EnvironmentStatus.CANCELLED,
                    message="operation cancelled while editing",
                    duration=self._duration(started_at),
                    path=request.path,
                )
            original = raw.decode("utf-8", errors="strict")
            occurrences = original.count(request.old_text)
            if occurrences != request.expected_replacements:
                return self._file_result(
                    status=EnvironmentStatus.ERROR,
                    message=(
                        "edit target occurrence count mismatch: "
                        f"expected {request.expected_replacements}, found {occurrences}"
                    ),
                    duration=self._duration(started_at),
                    path=request.path,
                    metadata={
                        "reason": "edit_occurrence_mismatch",
                        "expected_replacements": request.expected_replacements,
                        "actual_replacements": occurrences,
                    },
                )
            if self._cancelled(cancellation):
                return self._file_result(
                    status=EnvironmentStatus.CANCELLED,
                    message="operation cancelled before editing",
                    duration=self._duration(started_at),
                    path=request.path,
                )
            updated = original.replace(request.old_text, request.new_text)
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
            content, truncated, original_length, original_bytes = _truncate_text(
                updated, self._max_file_bytes
            )
            return self._file_result(
                status=EnvironmentStatus.SUCCESS,
                message="file edited",
                duration=self._duration(started_at),
                path=request.path,
                content=content,
                truncated=truncated,
                original_length=original_length,
                metadata={"original_length_bytes": original_bytes, "replacements": occurrences},
            )
        except _PathBoundaryError as exc:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message=exc.reason,
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "path_boundary"},
            )
        except UnicodeError:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message="unable to edit non-UTF-8 text file",
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "file_encoding"},
            )
        except OSError as exc:
            return self._file_result(
                status=EnvironmentStatus.ERROR,
                message=_public_os_error(exc, "unable to edit file"),
                duration=self._duration(started_at),
                path=self._safe_result_path(request.path),
                metadata={"reason": "file_error"},
            )

    def run_command(
        self, request: RunCommandRequest, cancellation: CancellationSignal
    ) -> CommandResult:
        started_at = time.monotonic()
        state_error = self._state_error(started_at, CommandResult)
        if state_error is not None:
            return state_error
        if self._cancelled(cancellation):
            return self._command_result(
                status=EnvironmentStatus.CANCELLED,
                message="command cancelled before start",
                duration=self._duration(started_at),
                timed_out=False,
            )
        try:
            logical_cwd = request.working_directory or ""
            _, cwd = self._resolve_existing(logical_cwd, kind="working directory", allow_empty=True)
            if not cwd.is_dir():
                raise _PathBoundaryError("working directory is not a directory")
        except _PathBoundaryError as exc:
            return self._command_result(
                status=EnvironmentStatus.ERROR,
                message=exc.reason,
                duration=self._duration(started_at),
                metadata={"reason": "path_boundary"},
            )
        process: subprocess.Popen[bytes] | None = None
        try:
            # This is intentionally shell=True: the ``bash`` tool accepts a
            # shell command string.  No command blacklist is used; shell
            # access remains the host user's own privilege boundary.
            process = subprocess.Popen(
                request.command,
                shell=True,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name != "nt"),
            )
            with self._process_lock:
                if self._closed:
                    self._terminate_process(process)
                else:
                    self._active_processes.add(process)
            stdout, stderr, status, timed_out, message = self._communicate_process(
                process,
                cancellation,
                request.timeout_seconds
                if request.timeout_seconds is not None
                else self._command_timeout_seconds,
                started_at,
            )
            if status is EnvironmentStatus.SUCCESS and process.returncode not in (0, None):
                status = EnvironmentStatus.ERROR
                message = f"command exited with code {process.returncode}"
            limit = (
                request.max_output_bytes
                if request.max_output_bytes is not None
                else self._max_command_output_bytes
            )
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            stdout_text, stderr_text, truncated, original_length = _truncate_command_output(
                stdout_text, stderr_text, limit
            )
            return self._command_result(
                status=status,
                message=message,
                duration=self._duration(started_at),
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=process.returncode,
                timed_out=timed_out,
                truncated=truncated,
                original_output_length=original_length,
                metadata={
                    "working_directory": logical_cwd or ".",
                    "shell": "host",
                    "original_output_length_bytes": len(stdout) + len(stderr),
                },
            )
        except (OSError, UnicodeError) as exc:
            return self._command_result(
                status=EnvironmentStatus.ERROR,
                message=_public_os_error(exc, "unable to start command"),
                duration=self._duration(started_at),
                metadata={"reason": "process_error"},
            )
        finally:
            if process is not None:
                with self._process_lock:
                    self._active_processes.discard(process)

    def _detect_rg(self) -> None:
        if self._rg_path is not _UNSET:
            return
        if not self._use_rg:
            self._rg_path = None
            return
        try:
            if os.path.sep in self._rg_input or (os.altsep and os.altsep in self._rg_input):
                candidate = Path(self._rg_input)
                self._rg_path = (
                    str(candidate)
                    if candidate.is_file() and os.access(candidate, os.X_OK)
                    else None
                )
            else:
                self._rg_path = shutil.which(self._rg_input)
        except (OSError, ValueError):
            self._rg_path = None

    def _state_error(self, started_at: float, result_type: type[Any]) -> Any | None:
        if self._closed:
            return self._empty_result(
                result_type,
                EnvironmentStatus.ERROR,
                "environment is closed",
                self._duration(started_at),
                {"reason": "closed"},
            )
        if not self._started or self._root is None:
            return self._empty_result(
                result_type,
                EnvironmentStatus.ERROR,
                "environment is not started",
                self._duration(started_at),
                {"reason": "not_started"},
            )
        return None

    def _empty_result(
        self,
        result_type: type[Any],
        status: EnvironmentStatus,
        message: str,
        duration: float,
        metadata: dict[str, Any],
    ) -> Any:
        common = {
            "status": status,
            "message": message,
            "duration_seconds": duration,
            "metadata": metadata,
        }
        if result_type is FileResult:
            return FileResult(**common)
        if result_type is ListResult:
            return ListResult(**common)
        if result_type is SearchResult:
            return SearchResult(**common)
        return CommandResult(**common)

    def _resolve_existing(
        self, logical_path: str, *, kind: str, allow_empty: bool = False
    ) -> tuple[Path, Path]:
        relative = _logical_relative_path(logical_path, allow_empty=allow_empty)
        assert self._root is not None
        candidate = self._root.joinpath(relative)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise _PathBoundaryError(f"{kind} does not exist") from exc
        except (OSError, RuntimeError) as exc:
            raise _PathBoundaryError(f"{kind} cannot be resolved") from exc
        if not _is_within(resolved, self._root):
            raise _PathBoundaryError("path escapes workspace")
        return candidate, resolved

    def _resolve_for_write(self, logical_path: str) -> tuple[Path, Path]:
        relative = _logical_relative_path(logical_path)
        assert self._root is not None
        candidate = self._root.joinpath(relative)
        # Existing final components (including symlinks) are checked by their
        # real path.  For a new target, check the nearest existing parent so a
        # symlink in a parent chain cannot redirect the write outside.
        if candidate.exists() or candidate.is_symlink():
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                # A broken link still has a real target path that must be
                # checked before open() is allowed to follow it.
                resolved = candidate.resolve(strict=False)
                if not _is_within(resolved, self._root):
                    raise _PathBoundaryError("path escapes workspace") from exc
                return candidate, candidate
            except (OSError, RuntimeError) as exc:
                raise _PathBoundaryError("path cannot be resolved") from exc
            if not _is_within(resolved, self._root):
                raise _PathBoundaryError("path escapes workspace")
            return candidate, resolved

        parent = candidate.parent
        while not parent.exists() and not parent.is_symlink() and parent != parent.parent:
            parent = parent.parent
        try:
            # ``strict=False`` is important for a broken symlink parent: its
            # target still determines whether a future create would escape.
            resolved_parent = parent.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise _PathBoundaryError("parent path cannot be resolved") from exc
        if not _is_within(resolved_parent, self._root):
            raise _PathBoundaryError("path escapes workspace")
        return candidate, candidate

    def _read_bytes(self, path: Path, cancellation: CancellationSignal) -> bytes | None:
        chunks: list[bytes] = []
        with path.open("rb") as handle:
            while True:
                if self._cancelled(cancellation):
                    return None
                chunk = handle.read(_READ_CHUNK_SIZE)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)

    def _select_lines(self, text: str, start_line: int | None, end_line: int | None) -> str:
        if start_line is None and end_line is None:
            return text
        lines = text.splitlines(keepends=True)
        start = 0 if start_line is None else max(0, start_line - 1)
        end = None if end_line is None else end_line
        return "".join(lines[start:end])

    def _collect_entries(
        self,
        root: Path,
        logical_root: str,
        *,
        recursive: bool,
        cancellation: CancellationSignal,
    ) -> tuple[list[str], bool, int]:
        prefix = _normalise_search_path(logical_root)
        if prefix == ".":
            prefix = ""
        pending: list[tuple[Path, str]] = [(root, prefix)]
        entries: list[str] = []
        skipped = 0
        cancelled = False
        while pending:
            current, current_logical = pending.pop(0)
            try:
                with os.scandir(current) as scanner:
                    children = sorted(scanner, key=lambda entry: entry.name)
            except OSError:
                raise
            for entry in children:
                if self._cancelled(cancellation):
                    cancelled = True
                    return entries, cancelled, skipped
                child = Path(entry.path)
                logical = f"{current_logical}/{entry.name}" if current_logical else entry.name
                if entry.is_symlink():
                    try:
                        target = child.resolve(strict=False)
                    except (OSError, RuntimeError):
                        skipped += 1
                        continue
                    if not _is_within(target, self._root):
                        # Do not expose an outside-resolving logical entry:
                        # callers can safely resolve every returned path
                        # without accidentally observing a host path.
                        skipped += 1
                        continue
                    entries.append(logical)
                    continue
                entries.append(logical)
                if recursive and entry.is_dir(follow_symlinks=False):
                    pending.append((child, logical))
        return sorted(entries), cancelled, skipped

    def _search_with_rg(
        self,
        request: SearchRequest,
        cancellation: CancellationSignal,
        resolved: Path,
    ) -> tuple[list[SearchMatch], dict[str, Any], EnvironmentStatus, str] | None:
        assert isinstance(self._rg_path, str)
        pattern = request.query
        path_argument = request.path or "."
        command = [
            self._rg_path,
            "--json",
            "--hidden",
            "--glob",
            "!.git",
            "--no-messages",
        ]
        if not request.use_regex:
            command.append("--fixed-strings")
        command.extend(("--", pattern, path_argument))
        try:
            stdout, stderr, status, _, message, returncode = self._run_capture(
                command, cancellation, None
            )
        except (OSError, UnicodeError):
            return None
        if status is EnvironmentStatus.CANCELLED:
            return [], {"engine": "rg", "rg_available": True}, status, message
        if returncode not in (0, 1):
            detail = stderr.decode("utf-8", errors="replace").strip()
            return (
                [],
                {"engine": "rg", "rg_available": True, "return_code": returncode},
                EnvironmentStatus.ERROR,
                detail or "search command failed",
            )
        matches: list[SearchMatch] = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
                if event.get("type") != "match":
                    continue
                data = event.get("data") or {}
                path_text = (data.get("path") or {}).get("text")
                line_number = data.get("line_number")
                line_text = ((data.get("lines") or {}).get("text", "")).rstrip("\n")
                if not isinstance(path_text, str) or not isinstance(line_number, int):
                    continue
                if path_text.startswith("/") or PureWindowsPath(path_text).is_absolute():
                    continue
                logical = _normalise_search_path(path_text)
                # Defensive post-validation protects against a future rg
                # option accidentally following an outside symlink.
                _, actual = self._resolve_existing(logical, kind="search result")
                if not _is_within(actual, self._root):
                    continue
                matches.append(SearchMatch(logical, line_number, line_text))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return (
            matches,
            {"engine": "rg", "rg_available": True},
            EnvironmentStatus.SUCCESS,
            "matches found" if matches else "no matches found",
        )

    def _search_with_python(
        self,
        request: SearchRequest,
        cancellation: CancellationSignal,
        resolved: Path,
    ) -> tuple[list[SearchMatch], dict[str, Any], EnvironmentStatus, str]:
        metadata: dict[str, Any] = {
            "engine": "stdlib",
            "rg_available": self.rg_available is True,
            "fallback": "python",
        }
        try:
            matcher = re.compile(request.query) if request.use_regex else None
        except re.error as exc:
            return [], metadata, EnvironmentStatus.ERROR, f"invalid search pattern: {exc.msg}"
        files: list[tuple[Path, str]] = []
        if resolved.is_file():
            files.append((resolved, _relative_logical(resolved, self._root)))
        elif resolved.is_dir():
            logical_root = _normalise_search_path(request.path)
            if logical_root == ".":
                logical_root = ""
            pending: list[tuple[Path, str]] = [(resolved, logical_root)]
            while pending:
                current, logical_root = pending.pop(0)
                try:
                    with os.scandir(current) as scanner:
                        children = sorted(scanner, key=lambda entry: entry.name)
                except OSError as exc:
                    return (
                        [],
                        metadata,
                        EnvironmentStatus.ERROR,
                        _public_os_error(exc, "unable to search files"),
                    )
                for entry in children:
                    if self._cancelled(cancellation):
                        return (
                            [],
                            metadata,
                            EnvironmentStatus.CANCELLED,
                            ("operation cancelled while searching"),
                        )
                    child = Path(entry.path)
                    logical = f"{logical_root}/{entry.name}" if logical_root else entry.name
                    if entry.is_symlink():
                        try:
                            target = child.resolve(strict=False)
                        except (OSError, RuntimeError):
                            continue
                        if not _is_within(target, self._root):
                            continue
                        if target.is_file():
                            files.append((target, logical))
                        # Never recurse through directory symlinks.
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name == ".git":
                            continue
                        pending.append((child, logical))
                    elif entry.is_file(follow_symlinks=False):
                        files.append((child, logical))
        files.sort(key=lambda item: item[1])
        matches: list[SearchMatch] = []
        for path, logical in files:
            if self._cancelled(cancellation):
                return (
                    matches,
                    metadata,
                    EnvironmentStatus.CANCELLED,
                    ("operation cancelled while searching"),
                )
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if self._cancelled(cancellation):
                            return (
                                matches,
                                metadata,
                                EnvironmentStatus.CANCELLED,
                                ("operation cancelled while searching"),
                            )
                        if matcher.search(line) if matcher is not None else request.query in line:
                            matches.append(SearchMatch(logical, line_number, line.rstrip("\n")))
            except OSError as exc:
                metadata.setdefault("skipped_files", 0)
                metadata["skipped_files"] += 1
                metadata.setdefault("errors", []).append(_public_os_error(exc, "file read failed"))
        return (
            matches,
            metadata,
            EnvironmentStatus.SUCCESS,
            "matches found" if matches else "no matches found",
        )

    def _run_capture(
        self,
        command: list[str],
        cancellation: CancellationSignal,
        timeout: float | None,
    ) -> tuple[bytes, bytes, EnvironmentStatus, bool, str, int | None]:
        process = subprocess.Popen(
            command,
            cwd=str(self._root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )
        with self._process_lock:
            if self._closed:
                self._terminate_process(process)
            else:
                self._active_processes.add(process)
        started_at = time.monotonic()
        cancelled = False
        timed_out = False
        try:
            while True:
                if self._closed or self._cancelled(cancellation):
                    cancelled = True
                    self._terminate_process(process)
                    break
                remaining = None if timeout is None else timeout - (time.monotonic() - started_at)
                if remaining is not None and remaining <= 0:
                    timed_out = True
                    self._terminate_process(process)
                    break
                wait_for = _POLL_INTERVAL if remaining is None else min(_POLL_INTERVAL, remaining)
                try:
                    stdout, stderr = process.communicate(timeout=wait_for)
                    if self._closed or self._cancelled(cancellation):
                        return (
                            stdout,
                            stderr,
                            EnvironmentStatus.CANCELLED,
                            False,
                            "operation cancelled while searching",
                            process.returncode,
                        )
                    status = EnvironmentStatus.SUCCESS
                    return stdout, stderr, status, False, "search complete", process.returncode
                except subprocess.TimeoutExpired:
                    continue
            stdout, stderr = process.communicate()
            if cancelled:
                return (
                    stdout,
                    stderr,
                    EnvironmentStatus.CANCELLED,
                    False,
                    ("operation cancelled while searching"),
                    process.returncode,
                )
            return (
                stdout,
                stderr,
                EnvironmentStatus.ERROR,
                timed_out,
                ("search timed out"),
                process.returncode,
            )
        finally:
            with self._process_lock:
                self._active_processes.discard(process)

    def _communicate_process(
        self,
        process: subprocess.Popen[bytes],
        cancellation: CancellationSignal,
        timeout: float,
        started_at: float,
    ) -> tuple[bytes, bytes, EnvironmentStatus, bool, str]:
        cancelled = False
        timed_out = False
        while True:
            if self._closed or self._cancelled(cancellation):
                cancelled = True
                self._terminate_process(process)
                break
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                timed_out = True
                self._terminate_process(process)
                break
            try:
                stdout, stderr = process.communicate(timeout=min(_POLL_INTERVAL, remaining))
                if self._closed or self._cancelled(cancellation):
                    return (
                        stdout,
                        stderr,
                        EnvironmentStatus.CANCELLED,
                        False,
                        "command cancelled during execution",
                    )
                return stdout, stderr, EnvironmentStatus.SUCCESS, False, "command completed"
            except subprocess.TimeoutExpired:
                continue
        stdout, stderr = process.communicate()
        if cancelled:
            return (
                stdout,
                stderr,
                EnvironmentStatus.CANCELLED,
                False,
                ("command cancelled during execution"),
            )
        return stdout, stderr, EnvironmentStatus.TIMEOUT, timed_out, "command timed out"

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        # ``shell=True`` can return its leader while a background descendant
        # still owns stdout/stderr.  Do not use ``poll()`` as an early return:
        # on POSIX the process group (whose id is the new-session leader PID)
        # remains the reliable handle for those descendants.
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
            return
        if process.poll() is None:
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except (OSError, ProcessLookupError):
                    pass

    def _cancelled(self, cancellation: CancellationSignal) -> bool:
        if self._closed:
            return True
        try:
            return bool(cancellation.is_cancelled)
        except AttributeError:
            try:
                return bool(cancellation.is_set())
            except AttributeError:
                return False

    def _duration(self, started_at: float) -> float:
        return max(0.0, time.monotonic() - started_at)

    def _request_path(self, request: object) -> str | None:
        path = getattr(request, "path", None)
        return path if isinstance(path, str) and _is_safe_result_path(path) else None

    def _safe_result_path(self, path: str) -> str | None:
        return path if _is_safe_result_path(path) else None

    def _file_result(self, **kwargs: Any) -> FileResult:
        if "duration" in kwargs:
            kwargs["duration_seconds"] = kwargs.pop("duration")
        return FileResult(**kwargs)

    def _list_result(self, **kwargs: Any) -> ListResult:
        if "duration" in kwargs:
            kwargs["duration_seconds"] = kwargs.pop("duration")
        return ListResult(**kwargs)

    def _search_result(self, **kwargs: Any) -> SearchResult:
        if "duration" in kwargs:
            kwargs["duration_seconds"] = kwargs.pop("duration")
        return SearchResult(**kwargs)

    def _command_result(self, **kwargs: Any) -> CommandResult:
        if "duration" in kwargs:
            kwargs["duration_seconds"] = kwargs.pop("duration")
        return CommandResult(**kwargs)


def _logical_relative_path(value: str, *, allow_empty: bool = False) -> Path:
    if not isinstance(value, str):
        raise _PathBoundaryError("path must be a string")
    if "\x00" in value:
        raise _PathBoundaryError("path contains NUL")
    if not value:
        if allow_empty:
            return Path()
        raise _PathBoundaryError("path must not be empty")
    normalised = value.replace("\\", "/")
    windows_path = PureWindowsPath(value)
    if normalised.startswith("/") or windows_path.is_absolute() or windows_path.drive:
        raise _PathBoundaryError("path must be relative to workspace")
    parts = tuple(part for part in normalised.split("/") if part not in ("", "."))
    if ".." in parts:
        raise _PathBoundaryError("path traversal is not allowed")
    # A literal dot names the workspace root and is distinct from an empty
    # path (the latter is only accepted by operations whose contract has a
    # default workspace target).
    is_dot = normalised.strip("/") == "."
    if not parts and not is_dot:
        if not allow_empty:
            raise _PathBoundaryError("path must not be empty")
    return Path(*parts)


def _is_safe_result_path(path: str) -> bool:
    try:
        _logical_relative_path(path, allow_empty=True)
    except _PathBoundaryError:
        return False
    return True


def _is_within(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _normalise_search_path(path: str) -> str:
    value = path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.strip("/")
    return value or "."


def _relative_logical(path: Path, root: Path | None) -> str:
    if root is None:
        return path.as_posix()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _bound_sequence(values: Iterable[str], limit: int) -> tuple[tuple[str, ...], bool]:
    values_tuple = tuple(values)
    if len(values_tuple) <= limit:
        return values_tuple, False
    return values_tuple[:limit], True


def _bound_search_matches(
    values: Iterable[SearchMatch], result_limit: int, byte_limit: int | None
) -> tuple[tuple[SearchMatch, ...], bool]:
    all_values = tuple(values)
    selected = all_values[:result_limit]
    truncated = len(all_values) > result_limit
    if byte_limit is None:
        return selected, truncated
    bounded: list[SearchMatch] = []
    used = 0
    for match in selected:
        size = len(f"{match.path}:{match.line_number}:{match.text}\n".encode())
        if used + size > byte_limit:
            truncated = True
            break
        bounded.append(match)
        used += size
    return tuple(bounded), truncated


def _truncate_text(text: str, limit: int | None) -> tuple[str, bool, int, int]:
    original_length = len(text)
    original_bytes = len(text.encode("utf-8"))
    if limit is None or original_bytes <= limit:
        return text, False, original_length, original_bytes
    if limit <= 0:
        return "", True, original_length, original_bytes
    marker = "\n...[truncated]...\n"
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= limit:
        return (
            marker_bytes[:limit].decode("utf-8", errors="ignore"),
            True,
            original_length,
            original_bytes,
        )
    budget = limit - len(marker_bytes)
    head_budget = budget // 2
    tail_budget = budget - head_budget
    head = _utf8_prefix(text, head_budget)
    tail = _utf8_suffix(text, tail_budget)
    return head + marker + tail, True, original_length, original_bytes


def _truncate_command_output(
    stdout: str, stderr: str, limit: int | None
) -> tuple[str, str, bool, int]:
    original_length = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
    if limit is None or original_length <= limit:
        return stdout, stderr, False, original_length
    if limit <= 0:
        return "", "", True, original_length
    # Reserve space for stderr first so a useful command diagnostic survives
    # a verbose stdout stream.  Each stream still uses head/tail truncation.
    stderr_budget = min(len(stderr.encode("utf-8")), max(1, limit // 3))
    stdout_budget = max(0, limit - stderr_budget)
    bounded_stdout, stdout_truncated, _, _ = _truncate_text(stdout, stdout_budget)
    bounded_stderr, stderr_truncated, _, _ = _truncate_text(stderr, stderr_budget)
    if len((bounded_stdout + bounded_stderr).encode("utf-8")) > limit:
        bounded_stdout, stdout_truncated, _, _ = _truncate_text(
            stdout,
            max(0, limit - len(bounded_stderr.encode("utf-8"))),
        )
    return (
        bounded_stdout,
        bounded_stderr,
        stdout_truncated or stderr_truncated or True,
        original_length,
    )


def _utf8_prefix(text: str, byte_limit: int) -> str:
    if byte_limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _utf8_suffix(text: str, byte_limit: int) -> str:
    if byte_limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    return encoded[-byte_limit:].decode("utf-8", errors="ignore")


def _public_os_error(error: OSError, prefix: str) -> str:
    detail = getattr(error, "strerror", None)
    return f"{prefix}: {detail}" if detail else prefix


# Protocol conformance is checked at import time without constructing an
# environment (which would otherwise resolve a workspace or detect ``rg``).
assert isinstance(
    LocalExecutionEnvironment.__new__(LocalExecutionEnvironment), ExecutionEnvironment
)


LocalEnvironment = LocalExecutionEnvironment


__all__ = ["LocalEnvironment", "LocalExecutionEnvironment"]
