"""Step: `verify_alpha_uploaded` — verify `Upload_To_Alpha_GPS` completed and succeeded (Phase 5).

Why this step exists:
  `upload_alpha` completes as soon as Azure DevOps queues `Upload_To_Alpha_GPS` (`state=pending`).
  Downstream `Progress_Beta_100` (`beta_play_store`) invokes `GooglePlayPromote@3` from
  `sourceTrack: alpha` -> `beta` 100%, which promotes whatever APK is currently latest on Alpha
  (and `Upload_Whats_New` implicitly `dependsOn: Upload_To_Alpha_GPS` in `AndroidRelease.yml`).
  Waiting for `Upload_To_Alpha_GPS` to reach `state=completed` + `result=succeeded` prevents
  `Upload_Whats_New` from failing in ADO and prevents `beta_play_store` from promoting a stale APK.

Read-only (`az`) -> `agent` step with `RECHECK_WHEN_BLOCKED = True`.
"""
from __future__ import annotations

from orchestrator.authority import OwnStepData
from orchestrator.evidence import StepData
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.step_context import StepContext
from orchestrator.step_context import MISSING
from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "verify_alpha_uploaded"
KIND = "agent"
NAME = "Verify Alpha APK upload succeeded"
REQUIRED_TOOLS = ("az",)
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True
EVIDENCE = (OwnStepData(),)

STAGE_ID = P.AUTH_ALPHA_STAGE_ID
STAGE_NAME = P.AUTH_ALPHA_STAGE_NAME
PREVIOUS_STEP_ID = "upload_alpha"
PREVIOUS_LABEL = "Upload Alpha - Google Play"
POLL_INTERVAL_MIN = 30

def build(context: StepContext):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"{ID}: {fail}")

    ok, info, detail = release_stage.resolve_pinned_target(
        context,
        stage_name=STAGE_NAME,
        stage_id=STAGE_ID,
        previous_step_id=PREVIOUS_STEP_ID,
        previous_label=PREVIOUS_LABEL,
        for_step_id=ID,
    )
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return Blocked(f"{ID}: couldn't inspect pipeline 397224 ({detail}){hint}.")
    if not info:
        return Blocked(
            f"{ID}: couldn't locate the Android Build Release run/stage '{STAGE_ID}' "
            f"({detail}). Investigate pipeline 397224.")

    build_id = info.get("build_id")
    state = str(info.get("stage_state") or "").lower()
    result = str(info.get("stage_result") or "").lower()
    url = info.get("url")
    links = [{"name": f"{STAGE_NAME} run", "url": url}] if url else []
    step_update = StepData({
        "checked_at": context.clock.iso(),
        "build_id": str(build_id) if build_id is not None else None,
        "stage_id": STAGE_ID,
        "stage_state": info.get("stage_state"),
        "stage_result": info.get("stage_result"),
    })

    if state != "completed":
        return InProgress(
            f"{ID}: '{STAGE_NAME}' ({STAGE_ID}) on Authenticator release build {build_id} is "
            f"{info.get('stage_state') or 'in progress'} — waiting for Google Play Alpha APK "
            "upload to finish.",
            poll_in_min=POLL_INTERVAL_MIN,
            links=links,
            updates=(step_update,),
        )

    if result not in ("succeeded", "succeededwithissues"):
        return Blocked(
            f"{ID}: '{STAGE_NAME}' ({STAGE_ID}) completed with result={info.get('stage_result')!r} "
            f"on Authenticator release build {build_id}. Re-run or fix the Alpha upload in "
            "pipeline 397224 before continuing (release-drainer re-checks automatically).",
            links=links,
            updates=(step_update,),
        )

    return Done(
        f"Verified '{STAGE_NAME}' ({STAGE_ID}) succeeded on Authenticator release build {build_id}.",
        links=links,
        updates=(step_update,),
    )

KNOWLEDGE = {
    "summary": "Verify Google Play Alpha APK upload (`Upload_To_Alpha_GPS`) completed and succeeded.",
    "what": (
        "Reads the pinned pipeline-397224 timeline and confirms stage `Upload_To_Alpha_GPS` "
        "('Upload Alpha - Google Play') has finished (`state=completed`, `result=succeeded`) "
        "before `upload_whats_new` and `beta_play_store` can run.\n"
    ),
    "who": (
        "Automated read-only check (`agent` step). Polls while the upload is running and "
        "automatically re-checks if retried after a failure (`RECHECK_WHEN_BLOCKED = True`).\n"
    ),
    "where": [
        "pipeline: msazure/One Android Build Release definition 397224",
        "stage_id: Upload_To_Alpha_GPS (Upload Alpha - Google Play)",
    ],
    "how": (
        "Resolves the exact pipeline-397224 build ID recorded by `upload_alpha` (or `signoff_start` "
        "if `upload_alpha` was completed manually) and checks `stage_state` and `stage_result` on "
        "`Upload_To_Alpha_GPS`.\n"
    ),
}
