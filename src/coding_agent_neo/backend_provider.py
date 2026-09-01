"""Workspace-scoped history repository and backend provider.

The public provider port and its immutable DTOs live in :mod:`backend`.  This
module contains the composition-owned implementation that is allowed to know
about the fixed JSONL repository.  Transport adapters receive only the port;
they never construct this repository or pass it a path supplied by a caller.
"""

from __future__ import annotations

import json
import re
import secrets
import stat
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from coding_agent_neo.backend import (
    AgentBackend,
    AgentBackendProvider,
    AgentBackendProviderError,
    BoundedText,
    HistoryDiagnostic,
    InvalidSessionHistoryCursorError,
    InvalidSessionHistoryIdError,
    InvalidSessionHistoryLimitError,
    SessionEventPage,
    SessionHistoryItem,
    SessionHistoryNotFoundError,
    SessionHistoryPage,
    SessionHistoryUnavailableError,
    SessionResumeUnavailableError,
)
from coding_agent_neo.models import EventEnvelope, EventType, RuntimeState
from coding_agent_neo.session import (
    SessionError,
    SessionFormatError,
    SessionReadResult,
    read_session,
    resolve_session_path,
)

HISTORY_TEXT_LIMIT = 4_096
HISTORY_LIST_LIMIT = 100
HISTORY_EVENT_LIMIT = 200
HISTORY_SEQUENCE_LIMIT = 2**63 - 1
HISTORY_EVENT_PAYLOAD_LIMIT = 65_536
HISTORY_PAGE_BYTES_LIMIT = 8 * 1024 * 1024
HISTORY_MAX_DIAGNOSTICS = 8
_CURSOR_CACHE_LIMIT = 128
_SESSION_ID_PATTERN = re.compile(r"^session_[A-Za-z0-9_-]{1,120}$")


_DIAGNOSTIC_MESSAGES = {
    "incomplete_tail": "history has an incomplete final record",
    "invalid_record": "history contains an invalid record",
    "unsupported_schema": "history uses an unsupported schema",
    "missing_root_agent": "history is missing a root agent",
    "missing_first_user_message": "history has no canonical root user message",
    "unreadable_candidate": "history candidate could not be read",
    "not_resumable": "history session cannot be resumed",
}


def _diagnostic(code: str) -> HistoryDiagnostic:
    """Return one fixed, safe diagnostic without retaining source details."""

    message = _DIAGNOSTIC_MESSAGES.get(code, "history candidate has an invalid shape")
    return HistoryDiagnostic(code, message)


def _append_diagnostic(
    diagnostics: list[HistoryDiagnostic], code: str, *, limit: int = HISTORY_MAX_DIAGNOSTICS
) -> None:
    if len(diagnostics) >= limit or any(item.code == code for item in diagnostics):
        return
    diagnostics.append(_diagnostic(code))


def _safe_diagnostics(items: Sequence[HistoryDiagnostic]) -> tuple[HistoryDiagnostic, ...]:
    """Bound and de-duplicate diagnostics before they cross the provider port."""

    result: list[HistoryDiagnostic] = []
    for item in items:
        if not isinstance(item, HistoryDiagnostic):
            continue
        if any(existing.code == item.code for existing in result):
            continue
        result.append(item)
        if len(result) >= HISTORY_MAX_DIAGNOSTICS:
            break
    return tuple(result)


