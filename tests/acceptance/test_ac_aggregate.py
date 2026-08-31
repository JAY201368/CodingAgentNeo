"""Invoke existing AC-02..AC-14 evidence tests from the acceptance suite."""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.ac_catalog import CATALOG

REPO_ROOT = Path(__file__).resolve().parents[2]

# module:function pairs. Parametrized tests stay in the catalog collect-only check.
EVIDENCE: tuple[tuple[str, str, str], ...] = (
    (
        "AC-02",
        "tests.integration.test_agent_limits",
        "test_consecutive_protocol_errors_have_a_hard_limit",
    ),
    (
        "AC-02",
        "tests.integration.test_agent_limits",
        "test_valid_protocol_call_resets_the_consecutive_error_counter",
    ),
    (
        "AC-02",
        "tests.unit.tools.test_registry",
        "test_unknown_inactive_and_invalid_arguments_are_structured_and_side_effect_free",
    ),
    (
        "AC-03",
        "tests.integration.test_agent_loop",
        "test_scripted_loop_uses_builtins_and_injected_tool_in_declared_order",
    ),
    (
        "AC-03",
        "tests.integration.test_agent_loop",
        "test_command_timeout_is_a_normal_result_the_model_can_handle",
    ),
    (
        "AC-03",
        "tests.unit.environment.test_local_environment",
        "test_command_reports_nonzero_and_timeout_without_losing_observability",
    ),
    (
        "AC-04",
        "tests.security.test_workspace_boundary",
        "test_absolute_and_parent_paths_are_rejected_before_file_side_effects",
    ),
    (
        "AC-04",
        "tests.security.test_workspace_boundary",
        "test_existing_and_pending_symlink_escape_is_rejected",
    ),
    (
        "AC-04",
        "tests.security.test_workspace_boundary",
        "test_recursive_list_and_search_never_follow_outside_symlink",
    ),
    ("AC-05", "tests.integration.test_cli", "test_interactive_bash_confirmation"),
    (
        "AC-05",
        "tests.integration.test_cli",
        "test_noninteractive_ask_denies_without_reading_and_auto_runs",
    ),
    (
        "AC-05",
        "tests.integration.test_frontend_contract",
        "test_interactive_approval_roundtrip_persists_request_before_execution",
    ),
    (
        "AC-06",
        "tests.unit.test_session_store",
        "test_jsonl_is_one_schema_v1_object_per_flushed_append",
    ),
    ("AC-06", "tests.integration.test_cli", "test_keyboard_interrupt_is_documented_and_persisted"),
    (
        "AC-06",
        "tests.integration.test_cli",
        "test_noninteractive_success_stdout_contract_and_parseable_session",
    ),
    (
        "AC-07",
        "tests.unit.test_session_recovery",
        "test_full_session_restores_ids_budget_tools_context_and_followup",
    ),
    (
        "AC-07",
        "tests.integration.test_resume_cli",
        "test_cli_resume_does_not_replay_historical_side_effects",
    ),
    (
        "AC-07",
        "tests.integration.test_resume_cli",
        "test_cli_resume_followup_continues_sequence_and_stdio_contract",
    ),
    (
        "AC-07",
        "tests.unit.test_session_recovery",
        "test_fake_environment_sees_no_historical_side_effects_during_resume",
    ),
    (
        "AC-07",
        "tests.unit.test_session_recovery",
        "test_local_environment_sees_no_historical_side_effects_during_resume",
    ),
    (
        "AC-08",
        "tests.integration.test_loop_compaction",
        "test_small_window_compacts_only_current_runtime_and_preserves_jsonl_prefix",
    ),
    (
        "AC-08",
        "tests.unit.test_context_builder",
        "test_budget_covers_full_prompt_tools_summary_messages_results_and_reserve",
    ),
    (
        "AC-08",
        "tests.unit.test_context_builder",
        "test_tool_calls_and_all_results_are_one_indivisible_group",
    ),
    (
        "AC-08",
        "tests.unit.test_compactor",
        "test_compactor_sends_old_summary_and_complete_history_without_tools",
    ),
    (
        "AC-08",
        "tests.architecture.test_forbidden_dependencies",
        "test_context_builder_does_not_scan_skills_or_external_resources",
    ),
    (
        "AC-09",
        "tests.integration.test_agent_limits",
        "test_model_step_limit_stops_a_repeating_model",
    ),
    (
        "AC-09",
        "tests.integration.test_agent_limits",
        "test_tool_call_limit_stops_before_the_next_declared_call",
    ),
    (
        "AC-09",
        "tests.integration.test_agent_limits",
        "test_wall_clock_limit_is_checked_after_a_slow_model_step",
    ),
    (
        "AC-10",
        "tests.security.test_event_redaction",
        "test_nested_secret_fields_and_inline_authorization_are_redacted",
    ),
    (
        "AC-10",
        "tests.security.test_model_redaction",
        "test_response_text_and_tool_arguments_redact_credentials",
    ),
    ("AC-10", "tests.unit.test_config", "test_key_is_looked_up_by_name_and_errors_are_redacted"),
    (
        "AC-11",
        "tests.unit.tools.test_builtin_tools",
        "test_six_builtins_pass_backend_requests_and_same_cancellation_signal",
    ),
    (
        "AC-11",
        "tests.architecture.test_forbidden_dependencies",
        "test_tools_do_not_import_or_call_host_side_effect_apis",
    ),
    (
        "AC-11",
        "tests.architecture.test_forbidden_dependencies",
        "test_only_local_environment_contains_generic_host_file_and_command_side_effects",
    ),
    (
        "AC-11",
        "tests.architecture.test_forbidden_dependencies",
        "test_loop_and_executor_have_no_tool_source_branches",
    ),
    (
        "AC-12",
        "tests.integration.test_runtime_isolation",
        "test_two_loops_do_not_share_messages_budget_tools_cancel_or_environment",
    ),
    ("AC-12", "tests.unit.test_runtime", "test_runtime_default_mutable_state_is_isolated"),
    (
        "AC-12",
        "tests.architecture.test_forbidden_dependencies",
        "test_loop_and_runtime_have_no_process_level_mutable_run_state",
    ),
    (
        "AC-13",
        "tests.integration.test_tool_lifecycle",
        "test_calls_get_fresh_internal_correlations_with_independent_provider_ids",
    ),
    (
        "AC-14",
        "tests.unit.environment.test_local_environment",
        "test_lifecycle_and_six_operations_use_backend_neutral_results",
    ),
    (
        "AC-14",
        "tests.unit.environment.test_local_environment",
        "test_cancel_before_side_effect_and_cancel_during_command",
    ),
    (
        "AC-14",
        "tests.unit.test_environment_contract",
        "test_protocol_declares_lifecycle_and_six_backend_neutral_operations",
    ),
    (
        "AC-14",
        "tests.architecture.test_forbidden_dependencies",
        "test_loop_and_tools_depend_on_environment_protocol_not_local",
    ),
    (
        "AC-14",
        "tests.architecture.test_forbidden_dependencies",
        "test_cli_does_not_import_backend_implementation_objects",
    ),
    (
        "AC-14",
        "tests.architecture.test_forbidden_dependencies",
        "test_backend_and_assembly_do_not_import_frontend_or_standard_streams",
    ),
    (
        "AC-14",
        "tests.architecture.test_forbidden_dependencies",
        "test_frontend_type_surface_does_not_name_agent_objects",
    ),
)


