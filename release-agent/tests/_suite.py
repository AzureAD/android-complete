"""Reviewed daily coverage; extended regressions remain available explicitly."""
from pathlib import Path

import pytest


BASELINE_CASES = 2172
CORE_CASE_BUDGET = BASELINE_CASES // 2
_SUMMARY = pytest.StashKey()

# Keep the complete engine/ownership/contract suites in the daily run. Move broad
# phase/provider matrices, renderer variants and older overlapping flows out.
EXTENDED_MODULES = frozenset({
    "test_automation.py",
    "test_bug_bash.py",
    "test_bugbash_broker_triage.py",
    "test_bugbash_cadence.py",
    "test_bugbash_chat_binding.py",
    "test_bugbash_full_render.py",
    "test_bugbash_header_links.py",
    "test_bugbash_invite_auth_link.py",
    "test_bugbash_mentions.py",
    "test_bugbash_progress_counts.py",
    "test_build_verify.py",
    "test_ccd.py",
    "test_core.py",
    "test_distribution.py",
    "test_distribution_live.py",
    "test_finalize.py",
    "test_gate_provider_recovery.py",
    "test_guarded_outcomes.py",
    "test_handler_execution.py",
    "test_oneauth_merge_review.py",
    "test_preflight.py",
    "test_progress_delivery_retention.py",
    "test_rc_auth_failure_render.py",
    "test_rc_evidence.py",
    "test_rc_report_delivery.py",
    "test_sim.py",
    "test_status_email.py",
    "test_tools.py",
    "test_two_plan_mapping.py",
    "test_ui_projection.py",
})

# Retain full parameter sets for these named regressions, never every-Nth tests
# or arbitrary samples. The complete release replay still covers all phases.
CORE_CASES = {
    "test_core.py": frozenset({
        "test_full_flow_replay_completes",
        "test_persistence_roundtrip",
        "test_conditional_hotfix_excluded_by_default",
        "test_state_lock_is_exclusive_then_releases",
        "test_step_modules_and_config_stay_in_sync",
        "test_failing_agent_requires_skip_override_not_done",
    }),
    "test_guarded_outcomes.py": frozenset({
        "test_preparation_is_not_human_completion_even_with_forged_attribution",
        "test_preissued_permit_cannot_overwrite_a_new_generation",
        "test_permits_are_issuer_bound_not_reconstructable_or_persisted",
        "test_unowned_effect_cannot_complete_through_observation_permit",
        "test_notification_invocation_permit_cannot_bypass_receipt_or_resume",
    }),
    "test_gate_provider_recovery.py": frozenset({
        "test_pending_build_prefix_collision_does_not_select_other_run",
        "test_multiple_build_approvals_fail_closed",
        "test_unique_stage_and_build_approval_are_discovered",
        "test_prepare_freezes_identity_and_comment",
        "test_submit_only_calls_fenced_writer_and_does_not_fabricate_mock_success",
        "test_reconcile_provider_failure_never_writes",
        "test_new_build_and_stage_completion_cannot_redirect_frozen_reconciliation",
    }),
    "test_rc_evidence.py": frozenset({
        "test_missing_provider_and_zero_ui_never_gate_clean",
        "test_new_partial_rc_cannot_reuse_previous_complete_rc",
        "test_cli_dispatch_and_recorder_cannot_bypass_predecessors",
        "test_gate_uses_unrounded_counts",
    }),
    "test_finalize.py": frozenset({
        "test_orchestrator_finalization_monitors_stable_stage_identifier",
        "test_orchestrator_finalization_ado_reads_respect_two_hour_cadence",
        "test_tag_authenticator_blocks_when_captured_version_disagrees_with_build",
        "test_tag_authenticator_recovery_uses_frozen_target",
        "test_tag_authenticator_conflict_different_commit_blocks",
        "test_wiki_payload_requires_captured_final_authenticator_build",
    }),
    "test_preflight.py": frozenset({
        "test_cg_agent_fetch_error_holds",
        "test_cg_agent_blocks_on_high",
        "test_oneauth_access_denied_blocks",
    }),
    "test_ccd.py": frozenset({
        "test_localization_command_timeout_holds",
        "test_localization_inflight_is_not_retriggered_by_release_worker",
    }),
}


def pytest_addoption(parser):
    parser.getgroup("release-agent").addoption(
        "--validation-suite", choices=("auto", "core", "extended", "full"), default="auto",
        help="auto: core for directory runs, all requested cases for file/node/-k/-m selectors",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "extended: regression coverage outside the daily core suite")


def selected_suite(config):
    requested = config.getoption("validation_suite")
    if requested != "auto":
        return requested
    if config.option.keyword or config.option.markexpr:
        return "full"
    if any("::" in arg or Path(arg).suffix.casefold() == ".py" for arg in config.args):
        return "full"
    return "core"


def is_extended(item):
    if item.get_closest_marker("git_integration") or item.get_closest_marker("extended"):
        return True
    filename = item.path.name
    name = getattr(item, "originalname", None) or item.name
    return filename in EXTENDED_MODULES and name not in CORE_CASES.get(filename, ())


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Mark before pytest evaluates -m; filter after its explicit selectors.
    total = len(items)
    for item in items:
        if (item.path.name in ("test_git_write_review.py", "test_oneauth_merge_review.py")
                and {"checkout", "git_oneauth", "oneauth"}.intersection(item.fixturenames)):
            item.add_marker(pytest.mark.git_integration)
        if is_extended(item):
            item.add_marker(pytest.mark.extended)
    yield
    suite = selected_suite(config)
    kept, deferred = [], []
    for item in items:
        extended = is_extended(item)
        if suite == "full" or extended == (suite == "extended"):
            kept.append(item)
        else:
            deferred.append(item)
    if deferred:
        config.hook.pytest_deselected(items=deferred)
        items[:] = kept
    label = "targeted" if config.getoption("validation_suite") == "auto" and suite == "full" else suite
    config.stash[_SUMMARY] = (label, len(kept), total - len(kept))


def pytest_terminal_summary(terminalreporter):
    summary = terminalreporter.config.stash.get(_SUMMARY, None)
    if summary:
        suite, selected, deferred = summary
        terminalreporter.write_line(
            f"Validation suite: {suite}; {selected} selected, {deferred} deselected. "
            "Use tests --validation-suite=full for all regressions."
        )