def _bounded_text(value: str) -> BoundedText:
    encoded = value.encode("utf-8")
    original_length = len(encoded)
    if original_length <= HISTORY_TEXT_LIMIT:
        return BoundedText(
            text=value,
            truncated=False,
            original_length=original_length,
            limit=HISTORY_TEXT_LIMIT,
        )
    # Decoding with ``ignore`` can only remove a partial code point at the
    # edge; it never cuts a Unicode character in the public projection.
    text = encoded[:HISTORY_TEXT_LIMIT].decode("utf-8", errors="ignore")
    return BoundedText(
        text=text,
        truncated=True,
        original_length=original_length,
        limit=HISTORY_TEXT_LIMIT,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _bounded_payload(
    payload: Mapping[str, Any],
    limit: int,
    *,
    serialized: str | None = None,
    original_length: int | None = None,
) -> Mapping[str, Any]:
    """Project a payload to a UTF-8 byte bound while retaining a safe preview."""

    serialized = _canonical_json(payload) if serialized is None else serialized
    original_length = (
        len(serialized.encode("utf-8")) if original_length is None else original_length
    )

    def encoded_size(value: Mapping[str, Any]) -> int:
        # Standard JSON with ASCII escaping is a conservative upper bound for
        # the compact UTF-8 representation used by the canonical store.
        return len(json.dumps(value).encode("utf-8"))

    if original_length <= limit and encoded_size(payload) <= limit:
        return payload

    def preview(character_count: int) -> dict[str, Any]:
        head_count = (character_count + 1) // 2
        tail_count = character_count // 2
        return {
            "truncated": True,
            "original_length": original_length,
            "limit": limit,
            "encoding": "utf-8",
            "head": serialized[:head_count],
            "tail": serialized[-tail_count:] if tail_count else "",
        }

    low = 0
    high = len(serialized)
    best = preview(0)
    while low <= high:
        middle = (low + high) // 2
        candidate = preview(middle)
        size = encoded_size(candidate)
        if size <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    # The lower bound used for page projection leaves enough room for the
    # fixed metadata.  Keep the guard so a future contract change cannot
    # accidentally return an over-limit object.
    if encoded_size(best) > limit:
        raise ValueError("payload preview metadata exceeds its bound")
    return best


def _project_event(
    event: EventEnvelope,
    payload_limit: int,
    *,
    serialized: str | None = None,
    original_length: int | None = None,
) -> EventEnvelope:
    payload = event.payload
    projected = _bounded_payload(
        payload,
        payload_limit,
        serialized=serialized,
        original_length=original_length,
    )
    if projected is payload:
        return event
    return EventEnvelope(
        schema_version=event.schema_version,
        session_id=event.session_id,
        event_id=event.event_id,
        agent_id=event.agent_id,
        parent_agent_id=event.parent_agent_id,
        sequence=event.sequence,
        type=event.type,
        correlation_id=event.correlation_id,
        provider_tool_call_id=event.provider_tool_call_id,
        timestamp=event.timestamp,
        payload=projected,
    )


def _serialized_page_size(page: SessionEventPage) -> int:
    value = page.to_dict()
    # Adapters may choose compact or standard JSON separators (and a legacy
    # caller may use ASCII escaping).  Reserve against all ordinary encoders
    # so the public 8 MiB bound does not depend on transport formatting.
    return max(
        len(_canonical_json(value).encode("utf-8")),
        len(json.dumps(value, ensure_ascii=False).encode("utf-8")),
        len(json.dumps(value).encode("utf-8")),
    )


def _event_page(
    session_id: str,
    events: Sequence[EventEnvelope],
    *,
    since: int,
    limit: int,
    diagnostics: Sequence[HistoryDiagnostic],
) -> SessionEventPage:
    eligible = tuple(event for event in events if event.sequence > since)
    selected = eligible[:limit]
    has_more = len(eligible) > len(selected)

    # A page with the normal per-payload bound is usually well below 8 MiB.
    # If many large events share one page, lower the preview bound uniformly;
    # envelope identity and sequence are always retained in full.  Cache each
    # canonical payload string so the page-size fallback does not repeatedly
    # serialize large event values.
    serialized_payloads = tuple(_canonical_json(event.payload) for event in selected)
    payload_lengths = tuple(len(value.encode("utf-8")) for value in serialized_payloads)

    empty_payload_events = tuple(
        EventEnvelope(
            schema_version=event.schema_version,
            session_id=event.session_id,
            event_id=event.event_id,
            agent_id=event.agent_id,
            parent_agent_id=event.parent_agent_id,
            sequence=event.sequence,
            type=event.type,
            correlation_id=event.correlation_id,
            provider_tool_call_id=event.provider_tool_call_id,
            timestamp=event.timestamp,
            payload={},
        )
        for event in selected
    )
    empty_page = SessionEventPage(
        session_id=session_id,
        events=empty_payload_events,
        next_cursor=empty_payload_events[-1].sequence if has_more and selected else None,
        has_more=has_more,
        diagnostics=_safe_diagnostics(diagnostics),
    )
    empty_page_size = _serialized_page_size(empty_page)
    bounded_payload_sum = sum(
        min(length, HISTORY_EVENT_PAYLOAD_LIMIT) for length in payload_lengths
    )
    if empty_page_size + bounded_payload_sum <= HISTORY_PAGE_BYTES_LIMIT:
        payload_limit = HISTORY_EVENT_PAYLOAD_LIMIT
    elif selected:
        # Keep a small amount of headroom for the payload key and the empty
        # object bytes already counted in the envelope baseline.
        payload_limit = max(
            128,
            (HISTORY_PAGE_BYTES_LIMIT - empty_page_size) // len(selected) - 1,
        )
        payload_limit = min(payload_limit, HISTORY_EVENT_PAYLOAD_LIMIT)
    else:
        payload_limit = HISTORY_EVENT_PAYLOAD_LIMIT

    def build(current_limit: int) -> SessionEventPage:
        projected = tuple(
            _project_event(
                event,
                current_limit,
                serialized=serialized_payloads[index],
                original_length=payload_lengths[index],
            )
            for index, event in enumerate(selected)
        )
        next_cursor = projected[-1].sequence if has_more and projected else None
        return SessionEventPage(
            session_id=session_id,
            events=projected,
            next_cursor=next_cursor,
            has_more=has_more,
            diagnostics=_safe_diagnostics(diagnostics),
        )

    page = build(payload_limit)
    while _serialized_page_size(page) > HISTORY_PAGE_BYTES_LIMIT and payload_limit > 128:
        # The baseline calculation is conservative, but account for JSON
        # escaping in preview head/tail strings if it ever falls short.
        current_size = _serialized_page_size(page)
        payload_limit = max(128, payload_limit * HISTORY_PAGE_BYTES_LIMIT // current_size - 1)
        page = build(payload_limit)
    if _serialized_page_size(page) > HISTORY_PAGE_BYTES_LIMIT:
        # 200 canonical envelopes with bounded IDs fit comfortably under 8
        # MiB at the metadata floor.  This is defensive for a future envelope
        # expansion and does not leak its details.
        raise SessionHistoryUnavailableError
    return page


def _validate_canonical_result(result: SessionReadResult) -> None:
    """Apply the Store sequence invariant at the history boundary."""

    if not result.events:
        return
    if result.events[0].sequence != 1:
        raise SessionFormatError(
            "session record violates identity or sequence invariants",
            line_number=1,
            byte_offset=0,
        )


def _state_from_events(events: Sequence[EventEnvelope]) -> str | None:
    state: RuntimeState | None = None
    terminal = False
    for event in events:
        name = str(event.type)
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if name == EventType.APPROVAL_REQUEST.value:
            if not terminal:
                state = RuntimeState.WAITING_FOR_APPROVAL
            continue
        if name == EventType.POLICY_DECISION.value:
            if state is RuntimeState.WAITING_FOR_APPROVAL:
                state = RuntimeState.RUNNING
            continue
        if name in {
            EventType.TURN_END.value,
            EventType.AGENT_END.value,
            EventType.SESSION_END.value,
        }:
            value = payload.get("state")
            try:
                parsed = value if isinstance(value, RuntimeState) else RuntimeState(value)
            except (TypeError, ValueError):
                # A terminal envelope with an unknown state cannot be safely
                # projected as an earlier state.
                state = None
                terminal = False
                continue
            state = parsed
            terminal = parsed in {
                RuntimeState.LIMIT_REACHED,
                RuntimeState.INTERRUPTED,
                RuntimeState.FAILED,
            }
            continue
        if (
            name
            in {
                EventType.SESSION_START.value,
                EventType.AGENT_START.value,
                EventType.USER_MESSAGE.value,
            }
            and not terminal
        ):
            state = RuntimeState.RUNNING
    return None if state is None else state.value


def _root_agent_id(events: Sequence[EventEnvelope]) -> str | None:
    for event in events:
        if str(event.type) == EventType.AGENT_START.value and event.parent_agent_id is None:
            return str(event.agent_id)
    return None


def _first_user_message(
    events: Sequence[EventEnvelope], root_agent_id: str | None
) -> tuple[BoundedText | None, str | None]:
    if root_agent_id is None:
        return None, "missing_root_agent"
    for event in events:
        if str(event.type) != EventType.USER_MESSAGE.value or str(event.agent_id) != root_agent_id:
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        text = payload.get("text")
        if isinstance(text, str):
            try:
                return _bounded_text(text), None
            except UnicodeError:
                return None, "missing_first_user_message"
        return None, "missing_first_user_message"
    return None, "missing_first_user_message"


@dataclass(frozen=True, slots=True)
class _Candidate:
    session_id: str
    path: Path
    unreadable: bool = False


class _FileSessionHistoryRepository:
    """Read-only fixed-directory repository used by the workspace provider."""

    def __init__(self, workspace: str | Path) -> None:
        try:
            self._workspace = Path(workspace).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            raise SessionHistoryUnavailableError from None
        self._sessions = self._workspace / ".coding-agent-neo" / "sessions"

    def path_for(self, session_id: str) -> Path:
        """Resolve a validated opaque ID to the fixed production location."""

        if not isinstance(session_id, str) or _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            raise InvalidSessionHistoryIdError
        # Keep direct lookup consistent with listing: an existing but damaged
        # fixed root is unavailable, while a missing normal root is simply an
        # empty repository and may still yield a not-found result below.
        self._root_available()
        try:
            return resolve_session_path(session_id, self._workspace)
        except (OSError, RuntimeError, TypeError, ValueError):
            # The ID was valid; a damaged/symlinked fixed component is a
            # repository availability failure, not an ID validation failure.
            raise SessionHistoryUnavailableError from None

    @staticmethod
    def _regular_file(path: Path) -> bool:
        try:
            info = path.stat(follow_symlinks=False)
        except Exception:
            return False
        return stat.S_ISREG(info.st_mode)

    def _root_available(self) -> bool:
        # A symlinked or otherwise damaged fixed component is unavailable.
        # A missing normal component remains an empty repository; callers may
        # distinguish that from a damaged root without following any link.
        root = self._workspace / ".coding-agent-neo"
        sessions = self._sessions
        try:
            root_info = root.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise SessionHistoryUnavailableError from None
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise SessionHistoryUnavailableError

        try:
            sessions_info = sessions.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise SessionHistoryUnavailableError from None
        if stat.S_ISLNK(sessions_info.st_mode) or not stat.S_ISDIR(sessions_info.st_mode):
            raise SessionHistoryUnavailableError
        return True

    def validate_creation_root(self) -> None:
        """Validate existing fixed components without creating directories."""

        root = self._workspace / ".coding-agent-neo"
        sessions = self._sessions
        try:
            root_info = root.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise SessionHistoryUnavailableError from None
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise SessionHistoryUnavailableError

        try:
            sessions_info = sessions.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise SessionHistoryUnavailableError from None
        if stat.S_ISLNK(sessions_info.st_mode) or not stat.S_ISDIR(sessions_info.st_mode):
            raise SessionHistoryUnavailableError

    def candidates(self) -> tuple[_Candidate, ...]:
        if not self._root_available():
            return ()
        try:
            entries = tuple(self._sessions.iterdir())
        except OSError:
            raise SessionHistoryUnavailableError from None

        candidates: list[_Candidate] = []
        for entry in entries:
            name = entry.name
            if not name.startswith("session_") or not name.endswith(".jsonl"):
                continue
            session_id = name[: -len(".jsonl")]
            if entry.is_symlink():
                continue
            try:
                path = self.path_for(session_id)
            except InvalidSessionHistoryIdError:
                # Unsafe names are not candidate IDs and are intentionally
                # omitted rather than echoed as diagnostics.
                continue
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except Exception:
                # The filename itself is safe to project, so an unreadable
                # regular candidate can still receive a bounded diagnostic.
                candidates.append(_Candidate(session_id=session_id, path=path, unreadable=True))
                continue
            # Directories, sockets, devices, and other non-regular entries
            # are not history candidates and are silently ignored.
            if not stat.S_ISREG(entry_stat.st_mode):
                continue
            candidates.append(
                _Candidate(
                    session_id=session_id,
                    path=path,
                    unreadable=False,
                )
            )
        return tuple(candidates)

    def read(self, candidate: _Candidate) -> SessionReadResult:
        if candidate.unreadable:
            raise SessionError("history candidate could not be read")
        if not self._regular_file(candidate.path) or candidate.path.is_symlink():
            raise SessionError("history candidate could not be read")
        try:
            result = read_session(candidate.path, expected_session_id=candidate.session_id)
            _validate_canonical_result(result)
        except (SessionFormatError, SessionError):
            raise
        except Exception:
            raise SessionError("history candidate could not be read") from None
        # Refuse a replacement/symlink race after parsing as well.  The
        # returned result is never associated with a path outside the fixed
        # directory.
        if candidate.path.is_symlink() or not self._regular_file(candidate.path):
            raise SessionError("history candidate could not be read")
        return result

    def read_by_id(self, session_id: str) -> tuple[Path, SessionReadResult]:
        path = self.path_for(session_id)
        try:
            present = path.exists() and not path.is_symlink() and self._regular_file(path)
        except Exception:
            raise SessionHistoryUnavailableError from None
        if not present:
            raise SessionHistoryNotFoundError
        candidate = _Candidate(session_id=session_id, path=path)
        try:
            result = self.read(candidate)
        except SessionError:
            raise SessionHistoryUnavailableError from None
        return path, result


class _ListCursorCache:
    """Short-lived opaque list cursors scoped to one provider instance."""

    def __init__(self) -> None:
        self._values: OrderedDict[str, tuple[tuple[str, ...], int]] = OrderedDict()
        self._lock = Lock()

    def issue(self, snapshot: Sequence[str], position: int) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._values[token] = (tuple(snapshot), position)
            self._values.move_to_end(token)
            while len(self._values) > _CURSOR_CACHE_LIMIT:
                self._values.popitem(last=False)
        return token

    def resolve(self, token: str) -> tuple[tuple[str, ...], int]:
        with self._lock:
            try:
                value = self._values[token]
            except (KeyError, TypeError):
                raise InvalidSessionHistoryCursorError from None
            self._values.move_to_end(token)
        return value


class LocalAgentBackendProvider:
    """Concrete workspace-scoped provider for history and backend creation.

    ``backend_factory`` and ``resume_validator`` are composition seams.  The
    default assembly supplies closures over the resolved application config;
    ordinary callers only receive this object through the provider port.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        backend_factory: Callable[[str | None], AgentBackend],
        resume_validator: Callable[[Path, str], Any] | None = None,
    ) -> None:
        if not callable(backend_factory):
            raise TypeError("backend_factory must be callable")
        # ``assembly`` passes the resolved workspace path.  Accepting a
        # config-like object as an internal convenience keeps direct embedded
        # composition from accidentally introducing a second workspace
        # argument while still deriving the repository from ``.workspace``.
        selected_workspace = getattr(workspace, "workspace", workspace)
        self._repository = _FileSessionHistoryRepository(selected_workspace)
        self._backend_factory = backend_factory
        self._resume_validator = resume_validator
        self._cursors = _ListCursorCache()

    def _validate_resume(self, path: Path, session_id: str) -> Any:
        if self._resume_validator is not None:
            return self._resume_validator(path, session_id)
        # Keep the concrete provider safe when it is constructed directly in
        # an embedded composition: full recovery validation, including
        # context grouping and budget reconstruction, is still required.  The
        # import is intentionally lazy because assembly composes this module.
        from coding_agent_neo.assembly import recover_session_plan

        plan = recover_session_plan(path, expected_session_id=session_id)
        from coding_agent_neo.tools.registry import default_tool_registry

        if plan.active_tools - set(default_tool_registry().registered_names):
            raise ValueError("session active tools are not registered")
        return plan

    def _summary(
        self,
        candidate: _Candidate,
        result: SessionReadResult | None,
        diagnostics: list[HistoryDiagnostic],
    ) -> SessionHistoryItem:
        events = () if result is None else result.events
        if result is not None:
            for item in result.diagnostics:
                if item.code == "incomplete_tail":
                    _append_diagnostic(diagnostics, "incomplete_tail")

        root_agent = _root_agent_id(events)
        if result is None:
            first_message = None
        else:
            first_message, first_diagnostic = _first_user_message(events, root_agent)
            if first_diagnostic is not None:
                _append_diagnostic(diagnostics, first_diagnostic)

        resumable = False
        if result is not None and events:
            try:
                self._validate_resume(candidate.path, candidate.session_id)
                resumable = True
            except Exception:
                _append_diagnostic(diagnostics, "not_resumable")

        return SessionHistoryItem(
            session_id=candidate.session_id,
            first_user_message=first_message,
            created_at=None if not events else events[0].timestamp,
            updated_at=None if not events else events[-1].timestamp,
            last_sequence=0 if not events else events[-1].sequence,
            last_state=_state_from_events(events),
            resumable=resumable,
            diagnostics=_safe_diagnostics(diagnostics),
        )

    def _discover_summaries(self) -> tuple[SessionHistoryItem, ...]:
        items: list[SessionHistoryItem] = []
        for candidate in self._repository.candidates():
            diagnostics: list[HistoryDiagnostic] = []
            if candidate.unreadable:
                _append_diagnostic(diagnostics, "unreadable_candidate")
                items.append(self._summary(candidate, None, diagnostics))
                continue
            try:
                result = self._repository.read(candidate)
            except SessionFormatError as error:
                message = str(error)
                code = "unsupported_schema" if "schema" in message else "invalid_record"
                _append_diagnostic(diagnostics, code)
                items.append(self._summary(candidate, None, diagnostics))
                continue
            except SessionError:
                _append_diagnostic(diagnostics, "unreadable_candidate")
                items.append(self._summary(candidate, None, diagnostics))
                continue
            except Exception:
                _append_diagnostic(diagnostics, "unreadable_candidate")
                items.append(self._summary(candidate, None, diagnostics))
                continue
            items.append(self._summary(candidate, result, diagnostics))

        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.updated_at is not None,
                    item.updated_at or "",
                    item.session_id,
                ),
                reverse=True,
            )
        )

    @staticmethod
    def _validate_limit(value: int, *, maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise InvalidSessionHistoryLimitError

    @staticmethod
    def _validate_list_cursor(cursor: str | None) -> None:
        if cursor is None:
            return
        if not isinstance(cursor, str) or not cursor or len(cursor) > 256 or not cursor.isascii():
            raise InvalidSessionHistoryCursorError

    @staticmethod
    def _validate_since(since: int) -> None:
        if (
            isinstance(since, bool)
            or not isinstance(since, int)
            or since < 0
            or since > HISTORY_SEQUENCE_LIMIT
        ):
            raise InvalidSessionHistoryCursorError

    def list_sessions(self, *, cursor: str | None = None, limit: int = 50) -> SessionHistoryPage:
        self._validate_limit(limit, maximum=HISTORY_LIST_LIMIT)
        self._validate_list_cursor(cursor)
        discovered = self._discover_summaries()
        if cursor is None:
            ordered = discovered
            start = 0
        else:
            snapshot_ids, start = self._cursors.resolve(cursor)
            by_id = {item.session_id: item for item in discovered}
            ordered = tuple(by_id[item_id] for item_id in snapshot_ids if item_id in by_id)

        page_items = ordered[start : start + limit]
        end = start + len(page_items)
        next_cursor = None
        if end < len(ordered):
            next_cursor = self._cursors.issue(
                tuple(item.session_id for item in ordered),
                end,
            )
        return SessionHistoryPage(sessions=tuple(page_items), next_cursor=next_cursor)

    def read_session_events(
        self, session_id: str, *, since: int = 0, limit: int = 200
    ) -> SessionEventPage:
        # Validate the opaque identifier before validating or constructing
        # anything from a filesystem path.  The path returned here is only an
        # internal validation result; the public page never exposes it.
        self._repository.path_for(session_id)
        self._validate_since(since)
        self._validate_limit(limit, maximum=HISTORY_EVENT_LIMIT)
        try:
            _path, result = self._repository.read_by_id(session_id)
        except (InvalidSessionHistoryIdError, SessionHistoryNotFoundError):
            raise
        except SessionHistoryUnavailableError:
            raise
        except Exception:
            raise SessionHistoryUnavailableError from None
        if not result.events:
            raise SessionHistoryUnavailableError
        try:
            return _event_page(
                str(session_id),
                result.events,
                since=since,
                limit=limit,
                diagnostics=tuple(
                    _diagnostic(item.code)
                    for item in result.diagnostics
                    if item.code == "incomplete_tail"
                ),
            )
        except AgentBackendProviderError:
            raise
        except Exception:
            raise SessionHistoryUnavailableError from None

    def create_session(self, *, resume_session_id: str | None = None) -> AgentBackend:
        if resume_session_id is None:
            self._repository.validate_creation_root()
            # A new-session factory failure is a startup/runtime failure, not
            # an invalid resume.  Preserve its normal classification for the
            # adapter's internal-error mapping.
            return self._backend_factory(None)

        # ID validation happens before constructing or opening any path.
        try:
            path, result = self._repository.read_by_id(resume_session_id)
        except (InvalidSessionHistoryIdError, SessionHistoryNotFoundError):
            raise
        except SessionHistoryUnavailableError:
            raise SessionResumeUnavailableError from None
        except Exception:
            raise SessionResumeUnavailableError from None
        if not result.events:
            raise SessionResumeUnavailableError
        try:
            self._validate_resume(path, resume_session_id)
            backend = self._backend_factory(resume_session_id)
        except (AgentBackendProviderError,):
            raise
        except Exception:
            raise SessionResumeUnavailableError from None
        return backend


__all__ = [
    "AgentBackendProvider",
    "HISTORY_EVENT_LIMIT",
    "HISTORY_EVENT_PAYLOAD_LIMIT",
    "HISTORY_LIST_LIMIT",
    "HISTORY_PAGE_BYTES_LIMIT",
    "HISTORY_SEQUENCE_LIMIT",
    "HISTORY_TEXT_LIMIT",
    "LocalAgentBackendProvider",
]
