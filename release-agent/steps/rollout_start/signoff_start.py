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
stage_started = release_stage.stage_started
stage_failed = release_stage.stage_failed


def resolve_target(context: StepContext):
    return release_stage.resolve_pinned_target(context, stage_name=STAGE_NAME)


def build(context: StepContext):
    return release_stage.build_pinned_stage_action(
        context,
        step_id=ID,
        write_command=WRITE_COMMAND,
        stage_name=STAGE_NAME,
        label=AUTOMATION_LABEL,
    )
