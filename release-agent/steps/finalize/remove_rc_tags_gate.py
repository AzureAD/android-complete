"""Step: `remove_rc_tags_gate` — approve the Release Orchestrator's 'Remove RC Tags' gate."""
from __future__ import annotations

from orchestrator.authority import WriteOperation
from orchestrator.outcomes import Blocked, Done, NeedsHuman
from orchestrator.step_context import StepContext
from steps.finalize._orchestrator_gate import (
    ApprovalParameters,
    GATE_CONFIG,
    GATE_PARAMETERS,
    approval_matches_stage,
    gate_links,
    pending_approval,
    prepare_expected_approval,
    reconcile_expected_approval,
    stage_state,
)
from tools.pipelines import ORCH_PARK_STAGE_ID

WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)
ID = "remove_rc_tags_gate"
KIND = "gate"
NAME = "Approve 'Remove RC Tags' gate"
REQUIRED_TOOLS = ('az',)
STAGE_ID = ORCH_PARK_STAGE_ID
STAGE = "Remove RC Tags"
APPROVAL_COMMAND = "approve-orchestrator-gate"
CONFIG = dict(GATE_CONFIG)
CONSEQUENCES = ("removes RC tags, publishes internal artifacts to the ADO Maven feed, publishes "
                "MSAL/Common to Maven Central, creates release-integration/<version> branches, "
                "updates the orchestrator pipeline variables, and prints GitHub PR compare links")
PARAMETERS = dict(GATE_PARAMETERS)

def _pending(context):
    return pending_approval(context, CONFIG)

def _links(info=None):
    return gate_links(info, CONFIG)

def _stage_state(context):
    return stage_state(context, STAGE_ID, CONFIG)

def build(context: StepContext):
    approval_ok, info, approval_detail = _pending(context)
    if approval_ok and info and approval_matches_stage(info, STAGE_ID, STAGE):
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
        expected_stage_id=STAGE_ID,
        comment=context.parameters.comment,
        default_comment="Approved via Scout (release-agent remove_rc_tags_gate).",
    )

def submit_approval(context: StepContext) -> tuple[bool, str]:
    """Invoke only core's fenced writer for the already-frozen request."""
    return context.approval.submit()

def reconcile_approval(context: StepContext) -> tuple[bool, str]:
    """Read the frozen approval; retain the hold without matching approved evidence."""
    return reconcile_expected_approval(context, expected_stage=STAGE)

KNOWLEDGE = {'summary': "1-click approve the Release Orchestrator's parked 'Remove RC Tags' gate — "
            'triggers publish.',
 'what': 'After Phase 3, the Release Orchestrator (def 2828) runs and PARKS at a manual '
         "approval on its 'Remove RC Tags' stage. This step is the human's 1-click approval of "
         'that gate: Scout finds the orchestrator run for the release, detects the parked '
         'stage + its pending approval, and shows a brief. When the release owner APPROVES, '
         'Scout submits the real ADO pipeline approval, which un-parks the orchestrator into '
         'the publish stages.\n',
 'who': 'The release owner decides (approve / deny). Scout runs the discovery and, on '
        'approval, submits the ADO approval automatically via the pipeline approvals API — no '
        'manual portal click needed.\n',
 'how': 'Preview `approve-orchestrator-gate --release <id> --preview --comment "<comment>"`. '
        "Show the exact approval/build/stage/comment and review hash, obtain the human's "
        'decision, then submit with the same comment, --review-hash and --approved-by. Deny '
        'with `deny` to HOLD. An interrupted or uncertain owned attempt requires its original '
        '--execution-id for read-only reconciliation, never another submission. Only exact '
        'approved provider evidence completes it; no pending approval, a completed stage or a '
        'newer run cannot fabricate a receipt.\n',
 'consequences': "Approving the 'Remove RC Tags' gate makes the Release Orchestrator "
                 'automatically: remove the RC tags; publish internal artifacts to the ADO '
                 'Maven feed; publish MSAL/Common to Maven Central; create '
                 'release-integration/<version> branches; update the orchestrator pipeline '
                 'variables (MSAL-PROD-Version, MSAL-PROD-BRANCH, Broker-PROD-Version); and '
                 'print GitHub PR compare links in the next stage. This is a REAL, '
                 'externally-visible publish (Maven Central + GitHub) — not easily undone. '
                 'Deny and investigate if anything looks off.\n',
 'links': [{'name': 'Release Orchestrator pipeline YAML',
            'url': 'https://identitydivision.visualstudio.com/Engineering/_git/AuthClientAndroidPipelines?path=/production/monthly-release/release-orchestrator.yml'}],
 'faqs': [{'q': 'What exactly happens when I approve?',
           'a': "Scout submits the ADO approval on the orchestrator's 'Remove RC Tags' stage. "
                'The orchestrator then removes RC tags and publishes: internal artifacts to '
                'the ADO Maven feed, MSAL/Common to Maven Central, creates '
                'release-integration/<version> branches, updates the pipeline variables '
                '(MSAL-PROD-Version, MSAL-PROD-BRANCH, Broker-PROD-Version), and prints GitHub '
                'PR compare links.'},
          {'q': 'Is it reversible?',
           'a': 'No — it publishes to Maven Central and GitHub. Treat approval as the point of '
                'no return; deny to hold if the RC verification or bug bash left anything '
                'unresolved.'},
          {'q': 'Nothing is parked — is that an error?',
           'a': 'Inspect the exact stage. A successful already-completed stage can be shown as '
                'nothing to approve, but an owned execution still needs its frozen approval '
                'receipt. If the stage has not parked, hold; never infer approval or advance '
                'early.'}]}
