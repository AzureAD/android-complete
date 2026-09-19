"""Production IO adapters, assembled at the invocation boundary, never by handlers."""
from __future__ import annotations

import json
from urllib import request

from .services import (
    AssetReads, EffectServices, IdentityReads, PipelineReads, RepositoryReads,
    Services, TestPlanReads,
)
from .step_context import thaw
from .evidence import BrokerResource
from .authority import WriteCapabilities, WriteOperation


def _text(path, encoding="utf-8"):
    with open(path, encoding=encoding) as stream:
        return stream.read()


def _json(path, encoding="utf-8-sig"):
    return json.loads(_text(path, encoding))


def _template(rel_path):
    from steps.lib.templating import template_path
    path = template_path(rel_path)
    try:
        return _text(path)
    except OSError:
        return {"error": f"template not found: {path}"}


def _changelog(url, timeout=20):
    req = request.Request(url, headers={"User-Agent": "release-agent-preflight/1.0"})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def production_services(*, status_email=None, status_recipients=None):
    from tools import pipelines as P, prs as PR, oneauth as OA, checks as C
    from tools import testplans as T, broker_plans as B, distribution as D
    from tools import bugbash as BB, maven as M, invite as I
    from .automations import load_defs

    def unavailable(*args, **kwargs):
        raise ValueError("Status email read model was not supplied for this invocation")

    return Services(
        PipelineReads(
            P.find_checker_runs, P.get_timeline, P.find_orchestrator_run,
            P.get_stages, P.mrwp_run_ids, P.get_build_status, P.get_test_summary,
            P.find_auth_ecs_build, P.find_auth_ui_test_build, P.collect_auth_ui_evidence,
            P.discover_versions, P.orchestrator_stage_state,
            P.orchestrator_finalization_status, P.find_orchestrator_pending_approval,
            P.find_auth_release_build,
            P.find_final_auth_build,
            P.merged_release_prs, P.release_change_manifest,
            C.latest_scheduled_build, I.local_flights,
            P.get_pipeline_approval,
        ),
        RepositoryReads(
            PR.remote_branch_exists, PR.gh_find_open_pr, PR.az_find_open_pr,
            PR.behind_count, PR.gradle_diff_files, PR.merge_conflict_preview,
            PR.gh_release_exists, PR.broker_change_list, OA.ahead_behind,
            OA.find_open_pr, OA.read_text, C.wiki_page_exists, C.fetch_cg_alerts,
        ),
        TestPlanReads(
            B.prepare_source, T.find_auth_query_suite, T.validate_auth_query_suite,
            D.auth_bugbash_cases, D.broker_manual_cases, D.case_assignment_snapshot,
            D.find_suite_id_by_name, D.read_point_testers, BB.gather_progress,
        ),
        IdentityReads(C.current_az_user, D.resolve_roster, BB.resolve_mention_people),
        AssetReads(_template, _changelog, _json, _text, D.load_config,
                   D.oncall_team, load_defs, status_recipients or unavailable,
                   status_email or unavailable, M.is_published, I.load_template),
    )


def production_effects(capabilities: WriteCapabilities, *, validate, committer, clock):
    if not isinstance(capabilities, WriteCapabilities):
        raise TypeError("Effect adapters require compiled WriteCapabilities")
    from tools import checks, pipelines, testplans, broker_plans, distribution

    def guarded(function):
        def call(*args, **kwargs):
            validate()
            return function(*thaw(args), **thaw(kwargs))
        return call

    def ensure(release_id, name, *, record, **kwargs):
        detached = thaw(record)

        def persist():
            committer.commit(BrokerResource(detached))
            validate()

        validate()
        return broker_plans.ensure_plan(
            release_id, name, detached, persist, now=clock.now, **thaw(kwargs))

    if WriteOperation.ENSURE_BROKER_PLAN in capabilities.operations and committer is None:
        raise ValueError("Broker adapter requires a durable evidence committer")
    ports = {
        WriteOperation.ONEAUTH_WRITE_ACCESS: checks.oneauth_write_access,
        WriteOperation.CREATE_LIGHTWEIGHT_TAG: pipelines.create_lightweight_tag,
        WriteOperation.ENSURE_BROKER_PLAN: ensure,
        WriteOperation.CREATE_AUTH_QUERY_SUITE: testplans.create_auth_query_suite,
        WriteOperation.FILL_AUTH_UI_RESULTS: testplans.fill_auth_ui_results,
        WriteOperation.FILL_UI_AUTOMATION_RESULTS: testplans.fill_ui_automation_results,
        WriteOperation.SET_ASSIGNED_TO: distribution.set_assigned_to,
        WriteOperation.SUBMIT_PIPELINE_APPROVAL: pipelines.submit_pipeline_approval,
    }
    return EffectServices(**{op.value: guarded(ports[op]) for op in capabilities.operations})
