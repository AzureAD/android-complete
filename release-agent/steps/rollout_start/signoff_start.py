"""Step: `signoff_start` — start Authenticator Release Sign Off from pipeline 397224."""
from __future__ import annotations

from orchestrator.outcomes import Blocked, Done, InProgress, NeedsSkill
from orchestrator.step_context import StepContext
from steps.lib.mockctx import MISSING
from tools import pipelines as P

ID = "signoff_start"
KIND = "scout"
WRITE_COMMAND = "start-release-signoff"

MOCKABLE = {
    "run": {"kind": "input",
            "desc": "Inject signoff run evidence (skip the pipeline-397224 lookup)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}

CONFIG = {"poll_interval_min": 30}


def _final_auth_build_id(context: StepContext):
    final = (context.evidence.pipeline_runs or {}).get("final_auth") or {}
    return final.get("authenticator_build_id")


def resolve_target(context: StepContext):
    injected = context.input("run", MISSING)
    if injected is not MISSING and injected:
        return (True, dict(injected), "")
    record = context.evidence.step("rollout_start", ID)
    if record.status == "in_flight" and record.data.get("build_id"):
        return context.services.pipelines.read_auth_signoff_run(record.data["build_id"])
    branch = (getattr(context.release, "versions", None) or {}).get("authenticator")
    if not branch:
        return (False, None, "no Authenticator release branch on record (state.versions.authenticator)")
    return context.services.pipelines.find_auth_signoff_run(
        branch,
        final_auth_build_id=_final_auth_build_id(context),
    )


def stage_started(info: dict) -> bool:
    return P.signoff_stage_started(info)


def stage_running(info: dict) -> bool:
    return P.signoff_stage_running(info)


def stage_succeeded(info: dict) -> bool:
    return P.signoff_stage_succeeded(info)


def stage_failed(info: dict) -> bool:
    return P.signoff_stage_failed(info)


def build(context: StepContext):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"signoff_start: {fail}")

    ok, info, detail = resolve_target(context)
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return Blocked(f"signoff_start: couldn't inspect pipeline 397224 ({detail}){hint}.")
    if not info:
        return Blocked(
            "signoff_start: couldn't locate the Android Build Release run for this "
            f"release ({detail}). The release owner must investigate pipeline 397224.")
    if stage_succeeded(info):
        return Done(
            note=f"Release Sign Off completed on Authenticator signoff build {info.get('build_id')}.",
            by="scout",
            links=[{"name": "Release Sign Off run", "url": info.get("url")}] if info.get("url") else [],
        )
    if stage_failed(info):
        return Blocked(
            "signoff_start: Release Sign Off already ran but did not pass "
            f"(result {info.get('stage_result') or 'unknown'}) on build {info.get('build_id')}.")
    if stage_running(info):
        return InProgress(
            f"Release Sign Off is {info.get('stage_state') or 'pending'} on "
            f"Authenticator signoff build {info.get('build_id')}; Scout is polling until it finishes.",
            links=[{"name": "Release Sign Off run", "url": info.get("url")}] if info.get("url") else [],
            poll_in_min=CONFIG["poll_interval_min"],
        )
    if str(info.get("stage_state") or "").lower() == "completed":
        return Blocked(
            "signoff_start: Release Sign Off finished without a successful result "
            f"(result {info.get('stage_result') or 'unknown'}) on build {info.get('build_id')}.")

    build_id = info.get("build_id")
    stage = info.get("stage_name") or P.AUTH_SIGNOFF_STAGE_NAME
    summary = f"Start '{stage}' on Authenticator signoff build {build_id} (pipeline 397224)."
    return NeedsSkill(
        tool=WRITE_COMMAND,
        payload={
            "release": context.release.release_id,
            "plan": {
                "build_id": build_id,
                "build_number": info.get("build_number"),
                "stage": stage,
                "stage_state": info.get("stage_state"),
                "match_basis": info.get("match_basis"),
                "url": info.get("url"),
            },
            "followup_command": (
                f"{WRITE_COMMAND} --release {context.release.release_id} "
                "--execute --auto-approve --executor release-signoff-automation"),
            "execution_instructions": (
                "Run the checked command with --execute --auto-approve. It recomputes the "
                "pipeline-397224 target, checkpoints the current plan hash, starts exactly the "
                "Release Sign Off stage, records the step in-flight, and polls until the stage finishes."),
        },
        record_as=ID,
        summary=summary,
        note=summary,
        outbound=True,
    )
