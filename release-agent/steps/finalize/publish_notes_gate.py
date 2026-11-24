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
    approval_matches_stage,
    gate_links,
    pending_approval,
    prepare_expected_approval,
    reconcile_expected_approval,
    stage_state,
)
from tools.pipelines import ORCH_NOTES_STAGE_ID

WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)
ID = "publish_notes_gate"
KIND = "gate"
NAME = "Approve 'Publish GitHub Release Notes' gate"
REQUIRED_TOOLS = ('az',)
APPROVAL_COMMAND = "approve-orchestrator-gate"
STAGE_ID = ORCH_NOTES_STAGE_ID
STAGE = "Publish GitHub Release Notes"
CONFIG = dict(GATE_CONFIG)
CONSEQUENCES = "publishes the GitHub release notes for MSAL and Common"
MOCKABLE = dict(GATE_MOCKABLE)
PARAMETERS = dict(GATE_PARAMETERS)


def _pending(context):
    return pending_approval(context, CONFIG)


def _stage_state(context):
    return stage_state(context, STAGE_ID, CONFIG)


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
    if info and approval_matches_stage(info, STAGE_ID, STAGE):
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
        expected_stage_id=STAGE_ID,
        comment=context.parameters.comment,
        default_comment="Approved via Scout (release-agent publish_notes_gate).",
    )


def submit_approval(context: StepContext) -> tuple[bool, str]:
    """Invoke only core's fenced writer for the already-frozen request."""
    return context.approval.submit()


def reconcile_approval(context: StepContext) -> tuple[bool, str]:
    """Read the frozen approval; retain the hold without matching approved evidence."""
    return reconcile_expected_approval(context, expected_stage=STAGE)

KNOWLEDGE = {'summary': "1-click approve the orchestrator's SECOND gate — 'Publish GitHub Release Notes'.",
 'what': 'After the integration PRs merge, the Release Orchestrator (def 2828) parks at a '
         "SECOND manual approval — the 'Publish GitHub Release Notes' stage. This step is the "
         "human's 1-click approval of that gate: on approval Scout submits the real ADO "
         'pipeline approval, which publishes the GitHub release notes for MSAL and Common. It '
         "is the sibling of remove_rc_tags_gate (the first gate, 'Remove RC Tags') and runs "
         'later in the phase.\n',
 'who': 'The release owner decides (approve / deny). Scout runs the discovery and, on '
        'approval, submits the ADO approval automatically. STAGE-SPECIFIC: it only ever '
        "approves the 'Publish GitHub Release Notes' stage — if the orchestrator is parked at "
        "any other stage the submit is refused, so it can't approve the wrong gate.\n",
 'how': 'Preview `approve-orchestrator-gate --release <id> --preview --comment "<comment>"`. '
        "Show the exact notes-stage approval/build/comment and review hash, obtain the human's "
        'approval, then repeat with the same comment, --review-hash and --approved-by. Deny '
        'with `deny` to HOLD. Pending or unknown evidence remains a hold. Recover an owned '
        'attempt by its --execution-id; never resubmit an attempted approval or replace it '
        "with a newer run's stage completion.\n",
 'consequences': 'Approving publishes the GitHub release notes for MSAL and Common (the '
                 "orchestrator's final publish stage). Make sure the integration PRs are "
                 'merged first.\n',
 'links': [{'name': 'Release Orchestrator pipeline YAML',
            'url': 'https://identitydivision.visualstudio.com/Engineering/_git/AuthClientAndroidPipelines?path=/production/monthly-release/release-orchestrator.yml'}],
 'faqs': [{'q': 'How is this different from remove_rc_tags_gate?',
           'a': "remove_rc_tags_gate approves the FIRST gate ('Remove RC Tags', which "
                'publishes to Maven Central + creates the integration branches). '
                "publish_notes_gate approves the SECOND gate ('Publish GitHub Release Notes'), "
                'which parks only after the integration PRs merge.'},
          {'q': 'What if I approve before the orchestrator reaches the notes gate?',
           'a': "Safe — the gate verifies the 'Publish GitHub Release Notes' stage is the "
                'parked one before submitting the ADO approval. If a different stage is parked '
                '(or none), it refuses to submit and stays holding.'},
          {'q': 'Nothing is parked — is that an error?',
           'a': "No. If the notes stage already completed, the step reports there's nothing to "
                "approve. If it hasn't parked yet, approve once it's waiting."}]}
