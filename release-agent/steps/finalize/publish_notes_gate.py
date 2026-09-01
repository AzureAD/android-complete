"""Step: `publish_notes_gate` — approve the orchestrator's 'Publish GitHub Release Notes' gate
(Phase 4, finalize; checklist Step 4).

The Release Orchestrator parks at a SECOND manual approval — the "Publish GitHub Release Notes"
stage — AFTER the integration PRs are merged (checklist Step 2). Approving it publishes the
GitHub release notes for MSAL and Common. This is the sibling of `gate_watch` (which approves the
first gate, 'Remove RC Tags'), so it runs later in the phase.

Unlike gate_watch, this gate is stage-SPECIFIC: it only ever approves the
'Publish GitHub Release Notes' stage. `submit_approval` refuses to submit if the orchestrator is
parked at any OTHER stage — so it can't accidentally approve an earlier gate.

  * build() shows the gate brief; if the notes stage has already completed it reports Done.
  * submit_approval(state, comment) — called by `approve-orchestrator-gate` when the human
    approves — re-discovers the pending approval, VERIFIES it's the notes stage, and submits it.

Mock knobs (mocks.local.yaml / tests):
  approval    : inject the pending-approval info {approval_id,build_id,stage,build_url} or None.
  stage_state : inject the notes-stage state {state,result} or None (skip the timeline read).
  submit      : 'skip' → do NOT submit the live ADO approval (offline/tests).
"""
from __future__ import annotations

from orchestrator.outcomes import Done, NeedsHuman
from steps.lib.mockctx import mock_input, MISSING
from tools import pipelines as P

ID = "publish_notes_gate"
KIND = "gate"

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
    "submit": {"kind": "input", "desc": "'skip' -> don't submit the live ADO approval (tests)."},
}


def _pending(state):
    inj = mock_input("approval", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    return P.find_orchestrator_pending_approval(CONFIG["org"], CONFIG["project"], state.release_id)


def _stage_state(state):
    inj = mock_input("stage_state", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    return P.orchestrator_stage_state(CONFIG["org"], CONFIG["project"], state.release_id, STAGE)


def _links(info=None):
    lk = [{"name": "Release Orchestrator YAML", "url": CONFIG["pipeline_ref"]}]
    if info and info.get("build_url"):
        lk.insert(0, {"name": f"Orchestrator build {info['build_id']}", "url": info["build_url"]})
    return lk


def build(state):
    # Already published? (the notes stage completed) → nothing to approve.
    oks, ss, _ds = _stage_state(state)
    if oks and ss and str(ss.get("state")).lower() == "completed" \
            and str(ss.get("result")).lower() in ("succeeded", "succeededwithissues"):
        return Done(f"'{STAGE}' already completed for {state.release_id} — GitHub release notes "
                    f"are published; nothing to approve.", links=_links())

    ok, info, detail = _pending(state)
    if not ok:
        return NeedsHuman(
            f"publish_notes_gate: couldn't read the orchestrator gate ({detail}). Check the "
            f"Release Orchestrator run for {state.release_id} manually before approving.",
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
        f"The '{STAGE}' gate isn't parked yet for {state.release_id} — the orchestrator reaches "
        f"it after the integration PRs merge. Approve once it's parked (this gate verifies the "
        f"stage before submitting, so approving early is safe — it just won't submit yet).",
        attest=False)


def submit_approval(state, comment=""):
    """Submit the real ADO 'Publish GitHub Release Notes' approval. Called by
    `approve-orchestrator-gate` when the human approves. Refuses if the orchestrator is parked at
    any other stage (so it can't approve the wrong gate). Returns (ok, detail)."""
    if str(mock_input("submit", "")).lower() == "skip":
        return (True, "ADO approval submit skipped (mock).")
    ok, info, detail = _pending(state)
    if not ok:
        return (False, f"couldn't locate the orchestrator approval ({detail}).")
    if not info:
        return (False, f"no pending orchestrator approval — the '{STAGE}' gate isn't parked yet "
                       f"(it parks after the integration PRs merge). Try again once it's waiting.")
    if info.get("stage") != STAGE:
        return (False, f"the orchestrator is parked at '{info['stage']}', not '{STAGE}' — NOT "
                       f"approving. Resolve that gate first.")
    oks, ds = P.submit_pipeline_approval(
        CONFIG["org"], CONFIG["project"], info["approval_id"],
        comment or "Approved via Scout (release-agent publish_notes_gate).")
    if not oks:
        return (False, f"submitting the '{STAGE}' approval on build {info['build_id']} FAILED "
                       f"({ds}) - the ADO gate is NOT approved.")
    return (True, f"submitted the '{STAGE}' approval on Release Orchestrator build "
                  f"{info['build_id']} - the GitHub release notes will now publish.")
