"""Step: `beta_play_store` — owner-approved 100% Beta Play Store stage start."""
from __future__ import annotations

from dataclasses import dataclass

from orchestrator.outcomes import Blocked
from orchestrator.step_context import StepContext
from steps.rollout_start import release_stage
from tools import pipelines as P

ID = "beta_play_store"
KIND = "scout"
NAME = 'Start 100% Beta - Play Store (release-owner approval)'
REQUIRED_TOOLS = ('az',)
REQUIRES_APPROVAL = True
WRITE_COMMAND = "start-beta-play-store"
STAGE_ID = P.AUTH_BETA_STAGE_ID
STAGE_NAME = P.AUTH_BETA_STAGE_NAME
AUTOMATION_LABEL = "beta-play-store"
REQUIRES_OWNER_APPROVAL = True
PREVIOUS_STEP_ID = "upload_whats_new"
PREVIOUS_LABEL = "Upload What's New"

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
        stage_id=STAGE_ID,
        previous_step_id=PREVIOUS_STEP_ID,
        previous_label=PREVIOUS_LABEL,
        require_pin_in_resolve=True,
        for_step_id=ID,
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
        stage_id=STAGE_ID,
        label=AUTOMATION_LABEL,
        previous_step_id=PREVIOUS_STEP_ID,
        previous_label=PREVIOUS_LABEL,
        auto_approve=False,
        command_args=command_args,
        review_context={
            "release_owner_approval_required": True,
            "start_date": context.clock.now().date().isoformat(),
            "manager_approved_by": approver,
        },
    )

KNOWLEDGE = {'summary': 'Start 100% Beta - Play Store with release-owner approval.',
 'what': 'Locates the matching Android Build Release run in msazure/One pipeline 397224 and '
         'prepares the exact `100% Beta - Play Store` stage start. Unlike the earlier Phase-5 '
         'stage starts, this write is never automation-approved: the release owner must review '
         'and approve its exact build, stage and review hash.\n',
 'who': 'The release owner approves the exact checked write. Scout may execute it only when '
        '`--approved-by` exactly matches `state.owner_email` and that owner is the account '
        'currently signed in to Azure CLI. On Friday, the release must remain held unless a '
        'manager has explicitly approved the Friday override and the authenticated release '
        'owner attests who approved it. Manager identity is not independently authenticated.\n',
 'where': ['pipeline: msazure/One Android Build Release definition 397224',
           'stage: 100% Beta - Play Store',
           "release branch: state.versions.authenticator ('release/YYYY/MM/DD')"],
 'how': 'Run step-action for rollout_start.beta_play_store. On Monday through Thursday, '
        'preview `start-beta-play-store --release <id>`, show the exact request and hash to '
        'the release owner, and execute only after approval with the same hash and '
        '`--approved-by <owner-email>`. On Friday, tell the owner that releases cannot start '
        'on Friday. If they have manager approval, record the manager identity through '
        '`--param manager_approved_by=<manager>` and repeat it as `--manager-approved-by '
        '<manager>` in both preview and execution. Never use `--auto-approve` or `--as-of` for '
        'execution. The target is pinned to the exact pipeline-397224 build recorded by the '
        'completed Upload Alpha step.\n',
 'consequences': 'A real WRITE to Azure DevOps starts the 100% Beta - Play Store stage. The '
                 'reviewed plan binds the owner-local start date and any Friday manager '
                 'override evidence.\n',
 'faqs': [{'q': 'Can I start this stage on Friday?',
           'a': 'Not by default. Keep the release held; only proceed after explicit manager '
                'approval, with the manager identity bound into the reviewed request.'}]}
