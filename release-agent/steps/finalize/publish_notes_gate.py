"""Step: `publish_notes_gate` — approve the orchestrator's 'Publish GitHub Release Notes' gate
(Phase 4, finalize; checklist Step 4).

The Release Orchestrator parks at a SECOND manual approval — the "Publish GitHub Release Notes"
stage — AFTER the integration PRs are merged (checklist Step 2). Approving it publishes the
GitHub release notes for MSAL and Common. This is the sibling of `remove_rc_tags_gate` (which approves the
first gate, 'Remove RC Tags'), so it runs later in the phase.

Like remove_rc_tags_gate, this gate is stage-SPECIFIC: it only ever prepares an approval for the
'Publish GitHub Release Notes' stage. It refuses other parked stages, so it cannot approve an
earlier gate. Once core persists that approval identity, a newer run cannot replace it.

  * build() shows the gate brief; if the notes stage has already completed it reports Done.
  * prepare_approval(context) freezes the exact pending approval id, build owner, stage,
    coordinates, and comment before core authorizes a submission.
  * submit_approval(context) invokes core's fenced writer once, using only that frozen request.
  * reconcile_approval(context) reads the exact persisted approval, requiring matching id,
    build owner, and approved status. It never resubmits or treats stage completion as a receipt.

Build-preview / directly injected unit-test knobs (mocks.local.yaml / tests):
  approval    : inject the pending-approval info {approval_id,build_id,stage,build_url} or None.
  stage_state : inject the notes-stage state {state,result} or None (skip the timeline read).
  approval_state : exact provider object {id,status,pipeline:{owner:{_links:{web:{href}}}}}
                   or None for read-only reconciliation; href contains the owner buildId.
  submit      : legacy 'skip' is rejected during preparation, never a success or provider receipt.
Production rejects ANY nonempty gate mocks before approval preview, submission, or reconciliation.
BUILD previews keep their mock inputs. Offline lifecycle tests inject a fake approval writer/
read service directly; approval inputs alone do not mock IO or authorize provider actions.
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext

from orchestrator.outcomes import Done, NeedsHuman
from steps.finalize._orchestrator_gate import prepare_expected_approval, reconcile_expected_approval
from steps.lib.mockctx import MISSING
from tools import pipelines as P

from orchestrator.authority import WriteOperation

WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)
ID = "publish_notes_gate"
KIND = "gate"
APPROVAL_COMMAND = "approve-orchestrator-gate"

STAGE = "Publish GitHub Release Notes"     # the exact orchestrator stage name (def 2828)

CONFIG = {
    "org": P.ENGINEERING_ORG,
    "project": P.ENGINEERING_PROJECT,
    "pipeline_ref": ("https://identitydivision.visualstudio.com/Engineering/_git/"
                     "AuthClientAndroidPipelines?path=/production/monthly-release/"
                     "release-orchestrator.yml"),
}

CONSEQUENCES = "publishes the GitHub release notes for MSAL and Common"

MOCKABLE = {
    "approval": {"kind": "input", "desc": "Inject pending-approval info {approval_id,build_id,stage,build_url} or None."},
    "stage_state": {"kind": "input", "desc": "Inject the notes-stage state {state,result} or None (skip timeline read)."},
    "approval_state": {"kind": "input", "desc": "Exact approval {id,status,pipeline.owner._links.web.href} or None for read-only reconciliation."},
    "submit": {"kind": "input", "desc": "Legacy 'skip' is rejected for approval lifecycle; offline tests inject fake provider ports."},
}


def _pending(context):
    inj = context.input("approval", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    return context.services.pipelines.find_orchestrator_pending_approval(CONFIG["org"], CONFIG["project"], context.release.release_id)


def _stage_state(context):
    inj = context.input("stage_state", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    return context.services.pipelines.orchestrator_stage_state(CONFIG["org"], CONFIG["project"], context.release.release_id, STAGE)


def _completed(context):
    ok, stage_state, detail = _stage_state(context)
    is_complete = bool(
        stage_state
        and str(stage_state.get("state")).lower() == "completed"
        and str(stage_state.get("result")).lower()
        in ("succeeded", "succeededwithissues")
    )
    return ok, is_complete, detail


def _links(info=None):
    lk = [{"name": "Release Orchestrator YAML", "url": CONFIG["pipeline_ref"]}]
    if info and info.get("build_url"):
        lk.insert(0, {"name": f"Orchestrator build {info['build_id']}", "url": info["build_url"]})
    return lk


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


@dataclass(frozen=True)
class ApprovalParameters:
    comment: str = ""


PARAMETERS = {"prepare_approval": ApprovalParameters}


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
