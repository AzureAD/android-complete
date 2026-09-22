"""Step: `signoff_start` — start Authenticator Release Sign Off from pipeline 397224."""
from __future__ import annotations

from orchestrator.step_context import StepContext
from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "signoff_start"
KIND = "scout"
WRITE_COMMAND = "start-release-signoff"
STAGE_NAME = P.AUTH_SIGNOFF_STAGE_NAME
AUTOMATION_LABEL = "release-signoff"

MOCKABLE = release_stage.MOCKABLE


def resolve_target(context: StepContext):
    return release_stage.resolve_target(context, stage_name=STAGE_NAME)


def stage_started(info: dict) -> bool:
    return release_stage.stage_started(info)


def stage_failed(info: dict) -> bool:
    return release_stage.stage_failed(info)


def build(context: StepContext):
    return release_stage.build_stage_action(
        context,
        step_id=ID,
        write_command=WRITE_COMMAND,
        stage_name=STAGE_NAME,
        label=AUTOMATION_LABEL,
    )
