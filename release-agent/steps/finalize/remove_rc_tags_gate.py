"""Step: `remove_rc_tags_gate` — approve the Release Orchestrator's 'Remove RC Tags' gate."""
from __future__ import annotations

from orchestrator.authority import WriteOperation
from orchestrator.outcomes import Blocked, Done, NeedsHuman
from orchestrator.step_context import StepContext
from steps.finalize._orchestrator_gate import (
    ApprovalParameters,
    GATE_CONFIG,
    GATE_MOCKABLE,
    GATE_PARAMETERS,
    gate_links,
    pending_approval,
    prepare_expected_approval,
    reconcile_expected_approval,
    stage_state,
)

WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)
ID = "remove_rc_tags_gate"
KIND = "gate"
STAGE = "Remove RC Tags"
APPROVAL_COMMAND = "approve-orchestrator-gate"
CONFIG = dict(GATE_CONFIG)
CONSEQUENCES = ("removes RC tags, publishes internal artifacts to the ADO Maven feed, publishes "
                "MSAL/Common to Maven Central, creates release-integration/<version> branches, "
                "updates the orchestrator pipeline variables, and prints GitHub PR compare links")
MOCKABLE = dict(GATE_MOCKABLE)
PARAMETERS = dict(GATE_PARAMETERS)


def _pending(context):
    return pending_approval(context, CONFIG)


def _links(info=None):
    return gate_links(info, CONFIG)


def _stage_state(context):
    return stage_state(context, STAGE, CONFIG)


def build(context: StepContext):
    approval_ok, info, approval_detail = _pending(context)
    if approval_ok and info and info.get("stage") == STAGE:
        return NeedsHuman(
            f"Release Orchestrator build {info['build_id']} is parked at '{STAGE}'. "
            f"APPROVING submits the ADO approval, after which the orchestrator automatically "
            f"{CONSEQUENCES} - a real, externally-visible publish. Approve to publish, or deny to hold.",
            attest=False,
        )
    if approval_ok and info:
        return NeedsHuman(
            f"The Release Orchestrator is parked at '{info.get('stage')}', not '{STAGE}'. "
            f"This step only approves '{STAGE}' and will not submit another gate. "
            "Inspect the orchestrator state before continuing.",
            attest=False,
        )

    stage_ok, stage_state, stage_detail = _stage_state(context)
    if not stage_ok:
        return NeedsHuman(
            f"remove_rc_tags_gate: couldn't read the approval "
            f"({approval_detail or 'no pending approval visible'}) or the '{STAGE}' stage "
            f"({stage_detail}). "
            "Inspect the Release Orchestrator run and retry; this gate will not be skipped.",
            attest=False,
        )
    if stage_state and str(stage_state.get("state")).lower() == "completed":
        result = str(stage_state.get("result") or "").lower()
        if result in ("succeeded", "succeededwithissues"):
            return Done(
                f"'{STAGE}' already completed successfully for "
                f"{context.release.release_id} — nothing remains to approve.",
                links=_links(),
            )
        return Blocked(
            f"'{STAGE}' completed with result {stage_state.get('result')!r}; "
            "investigate the Release Orchestrator before continuing.",
            links=_links(),
        )

    if not approval_ok:
        return NeedsHuman(
            f"remove_rc_tags_gate: couldn't read the orchestrator approval ({approval_detail}). "
            "Inspect the Release Orchestrator run and retry; this gate will not be skipped.",
            attest=False,
        )
    state = str((stage_state or {}).get("state") or "not reached")
    return NeedsHuman(
        f"The '{STAGE}' gate isn't parked yet for {context.release.release_id} "
        f"(stage state: {state}). Retry after the Release Orchestrator reaches it; "
        "this step remains incomplete and cannot be approved early.",
        attest=False,
    )


def prepare_approval(context: StepContext[ApprovalParameters]):
    """Freeze the exact stage/approval/build and comment without submitting."""
    return prepare_expected_approval(
        context,
        pending=_pending,
        config=CONFIG,
        expected_stage=STAGE,
        comment=context.parameters.comment,
        default_comment="Approved via Scout (release-agent remove_rc_tags_gate).",
    )


def submit_approval(context: StepContext) -> tuple[bool, str]:
    """Invoke only core's fenced writer for the already-frozen request."""
    return context.approval.submit()


def reconcile_approval(context: StepContext) -> tuple[bool, str]:
    """Read the frozen approval; retain the hold without matching approved evidence."""
    return reconcile_expected_approval(context, expected_stage=STAGE)
