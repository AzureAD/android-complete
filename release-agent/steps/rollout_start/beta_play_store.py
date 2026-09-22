"""Step: `beta_play_store` — owner-approved 100% Beta Play Store stage start."""
from __future__ import annotations

from dataclasses import dataclass

from orchestrator.outcomes import Blocked
from orchestrator.step_context import StepContext
from steps.rollout_start import release_stage
from steps.lib.mockctx import MISSING
from tools import pipelines as P

ID = "beta_play_store"
KIND = "scout"
WRITE_COMMAND = "start-beta-play-store"
STAGE_NAME = P.AUTH_BETA_STAGE_NAME
AUTOMATION_LABEL = "beta-play-store"
REQUIRES_OWNER_APPROVAL = True

MOCKABLE = release_stage.MOCKABLE

# Product policy: the authenticated release owner attests that this named manager
# approved the Friday exception. There is intentionally no separate manager login
# or durable two-party receipt.


@dataclass(frozen=True)
class BuildParameters:
    manager_approved_by: str | None = None


PARAMETERS = {"build": BuildParameters}


def resolve_target(context: StepContext):
    pinned = release_stage.completed_stage_build_id(context, "upload_alpha")
    if pinned is None and context.input("run", MISSING) is MISSING:
        return (
            False,
            None,
            "completed Upload Alpha evidence has no pipeline-397224 build identity",
        )
    return release_stage.resolve_target(
        context, stage_name=STAGE_NAME, pinned_build_id=pinned)


def stage_started(info: dict) -> bool:
    return release_stage.stage_started(info)


def stage_failed(info: dict) -> bool:
    return release_stage.stage_failed(info)


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
    pinned = release_stage.completed_stage_build_id(context, "upload_alpha")
    if pinned is None and context.input("run", MISSING) is MISSING:
        return Blocked(
            f"{ID}: completed Upload Alpha evidence has no pipeline-397224 build identity.")
    command_args = (
        f' --manager-approved-by "{approver}"' if approver else "")
    return release_stage.build_stage_action(
        context,
        step_id=ID,
        write_command=WRITE_COMMAND,
        stage_name=STAGE_NAME,
        label=AUTOMATION_LABEL,
        auto_approve=False,
        command_args=command_args,
        review_context={
            "release_owner_approval_required": True,
            "start_date": context.clock.now().date().isoformat(),
            "manager_approved_by": approver,
        },
        pinned_build_id=pinned,
    )
