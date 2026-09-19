"""Named IO ports. Pure renderers, validators and allocation algorithms stay functions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PipelineReads:
    find_checker_runs: Callable[..., tuple]
    get_timeline: Callable[..., tuple]
    find_orchestrator_run: Callable[..., tuple]
    get_stages: Callable[..., tuple]
    mrwp_run_ids: Callable[..., tuple]
    get_build_status: Callable[..., tuple]
    get_test_summary: Callable[..., tuple]
    find_auth_ecs_build: Callable[..., tuple]
    find_auth_ui_test_build: Callable[..., tuple]
    collect_auth_ui_evidence: Callable[..., tuple]
    discover_versions: Callable[..., tuple]
    orchestrator_stage_state: Callable[..., tuple]
    orchestrator_finalization_status: Callable[..., tuple]
    find_orchestrator_pending_approval: Callable[..., tuple]
    find_auth_release_build: Callable[..., tuple]
    find_final_auth_build: Callable[..., tuple]
    find_auth_signoff_run: Callable[..., tuple]
    read_auth_signoff_run: Callable[..., tuple]
    merged_release_prs: Callable[..., tuple]
    release_change_manifest: Callable[..., tuple]
    latest_scheduled_build: Callable[..., tuple]
    local_flights: Callable[..., tuple]
    get_pipeline_approval: Callable[..., tuple]


@dataclass(frozen=True)
class RepositoryReads:
    remote_branch_exists: Callable[..., tuple]
    gh_find_open_pr: Callable[..., tuple]
    az_find_open_pr: Callable[..., tuple]
    behind_count: Callable[..., tuple]
    gradle_diff_files: Callable[..., tuple]
    merge_conflict_preview: Callable[..., tuple]
    gh_release_exists: Callable[..., tuple]
    broker_change_list: Callable[..., tuple]
    oneauth_ahead_behind: Callable[..., tuple]
    oneauth_find_open_pr: Callable[..., tuple]
    oneauth_read_text: Callable[..., tuple]
    wiki_page_exists: Callable[..., tuple]
    fetch_cg_alerts: Callable[..., tuple]


@dataclass(frozen=True)
class TestPlanReads:
    prepare_broker_source: Callable[..., tuple]
    find_auth_query_suite: Callable[..., tuple]
    validate_auth_query_suite: Callable[..., tuple]
    auth_bugbash_cases: Callable[..., tuple]
    broker_manual_cases: Callable[..., tuple]
    case_assignment_snapshot: Callable[..., tuple]
    find_suite_id_by_name: Callable[..., tuple]
    read_point_testers: Callable[..., tuple]
    gather_progress: Callable[..., dict]


@dataclass(frozen=True)
class IdentityReads:
    current_az_user: Callable[..., str]
    resolve_roster: Callable[..., tuple]
    resolve_mention_people: Callable[..., tuple]


@dataclass(frozen=True)
class AssetReads:
    template: Callable[..., object]
    changelog: Callable[..., str]
    json_file: Callable[..., object]
    text_file: Callable[..., str]
    distribution_config: Callable[..., dict]
    oncall_team: Callable[..., tuple]
    automation_definitions: Callable[..., list]
    status_recipients: Callable[[], list]
    status_email: Callable[..., dict]
    is_published: Callable[..., tuple]
    invite_template: Callable[[], str]


@dataclass(frozen=True)
class Services:
    pipelines: PipelineReads
    repositories: RepositoryReads
    testplans: TestPlanReads
    identities: IdentityReads
    assets: AssetReads


@dataclass(frozen=True)
class EffectServices:
    """Only the owning operation is installed; all other write ports are absent."""
    oneauth_write_access: Callable[..., tuple] | None = None
    create_lightweight_tag: Callable[..., tuple] | None = None
    ensure_broker_plan: Callable[..., tuple] | None = None
    create_auth_query_suite: Callable[..., tuple] | None = None
    fill_auth_ui_results: Callable[..., tuple] | None = None
    fill_ui_automation_results: Callable[..., tuple] | None = None
    set_assigned_to: Callable[..., tuple] | None = None
    submit_pipeline_approval: Callable[..., tuple] | None = None