def _load(module_name: str, func_name: str) -> Callable[..., Any]:
    return getattr(importlib.import_module(module_name), func_name)


def _invoke(evidence_fn: Callable[..., Any], **available: Any) -> None:
    parameters = inspect.signature(evidence_fn).parameters
    kwargs = {name: available[name] for name in parameters if name in available}
    missing = [
        name
        for name, parameter in parameters.items()
        if name not in kwargs
        and parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    if missing:
        raise AssertionError(f"{evidence_fn.__name__} needs fixtures {missing}")
    evidence_fn(**kwargs)


@pytest.mark.parametrize(
    ("ac_id", "module_name", "func_name"),
    EVIDENCE,
    ids=[f"{ac_id}-{func_name}" for ac_id, _, func_name in EVIDENCE],
)
def test_ac_referenced_evidence(
    ac_id: str,
    module_name: str,
    func_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del ac_id
    _invoke(_load(module_name, func_name), tmp_path=tmp_path, monkeypatch=monkeypatch)


def test_ac_catalog_nodeids_still_collect() -> None:
    nodeids = [nodeid for items in CATALOG.values() for nodeid in items]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *nodeids],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    collected = completed.stdout
    for nodeid in nodeids:
        stem = nodeid.split("::", 1)[1]
        assert stem in collected
