"""Step: `upload_alpha` — start Authenticator Upload Alpha stage."""
from __future__ import annotations

from orchestrator.outcomes import Blocked
from orchestrator.step_context import StepContext
from steps.lib.mockctx import MISSING
from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "upload_alpha"
KIND = "scout"
WRITE_COMMAND = "start-upload-alpha"
STAGE_NAME = P.AUTH_ALPHA_STAGE_NAME
AUTOMATION_LABEL = "upload-alpha"
PREVIOUS_STEP_ID = "upload_whats_new"

MOCKABLE = release_stage.MOCKABLE


def resolve_target(context: StepContext):
    return release_stage.resolve_target(
        context,
        stage_name=STAGE_NAME,
        pinned_build_id=release_stage.completed_stage_build_id(
            context, PREVIOUS_STEP_ID),
    )


def stage_started(info: dict) -> bool:
    return release_stage.stage_started(info)


def stage_failed(info: dict) -> bool:
    return release_stage.stage_failed(info)


def build(context: StepContext):
    pinned = release_stage.completed_stage_build_id(context, PREVIOUS_STEP_ID)
    if pinned is None and context.input("run", MISSING) is MISSING:
        return Blocked(
            f"{ID}: completed Upload What's New evidence has no pipeline-397224 build identity.")
    return release_stage.build_stage_action(
        context,
        step_id=ID,
        write_command=WRITE_COMMAND,
        stage_name=STAGE_NAME,
        label=AUTOMATION_LABEL,
        pinned_build_id=pinned,
    )
