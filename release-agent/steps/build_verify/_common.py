"""Shared config + helpers for the Phase 2 (build_verify) release-verification steps.

Underscore-prefixed so steps.discover() skips it (it's not a step). Holds the ADO
coordinates for the three Engineering release pipelines and the recovery / escalation
links surfaced when a step blocks, plus small resolvers the step modules reuse.
"""
from __future__ import annotations

ORG = "https://identitydivision.visualstudio.com"
PROJECT = "Engineering"

CHECKER_DEF = 3038          # Code Complete Calendar Checker (fires the release on the CCD)
ORCHESTRATOR_DEF = 2828     # Release Orchestrator (the spine)
MRWP_DEF = 2519             # Monthly Release Work Pipeline (RC testing; runs ECS + Local)

# The orchestrator stages that must be green before RC testing is trustworthy, and the
# stage it should be PARKED at (a human approval gate the owner clears in a later phase).
ORCH_REQUIRED_STAGES = [
    "Validate Branch and Versions availability",
    "Create Release Branches",
    "Trigger RC Testing",
]
ORCH_PARK_STAGE = "Remove RC Tags"

# Surfaced in every block reason so the engineer knows how to recover / escalate.
RECOVERY_TSG = ("https://eng.ms/docs/microsoft-security/identity/"
                "entra-developer-application-platform/auth-client/"
                "authn-sdk-msal-android/android-auth-libraries/releases/"
                "internal-release-checklist/release-orchestrator-recovery")
ESCALATION_CHAT = ("https://teams.microsoft.com/l/chat/"
                   "19:976a859f167f44e59c4ceca8b1d23581@thread.v2/conversations")

# Standard help tail appended to orchestrator/MRWP block reasons.
UNBLOCK_HELP = (
    "\n→ Each failed stage's output describes the root cause + corrective action. "
    "Follow it, then click Retry on the failed stage. Recovery TSG: "
    f"{RECOVERY_TSG} . If unresolved within 2h, escalate: {ESCALATION_CHAT}")


def build_url(build_id):
    return f"{ORG}/{PROJECT}/_build/results?buildId={build_id}"


def links_for(build_id, name="ADO run"):
    return [{"name": name, "url": build_url(build_id)}]


def stash_runs(state, **ids):
    """Record resolved pipeline run ids on state.pipeline_runs (drop None values),
    stamped with resolved_at. Called each time Phase 2 resolves the chain so the ids
    are in state (for status details + the digest). Re-resolved on every pass, so a
    re-triggered MRWP run (new id) overwrites the old one."""
    from datetime import datetime, timezone
    pr = dict(getattr(state, "pipeline_runs", {}) or {})
    for k, v in ids.items():
        if v is not None:
            pr[k] = str(v)
    pr["resolved_at"] = datetime.now(timezone.utc).isoformat()
    state.pipeline_runs = pr


def verify_mrwp(state, provider):
    """Shared body for the mrwp_ecs / mrwp_local steps. `provider` is 'ECS' or 'Local'.

    Resolves this release's MRWP (def 2519) run for the provider — from the orchestrator
    run's RC-<provider>=<id> tag, or a log-parse fallback — then applies the release
    stage-completion rule (every stage must have executed; skipped/canceled/pending =
    block) and attaches the Test-tab summary. Uses the step's mock knobs when present:
      mrwp_id : inject the MRWP build id (skip the orchestrator lookup)
      stages  : inject the stage list [{name,state,result}]
      tests   : inject the test summary {total,passed,failed[,runs]}
    Returns a Done/Blocked outcome.
    """
    from orchestrator.outcomes import Done, Blocked
    from steps.lib.mockctx import mock_input, MISSING
    from tools import pipelines as P

    label = f"MRWP {provider}"
    # 1) resolve the MRWP build id for this provider
    mid = mock_input("mrwp_id", MISSING)
    if mid is MISSING:
        ok, run, detail = P.find_orchestrator_run(ORG, PROJECT, ORCHESTRATOR_DEF, state.release_id)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read orchestrator run ({detail}){hint}.")
        if not run:
            return Blocked(
                f"{label}: no orchestrator run found for {state.release_id} — can't locate "
                f"the RC-testing runs. Verify the orchestrator first.")
        ok2, ids, detail2, source = P.mrwp_run_ids(ORG, PROJECT, run)
        if not ok2:
            hint = " — run `az login`" if str(detail2).startswith("AUTH") else ""
            return Blocked(
                f"{label}: could not resolve the MRWP run id ({detail2}){hint}.",
                links=links_for(run.get("id"), "Release Orchestrator run"))
        mid = ids.get(provider)
        if not mid:
            return Blocked(f"{label}: orchestrator didn't record a {provider} RC-testing run.")

    links = links_for(mid, f"{label} run")

    # 2) stage-completion rule
    stages = mock_input("stages", MISSING)
    if stages is MISSING:
        ok, stages, detail = P.get_stages(ORG, PROJECT, mid)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read stages for run {mid} ({detail}){hint}.", links=links)
    comp = P.stage_completion(stages)
    if not comp["complete"]:
        never = ", ".join(n for n in comp["never_ran"] if n) or "(unknown)"
        return Blocked(
            f"{label} run {mid} did NOT run to completion — {len(comp['never_ran'])} stage(s) "
            f"never ran (pending/skipped/canceled): {never}. A stage that never ran means the "
            f"pipeline aborted partway.{UNBLOCK_HELP}", links=links)

    # 3) test summary (best-effort — never blocks; red/yellow tests are triaged later)
    tests = mock_input("tests", MISSING)
    if tests is MISSING:
        ok, tests, _ = P.get_test_summary(ORG, PROJECT, mid)
        if not ok:
            tests = None
    tnote = ""
    if tests:
        tnote = f" Tests: {tests['passed']}/{tests['total']} passed, {tests['failed']} failed."
    stage_note = f"{comp['ran']}/{comp['total']} stages ran"
    extras = []
    if comp["failed"]:
        extras.append(f"{len(comp['failed'])} red")
    if comp["yellow"]:
        extras.append(f"{len(comp['yellow'])} yellow")
    extra = f" ({', '.join(extras)} — triaged later)" if extras else ""
    stash_runs(state, **{f"mrwp_{provider.lower()}": mid})
    return Done(
        f"{label} run {mid} ran to completion — {stage_note}{extra}.{tnote}", links=links)
