"""Step: `gate_watch` — approve the Release Orchestrator's parked gate (Phase 4, finalize, F1).

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

from orchestrator.outcomes import Done, NeedsHuman
from steps.finalize._orchestrator_gate import prepare_expected_approval, reconcile_expected_approval
from steps.lib.mockctx import MISSING
from tools import pipelines as P

from orchestrator.authority import WriteOperation

WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)
ID = "gate_watch"
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


def _completed(context):
    injected = context.input("stage_state", MISSING)
    if injected is MISSING:
        ok, stage_state, detail = context.services.pipelines.orchestrator_stage_state(
            CONFIG["org"], CONFIG["project"], context.release.release_id, STAGE
        )
    else:
        ok, stage_state, detail = True, injected, ""
    is_complete = bool(
        stage_state
        and str(stage_state.get("state")).lower() == "completed"
        and str(stage_state.get("result")).lower()
        in ("succeeded", "succeededwithissues")
    )
    return ok, is_complete, detail


def build(context: StepContext):
    ok, info, detail = _pending(context)
    if not ok:
        # can't check right now — surface it; the human can still decide manually.
        return NeedsHuman(
            f"gate_watch: couldn't read the orchestrator gate ({detail}). Check the Release "
            f"Orchestrator run for {context.release.release_id} manually before approving.", attest=False)
    if not info:
        return Done(f"No Release Orchestrator gate is parked for {context.release.release_id} — nothing to "
                    f"approve here (the orchestrator isn't waiting at a manual approval).",
                    links=_links())
    return NeedsHuman(
        f"Release Orchestrator build {info['build_id']} is parked at '{info['stage']}'. "
        f"APPROVING submits the ADO approval, after which the orchestrator automatically "
        f"{CONSEQUENCES} - a real, externally-visible publish. Approve to publish, or deny to hold.",
        attest=False)


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
        default_comment="Approved via Scout (release-agent gate_watch).",
    )


def submit_approval(context: StepContext) -> tuple[bool, str]:
    """Invoke only core's fenced writer for the already-frozen request."""
    return context.approval.submit()


def reconcile_approval(context: StepContext) -> tuple[bool, str]:
    """Read the frozen approval; retain the hold without matching approved evidence."""
    return reconcile_expected_approval(context, expected_stage=STAGE)
