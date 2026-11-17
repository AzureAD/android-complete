"""Step: `beta_play_store` — owner-approved 100% Beta Play Store stage start."""
from __future__ import annotations

from dataclasses import dataclass

from orchestrator.outcomes import Blocked
from orchestrator.step_context import StepContext
from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "beta_play_store"
KIND = "scout"
WRITE_COMMAND = "start-beta-play-store"
STAGE_NAME = P.AUTH_BETA_STAGE_NAME
AUTOMATION_LABEL = "beta-play-store"
REQUIRES_OWNER_APPROVAL = True

MOCKABLE = release_stage.MOCKABLE
stage_started = release_stage.stage_started
stage_failed = release_stage.stage_failed

# Product policy: the authenticated release owner attests that this named manager
# approved the Friday exception. There is intentionally no separate manager login
# or durable two-party receipt.


@dataclass(frozen=True)
class BuildParameters:
    manager_approved_by: str | None = None


PARAMETERS = {"build": BuildParameters}


def resolve_target(context: StepContext):
    return release_stage.resolve_pinned_target(
        context,
        stage_name=STAGE_NAME,
        previous_step_id="upload_alpha",
        previous_label="Upload Alpha",
        require_pin_in_resolve=True,
    )


def manager_approval(
    context: StepContext,
    value: str | None,
    *,
    now=None,
) -> str | None:
    approver = str(value or "").strip()
    if (now or context.clock.now()).weekday() == 4:
        if not approver:
            raise ValueError(
                "A release cannot start on Friday. Keep this step held, or obtain manager "
                "approval and attest to Scout who approved the Friday override.")
        return approver
    if approver:
        raise ValueError("Manager override evidence is accepted only for a Friday start.")
    return None


def build(context: StepContext[BuildParameters]):
    try:
        approver = manager_approval(
            context, context.parameters.manager_approved_by)
    except ValueError as exc:
        return Blocked(f"{ID}: {exc}")
    command_args = (
        f' --manager-approved-by "{approver}"' if approver else "")
    return release_stage.build_pinned_stage_action(
        context,
        step_id=ID,
        write_command=WRITE_COMMAND,
        stage_name=STAGE_NAME,
        label=AUTOMATION_LABEL,
        previous_step_id="upload_alpha",
        previous_label="Upload Alpha",
        auto_approve=False,
        command_args=command_args,
        review_context={
            "release_owner_approval_required": True,
            "start_date": context.clock.now().date().isoformat(),
            "manager_approved_by": approver,
        },
    )
