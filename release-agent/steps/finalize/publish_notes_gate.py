"""Step: `publish_notes_gate` — approve the orchestrator's 'Publish GitHub Release Notes' gate."""
from __future__ import annotations

from orchestrator.authority import WriteOperation
from orchestrator.outcomes import Done, NeedsHuman
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
ID = "publish_notes_gate"
KIND = "gate"
APPROVAL_COMMAND = "approve-orchestrator-gate"
STAGE = "Publish GitHub Release Notes"
CONFIG = dict(GATE_CONFIG)
CONSEQUENCES = "publishes the GitHub release notes for MSAL and Common"
MOCKABLE = dict(GATE_MOCKABLE)
PARAMETERS = dict(GATE_PARAMETERS)


def _pending(context):
    return pending_approval(context, CONFIG)


def _stage_state(context):
    return stage_state(context, STAGE, CONFIG)


def _links(info=None):
    return gate_links(info, CONFIG)


def build(context: StepContext):
    # Already published? (the notes stage completed) → nothing to approve.
    oks, ss, _ds = _stage_state(context)
    if oks and ss and str(ss.get("state")).lower() == "completed" \
            and str(ss.get("result")).lower() in ("succeeded", "succeededwithissues"):
        return Done(f"'{STAGE}' already completed for {context.release.release_id} — GitHub release notes "
                    f"are published; nothing to approve.", links=_links())

    ok, info, detail = _pending(context)
    if not ok:
        return NeedsHuman(
            f"publish_notes_gate: couldn't read the orchestrator gate ({detail}). Check the "
            f"Release Orchestrator run for {context.release.release_id} manually before approving.",
            attest=False)
    if info and info.get("stage") == STAGE:
        return NeedsHuman(
            f"Release Orchestrator build {info['build_id']} is parked at '{STAGE}'. APPROVING "
            f"submits the ADO approval, after which the orchestrator {CONSEQUENCES}. Make sure "
            f"the integration PRs are merged first. Approve to publish the notes, or deny to hold.",
            attest=False)
    if info:
        # parked, but at a DIFFERENT stage (e.g. the earlier 'Remove RC Tags' gate).
        return NeedsHuman(
            f"The Release Orchestrator is parked at '{info['stage']}', NOT the '{STAGE}' gate. "
            f"Resolve that gate first (and make sure the integration PRs are merged); this gate "
            f"only approves '{STAGE}'. It will not submit until the notes stage is the parked one.",
            attest=False)
    # nothing parked yet — the orchestrator hasn't reached the notes gate.
    return NeedsHuman(
        f"The '{STAGE}' gate isn't parked yet for {context.release.release_id} — the orchestrator reaches "
        f"it after the integration PRs merge. Approve once it's parked (this gate verifies the "
        f"stage before submitting, so approving early is safe — it just won't submit yet).",
        attest=False)


def prepare_approval(context: StepContext[ApprovalParameters]):
    """Freeze the exact notes-stage approval and comment without submitting."""
    return prepare_expected_approval(
        context,
        pending=_pending,
        config=CONFIG,
        expected_stage=STAGE,
        comment=context.parameters.comment,
        default_comment="Approved via Scout (release-agent publish_notes_gate).",
    )


def submit_approval(context: StepContext) -> tuple[bool, str]:
    """Invoke only core's fenced writer for the already-frozen request."""
    return context.approval.submit()


def reconcile_approval(context: StepContext) -> tuple[bool, str]:
    """Read the frozen approval; retain the hold without matching approved evidence."""
    return reconcile_expected_approval(context, expected_stage=STAGE)
