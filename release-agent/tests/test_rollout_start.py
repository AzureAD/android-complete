"""Phase-5 Authenticator rollout-start workflow, stage-identifier, and verification tests."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from orchestrator import drain
from orchestrator.outcomes import Blocked, Done, InProgress, NeedsSkill
from orchestrator.state import ReleaseState, StepState
from orchestrator.workflow import normalize_workflow_config
from steps.rollout_start import (
    beta_play_store,
    identify_auth_build,
    signoff_start,
    upload_alpha,
    upload_whats_new,
    verify_alpha_uploaded,
)
from tests._context import context, fresh_orchestrator, invoke
from tests._harness import CONFIG, _active_phase, _active_step


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _state():
    state = ReleaseState(
        release_id="2026-09",
        target_month="2026-10",
        owner_email="owner@microsoft.com",
        owner_name="Release Owner",
    )
    state.versions = {
        "authenticator": "release/2026/09/10",
        "broker": "16.6.0",
        "common": "24.7.0",
        "msal": "8.5.0",
    }
    state.pipeline_runs = {
        "rcs": [{
            "rc": 1,
            "auth": {
                "build": {
                    "run_id": "180400001",
                    "result": "succeeded",
                    "complete": True,
                },
            },
        }],
        "final_auth": {
            "authenticator_build_id": "180500000",
            "authenticator_version": "6.2609.6000",
            "authenticator_commit": COMMIT,
            "authenticator_build_number": "20260910.5",
        },
    }
    state.set_step("bug_bash", "clone_plans_auth", StepState(
        status="done", data={"suite_id": 3752749, "suite_name": "October Authenticator"}))
    state.set_step("bug_bash", "native_auth_signoff", StepState(
        status="done", completed_at="2026-09-12T18:00:00+00:00"))
    state.set_step("bug_bash", "bugbash_complete", StepState(
        status="done", completed_at="2026-09-12T19:00:00+00:00"))
    state.set_step("rollout_start", "wiki_payload", StepState(
        status="done",
        links=[{"name": "Release payload page", "url": "https://wiki/payload"}]))
    return state


def _production_context(state, *, now=None):
    ctx = context(state, now=now)
    pipelines = replace(
        ctx.services.pipelines,
        find_final_auth_build=lambda branch, **kwargs: (True, {
            "build_id": 180500000,
            "version": "6.2609.6000",
            "commit": COMMIT,
            "build_number": "20260910.5",
        }, ""),
    )
    return replace(ctx, services=replace(ctx.services, pipelines=pipelines))


def _signoff_run(**extra):
    return {
        "build_id": 397224001,
        "build_number": "signoff.1",
        "source_branch": "refs/heads/release/2026/09/10",
        "source_version": COMMIT,
        "status": "completed",
        "result": "succeeded",
        "stage_name": "Release Sign Off",
        "stage_ref": "Sign_Off",
        "stage_id": "stage-1",
        "stage_state": "notStarted",
        "stage_result": None,
        "match_basis": "release_branch",
        "url": "https://msazure.visualstudio.com/One/_build/results?buildId=397224001",
        **extra,
    }


def test_rollout_start_dependency_graph_and_parallel_branches():
    """Verify the approved 8-step `rollout_start` DAG and parallel fan-out after `identify_auth_build`."""
    wf = normalize_workflow_config(yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8")))
    phase = next(p for p in wf["phases"] if p["id"] == "rollout_start")
    deps = {s["id"]: list(s.get("depends_on") or []) for s in phase["steps"]}
    assert list(deps.keys()) == [
        "identify_auth_build",
        "tag_authenticator",
        "wiki_payload",
        "signoff_start",
        "upload_alpha",
        "verify_alpha_uploaded",
        "upload_whats_new",
        "beta_play_store",
    ]
    assert deps == {
        "identify_auth_build": ["finalize.integ_prs_merged"],
        "tag_authenticator": ["identify_auth_build"],
        "wiki_payload": ["identify_auth_build"],
        "signoff_start": ["identify_auth_build"],
        "upload_alpha": ["signoff_start"],
        "verify_alpha_uploaded": ["upload_alpha"],
        "upload_whats_new": ["verify_alpha_uploaded"],
        "beta_play_store": ["upload_whats_new"],
    }

    # Once `identify_auth_build` completes, `tag_authenticator`, `wiki_payload`, and `signoff_start`
    # are all simultaneously ready in parallel!
    st = _state()
    _active_phase(st, "rollout_start")
    for s_id in deps:
        st.set_step("rollout_start", s_id, StepState(status="pending"))
    st.set_step("rollout_start", "identify_auth_build", StepState(status="done"))
    orch = fresh_orchestrator(CONFIG, st, step_defaults={})
    ready = {
        s.definition.id for s in orch.scheduling().steps
        if s.definition.phase_id == "rollout_start" and not s.complete and s.prerequisites_met
    }
    assert ready == {"tag_authenticator", "wiki_payload", "signoff_start"}


def test_identify_auth_build_records_final_pipeline_evidence():
    assert identify_auth_build.RECHECK_WHEN_BLOCKED is True
    build = {
        "build_id": 180500000,
        "version": "6.2609.6000",
        "commit": COMMIT,
        "build_number": "6.2609.6000",
        "status": "completed",
        "result": "succeeded",
    }
    state = _state()
    result = identify_auth_build.build(context(state, inputs={"build": build}))
    assert result.kind == "done"
    evidence = result.updates[0].values["final_auth"]
    assert evidence["authenticator_build_id"] == "180500000"
    assert evidence["authenticator_version"] == "6.2609.6000"
    assert evidence["authenticator_commit"] == COMMIT


def test_identify_auth_build_blocks_when_no_run_found():
    ctx = _production_context(_state())
    pipelines = replace(
        ctx.services.pipelines,
        find_final_auth_build=lambda branch: (True, None, "no run on release branch"),
    )
    result = identify_auth_build.build(replace(ctx, services=replace(ctx.services, pipelines=pipelines)))
    assert isinstance(result, Blocked)
    assert "no final Authenticator build found" in result.reason
    assert "email and Scout" in result.reason


def test_signoff_start_prepares_checked_stage_start():
    ctx = _production_context(_state())
    calls = []
    pipelines = replace(
        ctx.services.pipelines,
        find_auth_signoff_run=lambda branch, **kwargs: (calls.append(kwargs) or True, _signoff_run(), ""),
    )
    result = signoff_start.build(replace(ctx, services=replace(ctx.services, pipelines=pipelines)))
    assert isinstance(result, NeedsSkill)
    assert result.tool == "start-release-signoff"
    assert "--auto-approve" in result.payload["followup_command"]
    assert result.payload["plan"]["build_id"] == 397224001
    assert result.payload["plan"]["stage"] == "Release Sign Off"
    assert result.payload["plan"]["stage_id"] == "Sign_Off"
    assert calls[0]["stage_name"] == "Sign_Off"


@pytest.mark.parametrize("module,stage_id,stage,command,previous", [
    (upload_alpha, "Upload_To_Alpha_GPS", "Upload Alpha - Google Play", "start-upload-alpha", "signoff_start"),
    (upload_whats_new, "Upload_Whats_New", "Upload What's New", "start-upload-whats-new", "verify_alpha_uploaded"),
])
def test_release_upload_stage_prepares_checked_stage_start(module, stage_id, stage, command, previous):
    state = _state()
    state.set_step("rollout_start", previous, StepState(
        status="done",
        links=[{
            "name": "prior stage run",
            "url": "https://msazure.visualstudio.com/One/_build/results?buildId=397224001",
        }],
    ))
    ctx = _production_context(state)
    calls = []
    pipelines = replace(
        ctx.services.pipelines,
        find_auth_signoff_run=lambda branch, **kwargs: (
            calls.append(kwargs) or True,
            _signoff_run(stage_name=stage, stage_ref=stage_id),
            ""),
    )
    result = module.build(replace(ctx, services=replace(ctx.services, pipelines=pipelines)))
    assert isinstance(result, NeedsSkill)
    assert result.tool == command
    assert "--auto-approve" in result.payload["followup_command"]
    assert result.payload["plan"]["build_id"] == 397224001
    assert result.payload["plan"]["stage"] == stage
    assert result.payload["plan"]["stage_id"] == stage_id
    assert calls[0]["stage_name"] == stage_id
    assert calls[0]["build_id"] == "397224001"


@pytest.mark.extended
@pytest.mark.parametrize("module,label", [
    (upload_alpha, "Release Sign Off"),
    (upload_whats_new, "Verify Alpha APK upload"),
])
def test_release_upload_stage_blocks_without_prior_build_identity(module, label):
    result = module.build(context(_state()))
    assert isinstance(result, Blocked)
    assert f"completed {label} evidence has no pipeline-397224 build identity" in result.reason


def test_verify_alpha_uploaded_waits_blocks_and_resolves_cleanly():
    """`verify_alpha_uploaded` stays `InProgress` while `Upload_To_Alpha_GPS` runs, blocks with
    `RECHECK_WHEN_BLOCKED` on failure, and resolves `Done` with a pinned link once succeeded."""
    assert verify_alpha_uploaded.RECHECK_WHEN_BLOCKED is True
    state = _state()
    state.set_step("rollout_start", "upload_alpha", StepState(
        status="done",
        links=[{
            "name": "Upload Alpha - Google Play run",
            "url": "https://msazure.visualstudio.com/One/_build/results?buildId=397224001",
        }],
    ))

    in_prog = invoke(verify_alpha_uploaded.build, state) if False else verify_alpha_uploaded.build(context(
        state,
        inputs={"run": _signoff_run(
            stage_name="Upload Alpha - Google Play",
            stage_ref="Upload_To_Alpha_GPS",
            stage_state="inProgress",
            stage_result=None,
        )},
    ))
    assert isinstance(in_prog, InProgress)
    assert "Upload_To_Alpha_GPS" in in_prog.note

    failed = verify_alpha_uploaded.build(context(
        state,
        inputs={"run": _signoff_run(
            stage_name="Upload Alpha - Google Play",
            stage_ref="Upload_To_Alpha_GPS",
            stage_state="completed",
            stage_result="failed",
        )},
    ))
    assert isinstance(failed, Blocked)
    assert "result='failed'" in failed.reason

    orch = fresh_orchestrator(CONFIG, state, step_defaults={})
    _active_step(state, "rollout_start", "verify_alpha_uploaded")
    state.set_step("rollout_start", "verify_alpha_uploaded", StepState(status="blocked", note=failed.reason))
    step_def = next(
        s.definition for s in orch.scheduling().steps
        if s.definition.phase_id == "rollout_start" and s.definition.id == "verify_alpha_uploaded"
    )
    assert drain._should_recheck_step(step_def, state.get_step("rollout_start", "verify_alpha_uploaded")) is True

    succeeded = verify_alpha_uploaded.build(context(
        state,
        inputs={"run": _signoff_run(
            stage_name="Upload Alpha - Google Play",
            stage_ref="Upload_To_Alpha_GPS",
            stage_state="completed",
            stage_result="succeeded",
        )},
    ))
    assert isinstance(succeeded, Done)
    assert "397224001" in succeeded.note
    assert succeeded.links == [{
        "name": "Upload Alpha - Google Play run",
        "url": "https://msazure.visualstudio.com/One/_build/results?buildId=397224001",
    }]


def test_beta_stage_requires_owner_review_and_binds_exact_stage():
    state = _state()
    state.set_step("rollout_start", "upload_whats_new", StepState(
        status="done",
        links=[{
            "name": "Upload What's New run",
            "url": "https://msazure.visualstudio.com/One/_build/results?buildId=397224001",
        }],
    ))
    ctx = _production_context(state, now=datetime(
        2026, 9, 21, 17, 0, tzinfo=timezone.utc))
    calls = []
    pipelines = replace(
        ctx.services.pipelines,
        find_auth_signoff_run=lambda branch, **kwargs: (
            calls.append(kwargs) or True,
            _signoff_run(
                stage_name="100% Beta - Play Store",
                stage_ref="Progress_Beta_100"),
            ""),
    )
    result = beta_play_store.build(replace(
        ctx,
        services=replace(ctx.services, pipelines=pipelines),
        parameters=beta_play_store.BuildParameters(),
    ))
    assert isinstance(result, NeedsSkill)
    assert result.tool == "start-beta-play-store"
    assert "--auto-approve" not in result.payload["followup_command"]
    assert result.payload["plan"]["stage"] == "100% Beta - Play Store"
    assert result.payload["plan"]["stage_id"] == "Progress_Beta_100"
    assert result.payload["plan"]["release_owner_approval_required"] is True
    assert result.payload["plan"]["start_date"] == "2026-09-21"
    assert result.payload["plan"]["manager_approved_by"] is None
    assert calls[0]["stage_name"] == "Progress_Beta_100"
    assert calls[0]["build_id"] == "397224001"
    assert "release owner" in result.payload["execution_instructions"]


def test_beta_stage_blocks_friday_without_manager_approval():
    friday = datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)
    blocked = beta_play_store.build(context(
        _state(),
        now=friday,
        model=beta_play_store.BuildParameters,
    ))
    assert isinstance(blocked, Blocked)
    assert "cannot start on Friday" in blocked.reason
    assert "manager approval" in blocked.reason

    approved = beta_play_store.build(context(
        _state(),
        now=friday,
        parameters={"manager_approved_by": "manager@microsoft.com"},
        model=beta_play_store.BuildParameters,
        inputs={"run": _signoff_run(
            stage_name="100% Beta - Play Store",
            stage_ref="Progress_Beta_100")},
    ))
    assert isinstance(approved, NeedsSkill)
    assert '--manager-approved-by "manager@microsoft.com"' in \
        approved.payload["followup_command"]


def test_beta_stage_blocks_without_prior_stage_build_identity():
    result = beta_play_store.build(context(
        _state(),
        now=datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc),
        model=beta_play_store.BuildParameters,
    ))
    assert isinstance(result, Blocked)
    assert "Upload What's New evidence has no pipeline-397224 build identity" in result.reason


def test_signoff_start_is_done_when_stage_already_started():
    result = signoff_start.build(context(_state(), inputs={
        "run": _signoff_run(stage_state="inProgress", stage_result=None),
    }))
    assert result.kind == "done"
    assert result.links == [{
        "name": "Release Sign Off run",
        "url": "https://msazure.visualstudio.com/One/_build/results?buildId=397224001",
    }]


def test_signoff_start_blocks_when_no_release_build_found():
    ctx = _production_context(_state())
    pipelines = replace(
        ctx.services.pipelines,
        find_auth_signoff_run=lambda branch, **kwargs: (True, None, "no Android Build Release run"),
    )
    result = signoff_start.build(replace(ctx, services=replace(ctx.services, pipelines=pipelines)))
    assert isinstance(result, Blocked)
    assert "pipeline 397224" in result.reason


def test_canonical_yaml_identifiers_survive_display_title_changes():
    """Verify Phases 2, 4, and 5 match YAML `identifier`s even if `displayName` strings change."""
    from tools.pipelines import auth_app as A, orchestrator as O
    from steps.build_verify import orchestrator_health as OH
    from steps.finalize import remove_rc_tags_gate as RG, publish_notes_gate as PG

    # 1. Phase 2 checker job identifier (`TriggerMonthlyRelease`) with renamed displayName:
    recs = [{"type": "Job", "identifier": "TriggerMonthlyRelease", "name": "Renamed Trigger Title", "result": "succeeded"}]
    assert O.named_record(recs, O.TRIGGER_JOB_ID, aliases=(O.TRIGGER_JOB,)) is recs[0]

    # 2. Phase 2 orchestrator_health stage identifiers (`ValidateBranchesAndVersions`, `BranchSetup`, `TriggerRC`, `RemoveRCTags`):
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09")
    renamed_stages = [
        {"identifier": "ValidateBranchesAndVersions", "name": "Renamed Validate", "state": "completed", "result": "succeeded"},
        {"identifier": "BranchSetup", "name": "Renamed Branches", "state": "completed", "result": "succeeded"},
        {"identifier": "TriggerRC", "name": "Renamed Trigger RC", "state": "completed", "result": "succeeded"},
        {"identifier": "RemoveRCTags", "name": "Renamed Gate", "state": "pending", "result": None},
    ]
    oh_res = OH.build(context(st, inputs={
        "run": {"id": 5001, "tags": [
            "AuthenticatorBranch=release-2026-09-09",
            "NextCommonVersion=1.0.0", "NextMsalVersion=1.0.0", "NextBrokerVersion=1.0.0",
        ]},
        "stages": renamed_stages,
    }))
    assert oh_res.kind == "done"

    # 3. Phase 4 gates match `stage_id` (`RemoveRCTags` / `PublishGitHubReleaseNotes`) despite renamed display title:
    rg_res = RG.build(context(st, inputs={
        "approval": {"approval_id": "A1", "build_id": 5001, "stage": "Renamed Gate Title", "stage_id": "RemoveRCTags", "build_url": "u"},
        "stage_state": {"state": "pending", "result": None},
    }))
    assert rg_res.kind == "needs_human" and "APPROVING submits" in rg_res.prompt

    pg_res = PG.build(context(st, inputs={
        "approval": {"approval_id": "A2", "build_id": 5001, "stage": "Renamed Notes Title", "stage_id": "PublishGitHubReleaseNotes", "build_url": "u"},
        "stage_state": {"state": "pending", "result": None},
    }))
    assert pg_res.kind == "needs_human" and "APPROVING submits" in pg_res.prompt

    # 4. Phase 5 `AndroidRelease.yml` stage identifiers (`Sign_Off`, `Upload_To_Alpha_GPS`, `Upload_Whats_New`, `Progress_Beta_100`):
    auth_records = [
        {"id": "s1", "type": "Stage", "identifier": "Sign_Off", "name": "Renamed Signoff", "state": "notStarted", "result": None},
        {"id": "s2", "type": "Stage", "identifier": "Upload_To_Alpha_GPS", "name": "Upload Alpha - Google Play", "state": "notStarted", "result": None},
        {"id": "s3", "type": "Stage", "identifier": "Upload_Whats_New", "name": "Renamed Whats New", "state": "notStarted", "result": None},
        {"id": "s4", "type": "Stage", "identifier": "Progress_Beta_100", "name": "Renamed Beta", "state": "notStarted", "result": None},
    ]
    for stage_id in (A.AUTH_SIGNOFF_STAGE_ID, A.AUTH_ALPHA_STAGE_ID, A.AUTH_WHATS_NEW_STAGE_ID, A.AUTH_BETA_STAGE_ID):
        info = A._signoff_info({"id": 777}, auth_records, match_basis="release_branch", stage_name=stage_id)
        assert info is not None and info["stage_ref"] == stage_id

