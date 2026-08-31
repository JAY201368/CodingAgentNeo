"""Map baseline acceptance criteria to existing automated evidence.

The suite reuses these nodeids instead of copying whole test files. Fake Tool
and explicit system-prompt tests only prove those two injection seams; they
do not mean Skill or MCP discovery, loading, or transport is implemented.
"""

from __future__ import annotations

CATALOG: dict[str, tuple[str, ...]] = {
    "AC-01": (
        "tests/acceptance/test_ac01_closed_loop.py::test_ac01_scripted_local_environment_six_step_loop",
        "tests/acceptance/test_ac01_closed_loop.py::test_ac01_injected_fake_tool_is_absent_from_loop_source",
        "tests/integration/test_agent_loop.py::test_scripted_loop_uses_builtins_and_injected_tool_in_declared_order",
    ),
    "AC-02": (
        "tests/integration/test_agent_limits.py::test_consecutive_protocol_errors_have_a_hard_limit",
        "tests/integration/test_agent_limits.py::test_valid_protocol_call_resets_the_consecutive_error_counter",
        "tests/unit/tools/test_registry.py::test_unknown_inactive_and_invalid_arguments_are_structured_and_side_effect_free",
    ),
    "AC-03": (
        "tests/integration/test_agent_loop.py::test_scripted_loop_uses_builtins_and_injected_tool_in_declared_order",
        "tests/integration/test_agent_loop.py::test_command_timeout_is_a_normal_result_the_model_can_handle",
        "tests/unit/environment/test_local_environment.py::test_command_reports_nonzero_and_timeout_without_losing_observability",
    ),
    "AC-04": (
        "tests/security/test_workspace_boundary.py::test_absolute_and_parent_paths_are_rejected_before_file_side_effects",
        "tests/security/test_workspace_boundary.py::test_existing_and_pending_symlink_escape_is_rejected",
        "tests/security/test_workspace_boundary.py::test_recursive_list_and_search_never_follow_outside_symlink",
    ),
    "AC-05": (
        "tests/unit/test_policy.py::test_bash_asks_by_default",
        "tests/unit/test_policy.py::test_bash_is_allowed_in_automatic_modes",
        "tests/integration/test_cli.py::test_interactive_bash_confirmation",
        "tests/integration/test_cli.py::test_noninteractive_ask_denies_without_reading_and_auto_runs",
        "tests/integration/test_frontend_contract.py::test_interactive_approval_roundtrip_persists_request_before_execution",
    ),
    "AC-06": (
        "tests/acceptance/test_ac01_closed_loop.py::test_ac01_scripted_local_environment_six_step_loop",
        "tests/unit/test_session_store.py::test_jsonl_is_one_schema_v1_object_per_flushed_append",
        "tests/integration/test_cli.py::test_keyboard_interrupt_is_documented_and_persisted",
        "tests/integration/test_cli.py::test_noninteractive_success_stdout_contract_and_parseable_session",
    ),
    "AC-07": (
        "tests/unit/test_session_recovery.py::test_full_session_restores_ids_budget_tools_context_and_followup",
        "tests/unit/test_session_recovery.py::test_fake_environment_sees_no_historical_side_effects_during_resume",
        "tests/unit/test_session_recovery.py::test_local_environment_sees_no_historical_side_effects_during_resume",
        "tests/integration/test_resume_cli.py::test_cli_resume_does_not_replay_historical_side_effects",
        "tests/integration/test_resume_cli.py::test_cli_resume_followup_continues_sequence_and_stdio_contract",
    ),
    "AC-08": (
        "tests/integration/test_loop_compaction.py::test_small_window_compacts_only_current_runtime_and_preserves_jsonl_prefix",
        "tests/unit/test_context_builder.py::test_budget_covers_full_prompt_tools_summary_messages_results_and_reserve",
        "tests/unit/test_context_builder.py::test_tool_calls_and_all_results_are_one_indivisible_group",
        "tests/unit/test_compactor.py::test_compactor_sends_old_summary_and_complete_history_without_tools",
    ),
    "AC-09": (
        "tests/integration/test_agent_limits.py::test_model_step_limit_stops_a_repeating_model",
        "tests/integration/test_agent_limits.py::test_tool_call_limit_stops_before_the_next_declared_call",
        "tests/integration/test_agent_limits.py::test_consecutive_protocol_errors_have_a_hard_limit",
        "tests/integration/test_agent_limits.py::test_wall_clock_limit_is_checked_after_a_slow_model_step",
    ),
    "AC-10": (
        "tests/acceptance/test_ac10_secrets.py::test_ac10_tracked_tree_has_no_real_api_keys",
        "tests/security/test_event_redaction.py::test_nested_secret_fields_and_inline_authorization_are_redacted",
        "tests/security/test_model_redaction.py::test_response_text_and_tool_arguments_redact_credentials",
        "tests/unit/test_config.py::test_key_is_looked_up_by_name_and_errors_are_redacted",
    ),
    "AC-11": (
        "tests/unit/tools/test_builtin_tools.py::test_six_builtins_pass_backend_requests_and_same_cancellation_signal",
        "tests/architecture/test_forbidden_dependencies.py::test_tools_do_not_import_or_call_host_side_effect_apis",
        "tests/architecture/test_forbidden_dependencies.py::test_only_local_environment_contains_generic_host_file_and_command_side_effects",
    ),
    "AC-12": (
        "tests/integration/test_runtime_isolation.py::test_two_loops_do_not_share_messages_budget_tools_cancel_or_environment",
        "tests/unit/test_runtime.py::test_runtime_default_mutable_state_is_isolated",
    ),
    "AC-13": (
        "tests/integration/test_tool_lifecycle.py::test_calls_get_fresh_internal_correlations_with_independent_provider_ids",
        "tests/acceptance/test_ac01_closed_loop.py::test_ac01_scripted_local_environment_six_step_loop",
    ),
    "AC-14": (
        "tests/unit/environment/test_local_environment.py::test_lifecycle_and_six_operations_use_backend_neutral_results",
        "tests/unit/environment/test_local_environment.py::test_cancel_before_side_effect_and_cancel_during_command",
        "tests/unit/test_environment_contract.py::test_protocol_declares_lifecycle_and_six_backend_neutral_operations",
        "tests/architecture/test_forbidden_dependencies.py::test_loop_and_tools_depend_on_environment_protocol_not_local",
    ),
}

REAL_API_AC01_STATUS = "manual-2026-08-31"
