"""Step: `remove_rc_tags_gate` — approve the Release Orchestrator's first gate.

After Phase 3, the Release Orchestrator (def 2828) runs and PARKS at a manual approval on the
"Remove RC Tags" stage. This gate is the human's 1-click approval of that: when the release owner
approves this gate, Scout submits the real ADO pipeline approval, which advances the orchestrator
into the PUBLISH stages.

CONSEQUENCES of approving — the orchestrator then automatically:
  • removes the RC tags,
  • publishes internal artifacts to the ADO Maven feed,
  • publishes MSAL/Common to Maven Central,
  • creates release-integration/<version> branches,
  • updates Release Orchestrator pipeline variables (MSAL-PROD-Version, MSAL-PROD-BRANCH,
    Broker-PROD-Version),
  • prints GitHub PR compare links in the next stage.
This is a real, externally-visible publish — deny to HOLD if anything looks wrong.

Mechanics: `build(context)` discovers whether the orchestrator is parked (and on which build/stage)
so the gate brief can show it. `prepare_approval(context)` freezes the exact Remove RC Tags
approval id, owner build, stage, coordinates, and comment. Missing or different-stage identities
block preparation. Core persists/authorizes that request before `submit_approval(context)`
invokes its fenced writer once. `reconcile_approval(context)` reads ONLY the persisted approval
id and accepts matching approved evidence on its original build; it never discovers a newer run
or resubmits. Stage completion alone cannot settle an already-owned approval execution.

Build-preview / directly injected unit-test knobs (mocks.local.yaml / tests):
  approval : inject the pending-approval info {approval_id,build_id,stage,build_url} or None.
  stage_state : legacy build-preview input {state,result} or None; never recovery evidence.
  approval_state : exact provider object {id,status,pipeline:{owner:{_links:{web:{href}}}}}
                   or None for read-only reconciliation; href contains the owner buildId.
  submit   : legacy 'skip' is rejected during preparation, never a success or provider receipt.
Production rejects ANY nonempty gate mocks before approval preview, submission, or reconciliation.
BUILD previews keep their mock inputs. Offline lifecycle tests inject a fake approval writer/
read service directly; approval inputs alone do not mock IO or authorize provider actions.
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext

from orchestrator.outcomes import Blocked, Done, NeedsHuman
from steps.finalize._orchestrator_gate import prepare_expected_approval, reconcile_expected_approval
from steps.lib.mockctx import MISSING
from tools import pipelines as P

from orchestrator.authority import WriteOperation

WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)
ID = "remove_rc_tags_gate"
KIND = "gate"
STAGE = "Remove RC Tags"
APPROVAL_COMMAND = "approve-orchestrator-gate"

CONFIG = {
    "org": P.ENGINEERING_ORG,
    "project": P.ENGINEERING_PROJECT,
    "pipeline_ref": ("https://identitydivision.visualstudio.com/Engineering/_git/"
                     "AuthClientAndroidPipelines?path=/production/monthly-release/"
                     "release-orchestrator.yml"),
}

# One-line consequence summary reused in the gate brief.
CONSEQUENCES = ("removes RC tags, publishes internal artifacts to the ADO Maven feed, publishes "
                "MSAL/Common to Maven Central, creates release-integration/<version> branches, "
                "updates the orchestrator pipeline variables, and prints GitHub PR compare links")

MOCKABLE = {
    "approval": {"kind": "input", "desc": "Inject pending-approval info {approval_id,build_id,stage,build_url} or None."},
    "stage_state": {"kind": "input", "desc": "Inject the Remove RC Tags stage state {state,result} or None."},
    "approval_state": {"kind": "input", "desc": "Exact approval {id,status,pipeline.owner._links.web.href} or None for read-only reconciliation."},
    "submit": {"kind": "input", "desc": "Legacy 'skip' is rejected for approval lifecycle; offline tests inject fake provider ports."},
}


def _pending(context):
    """(ok, info|None, detail) — the orchestrator's pending approval for this release."""
    inj = context.input("approval", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    return context.services.pipelines.find_orchestrator_pending_approval(CONFIG["org"], CONFIG["project"], context.release.release_id)


def _links(info=None):
    lk = [{"name": "Release Orchestrator YAML", "url": CONFIG["pipeline_ref"]}]
    if info and info.get("build_url"):
        lk.insert(0, {"name": f"Orchestrator build {info['build_id']}", "url": info["build_url"]})
    return lk


def _stage_state(context):
    injected = context.input("stage_state", MISSING)
    if injected is MISSING:
        return context.services.pipelines.orchestrator_stage_state(
            CONFIG["org"], CONFIG["project"], context.release.release_id, STAGE
        )
    return True, injected, ""


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


@dataclass(frozen=True)
class ApprovalParameters:
    comment: str = ""


PARAMETERS = {"prepare_approval": ApprovalParameters}


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
