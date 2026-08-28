"""Contract tests for backend-neutral ExecutionEnvironment."""

from inspect import signature
from typing import get_type_hints

from tests.unit.fake_environment import FakeExecutionEnvironment

from coding_agent_neo.environment.base import ExecutionEnvironment
from coding_agent_neo.models import (
    CommandResult,
    EditFileRequest,
    EnvironmentRequest,
    EnvironmentResponse,
    EnvironmentStatus,
    FileResult,
    ListFilesRequest,
    ListResult,
    ReadFileRequest,
    RunCommandRequest,
    SearchRequest,
    SearchResult,
    WriteFileRequest,
)
from coding_agent_neo.runtime import CancellationSignal


def test_protocol_declares_lifecycle_and_six_backend_neutral_operations() -> None:
    expected_methods = {
        "start",
        "close",
        "read_file",
        "list_files",
        "search",
        "write_file",
        "edit_file",
        "run_command",
    }
    assert expected_methods <= set(ExecutionEnvironment.__dict__)

    fake = FakeExecutionEnvironment()
    assert isinstance(fake, ExecutionEnvironment)
    fake.start()
    cancellation = CancellationSignal()
    fake.read_file(ReadFileRequest("src/a.py"), cancellation)
    fake.list_files(ListFilesRequest(), cancellation)
    fake.search(SearchRequest("needle"), cancellation)
    fake.write_file(WriteFileRequest("src/a.py", "pass"), cancellation)
    fake.edit_file(EditFileRequest("src/a.py", "pass", "return None"), cancellation)
    fake.run_command(RunCommandRequest("pytest"), cancellation)
    fake.close()

    assert fake.started and fake.closed
    assert [call.operation for call in fake.calls] == [
        "read_file",
        "list_files",
        "search",
        "write_file",
        "edit_file",
        "run_command",
    ]
    assert all(call.cancellation is cancellation for call in fake.calls)
    assert isinstance(ReadFileRequest("src/a.py"), EnvironmentRequest)
    assert isinstance(fake.calls[0].request, EnvironmentRequest)
    assert isinstance(fake.responses.get("missing", FileResult()), EnvironmentResponse)


def test_protocol_signatures_use_request_result_and_cancellation_contracts() -> None:
    expected = {
        "read_file": (ReadFileRequest, FileResult),
        "list_files": (ListFilesRequest, ListResult),
        "search": (SearchRequest, SearchResult),
        "write_file": (WriteFileRequest, FileResult),
        "edit_file": (EditFileRequest, FileResult),
        "run_command": (RunCommandRequest, CommandResult),
    }
    for name, (request_type, result_type) in expected.items():
        hints = get_type_hints(getattr(ExecutionEnvironment, name))
        assert hints["request"] is request_type
        assert hints["cancellation"] is CancellationSignal
        assert hints["return"] is result_type
        parameters = list(signature(getattr(ExecutionEnvironment, name)).parameters.values())
        assert [parameter.name for parameter in parameters] == ["self", "request", "cancellation"]


def test_results_expose_only_backend_neutral_fields() -> None:
    request_types = (
        ReadFileRequest,
        ListFilesRequest,
        SearchRequest,
        WriteFileRequest,
        EditFileRequest,
        RunCommandRequest,
    )
    result_types = (FileResult, ListResult, SearchResult, CommandResult, SearchResult)
    forbidden_names = {
        "container_id",
        "docker_id",
        "host_path",
        "local_path",
        "subprocess",
        "exec_handle",
    }
    for model_type in request_types + result_types:
        assert not forbidden_names.intersection(model_type.__dataclass_fields__)

    cancelled = CommandResult(status=EnvironmentStatus.CANCELLED, message="cancelled")
    assert not cancelled.ok
