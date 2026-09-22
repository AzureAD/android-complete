"""Shared Phase-5 Authenticator release-pipeline stage launcher helpers."""
from __future__ import annotations

import re

from orchestrator.outcomes import Blocked, Done, NeedsSkill
from orchestrator.step_context import StepContext
from steps.lib.mockctx import MISSING
from tools import pipelines as P


def final_auth_build_id(context: StepContext):
    final = (context.evidence.pipeline_runs or {}).get("final_auth") or {}
    return final.get("authenticator_build_id")


def resolve_target(context: StepContext, *, stage_name: str, pinned_build_id=None):
    injected = context.input("run", MISSING)
    if injected is not MISSING and injected:
        return (True, dict(injected), "")
    branch = (getattr(context.release, "versions", None) or {}).get("authenticator")
    if not branch:
        return (False, None, "no Authenticator release branch on record (state.versions.authenticator)")
    return context.services.pipelines.find_auth_signoff_run(
        branch,
        final_auth_build_id=final_auth_build_id(context),
        build_id=pinned_build_id,
        stage_name=stage_name,
    )


def completed_stage_build_id(context: StepContext, step_id: str):
    """Read the already-durable pipeline build identity from a prerequisite's link."""
    record = context.evidence.step("rollout_start", step_id)
    for link in record.links or ():
        match = re.search(r"[?&]buildId=(\d+)(?:&|$)", str(link.get("url") or ""))
        if match:
            return match.group(1)
    return None


def stage_started(info: dict) -> bool:
    return P.signoff_stage_started(info)


def stage_failed(info: dict) -> bool:
    return P.signoff_stage_failed(info)


def build_stage_action(
    context: StepContext,
    *,
    step_id: str,
    write_command: str,
    stage_name: str,
    label: str,
    auto_approve: bool = True,
    command_args: str = "",
    review_context: dict | None = None,
    pinned_build_id=None,
):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"{step_id}: {fail}")

    ok, info, detail = resolve_target(
        context, stage_name=stage_name, pinned_build_id=pinned_build_id)
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return Blocked(f"{step_id}: couldn't inspect pipeline 397224 ({detail}){hint}.")
    if not info:
        return Blocked(
            f"{step_id}: couldn't locate the Android Build Release run/stage for this "
            f"release ({detail}). The release owner must investigate pipeline 397224.")
    if stage_failed(info):
        return Blocked(
            f"{step_id}: {stage_name} already ran but did not pass "
            f"(result {info.get('stage_result') or 'unknown'}) on build {info.get('build_id')}.")
    if stage_started(info):
        return Done(
            note=f"{stage_name} already started on Authenticator release build {info.get('build_id')}.",
            links=([{"name": f"{stage_name} run", "url": info["url"]}]
                   if info.get("url") else []),
        )

    build_id = info.get("build_id")
    summary = f"Start '{stage_name}' on Authenticator release build {build_id} (pipeline 397224)."
    followup = f"{write_command} --release {context.release.release_id}{command_args}"
    if auto_approve:
        followup += f" --execute --auto-approve --executor {label}-automation"
        instructions = (
            "Run the checked command with --execute --auto-approve. It recomputes the "
            "pipeline-397224 target, checkpoints the current plan hash, starts exactly the "
            f"{stage_name} stage, then verifies the stage is queued/running.")
    else:
        instructions = (
            "Preview the checked command and show its exact target and review hash to the "
            "release owner. Execute only after that owner approves, repeating the same "
            "arguments with --execute, --review-hash, and --approved-by set to the release "
            "owner's email. The command verifies owner identity before starting the stage.")
    reviewed_plan = {
        "build_id": build_id,
        "build_number": info.get("build_number"),
        "stage": stage_name,
        "stage_state": info.get("stage_state"),
        "match_basis": info.get("match_basis"),
        "url": info.get("url"),
        **(review_context or {}),
    }
    return NeedsSkill(
        tool=write_command,
        payload={
            "release": context.release.release_id,
            "plan": reviewed_plan,
            "followup_command": followup,
            "execution_instructions": instructions,
        },
        record_as=step_id,
        summary=summary,
        note=summary,
        outbound=True,
    )


MOCKABLE = {
    "run": {"kind": "input",
            "desc": "Inject release-pipeline stage evidence (skip the pipeline-397224 lookup)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}
