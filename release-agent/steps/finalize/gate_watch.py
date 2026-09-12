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

Mechanics: `build(state)` discovers whether the orchestrator is parked (and on which build/stage)
so the gate brief can show it. `submit_approval(state, comment)` is called by the
`approve-orchestrator-gate` CLI command WHEN the human approves this gate — it re-discovers the
pending approval and submits it via the pipeline approvals API, then the command records the
release-agent gate. The engine is untouched; the command composes submit + the normal `approve`.
Deterministic and best-effort (if nothing is parked it no-ops).

Mock knobs (mocks.local.yaml / tests):
  approval : inject the pending-approval info {approval_id,build_id,stage,build_url} or None.
  submit   : 'skip' → do NOT submit the live ADO approval (offline/tests).
"""
from __future__ import annotations

from orchestrator.outcomes import Done, NeedsHuman
from steps.lib.mockctx import mock_input, MISSING
from tools import pipelines as P

ID = "gate_watch"
KIND = "gate"

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
    "submit": {"kind": "input", "desc": "'skip' -> don't submit the live ADO approval (tests)."},
}


def _pending(state):
    """(ok, info|None, detail) — the orchestrator's pending approval for this release."""
    inj = mock_input("approval", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    return P.find_orchestrator_pending_approval(CONFIG["org"], CONFIG["project"], state.release_id)


def _links(info=None):
    lk = [{"name": "Release Orchestrator YAML", "url": CONFIG["pipeline_ref"]}]
    if info and info.get("build_url"):
        lk.insert(0, {"name": f"Orchestrator build {info['build_id']}", "url": info["build_url"]})
    return lk


def build(state):
    ok, info, detail = _pending(state)
    if not ok:
        # can't check right now — surface it; the human can still decide manually.
        return NeedsHuman(
            f"gate_watch: couldn't read the orchestrator gate ({detail}). Check the Release "
            f"Orchestrator run for {state.release_id} manually before approving.", attest=False)
    if not info:
        return Done(f"No Release Orchestrator gate is parked for {state.release_id} — nothing to "
                    f"approve here (the orchestrator isn't waiting at a manual approval).",
                    links=_links())
    return NeedsHuman(
        f"Release Orchestrator build {info['build_id']} is parked at '{info['stage']}'. "
        f"APPROVING submits the ADO approval, after which the orchestrator automatically "
        f"{CONSEQUENCES} - a real, externally-visible publish. Approve to publish, or deny to hold.",
        attest=False)


def submit_approval(state, comment=""):
    """Submit the real ADO orchestrator approval for this release. Called by the
    `approve-orchestrator-gate` command when the human approves the gate. Returns (ok, detail).
    Best-effort: no pending approval -> no-op success."""
    if str(mock_input("submit", "")).lower() == "skip":
        return (True, "ADO approval submit skipped (mock).")
    ok, info, detail = _pending(state)
    if not ok:
        return (False, f"couldn't locate the orchestrator approval ({detail}).")
    if not info:
        return (True, "no pending orchestrator approval to submit (already approved / not parked).")
    oks, ds = P.submit_pipeline_approval(
        CONFIG["org"], CONFIG["project"], info["approval_id"],
        comment or "Approved via Scout (release-agent gate_watch).")
    if not oks:
        return (False, f"submitting the '{info['stage']}' approval on build "
                       f"{info['build_id']} FAILED ({ds}) - the ADO gate is NOT approved.")
    return (True, f"submitted the '{info['stage']}' approval on Release Orchestrator build "
                  f"{info['build_id']} - publish stages will now run.")

